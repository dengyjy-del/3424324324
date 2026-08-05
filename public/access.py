"""
Доступ: проверка подписки на канал + состояние скрытого демо-режима.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

# Статусы, при которых считаем человека подписчиком.
MEMBER_STATUSES = frozenset({"member", "administrator", "creator"})

# Положительный результат кешируем, отрицательный — нет,
# чтобы кнопка «Проверить» срабатывала сразу после подписки.
POSITIVE_CACHE_TTL = 600.0


class SubscriptionGate:
    """
    Проверяет членство сразу в нескольких местах: канал, чат и т. д.

    Пустой список источников = гейт выключен. Пропускаем только тех, кто
    состоит во всех указанных: иначе смысл требования теряется.
    """

    def __init__(self, *chat_ids: str) -> None:
        self._chats = [c.strip() for c in chat_ids if c and c.strip()]
        self._cache: dict[int, float] = {}
        self._misconfigured = False

    @property
    def enabled(self) -> bool:
        return bool(self._chats)

    def forget(self, user_id: int) -> None:
        self._cache.pop(user_id, None)

    async def is_member(self, bot: Bot, user_id: int) -> bool:
        if not self.enabled:
            return True

        now = time.monotonic()
        if self._cache.get(user_id, 0.0) > now:
            return True

        is_member = True
        for chat_id in self._chats:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
            except TelegramAPIError as error:
                # Чаще всего: бот не администратор или неверный ID.
                # Пропускаем людей дальше, чтобы бот не выглядел сломанным,
                # но пишем в лог — чтобы владелец увидел причину.
                if not self._misconfigured:
                    self._misconfigured = True
                    logger.error(
                        "Не удалось проверить подписку на %s (%s). Добавь бота "
                        "администратором туда, иначе проверка не работает.",
                        chat_id,
                        error,
                    )
                continue

            self._misconfigured = False
            if member.status not in MEMBER_STATUSES:
                is_member = False
                break

        if is_member:
            self._cache[user_id] = now + POSITIVE_CACHE_TTL

        return is_member


class DemoState:
    """
    Скрытый режим съёмки. Состояние лежит в базе, а не в памяти процесса:
    на serverless каждый вызов функции может попасть на другой инстанс, и
    словарь в памяти там просто не работает.

    Автоистечение — защита от главной ошибки: забыть выключить режим и
    выдавать реальным пользователям оценки из демо-диапазона.
    """

    def __init__(self, db, ttl_minutes: float = 30.0) -> None:
        self._db = db
        self._ttl = max(1.0, ttl_minutes) * 60.0

    async def enable(self, user_id: int) -> float:
        await self._db.start_demo(user_id, self._ttl)
        return self._ttl / 60.0

    async def disable(self, user_id: int) -> None:
        await self._db.stop_demo(user_id)

    async def is_active(self, user_id: int) -> bool:
        return await self._db.demo_seconds_left(user_id) > 0

    async def minutes_left(self, user_id: int) -> int:
        left = await self._db.demo_seconds_left(user_id)
        return max(0, int(left / 60) + 1) if left > 0 else 0
