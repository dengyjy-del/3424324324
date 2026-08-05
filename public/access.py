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
    """Проверяет членство в канале. Пустой channel_id = гейт выключен."""

    def __init__(self, channel_id: str) -> None:
        self._channel_id = channel_id
        self._cache: dict[int, float] = {}
        self._misconfigured = False

    @property
    def enabled(self) -> bool:
        return bool(self._channel_id)

    def forget(self, user_id: int) -> None:
        self._cache.pop(user_id, None)

    async def is_member(self, bot: Bot, user_id: int) -> bool:
        if not self.enabled:
            return True

        now = time.monotonic()
        if self._cache.get(user_id, 0.0) > now:
            return True

        try:
            member = await bot.get_chat_member(self._channel_id, user_id)
        except TelegramAPIError as error:
            # Чаще всего: бот не админ в канале или неверный CHANNEL_ID.
            # Пропускаем людей дальше, чтобы бот не выглядел сломанным,
            # но пишем в лог один раз — чтобы владелец это увидел.
            if not self._misconfigured:
                self._misconfigured = True
                logger.error(
                    "Не удалось проверить подписку на %s (%s). "
                    "Добавь бота администратором в канал, иначе гейт не работает.",
                    self._channel_id,
                    error,
                )
            return True

        self._misconfigured = False
        is_member = member.status in MEMBER_STATUSES

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
