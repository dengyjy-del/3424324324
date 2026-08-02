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
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import engagement
import rating
from access import DemoState
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


def create_app(config: Config, db: Database, demo: DemoState) -> FastAPI:
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

    # ─────────────────────────── авторизация ───────────────────────────

    async def current_user(
        authorization: str = Header(default=""),
        x_init_data: str = Header(default="", alias="X-Init-Data"),
    ) -> TelegramUser:
        init_data = x_init_data or authorization.removeprefix("tma ").strip()
        try:
            return verify_init_data(init_data, config.bot_token)
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
            "stats": _stats_payload(stats),
        }

    @app.post("/api/age")
    async def declare_age(
        age: int = Form(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        if not 5 <= age <= 120:
            raise HTTPException(status_code=400, detail="Укажи реальный возраст")

        await db.set_declared_age(user.id, age)
        return {"age_ok": age >= config.min_age, "min_age": config.min_age}

    @app.post("/api/rate")
    async def rate(
        photo: UploadFile = File(...), user: TelegramUser = Depends(current_user)
    ) -> dict:
        await _require_age(user.id)

        # Суточный лимит: даёт причину вернуться завтра и заодно не
        # превращает приложение в перепроверку своей оценки по десять раз
        # за вечер. Не действует в режиме съёмки и у ID из ADMIN_IDS —
        # владельцу лимит мешал бы снимать контент и проверять сборки.
        in_demo_check = await demo.is_active(user.id)
        if not in_demo_check and not config.is_admin(user.id):
            used = await db.count_ratings_since(user.id, _day_start())
            if used >= config.daily_scan_limit:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"На сегодня отчёты закончились ({config.daily_scan_limit} "
                        "в сутки). Новые будут доступны после полуночи — "
                        "загляни отметить привычки."
                    ),
                )

        if photo.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=415, detail="Поддерживаются JPEG, PNG, WebP и HEIC"
            )

        payload = await photo.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой")
        if len(payload) < 1024:
            raise HTTPException(status_code=400, detail="Файл повреждён или пуст")

        # Единственное, что остаётся от снимка. Сами байты дальше не живут.
        photo_id = hashlib.sha256(payload).hexdigest()[:32]
        del payload

        in_demo = in_demo_check
        profile = rating.DEMO if in_demo else rating.NORMAL
        report = rating.generate_report(user.id, photo_id, config.score_salt, profile)

        if not in_demo:
            await db.save_rating(user.id, report.report_id, report.overall)

        return _report_payload(report, hide_id=in_demo)

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

        today_key = date.today().isoformat()
        marks = await db.get_habits(user.id, today_key)

        try:
            updated = engagement.toggle(marks.get(today_key, 0), key)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        await db.set_habit_mask(user.id, today_key, updated)
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
    async def guides() -> dict:
        return {"guides": GUIDES}

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

        used_today = await db.count_ratings_since(user_id, _day_start())
        unlimited = config.is_admin(user_id)

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
                "limit": config.daily_scan_limit,
                "left": max(0, config.daily_scan_limit - used_today),
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
