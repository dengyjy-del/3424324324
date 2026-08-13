"""Клавиатуры режима оценивания."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from ..core import config, grades
from ..core.moderation import REASONS

remove = ReplyKeyboardRemove


def consent_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Принимаю, начать", callback_data="rate:consent:yes")]]
    links = []
    if config.TERMS_URL:
        links.append(InlineKeyboardButton(text="Условия", url=config.TERMS_URL))
    if config.PRIVACY_URL:
        links.append(InlineKeyboardButton(text="Политика", url=config.PRIVACY_URL))
    if links:
        rows.append(links)
    rows.append([InlineKeyboardButton(text="Отказаться", callback_data="rate:consent:no")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gender_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="rate:gender:m"),
                InlineKeyboardButton(text="Женский", callback_data="rate:gender:f"),
            ]
        ]
    )


def card_kb(kind: str, target_id: int) -> InlineKeyboardMarkup:
    """Пять оценок + пропуск и жалоба."""
    ref = f"{kind}:{target_id}"
    grade_buttons = [
        InlineKeyboardButton(text=g.label, callback_data=f"rate:v:{ref}:{g.code}")
        for g in grades.GRADES
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            grade_buttons[:3],
            grade_buttons[3:],
            [
                InlineKeyboardButton(text="Пропустить", callback_data=f"rate:skip:{ref}"),
                InlineKeyboardButton(text="Пожаловаться", callback_data=f"rate:rep:{ref}"),
            ],
        ]
    )


def report_reasons_kb(kind: str, target_id: int) -> InlineKeyboardMarkup:
    ref = f"{kind}:{target_id}"
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"rate:repr:{ref}:{code}")]
        for code, title in REASONS.items()
    ]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"rate:repcancel:{ref}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Дальше", callback_data="rate:next")]]
    )


def main_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text="Оценивать"), KeyboardButton(text="Моя анкета")]]
    if config.WEBAPP_URL:
        rows.append(
            [KeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=config.WEBAPP_URL))]
        )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def profile_kb(status: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Сменить фото", callback_data="rate:editphoto")],
        [InlineKeyboardButton(text="Сменить имя", callback_data="rate:editname")],
        [InlineKeyboardButton(text="Удалить анкету", callback_data="rate:delete:ask")],
    ]
    if status == "active":
        rows.insert(0, [InlineKeyboardButton(text="Оценивать", callback_data="rate:next")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data="rate:delete:yes")],
            [InlineKeyboardButton(text="Оставить", callback_data="rate:delete:no")],
        ]
    )


def moderation_kb(report_id: int) -> InlineKeyboardMarkup:
    """Кнопки под жалобой в чате модерации."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить анкету", callback_data=f"mod:delete:{report_id}"
                ),
                InlineKeyboardButton(
                    text="Скрыть на 24ч", callback_data=f"mod:hide24:{report_id}"
                ),
            ],
            [
                InlineKeyboardButton(text="Отклонить", callback_data=f"mod:reject:{report_id}"),
                InlineKeyboardButton(text="Забанить", callback_data=f"mod:ban:{report_id}"),
            ],
        ]
    )
