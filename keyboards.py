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
    # ChadMatch — первым и отдельной строкой. Цвет инлайн-кнопкам Telegram
    # задать не даёт, поэтому выделяем единственным доступным способом:
    # своя строка над остальными, рамка из символов и яркая эмодзи.
    builder.row(
        InlineKeyboardButton(text="🔥 ChadMatch — оценки от людей", callback_data="peer")
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


def open_app(webapp_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✨ Открыть приложение", web_app=WebAppInfo(url=webapp_url)
        )
    )
    return builder.as_markup()


# ───────────────────── режим взаимных оценок ───────────────────────────────


def report_actions(report_id: int, target: str) -> InlineKeyboardMarkup:
    """Решение по жалобе: удалить анкету или скрыть на сутки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚫 Удалить анкету", callback_data=f"rep:del:{report_id}:{target}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🙈 Скрыть на 24 часа", callback_data=f"rep:hide:{report_id}:{target}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✅ Оставить", callback_data=f"rep:keep:{report_id}:{target}"
        )
    )
    return builder.as_markup()


def peer_vote(target: str) -> InlineKeyboardMarkup:
    """Кнопки оценки для бота: та же шкала, что и в приложении."""
    from peer import PEER_TIERS

    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for tier in PEER_TIERS:
        row.append(
            InlineKeyboardButton(
                text=f"{tier.emoji} {tier.title}",
                callback_data=f"pv:{tier.key}:{target}",
            )
        )
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    builder.row(
        InlineKeyboardButton(text="🚩 Пожаловаться", callback_data=f"pr:{target}"),
        InlineKeyboardButton(text="⏭ Дальше", callback_data="pnext"),
    )
    return builder.as_markup()


def peer_report_reasons(target: str) -> InlineKeyboardMarkup:
    from peer import REPORT_REASONS

    builder = InlineKeyboardBuilder()
    for key, title in REPORT_REASONS:
        builder.row(
            InlineKeyboardButton(text=title, callback_data=f"prr:{key}:{target}")
        )
    builder.row(InlineKeyboardButton(text="← Отмена", callback_data="pnext"))
    return builder.as_markup()


def peer_demo_card(username: str = "") -> InlineKeyboardMarkup:
    """
    Карточка для съёмки: как выглядит уведомление об оценке.

    Кнопки «Написать» здесь нет намеренно: прямая переписка после низкой
    оценки — самый короткий путь к конфликту, и убирать её потом сложнее,
    чем не добавлять сейчас.
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ Оценить в ответ", callback_data="demo:rate"))
    return builder.as_markup()


def peer_menu(webapp_url: str = "") -> InlineKeyboardMarkup:
    """Меню ChadMatch: приложение удобнее, но всё работает и в чате."""
    builder = InlineKeyboardBuilder()
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="🔥 Открыть ChadMatch", web_app=WebAppInfo(url=webapp_url)
            )
        )
    builder.row(InlineKeyboardButton(text="⭐ Оценивать здесь", callback_data="prate"))
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="back"))
    return builder.as_markup()


def peer_rated(webapp_url: str = "") -> InlineKeyboardMarkup:
    """Уведомление об оценках: посмотреть, кто оценил, или открыть приложение."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👀 Кто оценил", callback_data="pwho")
    )
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="🔥 Открыть ChadMatch", web_app=WebAppInfo(url=webapp_url)
            )
        )
    return builder.as_markup()


def peer_answer(target: str) -> InlineKeyboardMarkup:
    """Карточка того, кто тебя оценил: ответить оценкой или пропустить."""
    from peer import PEER_TIERS

    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for tier in PEER_TIERS:
        row.append(
            InlineKeyboardButton(
                text=f"{tier.emoji} {tier.title}",
                callback_data=f"pv:{tier.key}:{target}",
            )
        )
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    builder.row(
        InlineKeyboardButton(text="🚩 Пожаловаться", callback_data=f"pr:{target}"),
        InlineKeyboardButton(text="✖️ Закрыть", callback_data="pclose"),
    )
    return builder.as_markup()
