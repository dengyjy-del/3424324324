"""Доставка жалоб модераторам и уведомлений владельцам анкет."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import FSInputFile

from ..core import config, db, moderation, photos
from . import keyboards as kb

log = logging.getLogger(__name__)


def _recipients() -> list[int]:
    out = list(config.ADMIN_IDS)
    if config.MOD_CHAT_ID is not None and config.MOD_CHAT_ID not in out:
        out.append(config.MOD_CHAT_ID)
    return out


async def send_report_to_mods(bot: Bot, report_id: int) -> None:
    """Шлёт карточку жалобы с фото и кнопками решения."""
    report = await db.get_report(report_id)
    if report is None:
        log.error("жалоба %s не найдена", report_id)
        return

    targets = _recipients()
    if not targets:
        log.error("некому отправить жалобу: ADMIN_IDS и MOD_CHAT_ID пусты")
        return

    text = moderation.format_report(report)
    markup = kb.moderation_kb(report_id)
    snap = report["snapshot"]

    for chat_id in targets:
        try:
            await _send_one(bot, chat_id, text, markup, snap)
        except Exception:  # noqa: BLE001 — один недоступный админ не должен ронять остальных
            log.exception("не доставлено админу %s", chat_id)


async def _send_one(bot: Bot, chat_id: int, text: str, markup, snap: dict) -> None:
    file_id = snap.get("photo_file_id")
    if file_id:
        try:
            await bot.send_photo(chat_id, file_id, caption=text, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001
            log.warning("file_id не сработал для админа, пробую файл с диска")

    path = None
    if snap.get("kind") == "live" and snap.get("photo_path"):
        path = photos.path_for(snap["photo_path"])
    elif snap.get("kind") == "seed" and snap.get("file_name"):
        path = config.SEED_DIR / snap["file_name"]

    if path and path.exists():
        await bot.send_photo(chat_id, FSInputFile(path), caption=text, reply_markup=markup)
        return

    await bot.send_message(chat_id, text + "\n\n<i>Фото недоступно.</i>", reply_markup=markup)


async def notify_user(bot: Bot, user_id: int, text: str) -> bool:
    """Сообщение владельцу анкеты о решении модератора."""
    try:
        await bot.send_message(user_id, text)
        return True
    except Exception:  # noqa: BLE001 — человек мог заблокировать бота
        log.info("не удалось уведомить пользователя %s", user_id)
        return False
