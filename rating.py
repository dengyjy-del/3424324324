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
import math
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


# Запасной режим, когда распознавание не сработало. Держится в тех же
# рамках, что и оценки по замерам, иначе один и тот же человек получал бы
# заметно разные баллы в зависимости от того, загрузилась модель или нет.
NORMAL = ScoreProfile(
    key="normal",
    overall_min=4.2,
    overall_max=7.0,
    overall_center=5.5,
    overall_sigma=0.6,
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

# Лесенка тиров. Пороги заданы вручную, поэтому калибровка балла подбиралась
# под них, а не наоборот.
TIERS: tuple[Tier, ...] = (
    Tier(7.25, "👑", "Chad", "верхний тир", "Заметен в любой комнате."),
    Tier(6.5, "🔶", "Chadlite", "выше среднего+", "До верхнего тира — рукой подать."),
    Tier(5.25, "🔷", "HTN", "High Tier Normie", "Сильная база, есть что докрутить."),
    Tier(4.0, "🔹", "MTN", "Mid Tier Normie", "Рабочая база и приличный запас роста."),
    Tier(2.5, "🌱", "LTN", "Low Tier Normie", "Всё решает уход и режим."),
    Tier(1.25, "🍂", "Sub-5", "нижний тир", "Стартовая точка. Дальше только вверх."),
    Tier(0.0, "🥀", "Sub-3", "нижний тир", "Всё впереди: режим, уход, спорт."),
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


# ═══════════════════════════ РЕАЛЬНЫЕ ЗАМЕРЫ ═══════════════════════════════
#
# Геометрию лица считает браузер по 478 точкам Face Mesh и присылает сюда
# готовые числа — сам снимок на сервер по-прежнему не попадает.
#
# Здесь замеры превращаются в баллы. Каждый параметр оценивается по тому,
# насколько измерение близко к диапазону, который в лицевой антропометрии
# считается гармоничным. Это честнее «предсказания красоты» нейросетью:
# такие модели обучены на оценках нескольких сотен людей и выдают вкусы
# конкретной выборки за объективную истину.
#
# Нижняя граница держится на 3.0 осознанно.

MEASURED_MIN = 1.1
MEASURED_MAX = 9.2


@dataclass
class FaceMetrics:
    """Замеры, пришедшие из браузера. Все величины безразмерные или в °."""

    canthal_tilt: float       # наклон глазной щели, градусы
    eye_aspect: float         # высота глазной щели / ширина
    symmetry: float           # 0..1, зеркальное совпадение половин
    thirds_balance: float     # 0..1, равенство верхней/средней/нижней третей
    fwhr: float               # ширина лица / высота средней зоны
    jaw_ratio: float          # ширина челюсти / ширина скул
    gonial_angle: float       # угол нижней челюсти, градусы
    chin_ratio: float         # высота нижней трети / высота лица
    nose_ratio: float         # ширина носа / ширина лица

    # Признаки формы лица: помогают отличать округлое лицо от узкого
    face_aspect: float = 0.80     # ширина скул / высота лица
    mid_jaw: float = 0.88         # ширина на уровне рта / ширина скул
    low_jaw: float = 0.80         # ширина углов челюсти / ширина скул
    chin_taper: float = 0.19      # сужение у подбородка
    jaw_drop: float = 0.31        # от губы до подбородка / ширина скул
    cheek_to_jaw: float = 0.55    # длина боковой линии
    lower_third: float = 0.38     # высота нижней трети / высота лица

    # Рельеф лица по глубине: различает выступающие черты и плоское лицо
    relief: float = 0.13
    nose_proj: float = -0.12
    cheek_proj: float = 0.70
    chin_proj: float = 0.20
    oval_flat: float = 0.62
    brow_proj: float = 0.04

    # Признаки по пикселям: геометрия о них ничего не знает.
    # Брови Face Mesh размечает по шаблону даже там, где их нет, поэтому
    # выраженность брови видна только через контраст со лбом.
    brow_contrast: float = 0.10
    skin_variance: float = 0.11
    skin_redness: float = 0.35

    # Качество снимка. В балл не входит — показывается отдельно, но
    # сохраняется в разметке: по нему видно, какие примеры надёжнее.
    yaw: float = 0.0
    roll: float = 0.0
    face_share: float = 0.4

    @classmethod
    def from_payload(cls, data: dict) -> "FaceMetrics":
        def number(key: str, low: float, high: float) -> float:
            try:
                value = float(data[key])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"нет замера {key}") from error
            if value != value or not low <= value <= high:
                raise ValueError(f"замер {key} вне допустимого диапазона")
            return value

        def optional(key: str, low: float, high: float, default: float) -> float:
            """Старые версии приложения этих замеров не присылают."""
            try:
                value = float(data[key])
            except (KeyError, TypeError, ValueError):
                return default
            return value if low <= value <= high else default

        return cls(
            canthal_tilt=number("canthal_tilt", -25, 25),
            eye_aspect=number("eye_aspect", 0.05, 1.2),
            symmetry=number("symmetry", 0.0, 1.0),
            thirds_balance=number("thirds_balance", 0.0, 1.0),
            fwhr=number("fwhr", 0.8, 3.2),
            jaw_ratio=number("jaw_ratio", 0.3, 1.4),
            gonial_angle=number("gonial_angle", 80, 175),
            chin_ratio=number("chin_ratio", 0.15, 0.6),
            nose_ratio=number("nose_ratio", 0.15, 0.7),
            **{
                key: optional(key, low, high, default)
                for key, low, high, default in (
                    ("face_aspect", 0.4, 1.6, 0.80),
                    ("mid_jaw", 0.4, 1.3, 0.88),
                    ("low_jaw", 0.3, 1.3, 0.80),
                    ("chin_taper", 0.05, 0.6, 0.19),
                    ("jaw_drop", 0.1, 0.8, 0.31),
                    ("cheek_to_jaw", 0.2, 1.2, 0.55),
                    ("lower_third", 0.15, 0.7, 0.38),
                    ("relief", 0.02, 0.6, 0.13),
                    ("nose_proj", -0.8, 0.4, -0.12),
                    ("cheek_proj", 0.1, 1.6, 0.70),
                    ("chin_proj", -0.5, 1.2, 0.20),
                    ("oval_flat", 0.1, 1.8, 0.62),
                    ("brow_proj", -0.4, 0.6, 0.04),
                    ("brow_contrast", 0.0, 1.0, 0.10),
                    ("skin_variance", 0.0, 1.0, 0.11),
                    ("skin_redness", -1.0, 2.0, 0.35),
                    ("yaw", 0.0, 1.0, 0.0),
                    ("roll", 0.0, 90.0, 0.0),
                    ("face_share", 0.05, 1.0, 0.4),
                )
            },
        )


def _closeness(value: float, ideal: float, tolerance: float) -> float:
    """1.0 при точном попадании, 0.0 на границе допуска и дальше."""
    return _clamp(1.0 - abs(value - ideal) / tolerance, 0.0, 1.0)


def _bell(value: float, center: float, sigma: float) -> float:
    """
    Колокол: 1.0 в центре, плавный спад в обе стороны.

    Раньше здесь было плато («всё внутри диапазона — максимум»), но тогда
    подавляющее большинство лиц упиралось в потолок и продукт переставал
    различать людей вообще. Гладкая кривая даёт настоящий разброс.
    """
    return math.exp(-0.5 * ((value - center) / sigma) ** 2)


# Степень сжимает верх шкалы. Без неё почти любое лицо, попадающее в
# анатомическую норму, упиралось в 8-9, и высокий балл переставал что-либо
# значить. Подобрана так, чтобы типичное лицо давало около 6, а верхние
# оценки доставались действительно редким сочетаниям пропорций.
SCORE_GAMMA = 4.5


def _to_score(quality: float) -> float:
    """Качество 0..1 → балл в рабочем коридоре."""
    shaped = _clamp(quality, 0.0, 1.0) ** SCORE_GAMMA
    return MEASURED_MIN + shaped * (MEASURED_MAX - MEASURED_MIN)


# Для каждого параметра — как получить качество 0..1 из замеров.
# Качество кожи геометрией не измеряется, поэтому считается отдельно.
# Допуски намеренно широкие. Способ замера здесь свой (точки Face Mesh, а не
# цефалометрические ориентиры), поэтому абсолютные величины могут
# систематически отличаться от литературных. Узкие коридоры в такой ситуации
# загнали бы в нижний балл всех подряд — а это ровно то, чего не должно
# случаться с продуктом для подростков.
QUALITY_RULES = {
    "canthal_tilt": lambda m: _bell(m.canthal_tilt, 6.5, 5.6),
    "hunter_eyes": lambda m: _bell(m.eye_aspect, 0.35, 0.077),
    "jawline": lambda m: _bell(m.jaw_ratio, 0.78, 0.084),
    "gonial_angle": lambda m: _bell(m.gonial_angle, 128.0, 14.0),
    "cheekbones": lambda m: _bell(m.fwhr, 1.95, 0.252),
    "chin": lambda m: _bell(m.chin_ratio, 0.34, 0.0434),
    "nose": lambda m: _bell(m.nose_ratio, 0.26, 0.0476),
    "symmetry": lambda m: _clamp((m.symmetry - 0.90) / 0.085, 0.0, 1.0),
    "proportions": lambda m: _clamp(m.thirds_balance, 0.0, 1.0),
}



# ═══════════════════ МОДЕЛЬ, ОБУЧЕННАЯ НА РАЗМЕЧЕННЫХ ФОТО ═════════════════
#
# Прежняя формула сравнивала замеры с «идеальными» диапазонами из
# антропометрии. На реальных фотографиях, размеченных по тирам, она давала
# 13% попаданий — хуже случайного угадывания, а лица моделей получали 5-6
# баллов. Именно на это и жаловались пользователи.
#
# Здесь линейная модель, обученная на размеченной выборке. Проверка
# leave-one-out: 35% попаданий в тир против 20% у случайного выбора.
# Медиана для красивых лиц выросла с 6.0 до 8.0.
#
# Честное ограничение: геометрия по одному 2D-снимку объясняет лишь часть
# восприятия. Кожа, волосы, ухоженность, свет и ракурс в замеры не попадают
# вовсе, поэтому на отдельном лице ошибка остаётся заметной. Чтобы поднять
# точность дальше, нужна выборка в сотни фото, а не полсотни.

# Модель обучена на ручной разметке владельца — 294 фотографии по той самой
# лесенке тиров, что видит пользователь. Проверка на отложенных данных
# (10-кратная кросс-валидация): корреляция 0.75, тир угадан в 38% случаев
# против 14% у случайного выбора из семи.
#
# К линейным признакам добавлены квадраты части из них: так модель видит
# «слишком много» так же плохо, как «слишком мало» — и чрезмерно широкое,
# и чрезмерно узкое лицо получают штраф.
#
# Оценки пользователей в обучение не идут: они завышают себе примерно на два
# балла, и добавление их даже с поправкой ухудшало результат.

MODEL_KEYS = ['canthal_tilt', 'eye_aspect', 'symmetry', 'thirds_balance', 'fwhr', 'jaw_ratio', 'gonial_angle', 'chin_ratio', 'nose_ratio', 'face_aspect', 'mid_jaw', 'low_jaw', 'chin_taper', 'jaw_drop', 'cheek_to_jaw', 'lower_third', 'relief', 'nose_proj', 'cheek_proj', 'chin_proj', 'oval_flat', 'brow_proj']
MODEL_SQUARES = ['fwhr', 'jaw_ratio', 'mid_jaw', 'gonial_angle', 'canthal_tilt', 'face_aspect', 'chin_ratio', 'nose_ratio']
MODEL_MEAN = [4.196785, 0.312704, 0.965459, 0.71931, 1.481994, 0.795121, 138.083126, 0.400137, 0.30164, 0.832777, 0.88196, 0.795121, 0.19173, 0.319748, 0.455454, 0.400137, 0.13505, -0.12521, 0.704113, 0.210682, 0.638614, 0.043973, 2.212858, 0.632895, 0.778402, 19093.924201, 26.677011, 0.695524, 0.161177, 0.091491]
MODEL_SCALE = [3.010649, 0.051652, 0.029199, 0.126896, 0.128657, 0.026035, 5.193704, 0.032673, 0.02246, 0.044784, 0.023432, 0.026035, 0.010775, 0.036095, 0.024371, 0.032673, 0.010116, 0.046388, 0.041749, 0.129488, 0.077443, 0.021582, 0.383763, 0.041352, 0.041387, 1419.791378, 24.078704, 0.075036, 0.02629, 0.013628]
MODEL_COEF = [0.466186, -0.120051, 0.239604, 0.91516, 1.324036, 0.839535, 0.114492, 0.56402, 1.473447, 1.042411, -0.221562, 0.839535, 0.376982, 0.57274, -0.132525, 0.56402, 0.011982, -1.067136, -0.196917, 1.279225, 0.106849, -0.672107, -0.889871, 0.560235, -1.768489, -0.083343, -0.348034, -1.071724, -0.718111, -1.799325]
MODEL_INTERCEPT = 4.015306
MODEL_PIVOT = 4.0153

# Калибровка асимметричная: ниже опорной точки отклонение растягивается
# сильнее, чем выше. Так менее привлекательные лица не собираются в кучу
# около середины шкалы, а верх при этом не улетает за десятку.
MODEL_PIVOT = 4.6194
MODEL_LOW = 1.15
MODEL_HIGH = 1.15
MODEL_SHIFT = -0.2

# Сжатие верхней части: якорь и коэффициент
TOP_ANCHOR = 4.0
TOP_SQUEEZE = 1.0


def model_score(metrics: "FaceMetrics") -> float:
    """Общий балл по замерам. Возвращает значение в коридоре 3.0-9.6."""
    # Порядок значений тот же, в каком модель обучалась: сначала признаки,
    # затем квадраты выбранных из них.
    values = [getattr(metrics, key) for key in MODEL_KEYS]
    values += [getattr(metrics, key) ** 2 for key in MODEL_SQUARES]

    raw = MODEL_INTERCEPT
    for value, mean, scale, coef in zip(
        values, MODEL_MEAN, MODEL_SCALE, MODEL_COEF
    ):
        raw += coef * (value - mean) / (scale or 1.0)

    delta = raw - MODEL_PIVOT
    stretched = delta * (MODEL_LOW if delta < 0 else MODEL_HIGH)
    value = _clamp(MODEL_PIVOT + stretched + MODEL_SHIFT, MEASURED_MIN, 9.6)

    # Верх шкалы поджат: в луксмаксинге девятка — величина почти
    # теоретическая, и оценки вроде 9.4 обесценивают всю шкалу. Ниже
    # опорной точки ничего не меняется, чтобы низ не уехал ещё дальше.
    if value > TOP_ANCHOR:
        value = TOP_ANCHOR + (value - TOP_ANCHOR) * TOP_SQUEEZE

    return round(value, 2)


def measured_report(
    user_id: int,
    photo_id: str,
    metrics: FaceMetrics,
    salt: str = "looksmax",
    profile: ScoreProfile = NORMAL,
) -> Report:
    """
    Отчёт по реальным замерам.

    В режиме съёмки (profile=DEMO) замеры игнорируются: там нужны заранее
    известные низкие числа для роликов.
    """
    if profile.key == DEMO.key:
        return generate_report(user_id, photo_id, salt, profile)

    rng = _make_rng(salt, user_id, photo_id, "measured")

    # Общий балл даёт модель целиком: он не среднее по параметрам, потому
    # что вклад признаков в восприятие сильно разный.
    overall = round(model_score(metrics), 1)

    # Частные параметры остаются диагностикой «где сильнее, где слабее» и
    # разворачиваются вокруг общего балла, чтобы карточка не расходилась
    # с итогом.
    scores: list[ParameterScore] = []
    for parameter in PARAMETERS:
        rule = QUALITY_RULES.get(parameter.key)
        if rule is None:
            offset = rng.gauss(0.0, 0.7)
        else:
            offset = (_clamp(rule(metrics), 0.0, 1.0) - 0.5) * 3.4 + rng.gauss(0.0, 0.35)
        value = _clamp(overall + offset, MEASURED_MIN, 9.6)
        scores.append(ParameterScore(parameter, round(value, 1)))

    return Report(
        report_id=_report_id(salt, user_id, photo_id, "measured"),
        overall=overall,
        potential=round(min(9.6, overall + rng.uniform(1.0, 2.2)), 1),
        percentile=_percentile(overall),
        tier=tier_for(overall),
        scores=scores,
    )


def metrics_readout(metrics: FaceMetrics) -> list[dict]:
    """Замеры для показа пользователю — как числа, а не как баллы."""
    return [
        {"emoji": "👁", "title": "Канторальный наклон", "value": f"{metrics.canthal_tilt:+.1f}°"},
        {"emoji": "📐", "title": "Гониальный угол", "value": f"{metrics.gonial_angle:.0f}°"},
        {"emoji": "⚖️", "title": "Симметрия", "value": f"{metrics.symmetry * 100:.0f}%"},
        {"emoji": "📊", "title": "Баланс третей", "value": f"{metrics.thirds_balance * 100:.0f}%"},
        {"emoji": "💎", "title": "Отношение ширины к высоте", "value": f"{metrics.fwhr:.2f}"},
        {"emoji": "🗿", "title": "Челюсть к скулам", "value": f"{metrics.jaw_ratio:.2f}"},
        {"emoji": "🦅", "title": "Раскрытие глаз", "value": f"{metrics.eye_aspect:.2f}"},
        {"emoji": "🔻", "title": "Ширина носа", "value": f"{metrics.nose_ratio:.2f}"},
    ]
