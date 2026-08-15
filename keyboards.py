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
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"pnext:{target}"),
    )
    builder.row(InlineKeyboardButton(text="← Выйти", callback_data="pback"))
    return builder.as_markup()


def peer_empty(webapp_url: str = "") -> InlineKeyboardMarkup:
    """Очередь кончилась — не оставляем человека без единой кнопки."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Проверить снова", callback_data="prate"))
    builder.row(InlineKeyboardButton(text="← В ChadMatch", callback_data="pback"))
    return builder.as_markup()


def peer_report_reasons(target: str) -> InlineKeyboardMarkup:
    from peer import REPORT_REASONS

    builder = InlineKeyboardBuilder()
    for key, title in REPORT_REASONS:
        builder.row(
            InlineKeyboardButton(text=title, callback_data=f"prr:{key}:{target}")
        )
    builder.row(
        InlineKeyboardButton(text="← Отмена", callback_data=f"pnext:{target}"),
        InlineKeyboardButton(text="🏠 Выйти", callback_data="pback"),
    )
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


def peer_menu(webapp_url: str = "", has_profile: bool = False) -> InlineKeyboardMarkup:
    """
    Меню ChadMatch. Набор кнопок зависит от того, есть ли анкета: без неё
    оценивать нельзя, и предлагать это — тупик.
    """
    builder = InlineKeyboardBuilder()

    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="🔥 Открыть ChadMatch", web_app=WebAppInfo(url=webapp_url)
            )
        )

    if has_profile:
        builder.row(
            InlineKeyboardButton(text="⭐ Оценивать", callback_data="prate"),
            InlineKeyboardButton(text="👀 Кто оценил", callback_data="pwho"),
        )
        builder.row(
            InlineKeyboardButton(text="🗑 Удалить анкету", callback_data="pdel")
        )

    builder.row(InlineKeyboardButton(text="🏠 В меню", callback_data="menu"))
    return builder.as_markup()


def peer_after_save(webapp_url: str = "") -> InlineKeyboardMarkup:
    """Анкета создана — сразу предлагаем следующий шаг."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⭐ Начать оценивать", callback_data="prate"))
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="🔥 Открыть ChadMatch", web_app=WebAppInfo(url=webapp_url)
            )
        )
    return builder.as_markup()


def peer_confirm_delete() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑 Да, удалить", callback_data="pdelyes"),
        InlineKeyboardButton(text="← Отмена", callback_data="pback"),
    )
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
    """
    Карточка того, кто тебя оценил.

    Префикс pvw отличает этот поток от общей очереди: после ответа нужно
    показать следующего оценившего, а не случайную анкету.
    """
    from peer import PEER_TIERS

    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    for tier in PEER_TIERS:
        row.append(
            InlineKeyboardButton(
                text=f"{tier.emoji} {tier.title}",
                callback_data=f"pvw:{tier.key}:{target}",
            )
        )
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    builder.row(
        InlineKeyboardButton(text="🚩 Пожаловаться", callback_data=f"pr:{target}"),
        InlineKeyboardButton(text="⏭ Дальше", callback_data=f"pwnext:{target}"),
    )
    builder.row(InlineKeyboardButton(text="✖️ Закрыть", callback_data="pclose"))
    return builder.as_markup()


def legal_links(terms_url: str = "", privacy_url: str = "") -> InlineKeyboardMarkup:
    """Ссылки на документы. Кнопки появляются только у заданных адресов."""
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []
    if terms_url:
        row.append(InlineKeyboardButton(text="📄 Условия", url=terms_url))
    if privacy_url:
        row.append(InlineKeyboardButton(text="🔒 Приватность", url=privacy_url))
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="🏠 В меню", callback_data="menu"))
    return builder.as_markup()


def broadcast_confirm() -> InlineKeyboardMarkup:
    """Подтверждение рассылки: письмо на всю базу не отзывается."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📨 Отправить", callback_data="bcast:go"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="bcast:stop"),
    )
    return builder.as_markup()


def broadcast_more() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➡️ Продолжить", callback_data="bcast:go"),
        InlineKeyboardButton(text="⏹ Остановить", callback_data="bcast:stop"),
    )
    return builder.as_markup()


def broadcast_cta(webapp_url: str = "") -> InlineKeyboardMarkup:
    """
    Кнопка под рассылкой. Без неё письмо о новом режиме заканчивается
    ничем: человек дочитал, закрыл чат и не дошёл до самого режима.
    """
    builder = InlineKeyboardBuilder()
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text="🔥 Открыть ChadMatch", web_app=WebAppInfo(url=webapp_url)
            )
        )
    builder.row(
        InlineKeyboardButton(text="⭐ Оценивать прямо в боте", callback_data="peer")
    )
    return builder.as_markup()
