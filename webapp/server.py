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

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
import hmac
import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import engagement
import rating
from access import DemoState, SubscriptionGate
from config import Config, redact_secrets
from database import BaseDatabase as Database
from webapp.auth import AuthError, TelegramUser, verify_init_data
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
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def create_app(
    config: Config,
    db: Database,
    demo: DemoState,
    gate: SubscriptionGate | None = None,
    bot=None,
) -> FastAPI:
    app = FastAPI(title="Looksmax Mini App", docs_url=None, redoc_url=None)

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

    # Имя бота, чей токен настроен. Нужно, чтобы в ошибке подписи сразу было
    # видно, через какого бота приложение обязано открываться.
    bot_name_cache: dict[str, str] = {}

    async def configured_bot_name() -> str:
        if "name" not in bot_name_cache and bot is not None:
            try:
                me = await bot.get_me()
                bot_name_cache["name"] = f"@{me.username}"
            except Exception:  # noqa: BLE001 — имя не критично
                bot_name_cache["name"] = ""
        return bot_name_cache.get("name", "")

    # ─────────────────────────── авторизация ───────────────────────────

    async def current_user(
        authorization: str = Header(default=""),
        x_init_data: str = Header(default="", alias="X-Init-Data"),
    ) -> TelegramUser:
        init_data = x_init_data or authorization.removeprefix("tma ").strip()
        try:
            return verify_init_data(init_data, config.bot_token)
        except AuthError as error:
            detail = str(error)
            name = await configured_bot_name()
            if name and "подпись" in detail.lower():
                detail = (
                    f"{detail} Сейчас на сервере настроен бот {name} — "
                    "открывай приложение через него."
                )
            raise HTTPException(status_code=401, detail=detail) from error

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
            # поэтому при смене бота ничего править не придётся.
            "bot_username": await configured_bot_name(),
            "is_admin": config.is_admin(user.id),
            "stats": _stats_payload(stats),
            "subscribed": await _is_subscribed(user.id),
            "channel": {
                "required": bool(gate is not None and gate.enabled and bot is not None),
                "url": config.channel_url,
                "title": config.channel_title,
            },
        }

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
            used = await db.count_ratings_since(user.id, _day_start())
            if used >= config.daily_scan_limit + bought:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Отчёты на сегодня закончились. Можно докупить попытку "
                        f"за {engagement.EXTRA_SCAN_PRICE} XP или вернуться "
                        "после полуночи."
                    ),
                )

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
        return {
            "user": {"name": user.display_name, "photo": user.photo_url},
            "stats": _stats_payload(stats),
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
            rating.FaceMetrics.from_payload(payload)
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=422, detail="Замеры не разобрать") from None

        added = await db.add_label(photo_hash[:32], round(score, 2), metrics)
        stats = await db.label_stats()
        return {"ok": True, "added": added, "total": stats["total"]}

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
                "limit": config.daily_scan_limit + extra,
                "left": max(0, config.daily_scan_limit + extra - used_today),
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
            # Ассеты неизменяемы в пределах деплоя: CDN Vercel закеширует их
            # и перестанет будить функцию на каждый запрос.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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
