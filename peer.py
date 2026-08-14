"""
Режим взаимных оценок.

Пользователь выкладывает своё фото и оценивает чужие. В отличие от скана,
здесь снимок действительно хранится на сервере — иначе показывать его другим
нечего. Отсюда все ограничения этого модуля.

Главное из них: режим доступен только с 18 лет, и проверяется это на
сервере, а не галочкой в интерфейсе. Основное приложение работает с 13, но
там фото не покидает устройство. Как только снимок начинают видеть чужие
люди, возраст перестаёт быть формальностью: витрина с лицами
несовершеннолетних — это не та функция, которую можно чинить постфактум.
"""

from __future__ import annotations

from dataclasses import dataclass

# Возрастной порог именно для этого режима. Не берётся из настроек: значение
# ниже 18 здесь недопустимо ни при какой конфигурации.
PEER_MIN_AGE = 18

# Версия правил. При изменении текста согласие спрашивается заново.
TERMS_VERSION = "2026-07-16"

MAX_NAME_LENGTH = 24
MIN_NAME_LENGTH = 2

# Сколько оценок можно выставить за сутки. Ограничение против накрутки и
# бездумного пролистывания.
DAILY_VOTE_LIMIT = 120

# Сколько времени анкета скрыта после жалобы, подтверждённой модератором.
HIDE_HOURS = 24


@dataclass(frozen=True)
class PeerTier:
    key: str
    emoji: str
    title: str
    score: float


# Шкала оценивания. Значения нужны, чтобы усреднять и показывать владельцу
# анкеты понятный итог.
PEER_TIERS: tuple[PeerTier, ...] = (
    PeerTier("sub3", "🥀", "Sub 3", 0.8),
    PeerTier("sub5", "🍂", "Sub 5", 1.9),
    PeerTier("ltn", "🌱", "LTN", 3.2),
    PeerTier("mtn", "🔹", "MTN", 4.6),
    PeerTier("htn", "🔷", "HTN", 5.9),
    PeerTier("chadlite", "🔶", "Chadlite", 6.9),
    PeerTier("chad", "👑", "Chad", 8.0),
)

TIER_BY_KEY = {tier.key: tier for tier in PEER_TIERS}


REPORT_REASONS: tuple[tuple[str, str], ...] = (
    ("not_self", "Чужое фото"),
    ("minor", "На фото несовершеннолетний"),
    ("nsfw", "Непристойный контент"),
    ("ads", "Реклама или спам"),
    ("other", "Другое"),
)

REASON_TITLES = dict(REPORT_REASONS)


def tier_for_score(value: float) -> PeerTier:
    """Тир по среднему баллу — для показа владельцу анкеты."""
    best = PEER_TIERS[0]
    for tier in PEER_TIERS:
        if value >= tier.score:
            best = tier
    return best


def clean_name(raw: str) -> str:
    """
    Имя для показа другим. Убираем ссылки и служебные символы: анкета —
    не место для контактов и приглашений.
    """
    name = " ".join((raw or "").split())
    for bad in ("@", "http://", "https://", "t.me", "<", ">", "/"):
        name = name.replace(bad, "")
    name = " ".join(name.split())
    return name[:MAX_NAME_LENGTH]


def name_error(name: str) -> str | None:
    if len(name) < MIN_NAME_LENGTH:
        return "Имя слишком короткое"
    if not any(ch.isalpha() for ch in name):
        return "В имени должны быть буквы"
    return None


def age_error(age: int) -> str | None:
    if age < PEER_MIN_AGE:
        return (
            f"Режим оценок доступен с {PEER_MIN_AGE} лет. "
            "Разбор по фото в основном разделе работает и раньше."
        )
    if age > 99:
        return "Проверь возраст"
    return None


CONSENT_TEXT = (
    "Твоё фото увидят другие пользователи и смогут его оценить.\n\n"
    "• Загружай только собственное фото\n"
    "• Режим работает с 18 лет\n"
    "• Жалобы разбирает модератор, анкету могут скрыть или удалить\n"
    "• Анкету можно удалить в любой момент, фото удаляется вместе с ней"
)
