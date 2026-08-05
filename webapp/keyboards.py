"""Инлайн-клавиатуры."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

DETAILS_PREFIX = "det:"
CHECK_SUB = "checksub"


def gate_menu(channel_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url))
    builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data=CHECK_SUB))
    return builder.as_markup()


def main_menu(webapp_url: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="✨ Открыть приложение", web_app=WebAppInfo(url=webapp_url)
            )
        )
    builder.row(InlineKeyboardButton(text="📸 Как получить отчёт", callback_data="howto"))
    builder.row(
        InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="ref"),
        InlineKeyboardButton(text="🔑 Ввести код", callback_data="refenter"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="ℹ️ О боте", callback_data="about"),
    )
    return builder.as_markup()


def report_menu(
    photo_id: str, demo: bool = False, webapp_url: str = ""
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔬 Подробный разбор",
            callback_data=f"{DETAILS_PREFIX}{photo_id}",
        )
    )
    # В режиме съёмки лишние кнопки только мешают кадру.
    if not demo:
        if webapp_url:
            builder.row(
                InlineKeyboardButton(
                    text="📈 История в приложении", web_app=WebAppInfo(url=webapp_url)
                )
            )
        builder.row(
            InlineKeyboardButton(text="📊 Моя статистика", callback_data="stats")
        )
    return builder.as_markup()


def details_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Моя статистика", callback_data="stats"))
    builder.row(InlineKeyboardButton(text="🏠 В меню", callback_data="menu"))
    return builder.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 В меню", callback_data="menu"))
    return builder.as_markup()
