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
RECOMMENDED_VARS = ("DATABASE_URL", "WEBHOOK_SECRET", "SETUP_KEY", "ADMIN_IDS")

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
    from config import load_config, redact_secrets
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

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    app = create_app(config, db, demo, gate, bot)

    dispatcher = Dispatcher()
    dispatcher["db"] = db
    dispatcher["config"] = config
    dispatcher["gate"] = gate
    dispatcher["demo"] = demo

    _throttle = ThrottleMiddleware(config.cooldown_seconds)
    _subscription = SubscriptionMiddleware()
    for _observer in (dispatcher.message, dispatcher.callback_query):
        _observer.middleware(_throttle)
        _observer.middleware(_subscription)

    dispatcher.include_router(handlers.router)

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

        info = {
            "ok": True,
            "bot": bot_name,
            "database": "postgres" if config.uses_postgres else "sqlite",
            "webapp_url": config.webapp_url,
            "admin_ids_set": bool(config.admin_ids),
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

    @app.post("/api/telegram")
    async def telegram_webhook(
        request: Request,
        secret: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
    ) -> JSONResponse:
        # Секрет задаётся при регистрации вебхука и приходит в заголовке.
        # Без него адрес вебхука мог бы дёргать кто угодно.
        if config.webhook_secret and secret != config.webhook_secret:
            raise HTTPException(status_code=403, detail="bad secret")

        payload = await request.json()

        try:
            await dispatcher.feed_webhook_update(bot, Update.model_validate(payload))
        except Exception:
            # Отвечать ошибкой нельзя: любой не-200 Telegram считает недоставкой
            # и шлёт тот же апдейт снова. На serverless это цикл из повторов и
            # дубли сообщений у пользователя.
            logger.exception("Апдейт %s не обработан", payload.get("update_id"))

        return JSONResponse({"ok": True})

    @app.get("/api/setup")
    async def setup(key: str = "") -> JSONResponse:
        """
        Открыть один раз после деплоя:
            https://<домен>/api/setup?key=<SETUP_KEY>
        Повторный вызов безопасен.
        """
        if not config.setup_key:
            raise HTTPException(
                status_code=400,
                detail="Не задана переменная SETUP_KEY — добавь её в Vercel и передеплой",
            )
        if key != config.setup_key:
            raise HTTPException(status_code=403, detail="Неверный ключ")

        base = config.webapp_url
        if not base.startswith("https://"):
            raise HTTPException(
                status_code=400, detail="Не удалось определить адрес: задай WEBAPP_URL"
            )

        await bot.set_webhook(
            url=f"{base}/api/telegram",
            secret_token=config.webhook_secret or None,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🧬 Главный экран"),
                BotCommand(command="stats", description="📊 Моя статистика"),
                BotCommand(command="about", description="ℹ️ О боте"),
                BotCommand(command="help", description="📖 Справка"),
            ]
        )
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=base))
        )

        me = await bot.get_me()
        info = await bot.get_webhook_info()

        return JSONResponse(
            {
                "ok": True,
                "bot": f"@{me.username}",
                "webhook": info.url,
                "webapp": base,
                "database": "postgres"
                if "postgres" in config.database_url
                else "sqlite (на Vercel данные будут теряться!)",
                "next": "Открой бота в Telegram и отправь /start",
            }
        )
