"""Режим съёмки для раздела оценок.

Собирает постановочную карточку вида «<ник> оценил(а) тебя на <оценка>»
для записи промо-роликов. Работает поверх существующего DemoState:
код тот же, что включает основной режим съёмки.

Материал постановочный по определению — карточка собирается из фото и
оценки, которые вводит админ, а не из действий реального пользователя.
Каждая сборка пишется в rate_demo_log, чтобы её можно было отличить от
настоящих оценок.

Ограничения намеренные:
  • только для ID из ADMIN_IDS и только при включённом режиме съёмки;
  • ник проходит ту же проверку, что и имя в анкете, — без ссылок;
  • оценка берётся из той же шкалы, что и в реальном разделе.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core import db, grades, texts


@dataclass
class Draft:
    """Незаконченная карточка: копится между шагами диалога."""

    photo_file_id: str | None = None
    nickname: str | None = None
    grade: str | None = None
    extras: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.photo_file_id and self.nickname and self.grade)


def caption(nickname: str, grade: str) -> str:
    """Подпись карточки.

    Форма «оценил(а)» — потому что пол автора оценки неизвестен,
    как и в настоящем уведомлении.
    """
    return (
        f"<b>{_esc(nickname)}</b> оценил(а) тебя на "
        f"<b>{grades.label(grade)}</b>"
    )


def card_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под карточкой.

    callback_data ведёт на заглушки: в кадре нужен вид кнопок, а нажатие
    во время записи не должно ничего менять в базе.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Оценить", callback_data="demo:rate"),
                InlineKeyboardButton(text="Написать", callback_data="demo:write"),
            ]
        ]
    )


def grade_keyboard() -> InlineKeyboardMarkup:
    """Выбор оценки: та же шкала и тот же порядок, что в разделе."""
    buttons = [
        InlineKeyboardButton(text=g.label, callback_data=f"demo:g:{g.code}")
        for g in grades.GRADES
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:3],
            buttons[3:],
            [InlineKeyboardButton(text="Отмена", callback_data="demo:cancel")],
        ]
    )


def validate_nickname(raw: str) -> tuple[bool, str]:
    """Ник для карточки. Правила те же, что для имени в анкете."""
    text = raw.strip()
    if text.startswith("@"):
        # Форма «@ник» в кадре выглядит естественно, но собаку в проверку
        # не пускаем — она же запрещена в именах анкет.
        text = text[1:].strip()
    return texts.validate_name(text)


async def register(admin_id: int, nickname: str, grade: str) -> None:
    await db.log_demo_card(admin_id, nickname, grade)


# ── Тексты диалога ──────────────────────────────────────────────────────────

ASK_PHOTO = (
    "<b>Съёмка: карточка оценки</b>\n\n"
    "Пришли фото, которое должно быть на карточке.\n\n"
    "Дальше спрошу ник и оценку, и соберу сообщение в том виде, "
    "в каком оно приходит в разделе оценок."
)

ASK_NICK = "Теперь ник — он попадёт в подпись. Например: Алиса"

ASK_GRADE = "Какую оценку он(а) поставил(а)?"

CANCELLED = "Сборка карточки отменена."

NOT_IN_DEMO = (
    "Режим съёмки выключен. Включи его кодом, потом набери /demo_card."
)

DENIED = "Команда доступна только владельцу бота."

RIGHTS_REMINDER = (
    "Карточка собрана.\n\n"
    "<i>Материал постановочный: ролик увидят посторонние, поэтому фото "
    "должно быть твоим, стоковым с подходящей лицензией или "
    "сгенерированным. Чужое фото в промо подставляет и человека, и бота.</i>"
)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
