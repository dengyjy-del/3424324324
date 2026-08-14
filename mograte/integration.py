"""Раздел оценок внутри существующего мини-аппа.

Даёт две вещи:
  • mount_rate_api(app, config, bot) — маршруты /api/rate/* для FastAPI;
  • attach_rate(dispatcher, config, bot) — роутеры бота.

Раздел закрыт для всех, кроме ADMIN_IDS: пока идёт обкатка, наружу
его не выпускаем. Чтобы открыть всем, поставь RATE_ADMIN_ONLY=0.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .core import config as rate_config
from .core import db, feed, grades, moderation, photos, texts

log = logging.getLogger(__name__)


def _admin_only() -> bool:
    return os.getenv("RATE_ADMIN_ONLY", "1").strip() not in {"0", "false", "no"}


def mount_rate_api(app, config, bot=None) -> None:
    """Вешает /api/rate/* на существующее приложение FastAPI."""
    router = APIRouter(prefix="/api/faces")

    # Проверку initData берём ту же, что использует основной мини-апп,
    # чтобы не заводить второй способ аутентификации.
    from webapp.auth import verify_init_data  # type: ignore

    def _user(request: Request, body: dict | None = None):
        raw = request.headers.get("X-Telegram-Init-Data", "")
        if not raw and body:
            raw = body.get("initData", "")
        try:
            # Подпись сверяется со всеми токенами: мини-апп один на бота
            # и все зеркала, а какой из них его открыл — неизвестно.
            tg_user = verify_init_data(raw, config.bot_tokens)
        except Exception as exc:  # noqa: BLE001 — наружу отдаём один и тот же 401
            raise PermissionError(str(exc)) from exc
        return {
            "id": int(tg_user.id),
            "first_name": getattr(tg_user, "first_name", "") or "",
            "username": getattr(tg_user, "username", None),
        }

    def _deny(message: str, status: int = 401):
        return JSONResponse({"error": message}, status_code=status)

    async def _guard(request: Request, body: dict | None = None):
        """Возвращает (user, None) либо (None, ответ с ошибкой)."""
        try:
            user = _user(request, body)
        except PermissionError as exc:
            return None, _deny(str(exc))
        if _admin_only() and not config.is_admin(int(user["id"])):
            return None, _deny("Раздел ещё закрыт", 403)
        return user, None

    @router.get("/state")
    async def state(request: Request):
        user, err = await _guard(request)
        if err:
            return err
        uid = int(user["id"])
        await db.unhide_expired()

        consent = await db.has_consent(uid)
        prof = await db.get_profile(uid)

        payload = {
            "consent": consent,
            "minAge": rate_config.MIN_AGE,
            "grades": [
                {"code": g.code, "label": g.label, "color": g.color} for g in grades.GRADES
            ],
            "reasons": moderation.REASONS,
            "links": {"terms": rate_config.TERMS_URL, "privacy": rate_config.PRIVACY_URL},
            "support": rate_config.SUPPORT_HANDLE,
            "disclaimer": {"title": texts.DISCLAIMER_TITLE, "body": texts.DISCLAIMER_BODY},
            "suggestedName": user.get("first_name") or "",
            "profile": None,
        }

        if prof is None or prof["status"] in {"draft", "deleted"}:
            unfinished = (
                prof is not None
                and prof["status"] == "draft"
                and prof["display_name"]
                and prof["age"]
            )
            payload["screen"] = (
                "consent" if not consent else ("photo" if unfinished else "register")
            )
            return payload

        payload["profile"] = {
            "name": prof["display_name"],
            "age": prof["age"],
            "status": prof["status"],
            "photo": f"/api/faces/media/{prof['photo_path']}" if prof["photo_path"] else None,
            "votesCount": prof["votes_count"],
            "tier": grades.tier_label(prof["votes_weight"], prof["votes_count"]),
            "hiddenUntil": prof["hidden_until"],
            "needsReupload": bool(prof["needs_reupload"]),
        }

        if not consent:
            payload["screen"] = "consent"
        elif prof["status"] == "banned":
            payload["screen"] = "banned"
        elif prof["status"] == "hidden":
            payload["screen"] = "hidden"
        elif prof["status"] == "awaiting_photo" or prof["needs_reupload"]:
            payload["screen"] = "reupload"
        else:
            payload["screen"] = "feed"
        return payload

    @router.post("/consent")
    async def consent(request: Request):
        body = await _json(request)
        user, err = await _guard(request, body)
        if err:
            return err
        await db.save_consent(int(user["id"]), source="webapp")
        return {"ok": True}

    @router.post("/register")
    async def register(request: Request):
        body = await _json(request)
        user, err = await _guard(request, body)
        if err:
            return err
        uid = int(user["id"])
        if not await db.has_consent(uid):
            return _deny("Сначала примите условия", 403)

        ok_name, name = texts.validate_name(str(body.get("name", "")))
        if not ok_name:
            return JSONResponse({"error": name, "field": "name"}, status_code=400)
        ok_age, age = texts.validate_age(body.get("age", ""))
        if not ok_age:
            return JSONResponse({"error": age, "field": "age"}, status_code=400)

        await db.upsert_profile(uid, display_name=name, age=int(age))
        return {"ok": True}

    @router.post("/upload")
    async def upload(request: Request):
        user, err = await _guard(request)
        if err:
            return err
        uid = int(user["id"])
        if not await db.has_consent(uid):
            return _deny("Сначала примите условия", 403)

        form = await request.form()
        item = form.get("photo")
        if item is None:
            return JSONResponse({"error": "Файл не получен"}, status_code=400)
        raw = await item.read() if hasattr(item, "read") else bytes(item)

        try:
            file_name = photos.save_bytes(raw, uid)
        except photos.PhotoError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        prof = await db.get_profile(uid)
        if prof is None or not prof["display_name"]:
            photos.remove(file_name)
            return JSONResponse({"error": "Сначала заполните анкету"}, status_code=400)
        if prof["photo_path"]:
            photos.remove(prof["photo_path"])

        await db.upsert_profile(
            uid,
            photo_path=file_name,
            photo_file_id=None,
            status="active",
            needs_reupload=0,
            hidden_until=0,
        )
        return {"ok": True, "photo": f"/api/faces/media/{file_name}"}

    @router.get("/next")
    async def next_card(request: Request):
        user, err = await _guard(request)
        if err:
            return err
        uid = int(user["id"])
        try:
            await feed.gate(uid)
        except feed.NotReady as nr:
            return JSONResponse(
                {"blocked": nr.reason, "message": texts.NOT_READY.get(nr.reason, "")},
                status_code=409,
            )
        try:
            card = await feed.next_card(uid)
        except feed.FeedEmpty:
            return {"empty": True, "message": texts.FEED_EMPTY}

        data = card.to_json()
        data["photo"] = (
            f"/api/faces/media/{card.photo_path}"
            if card.kind == "live"
            else f"/api/faces/seed/{card.photo_path}"
        )
        return {"card": data}

    @router.post("/vote")
    async def vote(request: Request):
        body = await _json(request)
        user, err = await _guard(request, body)
        if err:
            return err
        kind, grade = body.get("kind"), str(body.get("grade", ""))
        try:
            target = int(body.get("id"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Некорректная анкета"}, status_code=400)
        if kind not in {"live", "seed"} or not grades.is_valid(grade):
            return JSONResponse({"error": "Некорректная оценка"}, status_code=400)
        counted = await feed.vote(int(user["id"]), kind, target, grade)
        return {"ok": True, "counted": counted}

    @router.post("/report")
    async def report(request: Request):
        body = await _json(request)
        user, err = await _guard(request, body)
        if err:
            return err
        uid = int(user["id"])
        kind = body.get("kind")
        try:
            target = int(body.get("id"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "Некорректная анкета"}, status_code=400)
        if kind not in {"live", "seed"}:
            return JSONResponse({"error": "Некорректная анкета"}, status_code=400)

        if await db.already_reported(uid, kind, target):
            return {"ok": True, "duplicate": True}

        result = await moderation.file_report(
            uid, kind, target, str(body.get("reason", "other")),
            (body.get("comment") or "").strip()[:500] or None,
        )
        if bot is not None:
            try:
                from .tgbot.notify import send_report_to_mods

                await send_report_to_mods(bot, result.report_id)
            except Exception:  # noqa: BLE001 — жалоба уже сохранена
                log.exception("жалоба сохранена, но не доставлена админам")
        return {"ok": True, "autohidden": result.autohidden}

    @router.get("/media/{name}")
    async def media(name: str):
        path = photos.path_for(name)
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    @router.get("/seed/{name}")
    async def seed(name: str):
        from pathlib import Path

        path = rate_config.SEED_DIR / Path(name).name
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    app.include_router(router)
    log.info("Раздел оценок: /api/faces/* подключён (%s)",
             "только админы" if _admin_only() else "открыт всем")


async def _json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 — тело может быть пустым
        return {}


def attach_rate(dispatcher, config, bot=None) -> None:
    """Подключает роутеры раздела к диспетчеру бота.

    Режим съёмки идёт первым: иначе фото перехватит обработчик отчётов.
    """
    from .tgbot.admin_router import router as admin_router
    from .tgbot.demo_router import router as demo_router
    from .tgbot.rating_router import router as rating_router

    dispatcher.include_router(demo_router)
    dispatcher.include_router(rating_router)
    dispatcher.include_router(admin_router)
    log.info("Раздел оценок: роутеры бота подключены")
