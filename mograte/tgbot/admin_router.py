"""Админский роутер: решения по жалобам и служебные команды.

Подключение:

    from mograte.tgbot.admin_router import router as rate_admin_router
    dp.include_router(rate_admin_router)
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..core import config, db, moderation, photos, seed_loader
from . import keyboards as kb
from .notify import notify_user

log = logging.getLogger(__name__)
router = Router(name="mograte.admin")

ACTION_TITLES = {
    "delete": "Анкета удалена",
    "hide24": f"Анкета скрыта на {config.HIDE_HOURS} ч",
    "reject": "Жалоба отклонена",
    "ban": "Пользователь заблокирован",
}


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.callback_query(F.data.startswith("mod:"))
async def on_mod_action(call: CallbackQuery, bot: Bot) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return

    try:
        _, action, raw_id = call.data.split(":")
        report_id = int(raw_id)
    except ValueError:
        await call.answer("Некорректная команда")
        return

    report = await db.get_report(report_id)
    if report and report["status"] == "resolved":
        await call.answer("Жалоба уже закрыта", show_alert=True)
        await _strip(call, f"Решение принято ранее: {report.get('action') or '—'}")
        return

    result = await moderation.apply_action(report_id, call.from_user.id, action)

    if not result.ok:
        await call.answer(result.text, show_alert=True)
        return

    delivered = None
    if result.notify_user_id and result.notify_text:
        delivered = await notify_user(bot, result.notify_user_id, result.notify_text)

    suffix = ACTION_TITLES.get(action, action)
    if delivered is False:
        suffix += " · владельцу не доставлено (бот заблокирован)"
    elif delivered:
        suffix += " · владелец уведомлён"

    await call.answer(result.text)
    await _strip(call, f"✅ {suffix}\nМодератор: {call.from_user.id}")


async def _strip(call: CallbackQuery, note: str) -> None:
    """Убирает кнопки и дописывает итог, чтобы двое админов не решали дважды."""
    try:
        if call.message.caption is not None:
            await call.message.edit_caption(
                caption=f"{call.message.caption}\n\n{note}", reply_markup=None
            )
        else:
            await call.message.edit_text(
                f"{call.message.text}\n\n{note}", reply_markup=None
            )
    except Exception:  # noqa: BLE001
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass


# --- Команды --------------------------------------------------------------

@router.message(Command("rate_stats"))
async def cmd_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    s = await db.global_stats()
    await message.answer(
        "<b>Режим оценок</b>\n\n"
        f"Активных анкет: {s['active']}\n"
        f"Скрыто: {s['hidden']}\n"
        f"Ждут перезалива фото: {s['awaiting']}\n"
        f"Удалено: {s['deleted']}\n"
        f"Демо-анкет в показе: {s['seeds']}\n"
        f"Всего оценок: {s['votes']}\n"
        f"Открытых жалоб: {s['reports_open']}"
    )


@router.message(Command("rate_reports"))
async def cmd_reports(message: Message, bot: Bot) -> None:
    """Показывает необработанные жалобы — если карточка потерялась в чате."""
    if not is_admin(message.from_user.id):
        return
    pending = await db.pending_reports(limit=10)
    if not pending:
        await message.answer("Открытых жалоб нет.")
        return

    await message.answer(f"Открытых жалоб: {len(pending)}")
    for row in pending:
        report = await db.get_report(int(row["id"]))
        if not report:
            continue
        snap = report["snapshot"]
        text = moderation.format_report(report)
        markup = kb.moderation_kb(int(row["id"]))
        file_id = snap.get("photo_file_id")
        try:
            if file_id:
                await message.answer_photo(file_id, caption=text, reply_markup=markup)
                continue
            path = None
            if snap.get("kind") == "live" and snap.get("photo_path"):
                path = photos.path_for(snap["photo_path"])
            elif snap.get("kind") == "seed" and snap.get("file_name"):
                path = config.SEED_DIR / snap["file_name"]
            if path and path.exists():
                from aiogram.types import FSInputFile

                await message.answer_photo(
                    FSInputFile(path), caption=text, reply_markup=markup
                )
            else:
                await message.answer(text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            log.exception("не показал жалобу %s", row["id"])


@router.message(Command("rate_seed_reload"))
async def cmd_seed_reload(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    result = await seed_loader.load(verbose=False)
    text = (
        f"Демо-анкеты перечитаны.\n"
        f"Добавлено: {result['added']}\n"
        f"Обновлено: {result['updated']}\n"
        f"Активно: {result['total_active']}"
    )
    if result["skipped"]:
        shown = ", ".join(result["skipped"][:10])
        text += f"\n\nПропущены: {shown}"
    await message.answer(text)


@router.message(Command("rate_unhide"))
async def cmd_unhide(message: Message, bot: Bot) -> None:
    """Снять скрытие вручную: /rate_unhide <user_id>"""
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Формат: /rate_unhide <user_id>")
        return
    user_id = int(parts[1])
    prof = await db.get_profile(user_id)
    if not prof:
        await message.answer("Анкета не найдена.")
        return
    await db.set_status(user_id, "awaiting_photo", hidden_until=0, needs_reupload=1)
    await db.log_action(message.from_user.id, "live", user_id, "manual_unhide")
    await notify_user(
        bot,
        user_id,
        "Ограничение с вашей анкеты снято. Чтобы вернуться в показ, загрузите новое фото.",
    )
    await message.answer(f"С анкеты {user_id} снято скрытие, запрошено новое фото.")
