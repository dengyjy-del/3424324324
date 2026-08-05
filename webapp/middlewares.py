"""Middleware: антифлуд и обязательная подписка на канал."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

import keyboards
import texts
from access import SubscriptionGate
from config import Config


class ThrottleMiddleware(BaseMiddleware):
    """
    Ограничивает частоту тяжёлых действий.

    Фото — не чаще одного раза в `cooldown` секунд (с уведомлением).
    Колбэки — не чаще одного раза в 0.6 секунды (молча).
    """

    def __init__(self, cooldown: float = 3.0) -> None:
        self._cooldown = cooldown
        self._last_photo: dict[int, float] = {}
        self._last_callback: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()

        if isinstance(event, Message) and event.photo:
            last = self._last_photo.get(user.id, 0.0)
            if now - last < self._cooldown:
                await event.answer(texts.COOLDOWN)
                return None
            self._last_photo[user.id] = now

        elif isinstance(event, CallbackQuery):
            last = self._last_callback.get(user.id, 0.0)
            if now - last < 0.6:
                await event.answer()
                return None
            self._last_callback[user.id] = now

        return await handler(event, data)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Пускает дальше только подписчиков канала.

    Мимо гейта проходят: справка, экран «о боте», сама кнопка проверки,
    служебные команды и код режима съёмки — иначе владелец рискует
    заблокировать сам себя.
    """

    EXEMPT_CALLBACKS = frozenset({keyboards.CHECK_SUB, "about", "howto"})
    EXEMPT_COMMANDS = frozenset({"/about", "/help", "/myid", "/demo_off"})

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        gate: SubscriptionGate = data["gate"]
        config: Config = data["config"]

        if not gate.enabled:
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None or config.is_admin(user.id):
            return await handler(event, data)

        if self._is_exempt(event, config):
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await gate.is_member(bot, user.id):
            return await handler(event, data)

        await self._show_gate(event, config)
        return None

    def _is_exempt(self, event: TelegramObject, config: Config) -> bool:
        if isinstance(event, CallbackQuery):
            return event.data in self.EXEMPT_CALLBACKS

        if isinstance(event, Message):
            text = (event.text or "").strip()
            if not text:
                return False
            if config.demo_code and text == config.demo_code:
                return True
            return text.split()[0].split("@")[0].lower() in self.EXEMPT_COMMANDS

        return False

    async def _show_gate(self, event: TelegramObject, config: Config) -> None:
        markup = keyboards.gate_menu(config.channel_url)
        body = texts.gate(config.channel_title)

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message is not None:
                await event.message.answer(body, reply_markup=markup)
        elif isinstance(event, Message):
            await event.answer(body, reply_markup=markup)
