"""
Бэкенд Telegram Mini App.

Отдаёт статику приложения и JSON API. Авторизация — только через подпись
Telegram (webapp/auth.py): ни паролей, ни почты, ни своей сессионной базы.
initData проверяется на каждом запросе, состояние на сервере не хранится.

Фотография не сохраняется: из байтов берётся sha256, он же становится
идентификатором снимка для детерминированной оценки, после чего байты
выбрасываются.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
import hmac
import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import engagement
import peer
import rating
from access import DemoState, SubscriptionGate
from config import Config, redact_secrets
from database import BaseDatabase as Database
from webapp.auth import AuthError, TelegramUser, verify_init_data
from aiogram.types import BufferedInputFile

import keyboards
import texts
from webapp.guides import GUIDES

logger = logging.getLogger("looksmax.webapp")

# Ассеты лежат в public/ — Vercel раздаёт эту папку через CDN, не будя
# функцию. Локальный uvicorn берёт файлы оттуда же, чтобы источник был один.
PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
STATIC_DIR = PUBLIC_DIR / "static"

# Потолок продиктован платформой: serverless-функции Vercel не принимают
# тело больше 4.5 МБ. Клиент ужимает снимок до ~300 КБ ещё в браузере,
# так что до этого лимита доходит только мусор.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

# Фото анкеты хранится в базе, поэтому лимит жёстче: браузер ужимает снимок
# до ~700px перед отправкой, и в норме получается 60-120 КБ.
MAX_PEER_PHOTO_BYTES = 900 * 1024
# Папку ищем по нескольким путям: на serverless корень проекта не всегда
# совпадает с тем, что видно из модуля, и одна жёсткая константа молча даёт
# пустой список вместо ошибки.
SEED_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "public" / "seed",
    Path.cwd() / "public" / "seed",
    Path("/var/task/public/seed"),
)


def _seed_dir() -> Path | None:
    for candidate in SEED_CANDIDATES:
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


SEED_DIR = SEED_CANDIDATES[0]
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


SEED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _filler_identity(key: str, name: str | None, age: int | None):
    """Явно заданные имя и возраст, иначе выведенные из ключа."""
    if name and age:
        return name, age
    parsed = peer.parse_identity(key)
    return parsed if parsed else peer.filler_identity(key)


def seed_files() -> list[str]:
    """Снимки из папки репозитория. Пустой список — не ошибка, а вариант."""
    folder = _seed_dir()
    if folder is None:
        return []
    try:
        return sorted(
            item.name
            for item in folder.iterdir()
            if item.suffix.lower() in SEED_EXTENSIONS
        )
    except OSError:
        return []


def seed_photo_from_folder(name: str) -> bytes | None:
    """Читает снимок из папки. Имя без разделителей пути — проверено выше."""
    folder = _seed_dir()
    if folder is None or "/" in name or "\\" in name or ".." in name:
        return None
    try:
        return (folder / name).read_bytes()
    except OSError:
        return None


def seed_diagnostics() -> dict:
    """Что сервер видит на самом деле — чтобы не гадать при отладке."""
    return {
        "found": str(_seed_dir()) if _seed_dir() else None,
        "checked": [str(path) for path in SEED_CANDIDATES],
        "files": seed_files(),
    }


async def notify_report(
    bot, db, config, report_id: int, reporter_id: int, target: str, reason: str
) -> None:
    """
    Отправляет жалобу владельцу вместе с фото и кнопками решения.

    Молчаливый сбой здесь недопустим: непойманная жалоба означает, что
    спорный снимок продолжает показываться людям.
    """
    if bot is None or not config.admin_ids:
        logger.error("Жалоба %s не отправлена: нет бота или ADMIN_IDS", report_id)
        return

    title = peer.REASON_TITLES.get(reason, reason)
    caption = (
        f"🚨 <b>Жалоба #{report_id}</b>\n"
        f"Причина: {title}\n"
        f"На: <code>{target}</code>\n"
        f"От: <code>{reporter_id}</code>"
    )

    payload: bytes | None = None
    if target.startswith("u:"):
        with contextlib.suppress(ValueError):
            payload = await db.peer_photo(int(target[2:]))

    markup = keyboards.report_actions(report_id, target)
    for admin_id in config.admin_ids:
        try:
            if payload:
                await bot.send_photo(
                    admin_id,
                    BufferedInputFile(payload, filename="report.jpg"),
                    caption=caption,
                    reply_markup=markup,
                )
            else:
                await bot.send_message(admin_id, caption, reply_markup=markup)
        except Exception:  # noqa: BLE001
            logger.exception("Не доставлена жалоба %s админу %s", report_id, admin_id)


def create_app(
    config: Config,
    db: Database,
    demo: DemoState,
    gate: SubscriptionGate | None = None,
    bot=None,
    bots: dict[str, object] | None = None,
) -> FastAPI:
    """
    bot  — основной бот: через него проверяется подписка и уходят служебные
           запросы, поэтому админом канала достаточно сделать только его.
    bots — все боты по id, включая зеркала. Нужен, чтобы подставлять в
           интерфейс имя того бота, через которого пользователь пришёл.
    """
    app = FastAPI(title="Looksmax Mini App", docs_url=None, redoc_url=None)

    def _bot_by_id(bot_id: str):
        """
        Бот по id. Принимает и обычный словарь, и ленивый реестр с Vercel —
        от объекта нужен только метод get. Неизвестный id — основной бот.
        """
        if bots is not None and bot_id:
            found = bots.get(str(bot_id))
            if found is not None:
                return found
        return bot

    @app.exception_handler(Exception)
    async def any_error(request, error: Exception) -> JSONResponse:
        """
        Без этого необработанная ошибка уходит HTML-страницей платформы, и
        фронт показывает бесполезное «Что-то пошло не так». Отдаём JSON с
        типом ошибки, чтобы причина была видна прямо в интерфейсе.
        """
        logger.exception("Ошибка на %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"Сбой сервера: {type(error).__name__}. "
                f"{redact_secrets(str(error))[:180]}"
            },
        )

    # Имена ботов по id. Кешируем: getMe на каждый запрос — лишний поход в
    # Telegram, а имя меняется раз в жизни.
    bot_name_cache: dict[str, str] = {}

    async def configured_bot_name(bot_id: str = "") -> str:
        """Имя бота, через которого открыт мини-апп. Пусто = основной."""
        key = str(bot_id or config.primary_id)
        if key not in bot_name_cache:
            target = _bot_by_id(key)
            if target is None:
                return ""
            try:
                me = await target.get_me()
                bot_name_cache[key] = f"@{me.username}"
            except Exception:  # noqa: BLE001 — имя не критично
                bot_name_cache[key] = ""
        return bot_name_cache.get(key, "")

    # ─────────────────────────── авторизация ───────────────────────────

    async def current_user(
        authorization: str = Header(default=""),
        x_init_data: str = Header(default="", alias="X-Init-Data"),
    ) -> TelegramUser:
        init_data = x_init_data or authorization.removeprefix("tma ").strip()
        try:
            # Проверяем сразу по всем токенам: приложение одно на основного
            # бота и на все зеркала.
            return verify_init_data(init_data, config.bot_tokens)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    # ──────────────────────────── страницы ─────────────────────────────

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            PUBLIC_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    # ───────────────────────────── API ─────────────────────────────────

    @app.post("/api/session")
    async def session(user: TelegramUser = Depends(current_user)) -> dict:
        """Первый запрос приложения: кто пользователь и что ему уже доступно."""
        await db.ensure_user(user.id)
        age = await db.get_declared_age(user.id)
        stats = await db.get_stats(user.id)

        return {
            "user": {
                "id": user.id,
                "name": user.display_name,
                "username": user.username,
                "photo": user.photo_url,
            },
            "onboarded": age is not None,
            "age_ok": age is not None and age >= config.min_age,
            "min_age": config.min_age,
            "brand": config.brand_name,
            # Нужен для карточки «поделиться»: подпись не зашита в код,
            # поэтому при смене бота ничего править не придётся. У зеркал
            # подставляется имя того бота, через которого человек вошёл, —
            # иначе приглашения из зеркала уводили бы на основного бота.
            "bot_username": await configured_bot_name(user.bot_id),
            "is_admin": config.is_admin(user.id),
            "peer_available": await _peer_enabled(user.id),
            "legal": {"terms": config.terms_url, "privacy": config.privacy_url},
            "theme": await db.get_theme(user.id) or "classic",
            "model_version": rating.MODEL_VERSION,
            "stats": _stats_payload(stats),
            "subscribed": await _is_subscribed(user.id),
            "channel": {
                "required": bool(gate is not None and gate.enabled and bot is not None),
                "url": config.channel_url,
                "title": config.channel_title,
            },
        }

    THEMES = ("classic", "graphite", "mocha", "sapphire")

    @app.post("/api/theme")
    async def set_theme(
        theme: str = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        if theme not in THEMES:
            raise HTTPException(status_code=400, detail="Неизвестное оформление")
        await db.set_theme(user.id, theme)
        return {"ok": True, "theme": theme}

    @app.post("/api/age")
    async def declare_age(
        age: int = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        if not 5 <= age <= 120:
            raise HTTPException(status_code=400, detail="Укажи реальный возраст")

        await db.set_declared_age(user.id, age)
        return {"age_ok": age >= config.min_age, "min_age": config.min_age}

    @app.post("/api/check-subscription")
    async def check_subscription(user: TelegramUser = Depends(current_user)) -> dict:
        # Сбрасываем кеш, иначе кнопка не сработает сразу после подписки.
        if gate is not None:
            gate.forget(user.id)
        return {"subscribed": await _is_subscribed(user.id)}

    @app.post("/api/rate")
    async def rate(
        photo: UploadFile | None = File(default=None),
        photo_hash: str = Form(default=""),
        metrics: str = Form(default=""),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        """
        Два пути.

        Основной: браузер сам нашёл лицо и посчитал геометрию — присылает
        хеш снимка и замеры, само изображение никуда не уходит.

        Запасной: разметка лиц не загрузилась (старое устройство, недоступен
        CDN) — тогда как раньше принимаем файл. Ронять продукт из-за этого
        нельзя, но и проверить наличие лица в этом случае невозможно.
        """
        await _require_age(user.id)
        await _require_subscription(user.id)

        # Суточный лимит: даёт причину вернуться завтра и заодно не
        # превращает приложение в перепроверку своей оценки по десять раз
        # за вечер. Не действует в режиме съёмки и у ID из ADMIN_IDS —
        # владельцу лимит мешал бы снимать контент и проверять сборки.
        in_demo_check = await demo.is_active(user.id)
        if not in_demo_check and not config.is_admin(user.id):
            today_key = date.today().isoformat()
            bought = await db.count_purchases(user.id, f"scan:{today_key}:")
            gift = await _gift_scans()
            used = await db.count_ratings_since(user.id, _day_start())
            if used >= config.daily_scan_limit + bought + gift:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Отчёты на сегодня закончились. Можно докупить попытку "
                        f"за {engagement.EXTRA_SCAN_PRICE} XP или вернуться "
                        "после полуночи."
                    ),
                )

        await _apply_strictness()

        in_demo = in_demo_check
        profile = rating.DEMO if in_demo else rating.NORMAL
        face: rating.FaceMetrics | None = None

        if metrics:
            if not re.fullmatch(r"[0-9a-f]{16,64}", photo_hash or ""):
                raise HTTPException(status_code=400, detail="Некорректный снимок")
            try:
                face = rating.FaceMetrics.from_payload(json.loads(metrics))
            except (ValueError, json.JSONDecodeError):
                raise HTTPException(
                    status_code=422,
                    detail="Не удалось разобрать лицо. Попробуй другое фото.",
                ) from None
            photo_id = photo_hash[:32]
        else:
            if photo is None:
                raise HTTPException(status_code=400, detail="Нужно фото")
            if photo.content_type not in ALLOWED_TYPES:
                raise HTTPException(
                    status_code=415, detail="Поддерживаются JPEG, PNG, WebP и HEIC"
                )

            payload = await photo.read(MAX_UPLOAD_BYTES + 1)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Файл слишком большой")
            if len(payload) < 1024:
                raise HTTPException(status_code=400, detail="Файл повреждён или пуст")

            # Единственное, что остаётся от снимка. Байты дальше не живут.
            photo_id = hashlib.sha256(payload).hexdigest()[:32]
            del payload

        report = (
            rating.measured_report(user.id, photo_id, face, config.score_salt, profile)
            if face is not None
            else rating.generate_report(user.id, photo_id, config.score_salt, profile)
        )

        if not in_demo:
            await db.save_rating(user.id, report.report_id, report.overall)

        if not in_demo:
            await db.award_xp(
                user.id, f"scan:{date.today().isoformat()}", engagement.XP_PER_SCAN
            )

        payload = _report_payload(report, hide_id=in_demo)
        payload["model_version"] = rating.MODEL_VERSION
        payload["categories"] = rating.category_scores(report)
        # Владельцу отдаём сырые замеры: по ним видно, что именно посчитал
        # браузер, и совпадает ли это с тем, что считаю я при разборе жалоб.
        if config.is_admin(user.id) and face is not None:
            payload["debug"] = {
                "raw": round(rating.model_score(face), 2),
                "strictness": rating.STRICTNESS,
                "metrics": {k: round(getattr(face, k), 4) for k in rating.MODEL_KEYS},
            }

        payload["measurements"] = (
            rating.metrics_readout(face) if face is not None and not in_demo else []
        )
        return payload

    @app.get("/api/today")
    async def today(user: TelegramUser = Depends(current_user)) -> dict:
        """Экран «Сегодня»: привычки, серия, ранг, остаток сканов."""
        await db.ensure_user(user.id)
        return await _today_payload(user.id)

    @app.post("/api/habit")
    async def toggle_habit(
        key: str = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        await _require_age(user.id)
        await _require_subscription(user.id)

        today_key = date.today().isoformat()
        marks = await db.get_habits(user.id, today_key)

        try:
            updated = engagement.toggle(marks.get(today_key, 0), key)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        await db.set_habit_mask(user.id, today_key, updated)

        # Ключ события содержит дату и привычку, поэтому снять и поставить
        # галочку заново второй раз XP уже не принесёт.
        for habit in engagement.HABITS:
            if updated & (1 << habit.bit):
                await db.award_xp(
                    user.id, f"habit:{today_key}:{habit.key}", engagement.XP_PER_HABIT
                )
        if engagement.is_day_counted(updated):
            await db.award_xp(user.id, f"day:{today_key}", engagement.XP_DAY_BONUS)

        return await _today_payload(user.id)

    @app.get("/api/profile")
    async def profile(user: TelegramUser = Depends(current_user)) -> dict:
        stats = await db.get_stats(user.id)

        # Показатели ChadMatch идут рядом со сканами: это два ответа на один
        # вопрос, и пользователю интереснее видеть их вместе.
        available = await _peer_enabled(user.id)
        peer_block = {"available": available, "has_profile": False, "votes": 0, "rated": 0}

        if available:
            own = await db.peer_profile(user.id)
            result = await db.peer_result(f"u:{user.id}")
            peer_block.update(
                has_profile=bool(own and own.get("photo_key")),
                votes=result["count"],
                average=result["average"],
                tier=(
                    peer.tier_for_score(result["average"]).title
                    if result["count"] >= 3
                    else None
                ),
                rated=len(await db.peer_seen(user.id)),
            )

        return {
            "user": {"name": user.display_name, "photo": user.photo_url},
            "stats": _stats_payload(stats),
            "peer": peer_block,
        }

    @app.get("/api/history")
    async def history(
        period: str = "day", user: TelegramUser = Depends(current_user)
    ) -> dict:
        if period not in ("day", "week", "month"):
            raise HTTPException(status_code=400, detail="Неизвестный период")

        points = await db.history(user.id, period, limit=30)
        return {
            "period": period,
            "points": [
                {"label": p.label, "value": p.value, "count": p.count} for p in points
            ],
        }

    @app.get("/api/guides")
    async def guides(user: TelegramUser = Depends(current_user)) -> dict:
        owned = {g for g in await db.purchased(user.id) if not g.startswith("scan:")}
        earned, spent = await db.xp_balance(user.id)
        unlimited = config.is_admin(user.id)

        return {
            "guides": GUIDES,
            "balance": earned - spent,
            "unlimited": unlimited,
            "shop": [
                {
                    "id": guide.id,
                    "emoji": guide.emoji,
                    "title": guide.title,
                    "tagline": guide.tagline,
                    "price": guide.price,
                    "owned": guide.id in owned,
                    # Содержимое отдаём только купившему, иначе платный
                    # раздел можно было бы прочитать прямо из ответа API.
                    "blocks": list(guide.blocks) if guide.id in owned else [],
                }
                for guide in engagement.SHOP
            ],
        }

    @app.post("/api/buy")
    async def buy(
        guide_id: str = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        await _require_subscription(user.id)

        guide = engagement.SHOP_BY_ID.get(guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail="Гайд не найден")

        if guide_id in await db.purchased(user.id):
            return {"ok": True, "already": True}

        earned, spent = await db.xp_balance(user.id)
        free = config.is_admin(user.id)

        if not free and earned - spent < guide.price:
            raise HTTPException(
                status_code=402,
                detail=f"Не хватает {guide.price - (earned - spent)} XP",
            )

        # У владельца покупка ничего не списывает
        if not await db.purchase(user.id, guide_id, 0 if free else guide.price):
            return {"ok": True, "already": True}

        return {"ok": True, "balance": earned - spent - (0 if free else guide.price)}

    @app.post("/api/buy-scan")
    async def buy_scan(user: TelegramUser = Depends(current_user)) -> dict:
        """Докупка одной попытки за XP."""
        await _require_subscription(user.id)

        if config.is_admin(user.id):
            return {"ok": True, "unlimited": True}

        today_key = date.today().isoformat()
        bought = await db.count_purchases(user.id, f"scan:{today_key}:")

        if bought >= engagement.EXTRA_SCANS_PER_DAY:
            raise HTTPException(
                status_code=429,
                detail=f"Больше {engagement.EXTRA_SCANS_PER_DAY} докупок в день нельзя",
            )

        earned, spent = await db.xp_balance(user.id)
        price = engagement.EXTRA_SCAN_PRICE
        if earned - spent < price:
            raise HTTPException(
                status_code=402, detail=f"Не хватает {price - (earned - spent)} XP"
            )

        if not await db.purchase(user.id, f"scan:{today_key}:{bought + 1}", price):
            raise HTTPException(status_code=409, detail="Попробуй ещё раз")

        return {"ok": True, "balance": earned - spent - price}

    @app.post("/api/label")
    async def add_label(
        photo_hash: str = Form(...),
        score: float = Form(...),
        metrics: str = Form(...),
        refresh_only: int = Form(default=0),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        """
        Ручная разметка для обучения. Только для ID из ADMIN_IDS.

        Фотография сюда не приходит и нигде не сохраняется: замеры считает
        браузер, а на сервер уходят только числа и выставленный балл.
        """
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")

        if not 0.0 <= score <= 10.0:
            raise HTTPException(status_code=400, detail="Балл от 0 до 10")

        try:
            payload = json.loads(metrics)
            checked = rating.FaceMetrics.from_payload(payload)
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=422, detail="Замеры не разобрать") from None

        # Сохраняем разобранные значения, а не присланные: from_payload уже
        # подставил разумные величины вместо пустых и вышедших за диапазон.
        metrics = json.dumps(dataclasses.asdict(checked))

        try:
            # Если фото уже размечено, обновляем у него замеры и оставляем
            # прежнюю оценку. Так после появления новых признаков всю
            # выборку можно пересчитать, не проставляя баллы заново.
            existing = await db.refresh_label(photo_hash[:32], metrics)
            if existing is not None:
                stats = await db.label_stats()
                return {
                    "ok": True,
                    "added": False,
                    "refreshed": True,
                    "score": existing,
                    "total": stats["total"],
                }

            # В режиме пересчёта незнакомое фото пропускаем. Иначе оно попадёт
            # в выборку с оценкой-заглушкой и испортит обучение — так уже
            # случилось однажды: 83 снимка получили ровно 5.0 и обрушили
            # качество модели.
            if refresh_only:
                stats = await db.label_stats()
                return {
                    "ok": True,
                    "added": False,
                    "refreshed": False,
                    "skipped": True,
                    "total": stats["total"],
                }

            added = await db.add_label(photo_hash[:32], round(score, 2), metrics)
            stats = await db.label_stats()
        except Exception as error:  # noqa: BLE001
            logger.exception("Разметка не сохранилась")
            raise HTTPException(
                status_code=500,
                detail=f"Не сохранилось: {type(error).__name__}",
            ) from error

        return {"ok": True, "added": added, "total": stats["total"]}

    @app.post("/api/feedback")
    async def feedback(
        photo_hash: str = Form(...),
        score: float = Form(...),
        metrics: str = Form(...),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        """
        Оценка от самого пользователя: «а сколько бы поставил ты».

        Даёт обучающие данные без единой фотографии на сервере — приходят
        те же замеры и человеческое число. Помечаем источником "u", чтобы
        при обучении отделять от разметки владельца: люди систематически
        завышают себе, и этот сдвиг надо вычитать, а не выучивать.
        """
        await _require_age(user.id)

        if not 0.0 <= score <= 10.0:
            raise HTTPException(status_code=400, detail="Балл от 0 до 10")
        if not re.fullmatch(r"[0-9a-f]{16,64}", photo_hash or ""):
            raise HTTPException(status_code=400, detail="Некорректный снимок")

        try:
            rating.FaceMetrics.from_payload(json.loads(metrics))
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=422, detail="Замеры не разобрать") from None

        await db.add_label(f"u:{photo_hash[:30]}", round(score, 2), metrics)
        await db.award_xp(user.id, f"feedback:{photo_hash[:16]}", 3)
        return {"ok": True}

    @app.post("/api/labels/cleanup")
    async def cleanup_labels(
        score: float = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        """Удаляет записи с указанной оценкой — для чистки заглушек."""
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")
        removed = await db.delete_labels_by_score(round(score, 2))
        stats = await db.label_stats()
        return {"ok": True, "removed": removed, "total": stats["total"]}

    @app.get("/api/labels")
    async def labels(
        export: int = 0, user: TelegramUser = Depends(current_user)
    ) -> dict:
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")

        stats = await db.label_stats()
        if export:
            stats["rows"] = await db.export_labels()
        return stats


    # ═══════════════════ РЕЖИМ ВЗАИМНЫХ ОЦЕНОК ════════════════════════

    async def _peer_enabled(user_id: int) -> bool:
        """
        Доступен ли ChadMatch. Кроме настроек сборки учитывается рубильник
        в базе: пользовательский контент иногда нужно остановить немедленно,
        а передеплой на это тратить нельзя.
        """
        if config.is_admin(user_id) or user_id in config.peer_ids:
            return True
        if (await db.get_setting("peer_off") or "") == "1":
            return False
        return config.peer_open

    async def _peer_require_admin(user_id: int) -> None:
        if not await _peer_enabled(user_id):
            raise HTTPException(status_code=403, detail="Раздел временно закрыт")

    async def _peer_state(user_id: int) -> dict:
        profile = await db.peer_profile(user_id)
        seen = await db.peer_seen(user_id)
        used = await db.peer_votes_since(user_id, _day_start())

        state: dict = {
            "min_age": peer.PEER_MIN_AGE,
            "terms_version": peer.TERMS_VERSION,
            "consent_text": peer.CONSENT_TEXT,
            "legal": {"terms": config.terms_url, "privacy": config.privacy_url},
            "tiers": [
                {"key": t.key, "emoji": t.emoji, "title": t.title}
                for t in peer.PEER_TIERS
            ],
            "reasons": [
                {"key": k, "title": v} for k, v in peer.REPORT_REASONS
            ],
            "votes_left": max(0, peer.DAILY_VOTE_LIMIT - used),
            "rated": len(seen),
            "profile": None,
        }

        if profile:
            target = f"u:{user_id}"
            result = await db.peer_result(target)

            # Новые оценки с прошлого захода — для баннера в профиле
            raw_seen = await db.get_setting(f"rated_seen:{user_id}")
            try:
                seen_at = (
                    datetime.fromisoformat(raw_seen)
                    if raw_seen
                    else datetime.now(timezone.utc) - timedelta(days=30)
                )
            except ValueError:
                seen_at = datetime.now(timezone.utc) - timedelta(days=30)
            fresh_votes = await db.peer_votes_received(user_id, seen_at)
            hidden_until = profile.get("hidden_until")
            if isinstance(hidden_until, datetime):
                hidden_until = hidden_until.isoformat(timespec="seconds")

            state["profile"] = {
                "name": profile["name"],
                "age": profile["age"],
                "status": profile["status"],
                "has_photo": bool(profile.get("photo_key")),
                "hidden_until": hidden_until,
                "hidden_note": profile.get("hidden_note"),
                "terms_ok": profile.get("terms_version") == peer.TERMS_VERSION,
                "votes": result["count"],
                "average": result["average"],
                "tier": (
                    peer.tier_for_score(result["average"]).title
                    if result["count"] >= 3
                    else None
                ),
                "spread": result["spread"],
                "new_votes": fresh_votes,
            }

        return state

    @app.get("/api/peer/state")
    async def peer_state(user: TelegramUser = Depends(current_user)) -> dict:
        await _peer_require_admin(user.id)
        return await _peer_state(user.id)

    @app.post("/api/peer/profile")
    async def peer_save(
        name: str = Form(...),
        age: int = Form(...),
        accepted: int = Form(default=0),
        photo: UploadFile | None = File(default=None),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        """Создание и обновление анкеты. Фото обязательно при первом входе."""
        await _peer_require_admin(user.id)
        await _require_subscription(user.id)

        if not accepted:
            raise HTTPException(status_code=400, detail="Нужно принять правила")

        clean = peer.clean_name(name)
        problem = peer.name_error(clean) or peer.age_error(age)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

        payload: bytes | None = None
        key: str | None = None

        if photo is not None:
            if photo.content_type not in ALLOWED_TYPES:
                raise HTTPException(status_code=415, detail="Нужен JPEG, PNG или WebP")
            payload = await photo.read(MAX_PEER_PHOTO_BYTES + 1)
            if len(payload) > MAX_PEER_PHOTO_BYTES:
                raise HTTPException(status_code=413, detail="Фото слишком тяжёлое")
            if len(payload) < 1024:
                raise HTTPException(status_code=400, detail="Файл повреждён")
            key = hashlib.sha256(payload).hexdigest()[:32]

        existing = await db.peer_profile(user.id)
        if payload is None and not (existing and existing.get("photo_key")):
            raise HTTPException(status_code=400, detail="Нужно фото для анкеты")

        # Скрытая анкета возвращается только с новым снимком: смысл скрытия
        # в том, чтобы прежнее фото больше не показывалось.
        if (
            existing
            and existing.get("status") == "hidden"
            and payload is None
        ):
            raise HTTPException(
                status_code=400, detail="Загрузи новое фото, чтобы вернуться"
            )

        if existing and existing.get("status") == "banned":
            raise HTTPException(status_code=403, detail="Анкета заблокирована")

        await db.peer_save_profile(
            user.id, clean, age, payload, key, peer.TERMS_VERSION
        )
        return await _peer_state(user.id)

    @app.delete("/api/peer/profile")
    async def peer_remove(user: TelegramUser = Depends(current_user)) -> dict:
        """Удаление анкеты. Фото стирается вместе с ней."""
        await _peer_require_admin(user.id)
        await db.peer_delete(user.id)
        return {"ok": True}

    @app.post("/api/peer/seen")
    async def peer_seen_mark(user: TelegramUser = Depends(current_user)) -> dict:
        """Отмечает, что новые оценки просмотрены — баннер больше не нужен."""
        await _peer_require_admin(user.id)
        await db.set_setting(
            f"rated_seen:{user.id}",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        return {"ok": True}

    @app.get("/api/peer/next")
    async def peer_next(user: TelegramUser = Depends(current_user)) -> dict:
        """Очередь на оценку: сначала живые анкеты, потом снимки из папки."""
        await _peer_require_admin(user.id)

        profile = await db.peer_profile(user.id)
        if not profile or not profile.get("photo_key"):
            raise HTTPException(status_code=428, detail="Сначала своя анкета")
        if profile.get("status") != "active":
            raise HTTPException(status_code=423, detail="Анкета скрыта")

        used = await db.peer_votes_since(user.id, _day_start())
        if used >= peer.DAILY_VOTE_LIMIT:
            raise HTTPException(status_code=429, detail="Лимит оценок на сегодня")

        cards = [
            {
                "target": f"u:{row['user_id']}",
                "name": row["name"],
                "age": row["age"],
                "photo": f"/api/peer/photo/{row['user_id']}?v={row['photo_key'][:8]}",
            }
            for row in await db.peer_next(user.id, limit=12)
        ]

        if len(cards) < 6:
            seen = await db.peer_seen(user.id)

            # Снимки наполнения показываются как обычные анкеты: с именем
            # и возрастом. Служебная карточка без подписи оценивается иначе,
            # а нам нужно, чтобы наполнение было неотличимо от живых.
            for row in await db.peer_seed_list():
                target = f"pool:{row['key']}"
                if target in seen:
                    continue
                name, age = _filler_identity(row["key"], row.get("name"), row.get("age"))
                cards.append(
                    {
                        "target": target,
                        "name": name,
                        "age": age,
                        "photo": f"/api/peer/seed/{row['key']}",
                    }
                )
                if len(cards) >= 12:
                    break

            # Снимки из папки репозитория
            for file_name in seed_files():
                target = f"seed:{file_name}"
                if target in seen or len(cards) >= 12:
                    continue
                name, age = _filler_identity(file_name, None, None)
                cards.append(
                    {
                        "target": target,
                        "name": name,
                        "age": age,
                        "photo": f"/seed/{file_name}",
                    }
                )

        return {"cards": cards, "votes_left": peer.DAILY_VOTE_LIMIT - used}

    @app.get("/api/peer/photo/{target_id}")
    async def peer_photo(
        target_id: int, user: TelegramUser = Depends(current_user)
    ) -> Response:
        await _peer_require_admin(user.id)

        owner = await db.peer_profile(target_id)
        # Скрытую и заблокированную анкету не отдаём даже по прямой ссылке
        if not owner or (owner.get("status") != "active" and target_id != user.id):
            raise HTTPException(status_code=404, detail="Нет фото")

        payload = await db.peer_photo(target_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Нет фото")

        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=300"},
        )

    @app.get("/api/peer/seed/{key}")
    async def peer_seed_photo(
        key: str, user: TelegramUser = Depends(current_user)
    ) -> Response:
        await _peer_require_admin(user.id)
        payload = await db.peer_seed_photo(key)
        if not payload:
            raise HTTPException(status_code=404, detail="Нет снимка")
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=600"},
        )

    @app.post("/api/peer/seed")
    async def peer_seed_upload(
        photo: UploadFile = File(...),
        title: str = Form(default=""),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        """
        Пополнение пула прямо из приложения. Самый надёжный путь: не зависит
        от того, попала ли папка репозитория внутрь serverless-функции.
        """
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")
        if photo.content_type not in ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail="Нужен JPEG, PNG или WebP")

        payload = await photo.read(MAX_PEER_PHOTO_BYTES + 1)
        if len(payload) > MAX_PEER_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="Фото слишком тяжёлое")

        key = hashlib.sha256(payload).hexdigest()[:32]
        parsed = peer.parse_identity(title or photo.filename or "")
        name, age = parsed if parsed else (None, None)

        added = await db.peer_seed_add(key, payload, user.id, name, age)
        pool = await db.peer_seed_list()
        return {"ok": True, "added": added, "total": len(pool)}

    @app.get("/api/peer/seedlist")
    async def peer_seed_list(user: TelegramUser = Depends(current_user)) -> dict:
        """Список анкет наполнения с итоговыми именами и возрастом."""
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")

        items = []
        for row in await db.peer_seed_list():
            name, age = _filler_identity(row["key"], row.get("name"), row.get("age"))
            items.append(
                {
                    "key": row["key"],
                    "name": name,
                    "age": age,
                    # Отличаем заданное вручную от выведенного из снимка:
                    # владельцу важно видеть, что он уже поправил.
                    "custom": bool(row.get("name") and row.get("age")),
                    "photo": f"/api/peer/seed/{row['key']}",
                }
            )
        return {"items": items}

    @app.post("/api/peer/seedlist")
    async def peer_seed_edit(
        key: str = Form(...),
        name: str = Form(default=""),
        age: int = Form(default=0),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")

        clean = peer.clean_name(name)
        problem = peer.name_error(clean) or peer.age_error(age)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

        if not await db.peer_seed_update(key, clean, age):
            raise HTTPException(status_code=404, detail="Анкета не найдена")

        return {"ok": True, "name": clean, "age": age}

    @app.delete("/api/peer/seedlist/{key}")
    async def peer_seed_remove(
        key: str, user: TelegramUser = Depends(current_user)
    ) -> dict:
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")

        removed = await db.peer_seed_delete(key)
        return {"ok": True, "removed": removed}

    @app.get("/api/peer/diag")
    async def peer_diag(user: TelegramUser = Depends(current_user)) -> dict:
        """Что видно серверу: папка, пул в базе, число анкет."""
        if not config.is_admin(user.id):
            raise HTTPException(status_code=403, detail="Только для владельца")
        return {
            "folder": seed_diagnostics(),
            "pool_in_db": len(await db.peer_seed_list()),
            "stats": await db.peer_stats(),
        }

    @app.post("/api/peer/vote")
    async def peer_vote(
        target: str = Form(...),
        tier: str = Form(...),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        await _peer_require_admin(user.id)

        chosen = peer.TIER_BY_KEY.get(tier)
        if chosen is None:
            raise HTTPException(status_code=400, detail="Неизвестная оценка")
        if not re.fullmatch(r"(u:\d{1,20}|seed:[\w.\-]{1,80}|pool:[0-9a-f]{8,64})", target):
            raise HTTPException(status_code=400, detail="Некорректная цель")
        if target == f"u:{user.id}":
            raise HTTPException(status_code=400, detail="Себя оценивать нельзя")

        used = await db.peer_votes_since(user.id, _day_start())
        if used >= peer.DAILY_VOTE_LIMIT:
            raise HTTPException(status_code=429, detail="Лимит оценок на сегодня")

        fresh = await db.peer_vote(user.id, target, chosen.key, chosen.score)
        if fresh and target.startswith("u:"):
            with contextlib.suppress(ValueError):
                await _notify_rated(int(target[2:]))
        if fresh:
            # За оценку чужой анкеты капают XP: так режим кормит основную
            # механику, а не живёт отдельно от неё.
            await db.award_xp(user.id, f"peervote:{target}", 1)

        return {"ok": True, "counted": fresh, "votes_left": max(0, peer.DAILY_VOTE_LIMIT - used - 1)}

    async def _notify_rated(owner_id: int) -> None:
        """
        Сообщает владельцу анкеты, что его оценили.

        Уведомления копятся: отдельное сообщение на каждую оценку быстро
        превратило бы бота в спам, и человек его отключит. Поэтому не чаще
        раза в полчаса, зато с накопленным счётчиком.
        """
        if bot is None:
            return

        now = datetime.now(timezone.utc)
        raw = await db.get_setting(f"rated_notified:{owner_id}")

        try:
            last = datetime.fromisoformat(raw) if raw else now - timedelta(days=7)
        except ValueError:
            last = now - timedelta(days=7)

        if now - last < timedelta(minutes=peer.NOTIFY_COOLDOWN_MINUTES):
            return

        count = await db.peer_votes_received(owner_id, last)
        if count < 1:
            return

        await db.set_setting(f"rated_notified:{owner_id}", now.isoformat(timespec="seconds"))
        with contextlib.suppress(Exception):
            await bot.send_message(
                owner_id,
                texts.peer_rated_notice(count),
                reply_markup=keyboards.peer_rated(config.webapp_url),
            )

    @app.post("/api/peer/report")
    async def peer_report(
        target: str = Form(...),
        reason: str = Form(default="other"),
        user: TelegramUser = Depends(current_user),
    ) -> dict:
        await _peer_require_admin(user.id)

        if not re.fullmatch(r"(u:\d{1,20}|seed:[\w.\-]{1,80}|pool:[0-9a-f]{8,64})", target):
            raise HTTPException(status_code=400, detail="Некорректная цель")
        if reason not in peer.REASON_TITLES:
            reason = "other"

        report_id = await db.peer_add_report(user.id, target, reason)
        await notify_report(bot, db, config, report_id, user.id, target, reason)
        return {"ok": True}

    @app.get("/api/referral")
    async def referral(user: TelegramUser = Depends(current_user)) -> dict:
        code = await _ensure_ref_code(user.id)
        return {
            "code": code,
            "invited": await db.referral_count(user.id),
            "reward": engagement.XP_REFERRAL,
            "bonus": engagement.XP_REFERRAL_BONUS,
            "used": await db.referrer_of(user.id) is not None,
        }

    @app.post("/api/referral")
    async def use_referral(
        code: str = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        result = await apply_ref_code(db, user.id, code)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    # ─────────────────────────── хелперы ───────────────────────────────

    async def _apply_strictness() -> None:
        """Настройка живёт в базе, поэтому читаем её перед каждой оценкой."""
        value = await db.get_setting("strictness")
        try:
            rating.set_strictness(float(value or 0))
        except (TypeError, ValueError):
            rating.set_strictness(0.0)

    async def _gift_scans() -> int:
        """
        Сколько бесплатных попыток владелец подарил всем на сегодня.

        Хранится одним значением на дату, а не начислением каждому: подарок
        достаётся и тем, кто зайдёт позже, и сам сходит на нет в полночь
        вместе со сбросом счётчика.
        """
        value = await db.get_setting(f"gift_scans:{date.today().isoformat()}")
        try:
            return max(0, min(50, int(value or 0)))
        except (TypeError, ValueError):
            return 0

    def _day_start() -> datetime:
        """Полночь по UTC: с неё считается суточный лимит отчётов."""
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def _today_payload(user_id: int) -> dict:
        today_date = date.today()
        # Полугода истории хватает и для серии, и для рангов.
        since = (today_date - timedelta(days=200)).isoformat()

        marks = await db.get_habits(user_id, since)
        streak = engagement.compute_streak(marks, today_date)
        stats = await db.get_stats(user_id)
        scans_total = stats.count if stats else 0

        rank = engagement.rank_for(streak.total_days)
        upcoming, days_left = engagement.next_rank(streak.total_days)
        opened = engagement.unlocked(streak, scans_total)

        # Награда за каждые семь дней серии
        if streak.current and streak.current % 7 == 0:
            await db.award_xp(
                user_id, f"streak:{streak.current}", engagement.XP_STREAK_WEEK
            )

        earned, spent = await db.xp_balance(user_id)
        used_today = await db.count_ratings_since(user_id, _day_start())
        unlimited = config.is_admin(user_id)
        extra = await db.count_purchases(user_id, f"scan:{today_date.isoformat()}:")
        gift = await _gift_scans()

        return {
            "date": today_date.isoformat(),
            "habits": [
                {
                    "key": habit.key,
                    "emoji": habit.emoji,
                    "title": habit.title,
                    "hint": habit.hint,
                    "done": bool(marks.get(today_date.isoformat(), 0) & (1 << habit.bit)),
                }
                for habit in engagement.HABITS
            ],
            "done_today": engagement.count_done(marks.get(today_date.isoformat(), 0)),
            "need_today": engagement.DAY_THRESHOLD,
            "streak": {
                "current": streak.current,
                "best": streak.best,
                "total_days": streak.total_days,
                "perfect_days": streak.perfect_days,
                "grace_used": streak.grace_used,
            },
            "rank": {
                "emoji": rank.emoji,
                "title": rank.title,
                "caption": rank.caption,
                "next": None
                if upcoming is None
                else {"title": upcoming.title, "emoji": upcoming.emoji, "days_left": days_left},
            },
            "scans": {
                "used": used_today,
                "limit": config.daily_scan_limit + extra + gift,
                "left": max(0, config.daily_scan_limit + extra + gift - used_today),
                "gift": gift,
                "unlimited": unlimited,
                "extra": extra,
                "extra_price": engagement.EXTRA_SCAN_PRICE,
                "can_buy": extra < engagement.EXTRA_SCANS_PER_DAY,
            },
            "xp": {
                "balance": earned - spent,
                "earned": earned,
                "spent": spent,
                # Владельцу лимит мешал бы проверять магазин и снимать контент
                "unlimited": unlimited,
            },
            "achievements": [
                {
                    "code": item.code,
                    "emoji": item.emoji,
                    "title": item.title,
                    "description": item.description,
                    "unlocked": opened.get(item.code, False),
                }
                for item in engagement.ACHIEVEMENTS
            ],
        }

    async def _ensure_ref_code(user_id: int) -> str:
        code = await db.get_ref_code(user_id)
        if code:
            return code
        code = make_ref_code(user_id, config.score_salt)
        await db.set_ref_code(user_id, code)
        return await db.get_ref_code(user_id) or code

    async def _is_subscribed(user_id: int) -> bool:
        """Гейт выключен, бот не передан или это админ — считаем подписанным."""
        if gate is None or not gate.enabled or bot is None:
            return True
        if config.is_admin(user_id):
            return True
        return await gate.is_member(bot, user_id)

    async def _require_subscription(user_id: int) -> None:
        if not await _is_subscribed(user_id):
            raise HTTPException(
                status_code=403,
                detail="Нужна подписка на канал",
                headers={"X-Reason": "subscription"},
            )

    async def _require_age(user_id: int) -> None:
        age = await db.get_declared_age(user_id)
        if age is None:
            raise HTTPException(status_code=403, detail="Сначала укажи возраст")
        if age < config.min_age:
            raise HTTPException(
                status_code=403,
                detail=f"Приложение доступно с {config.min_age} лет",
            )

    @app.middleware("http")
    async def cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # Никакого immutable: имена файлов не содержат хеша сборки, а
            # значит браузер с годовым кешем просто не узнает об обновлении.
            # Именно из-за этого правки могли не доезжать до пользователей.
            response.headers["Cache-Control"] = "public, max-age=600, must-revalidate"
        return response

    # check_dir=False: отсутствие папки не должно ронять приложение на импорте —
    # это даёт нечитаемый 500 вместо внятной ошибки.
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR, check_dir=False),
        name="static",
    )
    return app


def _stats_payload(stats) -> dict | None:
    if stats is None:
        return None
    return {
        "count": stats.count,
        "best": stats.best,
        "average": stats.average,
        "last": stats.last,
    }


def _report_payload(report: rating.Report, hide_id: bool = False) -> dict:
    return {
        "report_id": None if hide_id else report.report_id,
        "overall": report.overall,
        "potential": report.potential,
        "percentile": report.percentile,
        "tier": {
            "code": report.tier.code,
            "title": report.tier.title,
            "emoji": report.tier.emoji,
            "comment": report.tier.comment,
        },
        "scores": [
            {
                "key": s.parameter.key,
                "emoji": s.parameter.emoji,
                "title": s.parameter.title,
                "value": s.value,
            }
            for s in report.scores
        ],
        "strongest": [s.parameter.key for s in report.strongest(3)],
        "weakest": [s.parameter.key for s in report.weakest(3)],
        "tips": [
            {
                "key": s.parameter.key,
                "title": s.parameter.title,
                "emoji": s.parameter.emoji,
                "text": rating.pick_tip(report, s, "looksmax"),
            }
            for s in report.weakest(3)
        ],
    }


# ───────────────────────── реферальные коды ────────────────────────────────

# Без похожих символов: 0/O и 1/I/L путают при переписывании с экрана.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def make_ref_code(user_id: int, salt: str, length: int = 6) -> str:
    digest = hashlib.sha256(f"{salt}|ref|{user_id}".encode()).digest()
    return "".join(CODE_ALPHABET[b % len(CODE_ALPHABET)] for b in digest[:length])


async def apply_ref_code(db, user_id: int, raw_code: str) -> dict:
    """
    Привязывает пользователя к пригласившему и начисляет обоим XP.

    Единая точка для бота и приложения, чтобы правила не разъезжались.
    """
    code = (raw_code or "").strip().upper()
    if not re.fullmatch(rf"[{CODE_ALPHABET}]{{4,12}}", code):
        return {"ok": False, "error": "Код состоит из букв и цифр без пробелов"}

    if await db.referrer_of(user_id) is not None:
        return {"ok": False, "error": "Ты уже вводил код приглашения"}

    owner = await db.user_by_ref_code(code)
    if owner is None:
        return {"ok": False, "error": "Такого кода не существует"}
    if owner == user_id:
        return {"ok": False, "error": "Свой собственный код ввести нельзя"}

    if not await db.bind_referrer(user_id, owner):
        return {"ok": False, "error": "Код уже применён"}

    await db.award_xp(user_id, f"refbonus:{owner}", engagement.XP_REFERRAL_BONUS)
    await db.award_xp(owner, f"referral:{user_id}", engagement.XP_REFERRAL)

    return {
        "ok": True,
        "bonus": engagement.XP_REFERRAL_BONUS,
        "owner_id": owner,
        "reward": engagement.XP_REFERRAL,
    }
