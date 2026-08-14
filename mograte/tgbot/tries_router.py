"""Выдача попыток конкретному человеку.

    /tries 123456789 5      — по ID
    /tries @nickname 5      — по юзернейму
    /tries @nickname        — 5 по умолчанию
    /tries @nickname 0      — снять выданное

Отличие от /gift: тот поднимает лимит всем сразу, этот — одному
человеку. Обе надбавки складываются и сгорают в полночь вместе со
сбросом суточного счётчика.

Начисление идёт в ту же таблицу purchases, куда попадают попытки,
купленные за XP: основной бот уже умеет их считать через
count_purchases("scan:<дата>:"). Поэтому его код менять не нужно —
и, соответственно, нечему ломаться на Vercel.

Про юзернеймы: Telegram не даёт ботам искать людей по @нику. Поэтому
ник запоминается в момент, когда человек пишет боту, — и команда
работает только для тех, кто уже заходил. Для остальных остаётся ID.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..core import db as rate_db

log = logging.getLogger(__name__)
router = Router(name="mograte.tries")

# Верхняя граница — та же, что у /gift: защита от опечатки вроде «50000».
MAX_TRIES = 50

USAGE = (
    "<b>Выдать попытки одному человеку</b>\n\n"
    "<code>/tries 123456789 5</code> — по ID\n"
    "<code>/tries @nickname 5</code> — по юзернейму\n"
    "<code>/tries @nickname</code> — 5 по умолчанию\n"
    "<code>/tries @nickname 0</code> — снять выданное\n\n"
    f"Не больше {MAX_TRIES} за раз. Попытки сгорают в полночь UTC, "
    "как и подарок из /gift, и складываются с ним."
)

UNKNOWN_NICK = (
    "Не знаю такого юзернейма.\n\n"
    "Telegram не позволяет ботам искать людей по @нику — я запоминаю ник "
    "только когда человек сам пишет боту. Попроси его отправить боту любое "
    "сообщение, либо укажи числовой ID."
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def scan_key(day: str | None = None) -> str:
    """Префикс, по которому основной бот считает докупленные попытки."""
    return f"scan:{day or _today()}:"


async def granted_for(db, user_id: int) -> int:
    """Сколько попыток уже есть у человека на сегодня."""
    try:
        return int(await db.count_purchases(user_id, scan_key()))
    except Exception:  # noqa: BLE001 — подсчёт не должен ронять команду
        return 0


async def grant(db, user_id: int, amount: int) -> int:
    """Начисляет попытки. Возвращает, сколько реально добавилось.

    Записи идут с ценой 0: это подарок, а не покупка за XP,
    и баланс он трогать не должен.
    """
    have = await granted_for(db, user_id)
    added = 0
    for i in range(have + 1, have + amount + 1):
        if await db.purchase(user_id, f"{scan_key()}{i}", 0):
            added += 1
    return added


@router.message(Command("tries"))
async def cmd_tries(message: Message, db, config) -> None:
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(USAGE)
        return

    target_raw = parts[1].strip()
    amount = 5
    if len(parts) > 2:
        try:
            amount = int(parts[2])
        except ValueError:
            await message.answer(USAGE)
            return

    if not 0 <= amount <= MAX_TRIES:
        await message.answer(USAGE)
        return

    if db is None:
        await message.answer(
            "Нет подключения к базе основного бота — начислять некуда.\n"
            "Проверь DATABASE_URL в .env."
        )
        return

    target_id = await _resolve(target_raw)
    if target_id is None:
        await message.answer(UNKNOWN_NICK if not target_raw.lstrip("-").isdigit() else USAGE)
        return

    # Человек мог ни разу не заходить — тогда строки в users нет,
    # и начисление повисло бы в воздухе.
    try:
        await db.ensure_user(target_id)
    except Exception:  # noqa: BLE001 — не критично для самой выдачи
        pass

    who = f"@{target_raw.lstrip('@')}" if not target_raw.lstrip("-").isdigit() else str(target_id)

    if amount == 0:
        have = await granted_for(db, target_id)
        await message.answer(
            f"Сейчас у {who} (<code>{target_id}</code>) начислено "
            f"{have} {_plural(have)} на сегодня.\n\n"
            "<i>Снять начисленное командой нельзя — записи о попытках "
            "общие с покупками за XP. Они сгорят сами в полночь UTC.</i>"
        )
        return

    added = await grant(db, target_id, amount)
    total = await granted_for(db, target_id)
    await message.answer(
        f"Начислил <b>{added}</b> {_plural(added)} для {who} "
        f"(<code>{target_id}</code>).\n"
        f"Всего сверх лимита на сегодня: {total}.\n\n"
        "Сгорят в полночь UTC. Складываются с подарком из /gift."
    )

    # Уведомляем адресата, если бот ему писать может.
    if amount > 0:
        try:
            await message.bot.send_message(
                target_id,
                f"Тебе начислено <b>{amount}</b> {_plural(amount)} на сегодня.",
            )
        except Exception:  # noqa: BLE001 — мог не начинать диалог или заблокировать
            await message.answer(
                "<i>Сообщить ему не удалось — вероятно, он не начинал диалог "
                "с ботом или заблокировал его. Попытки всё равно начислены.</i>"
            )


async def _resolve(raw: str) -> int | None:
    if raw.lstrip("-").isdigit():
        return int(raw)
    return await rate_db.resolve_username(raw)


def _plural(n: int) -> str:
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        return "попыток"
    tail %= 10
    if tail == 1:
        return "попытка"
    if 2 <= tail <= 4:
        return "попытки"
    return "попыток"


@router.message(Command("whois"))
async def cmd_whois(message: Message, config) -> None:
    """/whois @nickname — проверить, знаю ли я такой ник."""
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: <code>/whois @nickname</code>")
        return

    found = await rate_db.resolve_username(parts[1])
    if found is None:
        await message.answer(UNKNOWN_NICK)
    else:
        await message.answer(f"@{parts[1].lstrip('@')} → <code>{found}</code>")


class RememberUsername(BaseMiddleware):
    """Запоминает @username каждого, кто пишет боту.

    Именно middleware, а не хендлер: хендлер перехватил бы сообщение и
    сломал всю остальную обработку. Здесь мы только смотрим и пропускаем
    дальше, а ошибку записи глотаем — это побочная задача.
    """

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and getattr(user, "username", None):
            try:
                await rate_db.remember_username(user.id, user.username)
            except Exception:  # noqa: BLE001 — не мешаем основному потоку
                log.debug("не записал username", exc_info=True)
        return await handler(event, data)
