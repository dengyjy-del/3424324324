"""
Точка входа для Vercel.

Vercel запускает serverless-функции: постоянного процесса нет, поэтому long
polling здесь невозможен. Telegram сам шлёт апдейты POST-запросом на
/api/telegram — один запрос, один апдейт, ответ.

Про инициализацию: всё, что может упасть (чтение переменных окружения,
создание Bot, подключение файлов), обёрнуто в try. Если на этом этапе
происходит ошибка, приложение всё равно поднимается — но отдаёт страницу с
причиной. Без этого любая мелочь в конфиге превращается в безликий
FUNCTION_INVOCATION_FAILED, по которому нельзя понять вообще ничего.

Локально и на VPS ничего не меняется: там работает `python bot.py` с polling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

# Vercel кладёт функцию в api/, а модули проекта лежат уровнем выше.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("looksmax.vercel")

# ВАЖНО: это присваивание должно остаться на верхнем уровне файла.
# Сборщик Vercel ищет переменную `app` статическим разбором исходника, не
# запуская его, и присваивание внутри try/except попросту не видит — сборка
# падает с «Could not find a top-level "app"». Ниже объект заменяется на
# настоящее приложение, а при ошибке инициализации — на диагностическое.
app = FastAPI()

# Переменные, без которых приложение не поднимется или будет вести себя странно.
REQUIRED_VARS = ("BOT_TOKEN",)
RECOMMENDED_VARS = (
    "BOT_TOKENS",
    "DATABASE_URL",
    "WEBHOOK_SECRET",
    "SETUP_KEY",
    "ADMIN_IDS",
)

def _redact(text: str) -> str:
    """Локальная обёртка: config может не импортироваться при ранней ошибке."""
    try:
        from config import redact_secrets

        return redact_secrets(text)
    except Exception:
        return re.sub(r"://[^:/@\s]+:[^@/\s]+@", "://***:***@", text)


# ─────────────────────────── инициализация ─────────────────────────────────

BOOT_ERROR: str | None = None
BOOT_KIND: str | None = None

try:
    import handlers
    import texts
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.types import BotCommand, MenuButtonWebApp, Update, WebAppInfo
    from access import DemoState, SubscriptionGate
    from config import load_config, mask_token, redact_secrets
    from database import create_database
    from middlewares import SubscriptionMiddleware, ThrottleMiddleware
    from webapp.server import PUBLIC_DIR, create_app

    config = load_config()
    texts.configure(config.brand_name)

    if not (PUBLIC_DIR / "index.html").exists():
        # На Vercel интерфейс собирается отдельно и раздаётся с CDN, поэтому
        # в бандл функции он не попадает — это норма, а не ошибка. Локально
        # файл нужен, так что предупреждаем, но приложение не роняем.
        logger.warning(
            "Не найден %s — интерфейс отдаётся отдельной сборкой",
            PUBLIC_DIR / "index.html",
        )

    # Самая частая причина «работает локально, падает на Vercel»: не задан
    # DATABASE_URL, код уходит в SQLite, а файловая система функции доступна
    # только на чтение. Ловим это сразу, а не на первом запросе к базе.
    if config.is_serverless and not config.uses_postgres:
        raise RuntimeError(
            "Не задана переменная DATABASE_URL. На Vercel обязателен Postgres: "
            "файловая система функции доступна только для чтения, SQLite там "
            "не работает. Создай базу в Storage → Neon и передеплой проект."
        )

    db = create_database(config.database_url)
    demo = DemoState(db, config.demo_ttl_minutes)
    gate = SubscriptionGate(*config.gate_sources)

    # Боты создаются по требованию, а не все сразу. На холодный старт с
    # двадцатью зеркалами это экономит около полусекунды: конкретному
    # апдейту нужен один бот, а не весь список.
    def _make_bot(token: str) -> Bot:
        return Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    class BotRegistry:
        """Ленивый доступ к ботам по id. Ведёт себя как словарь на чтение."""

        def __init__(self) -> None:
            self._cache: dict[str, Bot] = {}

        def get(self, bot_id: str) -> Bot | None:
            key = str(bot_id).strip()
            if key in self._cache:
                return self._cache[key]
            token = config.token_for(key)
            if not token:
                return None
            self._cache[key] = _make_bot(token)
            return self._cache[key]

        def all(self) -> dict[str, Bot]:
            """Все боты разом — нужно только для setup и диагностики."""
            return {bot_id: self.get(bot_id) for bot_id in config.bot_ids}

        def __contains__(self, bot_id: object) -> bool:
            return bool(config.token_for(str(bot_id)))

        def __iter__(self):
            return iter(config.bot_ids)

        def __len__(self) -> int:
            return len(config.bot_ids)

    bots = BotRegistry()
    bot = bots.get(config.primary_id)

    app = create_app(config, db, demo, gate, bot, bots)

    # Диспетчер один на всех: хендлеры берут бота из самого апдейта
    # (message.bot), поэтому каждый отвечает от своего имени.
    dispatcher = Dispatcher()
    dispatcher["db"] = db
    dispatcher["config"] = config
    dispatcher["gate"] = gate
    dispatcher["demo"] = demo
    # Подписку проверяет всегда основной бот. Иначе администратором канала
    # пришлось бы делать все двадцать зеркал — а так достаточно одного.
    dispatcher["gate_bot"] = bot

    _throttle = ThrottleMiddleware(config.cooldown_seconds)
    _subscription = SubscriptionMiddleware()
    for _observer in (dispatcher.message, dispatcher.callback_query):
        _observer.middleware(_throttle)
        _observer.middleware(_subscription)

    dispatcher.include_router(handlers.router)

    # Одинаковое меню команд у основного бота и у всех зеркал.
    BOT_COMMANDS = [
        BotCommand(command="start", description="🧬 Главный экран"),
        BotCommand(command="stats", description="📊 Моя статистика"),
        BotCommand(command="about", description="ℹ️ О боте"),
        BotCommand(command="help", description="📖 Справка"),
    ]

except Exception as error:  # noqa: BLE001 — здесь ловим осознанно
    BOOT_KIND = type(error).__name__
    BOOT_ERROR = _redact(str(error)) or BOOT_KIND
    logger.exception("Инициализация не удалась")
    app = FastAPI()


# ─────────────────────── режим диагностики ─────────────────────────────────

DIAGNOSTIC_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Приложение не запустилось</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
   background:#07080d;color:#f2f3f7;
   font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,system-ui,sans-serif}}
 .card{{max-width:560px;width:100%;background:rgba(255,255,255,.055);
   border:1px solid rgba(255,255,255,.11);border-radius:22px;padding:26px}}
 h1{{margin:0 0 6px;font-size:21px;letter-spacing:-.02em}}
 p{{margin:0 0 18px;color:rgba(242,243,247,.58);font-size:14px}}
 pre{{margin:0 0 20px;padding:14px;border-radius:13px;background:rgba(255,61,113,.1);
   border:1px solid rgba(255,61,113,.25);color:#ff9db5;font-size:12.5px;
   white-space:pre-wrap;word-break:break-word}}
 h2{{margin:0 0 10px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
   color:rgba(242,243,247,.34)}}
 ul{{list-style:none;margin:0 0 20px;padding:0}}
 li{{display:flex;justify-content:space-between;gap:12px;padding:9px 0;
   border-bottom:1px solid rgba(255,255,255,.06);font-size:13.5px}}
 li:last-child{{border:0}} code{{font-size:12.5px;opacity:.8}}
 .ok{{color:#00e0c6}} .no{{color:#ff3d71}} .dim{{color:rgba(242,243,247,.34)}}
</style></head><body><div class="card">
<h1>Приложение не запустилось</h1>
<p>Функция поднялась, но инициализация не прошла. Причина ниже.</p>
<pre>{kind}: {message}</pre>
<h2>Переменные окружения</h2>
<ul>{rows}</ul>
<p class="dim" style="font-size:12.5px">Значения не показываются — только факт
наличия. После правки переменных нужен новый деплой: Vercel подхватывает их
только при сборке.</p>
</div></body></html>"""


def _env_rows() -> str:
    rows = []
    for name in REQUIRED_VARS + RECOMMENDED_VARS:
        present = bool((os.getenv(name) or "").strip())
        required = name in REQUIRED_VARS
        if present:
            mark, css = "задана", "ok"
        elif required:
            mark, css = "не задана", "no"
        else:
            mark, css = "не задана", "dim"
        rows.append(f'<li><code>{name}</code><span class="{css}">{mark}</span></li>')
    return "".join(rows)


if BOOT_ERROR is not None:

    @app.get("/api/health")
    async def broken_health() -> JSONResponse:
        # Показываем сразу и причину, и какие переменные заданы, чтобы всё
        # можно было починить за один заход. Значения не раскрываем.
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": BOOT_ERROR,
                "kind": BOOT_KIND,
                "env": {
                    name: bool((os.getenv(name) or "").strip())
                    for name in REQUIRED_VARS + RECOMMENDED_VARS
                },
            },
        )

    @app.api_route("/{full_path:path}", methods=["GET", "POST"])
    async def diagnostics(full_path: str) -> HTMLResponse:
        return HTMLResponse(
            DIAGNOSTIC_PAGE.format(
                kind=BOOT_KIND, message=BOOT_ERROR, rows=_env_rows()
            ),
            status_code=503,
        )

else:
    # ───────────────────────── обычная работа ──────────────────────────────

    _ready = False

    async def _ensure_ready() -> None:
        # Пул к базе создаётся один раз и переиспользуется, пока инстанс «тёплый».
        global _ready
        if not _ready:
            await db.connect()
            _ready = True

    @app.middleware("http")
    async def _db_ready(request: Request, call_next):
        try:
            await _ensure_ready()
        except Exception as error:
            logger.exception("Не удалось подключиться к базе")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "База данных недоступна: "
                    f"{type(error).__name__}. {_redact(str(error))[:180]} "
                    "Проверь DATABASE_URL и открой /api/health."
                },
            )
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        """Проверка живости: отвечает ли база и как настроен проект."""
        try:
            me = await bot.get_me()
            bot_name = f"@{me.username}"
        except Exception:  # noqa: BLE001 — имя не критично для проверки
            bot_name = "не удалось определить"

        import rating as _rating

        info = {
            "ok": True,
            "model": _rating.MODEL_VERSION,
            "bot": bot_name,
            "database": "postgres" if config.uses_postgres else "sqlite",
            "webapp_url": config.webapp_url,
            "admin_ids_set": bool(config.admin_ids),
            # Сколько ботов подхватилось из переменных. Если зеркал меньше,
            # чем вписано, — токен не прошёл проверку формата.
            "bots_total": len(bots),
            "mirrors": len(bots) - 1,
            "bot_ids": list(bots),
            "skipped_vars": list(config.rejected_token_vars),
            "details": "подробности по каждому боту: /api/bots?key=<SETUP_KEY>",
        }

        # Гейт подписки — самая частая причина «почему не требует подписку».
        channel = {"configured": bool(config.channel_id), "id": config.channel_id}
        if config.channel_id:
            try:
                me = await bot.get_me()
                member = await bot.get_chat_member(config.channel_id, me.id)
                channel["bot_is_admin"] = member.status in ("administrator", "creator")
                if not channel["bot_is_admin"]:
                    channel["problem"] = (
                        "Бот не администратор канала — проверка подписки "
                        "невозможна, пользователи проходят без неё."
                    )
            except Exception as error:
                channel["bot_is_admin"] = False
                channel["problem"] = (
                    f"{type(error).__name__}: {_redact(str(error))[:140]}"
                )
        else:
            channel["problem"] = "CHANNEL_ID пуст — гейт выключен"

        info["channel"] = channel
        try:
            await _ensure_ready()
            await db.ping()
            info["database_connected"] = True
        except Exception as error:
            info["ok"] = False
            info["database_connected"] = False
            info["database_error"] = (
                f"{type(error).__name__}: {_redact(str(error))[:200]}"
            )
            return JSONResponse(status_code=503, content=info)

        return JSONResponse(info)

    async def _handle_update(target: Bot, request: Request, secret: str) -> JSONResponse:
        # Секрет задаётся при регистрации вебхука и приходит в заголовке.
        # Без него адрес вебхука мог бы дёргать кто угодно.
        if config.webhook_secret and secret != config.webhook_secret:
            raise HTTPException(status_code=403, detail="bad secret")

        payload = await request.json()

        try:
            await dispatcher.feed_webhook_update(target, Update.model_validate(payload))
        except Exception:
            # Отвечать ошибкой нельзя: любой не-200 Telegram считает недоставкой
            # и шлёт тот же апдейт снова. На serverless это цикл из повторов и
            # дубли сообщений у пользователя.
            logger.exception("Апдейт %s не обработан", payload.get("update_id"))

        return JSONResponse({"ok": True})

    @app.post("/api/telegram")
    async def telegram_webhook(
        request: Request,
        secret: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
    ) -> JSONResponse:
        """Адрес без id — основной бот. Оставлен ради старых вебхуков."""
        return await _handle_update(bot, request, secret)

    @app.post("/api/telegram/{bot_key}")
    async def telegram_webhook_for(
        bot_key: str,
        request: Request,
        secret: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
    ) -> JSONResponse:
        """
        По адресу на каждого бота: /api/telegram/<id бота>.

        Telegram не сообщает в апдейте, какому боту тот адресован, — узнать
        это можно только по адресу, на который он пришёл. Поэтому id зашит
        в путь. Секретом id не является: он и так виден всем в @username_bot
        через getMe, а от чужих запросов защищает заголовок с секретом.
        """
        target = bots.get(bot_key.strip())
        if target is None:
            # 200, а не 404: на ошибку Telegram будет слать апдейт повторно
            # часами. Токен убрали из переменных — вебхук надо снять, а не
            # ронять функцию.
            logger.warning(
                "Апдейт для неизвестного бота %s — токена нет в переменных",
                bot_key,
            )
            return JSONResponse({"ok": True, "ignored": "unknown bot"})

        return await _handle_update(target, request, secret)

    @app.get("/api/setup")
    async def setup(key: str = "", only: str = "") -> JSONResponse:
        """
        Открыть один раз после деплоя — и потом после каждого добавления
        зеркала:
            https://<домен>/api/setup?key=<SETUP_KEY>

        Настраивает разом основного бота и все зеркала. Повторный вызов
        безопасен. Один бот отдельно: &only=<id бота>.
        """
        if not config.setup_key:
            raise HTTPException(
                status_code=400,
                detail="Не задана переменная SETUP_KEY — добавь её в Vercel и передеплой",
            )
        if key != config.setup_key:
            # Показываем ровно столько, чтобы владелец мог сверить свой ключ
            # с тем, что реально лежит в переменных, но не сам ключ.
            expected = config.setup_key
            raise HTTPException(
                status_code=403,
                detail=(
                    "Неверный ключ. В переменных Vercel лежит ключ длиной "
                    f"{len(expected)} символов, начинающийся на "
                    f"«{expected[:3]}» и заканчивающийся на «{expected[-2:]}». "
                    f"Ты передал длину {len(key)}"
                    + (f", начало «{key[:3]}»." if key else ".")
                    + " Проверь SETUP_KEY в Settings → Environment Variables "
                    "или перезапиши его и передеплой."
                ),
            )

        base = config.webapp_url
        if not base.startswith("https://"):
            raise HTTPException(
                status_code=400, detail="Не удалось определить адрес: задай WEBAPP_URL"
            )

        targets = _select_bots(only)
        if not targets:
            raise HTTPException(
                status_code=404,
                detail=f"Бот «{only}» не найден. Настроенные id: "
                + ", ".join(bots) ,
            )

        results = await _setup_many(targets, base)
        failed = [r for r in results if not r["ok"]]

        return JSONResponse(
            status_code=200 if not failed else 207,
            content={
                "ok": not failed,
                "bots_total": len(results),
                "bots_ok": len(results) - len(failed),
                "webapp": base,
                "database": "postgres"
                if "postgres" in config.database_url
                else "sqlite (на Vercel данные будут теряться!)",
                "bots": results,
                "skipped_vars": list(config.rejected_token_vars),
                "next": "Открой любого из ботов в Telegram и отправь /start"
                if not failed
                else "Часть ботов не настроилась — смотри поле error у них. "
                "Исправь токен и открой этот адрес ещё раз, "
                "можно точечно: /api/setup?key=...&only=<id бота>",
            },
        )

    async def _setup_one(bot_id: str, target: Bot, base: str) -> dict:
        """Настройка одного бота. Ошибка одного не мешает остальным."""
        result: dict = {"id": bot_id, "ok": False, "token": mask_token(target.token)}
        try:
            me = await target.get_me()
            result["bot"] = f"@{me.username}"

            await target.set_webhook(
                url=f"{base}/api/telegram/{bot_id}",
                secret_token=config.webhook_secret or None,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query", "my_chat_member"],
            )
            await target.set_my_commands(BOT_COMMANDS)
            await target.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Приложение", web_app=WebAppInfo(url=base)
                )
            )

            info = await target.get_webhook_info()
            result["webhook"] = info.url
            result["ok"] = True
        except Exception as error:  # noqa: BLE001 — причину показываем владельцу
            result["error"] = f"{type(error).__name__}: {_redact(str(error))[:200]}"
            if "Unauthorized" in str(error):
                result["hint"] = (
                    "Токен недействителен: бот удалён или токен перевыпущен "
                    "в @BotFather. Обнови переменную и передеплой."
                )
        return result

    async def _setup_many(targets: dict[str, Bot], base: str) -> list[dict]:
        """
        Настраиваем ботов параллельно, но не все разом.

        Последовательно двадцать ботов по четыре запроса каждый не уложатся
        в лимит времени функции. Семафор держит нагрузку в разумных рамках,
        чтобы Telegram не начал отвечать 429.
        """
        limit = asyncio.Semaphore(6)

        async def worker(bot_id: str, target: Bot) -> dict:
            async with limit:
                return await _setup_one(bot_id, target, base)

        return list(
            await asyncio.gather(
                *(worker(bot_id, target) for bot_id, target in targets.items())
            )
        )

    def _select_bots(only: str) -> dict[str, Bot]:
        """Пусто — все боты. Иначе — один по id или по номеру в списке."""
        only = (only or "").strip().lstrip("@")
        if not only:
            return bots.all()
        target = bots.get(only)
        if target is not None:
            return {only: target}
        if only.isdigit():
            index = int(only) - 1
            ids = list(bots)
            if 0 <= index < len(ids):
                return {ids[index]: bots.get(ids[index])}
        return {}

    @app.get("/api/bots")
    async def bots_status(key: str = "") -> JSONResponse:
        """
        Состояние всех ботов сразу: кто отвечает, куда смотрит вебхук,
        нет ли зависших апдейтов. Первое место, куда смотреть, если
        «зеркало молчит».
        """
        if not config.setup_key or key != config.setup_key:
            raise HTTPException(status_code=403, detail="Неверный ключ")

        base = config.webapp_url

        async def one(bot_id: str, target: Bot) -> dict:
            row: dict = {"id": bot_id, "ok": False, "token": mask_token(target.token)}
            try:
                me = await target.get_me()
                row["bot"] = f"@{me.username}"
                info = await target.get_webhook_info()
                row["webhook"] = info.url or ""
                row["expected"] = f"{base}/api/telegram/{bot_id}"
                # Совпадение адреса — главная проверка: чаще всего зеркало
                # молчит именно потому, что вебхук ему никто не поставил.
                row["webhook_ok"] = info.url in (
                    row["expected"],
                    f"{base}/api/telegram" if bot_id == config.primary_id else "",
                )
                row["pending"] = info.pending_update_count
                if info.last_error_message:
                    row["last_error"] = info.last_error_message[:160]
                row["ok"] = bool(row["webhook_ok"])
                if not row["webhook_ok"]:
                    row["hint"] = (
                        "Вебхук не выставлен или указывает не туда — "
                        "открой /api/setup?key=...&only=" + bot_id
                    )
            except Exception as error:  # noqa: BLE001
                row["error"] = f"{type(error).__name__}: {_redact(str(error))[:160]}"
            return row

        limit = asyncio.Semaphore(6)

        async def worker(bot_id: str, target: Bot) -> dict:
            async with limit:
                return await one(bot_id, target)

        rows = list(
            await asyncio.gather(
                *(worker(bot_id, target) for bot_id, target in bots.all().items())
            )
        )

        return JSONResponse(
            {
                "ok": all(row["ok"] for row in rows),
                "primary": config.primary_id,
                "total": len(rows),
                "working": sum(1 for row in rows if row["ok"]),
                "skipped_vars": list(config.rejected_token_vars),
                "bots": rows,
            }
        )
