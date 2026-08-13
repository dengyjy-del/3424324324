"""Шкала оценок и расчёт тира.

Пять кнопок с фиксированным порядком. Числовые веса нужны только
для усреднения — наружу пользователю всегда показывается код тира.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Grade:
    code: str
    label: str      # что написано на кнопке
    weight: int     # 1..5, для усреднения
    color: str      # для мини-аппа


GRADES: tuple[Grade, ...] = (
    Grade("sub3", "sub 3", 1, "#3E5C76"),
    Grade("sub5", "sub 5", 2, "#5B7B8A"),
    Grade("ltn", "LTN", 3, "#B08327"),
    Grade("mtn", "MTN", 4, "#D9662B"),
    Grade("chad", "Chad", 5, "#C8305A"),
)

BY_CODE: dict[str, Grade] = {g.code: g for g in GRADES}
CODES: tuple[str, ...] = tuple(g.code for g in GRADES)


def is_valid(code: str) -> bool:
    return code in BY_CODE


def weight(code: str) -> int:
    return BY_CODE[code].weight


def label(code: str) -> str:
    return BY_CODE[code].label


# Минимум оценок, до которого тир не показывается — иначе одна
# случайная оценка определяет весь профиль.
MIN_VOTES_FOR_TIER = 5

# Байесовское сглаживание: тянем среднее к центру шкалы, пока оценок мало.
# Вес приора держим ниже MIN_VOTES_FOR_TIER — иначе при минимальном числе
# оценок приор перевешивает реальные голоса и крайние тиры недостижимы.
PRIOR_WEIGHT = 2.0
PRIOR_MEAN = 3.0


def tier_from_votes(total_weight: int, votes: int) -> str | None:
    """Код тира по накопленным оценкам или None, если оценок мало."""
    if votes < MIN_VOTES_FOR_TIER:
        return None
    avg = (total_weight + PRIOR_MEAN * PRIOR_WEIGHT) / (votes + PRIOR_WEIGHT)
    if avg < 1.75:
        return "sub3"
    if avg < 2.60:
        return "sub5"
    if avg < 3.45:
        return "ltn"
    if avg < 4.30:
        return "mtn"
    return "chad"


def tier_label(total_weight: int, votes: int) -> str:
    code = tier_from_votes(total_weight, votes)
    if code is None:
        left = MIN_VOTES_FOR_TIER - votes
        return f"нужно ещё {left} " + _plural(left, "оценка", "оценки", "оценок")
    return BY_CODE[code].label


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many
