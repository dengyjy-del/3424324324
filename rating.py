"""
Движок оценки.

Как это работает:
  * Оценки генерируются алгоритмически, фото не анализируется и не скачивается.
  * Генератор детерминирован: seed = sha256(salt + user_id + file_unique_id).
    Одно и то же фото у одного и того же пользователя всегда даёт один и тот же
    отчёт — это убирает «накрутку» повторной отправкой и делает бота
    предсказуемым.
  * Общий балл всегда попадает в коридор 5.0-7.4 (центр ~6.15), а частные
    параметры расходятся вокруг него, чтобы отчёт выглядел живым: где-то 8+,
    где-то 4.5, но среднее держится в коридоре.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

# ─────────────────────────── профили оценок ────────────────────────────────


@dataclass(frozen=True)
class ScoreProfile:
    """
    Коридор, в который загоняются оценки.

    key            влияет на seed, поэтому одно фото в разных профилях
                   даёт разные (но стабильные) числа
    overall_*      границы и форма распределения общего балла
    param_*        границы отдельных параметров и их разброс
    """

    key: str
    overall_min: float
    overall_max: float
    overall_center: float
    overall_sigma: float
    param_min: float
    param_max: float
    param_spread: float


# Обычный режим: общий балл 5.0-7.4, центр ~6.15.
NORMAL = ScoreProfile(
    key="normal",
    overall_min=5.0,
    overall_max=7.4,
    overall_center=6.15,
    overall_sigma=0.55,
    param_min=3.6,
    param_max=9.3,
    param_spread=1.15,
)

# Демо-режим для съёмки контента: общий балл 2.0-3.5.
DEMO = ScoreProfile(
    key="demo",
    overall_min=2.0,
    overall_max=3.5,
    overall_center=2.7,
    overall_sigma=0.42,
    param_min=1.0,
    param_max=5.2,
    param_spread=0.85,
)

# Полоска с полублоками: 12 ячеек × 2 = 24 градации.
BAR_WIDTH = 12
BAR_FULL = "█"
BAR_HALF = "▌"
BAR_EMPTY = "░"


# ───────────────────────────────── модели ──────────────────────────────────


@dataclass(frozen=True)
class Parameter:
    key: str
    emoji: str
    title: str
    tips: tuple[str, ...]


@dataclass(frozen=True)
class Tier:
    threshold: float
    emoji: str
    code: str
    title: str
    comment: str


@dataclass
class ParameterScore:
    parameter: Parameter
    value: float

    @property
    def bar(self) -> str:
        return render_bar(self.value)


@dataclass
class Report:
    report_id: str
    overall: float
    potential: float
    percentile: int
    tier: Tier
    scores: list[ParameterScore] = field(default_factory=list)

    @property
    def bar(self) -> str:
        return render_bar(self.overall)

    @property
    def potential_bar(self) -> str:
        return render_bar(self.potential)

    def sorted_desc(self) -> list[ParameterScore]:
        return sorted(self.scores, key=lambda s: s.value, reverse=True)

    def strongest(self, count: int = 3) -> list[ParameterScore]:
        return self.sorted_desc()[:count]

    def weakest(self, count: int = 3) -> list[ParameterScore]:
        return self.sorted_desc()[-count:][::-1]


# ────────────────────────────── параметры ──────────────────────────────────

PARAMETERS: tuple[Parameter, ...] = (
    Parameter(
        key="canthal_tilt",
        emoji="👁",
        title="Канторальный наклон",
        tips=(
            "Наклон глазной щели — врождённая штука, но визуально он читается "
            "через отёчность. Сон 7-9 часов и меньше соли вечером убирают "
            "припухлость, из-за которой глаз кажется «опущенным».",
            "Работай с оправой бровей: аккуратная, чуть приподнятая к вискам "
            "линия брови вытягивает взгляд лучше любых упражнений.",
            "Холодный компресс утром на 1-2 минуты — самый дешёвый способ "
            "сделать зону глаз собраннее перед выходом.",
        ),
    ),
    Parameter(
        key="hunter_eyes",
        emoji="🦅",
        title="Посадка глаз",
        tips=(
            "Глубину посадки не изменишь, но её съедает недосып и обезвоживание. "
            "Вода в течение дня + стабильный режим сна дают заметный эффект за "
            "пару недель.",
            "Тёмные круги чаще всего про сон и генетику, а не про кремы. Если "
            "они выражены годами — это вопрос к дерматологу, а не к бьюти-блогу.",
            "Ухоженная бровь и отсутствие нависающей отёчности читаются как "
            "«собранный взгляд» гораздо сильнее, чем анатомия.",
        ),
    ),
    Parameter(
        key="jawline",
        emoji="🗿",
        title="Линия челюсти",
        tips=(
            "Чёткость линии челюсти — это в первую очередь общий процент жира и "
            "отёков, а не упражнения на челюсть. Регулярный спорт и нормальный "
            "сон работают, «жевательные» гаджеты — нет.",
            "Осанка решает: выведенная вперёд голова визуально стирает челюсть. "
            "Держи подбородок нейтрально, плечи развёрнутыми.",
            "Аккуратно оформленная борода или щетина — легальный чит-код для "
            "линии челюсти. Работает мгновенно.",
        ),
    ),
    Parameter(
        key="gonial_angle",
        emoji="📐",
        title="Гониальный угол",
        tips=(
            "Угол нижней челюсти задан анатомией. Что реально в твоих руках — "
            "убрать отёчность и держать осанку, чтобы угол не «плыл».",
            "Причёска с объёмом на макушке и короче по бокам делает нижнюю "
            "треть лица шире и угловатее.",
            "Ракурс важнее анатомии: камера чуть ниже уровня глаз и лёгкий "
            "поворот в три четверти выигрывают у любого фаса.",
        ),
    ),
    Parameter(
        key="cheekbones",
        emoji="💎",
        title="Скуловые кости",
        tips=(
            "Скулы проявляются при стабильном режиме и нормальной гидратации. "
            "Отёчное лицо скрывает даже отличную кость.",
            "Свет решает: боковой источник даёт тень под скулой, фронтальная "
            "вспышка убивает рельеф. Учитывай это на фото.",
            "Не гонись за «острыми» скулами через жёсткие ограничения в еде — "
            "это ломает и здоровье, и лицо. Работает только устойчивый режим.",
        ),
    ),
    Parameter(
        key="chin",
        emoji="⚡",
        title="Проекция подбородка",
        tips=(
            "Проекция подбородка сильно зависит от положения головы. Убери "
            "«черепашью» позу — и профиль меняется без всяких вмешательств.",
            "Форма бороды по линии подбородка визуально удлиняет нижнюю треть "
            "лица, если тебе не хватает проекции.",
            "Если проекция реально беспокоит — это вопрос к ортодонту, а не к "
            "интернет-советам. Только не решай ничего по фото из телеграма.",
        ),
    ),
    Parameter(
        key="nose",
        emoji="🔻",
        title="Гармония носа",
        tips=(
            "Нос почти всегда воспринимается хуже самим владельцем, чем "
            "окружающими. По исследованиям восприятия, люди замечают его "
            "в разы меньше, чем кажется.",
            "Причёска с объёмом и правильная длина визуально балансируют нос "
            "лучше, чем любые упражнения.",
            "Фронтальная камера телефона искажает центр лица и увеличивает нос "
            "на 20-30%. Оценивай себя только по фото с нормального расстояния.",
        ),
    ),
    Parameter(
        key="symmetry",
        emoji="⚖️",
        title="Симметрия лица",
        tips=(
            "Идеально симметричных лиц не существует — лёгкая асимметрия есть "
            "у всех и воспринимается как норма, а не как дефект.",
            "Часть асимметрии набегает от привычек: сон на одной стороне, "
            "жевание одной стороной, наклон головы к плечу. Это правится.",
            "Осанка и положение шеи выравнивают лицо на фото сильнее, чем любые "
            "процедуры.",
        ),
    ),
    Parameter(
        key="skin",
        emoji="✨",
        title="Качество кожи",
        tips=(
            "Самый управляемый параметр из всех. База: мягкое умывание, "
            "увлажнение, SPF каждый день. Три шага, никакой эзотерики.",
            "SPF — единственный пункт с доказанным долгим эффектом на вид кожи. "
            "Если делать только одну вещь, делай эту.",
            "Стойкие высыпания — к дерматологу, а не к 12-шаговым рутинам из "
            "тиктока. Рабочие протоколы существуют, но подбирает их врач.",
        ),
    ),
    Parameter(
        key="proportions",
        emoji="📊",
        title="Пропорции лица",
        tips=(
            "Пропорции трети лица правятся причёской: высота и объём сверху "
            "меняют восприятие всего лица за один поход к барберу.",
            "Подбери форму под свой тип лица, а не под тренд. Универсальных "
            "стрижек не бывает, и это нормально.",
            "Оправа очков — недооценённый инструмент: она задаёт горизонталь "
            "в центре лица и балансирует верх с низом.",
        ),
    ),
)

TIERS: tuple[Tier, ...] = (
    Tier(9.0, "🌟", "Gigachad", "запредельный тир", "Статистическая аномалия."),
    Tier(7.5, "👑", "Chad", "верхний тир", "Заметен в любой комнате."),
    Tier(6.8, "🔶", "Chadlite", "выше среднего+", "До верхнего тира — рукой подать."),
    Tier(6.0, "🔷", "HTN", "High Tier Normie", "Сильная база, есть что докрутить."),
    Tier(5.0, "🔹", "MTN", "Mid Tier Normie", "Рабочая база и приличный запас роста."),
    Tier(3.6, "🌱", "LTN", "Low Tier Normie", "Всё решает уход и режим."),
    Tier(0.0, "🍂", "Sub-LTN", "нижний тир", "Стартовая точка. Дальше только вверх."),
)


# ────────────────────────────── утилиты ────────────────────────────────────


def render_bar(value: float, width: int = BAR_WIDTH) -> str:
    """Полоска прогресса с шагом в полблока: ███████▌░░░░."""
    units = max(0.0, min(10.0, value)) / 10 * width
    halves = int(round(units * 2))
    full, half = divmod(halves, 2)
    full = min(full, width)
    tail = BAR_HALF if half and full < width else ""
    return BAR_FULL * full + tail + BAR_EMPTY * (width - full - len(tail))


def tier_for(overall: float) -> Tier:
    for tier in TIERS:
        if overall >= tier.threshold:
            return tier
    return TIERS[-1]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rebalance(values: list[float], target: float, low: float, high: float) -> list[float]:
    """
    Подгоняет список так, чтобы среднее равнялось target, а каждое значение
    лежало в [low, high]. Излишек равномерно раскидывается по «свободным»
    элементам, которые ещё не упёрлись в границы.
    """
    values = [_clamp(v, low, high) for v in values]
    count = len(values)

    for _ in range(12):
        residual = target * count - sum(values)
        if abs(residual) < 1e-6:
            break
        free = [
            i
            for i, v in enumerate(values)
            if (residual > 0 and v < high - 1e-9) or (residual < 0 and v > low + 1e-9)
        ]
        if not free:
            break
        share = residual / len(free)
        for i in free:
            values[i] = _clamp(values[i] + share, low, high)

    return values


def _make_rng(salt: str, user_id: int, photo_id: str, profile_key: str) -> random.Random:
    payload = f"{salt}|{profile_key}|{user_id}|{photo_id}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return random.Random(int(digest[:16], 16))


def _report_id(salt: str, user_id: int, photo_id: str, profile_key: str) -> str:
    payload = f"{salt}|id|{profile_key}|{user_id}|{photo_id}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:6].upper()


def _percentile(overall: float) -> int:
    """Мягкая кривая: 5.0 → ~19%, 6.2 → ~56%, 7.4 → ~93%."""
    return int(_clamp(round((overall - 4.4) * 31), 1, 97))


# ───────────────────────────── генерация ───────────────────────────────────


def generate_report(
    user_id: int,
    photo_id: str,
    salt: str = "looksmax",
    profile: ScoreProfile = NORMAL,
) -> Report:
    """Собирает детерминированный отчёт по (user_id, photo_id, профиль)."""
    rng = _make_rng(salt, user_id, photo_id, profile.key)

    target = _clamp(
        rng.gauss(profile.overall_center, profile.overall_sigma),
        profile.overall_min,
        profile.overall_max,
    )

    deltas = [rng.gauss(0.0, profile.param_spread) for _ in PARAMETERS]
    mean_delta = sum(deltas) / len(deltas)
    deltas = [d - mean_delta for d in deltas]

    raw = _rebalance(
        [target + d for d in deltas], target, profile.param_min, profile.param_max
    )
    values = [round(v, 1) for v in raw]

    overall = round(
        _clamp(sum(values) / len(values), profile.overall_min, profile.overall_max), 1
    )
    potential = round(min(9.6, overall + rng.uniform(1.2, 2.4)), 1)

    scores = [
        ParameterScore(parameter=param, value=value)
        for param, value in zip(PARAMETERS, values)
    ]

    return Report(
        report_id=_report_id(salt, user_id, photo_id, profile.key),
        overall=overall,
        potential=potential,
        percentile=_percentile(overall),
        tier=tier_for(overall),
        scores=scores,
    )


def pick_tip(report: Report, score: ParameterScore, salt: str = "looksmax") -> str:
    """Детерминированно выбирает совет для параметра в рамках отчёта."""
    payload = f"{salt}|tip|{report.report_id}|{score.parameter.key}".encode("utf-8")
    index = int(hashlib.md5(payload).hexdigest(), 16) % len(score.parameter.tips)
    return score.parameter.tips[index]
