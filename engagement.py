"""
Механики удержания.

Возврат в приложение завязан на ежедневные привычки, а не на повторную
оценку внешности. Так сделано осознанно:

  * оценка формируется алгоритмом, поэтому «зайди и переоцени себя» даёт
    пользователю шум вместо обратной связи — цифра меняется от света и
    ракурса, доверие к приложению падает, человек уходит;
  * отметки привычек — настоящие данные, поэтому серия и прогресс
    осязаемы и не разваливаются при проверке;
  * привычки из списка действительно влияют на внешность, так что
    вовлечение и польза совпадают, а не конфликтуют.

Дополнительно есть суточный лимит отчётов: он создаёт причину вернуться
завтра и заодно не даёт скатиться в проверку своей оценки по десять раз
в день.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# ────────────────────────────── привычки ───────────────────────────────────


@dataclass(frozen=True)
class Habit:
    bit: int          # позиция в битовой маске
    key: str
    emoji: str
    title: str
    hint: str


HABITS: tuple[Habit, ...] = (
    Habit(0, "sleep", "😴", "Сон 7–9 часов", "Главный пункт списка"),
    Habit(1, "spf", "☀️", "SPF утром", "Единственное с доказанным эффектом"),
    Habit(2, "water", "💧", "Вода в течение дня", "Меньше отёков к утру"),
    Habit(3, "training", "🏋️", "Тренировка или активность", "Осанка и разворот плеч"),
    Habit(4, "skincare", "✨", "Уход за кожей", "Умывание и увлажнение"),
)

HABIT_BY_KEY = {habit.key: habit for habit in HABITS}
ALL_BITS = (1 << len(HABITS)) - 1

# День засчитывается в серию, если выполнено хотя бы столько привычек.
DAY_THRESHOLD = 3


def count_done(mask: int) -> int:
    return bin(mask & ALL_BITS).count("1")


def is_day_counted(mask: int) -> bool:
    return count_done(mask) >= DAY_THRESHOLD


def toggle(mask: int, key: str) -> int:
    habit = HABIT_BY_KEY.get(key)
    if habit is None:
        raise ValueError(f"неизвестная привычка: {key}")
    return (mask ^ (1 << habit.bit)) & ALL_BITS


def mask_to_dict(mask: int) -> dict[str, bool]:
    return {habit.key: bool(mask & (1 << habit.bit)) for habit in HABITS}


# ─────────────────────────────── серия ─────────────────────────────────────


@dataclass
class Streak:
    current: int          # дней подряд сейчас
    best: int             # лучшая серия за всё время
    total_days: int       # всего засчитанных дней
    perfect_days: int     # дней, где выполнены все пять
    grace_used: bool      # прощён ли пропуск в текущей серии


def compute_streak(days: dict[str, int], today: date) -> Streak:
    """
    Считает серию по словарю {'YYYY-MM-DD': маска}.

    Один пропущенный день прощается: люди срываются, и жёсткий сброс серии
    после единственной пропущенной субботы выбрасывает их из приложения
    насовсем. Второй пропуск подряд серию всё же обрывает.
    """
    counted = {day for day, mask in days.items() if is_day_counted(mask)}
    total_days = len(counted)
    perfect_days = sum(1 for mask in days.values() if mask & ALL_BITS == ALL_BITS)

    # Сегодня может быть ещё не отмечено — это не повод рвать серию.
    cursor = today if today.isoformat() in counted else today - timedelta(days=1)

    current = 0
    grace_used = False
    pending_grace = False

    while True:
        if cursor.isoformat() in counted:
            current += 1
            if pending_grace:
                # Пропуск оказался внутри серии, а не на её краю.
                grace_used = True
                pending_grace = False
        elif current > 0 and not grace_used and not pending_grace:
            pending_grace = True
        else:
            break
        cursor -= timedelta(days=1)

    return Streak(
        current=current,
        best=max(current, _best_streak(counted)),
        total_days=total_days,
        perfect_days=perfect_days,
        grace_used=grace_used,
    )


def _best_streak(counted: set[str]) -> int:
    if not counted:
        return 0

    days = sorted(date.fromisoformat(value) for value in counted)
    best = run = 1

    for previous, current in zip(days, days[1:]):
        gap = (current - previous).days
        run = run + 1 if gap <= 2 else 1  # разрыв в один день прощается
        best = max(best, run)

    return best


# ─────────────────────────────── ранги ─────────────────────────────────────


@dataclass(frozen=True)
class Rank:
    threshold: int        # сколько засчитанных дней нужно
    emoji: str
    title: str
    caption: str


RANKS: tuple[Rank, ...] = (
    Rank(240, "👑", "Машина", "Режим стал образом жизни"),
    Rank(120, "💎", "Система", "Дисциплина на автопилоте"),
    Rank(60, "🔥", "Режим", "Втянулся всерьёз"),
    Rank(21, "⚡", "Разгон", "Привычка закрепилась"),
    Rank(7, "🌿", "База", "Первая неделя позади"),
    Rank(0, "🌱", "Старт", "Всё только начинается"),
)


def rank_for(total_days: int) -> Rank:
    for rank in RANKS:
        if total_days >= rank.threshold:
            return rank
    return RANKS[-1]


def next_rank(total_days: int) -> tuple[Rank | None, int]:
    """Следующий ранг и сколько дней до него."""
    higher = [rank for rank in RANKS if rank.threshold > total_days]
    if not higher:
        return None, 0
    target = min(higher, key=lambda rank: rank.threshold)
    return target, target.threshold - total_days


# ──────────────────────────── достижения ───────────────────────────────────


@dataclass(frozen=True)
class Achievement:
    code: str
    emoji: str
    title: str
    description: str


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("first_scan", "📸", "Первый отчёт", "Собрать первый разбор"),
    Achievement("scans_10", "📊", "Десятка", "Собрать 10 отчётов"),
    Achievement("perfect_day", "✅", "Идеальный день", "Выполнить все 5 привычек"),
    Achievement("streak_3", "🌿", "Три дня", "Серия из 3 дней"),
    Achievement("streak_7", "🔥", "Неделя", "Серия из 7 дней"),
    Achievement("streak_30", "🌟", "Месяц", "Серия из 30 дней"),
    Achievement("perfect_10", "💎", "Десять из десяти", "10 идеальных дней"),
    Achievement("total_100", "👑", "Сотня", "100 засчитанных дней"),
)


def unlocked(streak: Streak, scans: int) -> dict[str, bool]:
    return {
        "first_scan": scans >= 1,
        "scans_10": scans >= 10,
        "perfect_day": streak.perfect_days >= 1,
        "streak_3": streak.best >= 3,
        "streak_7": streak.best >= 7,
        "streak_30": streak.best >= 30,
        "perfect_10": streak.perfect_days >= 10,
        "total_100": streak.total_days >= 100,
    }
