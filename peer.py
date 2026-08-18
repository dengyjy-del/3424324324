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

import hashlib
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

# Через сколько пропущенная анкета может показаться снова. Насовсем
# скрывать неправильно: люди листают быстро и часто пропускают случайно.
SKIP_HOURS = 12

# Как часто человеку приходит «тебя оценили». Каждая оценка отдельным
# сообщением превратила бы бота в спам, поэтому уведомления копятся.
NOTIFY_COOLDOWN_MINUTES = 30


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
    """
    Проверка возраста для ChadMatch.

    Порог здесь выше, чем в остальном приложении, и это не формальность.
    В разборе снимок не покидает устройство, а тут он публикуется: его
    видят незнакомые люди и оценивают внешность. Витрина с лицами
    несовершеннолетних, доступная посторонним, — то, что нельзя починить
    задним числом, поэтому проверка живёт на сервере, а не в интерфейсе.
    """
    if age < 10 or age > 99:
        return "Проверь возраст"
    if age < PEER_MIN_AGE:
        return (
            f"ChadMatch работает с {PEER_MIN_AGE} лет: здесь твоё фото видят "
            "другие люди.\n\nОстальное приложение открыто и раньше — разбор "
            "по фото, привычки, серии и гайды. В разборе снимок вообще не "
            "уходит с устройства."
        )
    return None


CONSENT_TEXT = (
    "Твоё фото увидят другие пользователи и смогут его оценить.\n\n"
    "• Загружай только собственное фото\n"
    "• Режим работает с 18 лет\n"
    "• Жалобы разбирает модератор, анкету могут скрыть или удалить\n"
    "• Анкету можно удалить в любой момент, фото удаляется вместе с ней"
)


# ──────────────────── снимки наполнения ────────────────────────────────────
#
# Снимок из пула показывается как обычная анкета: с именем и возрастом.
# Без них карточка выглядит служебной, и оценивают её иначе — а нам нужно,
# чтобы наполнение было неотличимо от живых анкет.

FILLER_NAMES: tuple[str, ...] = (
    "Артём", "Никита", "Даня", "Кирилл", "Матвей", "Егор", "Тимур", "Марк",
    "Илья", "Рома", "Саша", "Влад", "Лёша", "Стас", "Гоша", "Денис",
    "Женя", "Костя", "Лев", "Миша", "Олег", "Паша", "Серёжа", "Ян",
    "Глеб", "Захар", "Макар", "Платон", "Савва", "Фёдор", "Юра", "Андрей",
)


def filler_identity(key: str) -> tuple[str, int]:
    """
    Имя и возраст по ключу снимка. Детерминированно: один и тот же снимок
    всегда представляется одинаково, иначе при повторном показе карточка
    выглядела бы другим человеком.
    """
    digest = hashlib.sha256(f"filler|{key}".encode()).digest()
    name = FILLER_NAMES[digest[0] % len(FILLER_NAMES)]
    age = PEER_MIN_AGE + digest[1] % 7  # 18-24
    return name, age


def parse_identity(raw: str) -> tuple[str, int] | None:
    """
    Разбирает «Имя, 19» — из подписи к фото или из имени файла
    вида «Имя_19.jpg». Возвращает None, если разобрать не вышло.
    """
    text = (raw or "").rsplit(".", 1)[0]
    for sep in (",", ";", "_", "-"):
        if sep in text:
            left, _, right = text.rpartition(sep)
            digits = "".join(ch for ch in right if ch.isdigit())
            name = clean_name(left)
            if digits and len(name) >= MIN_NAME_LENGTH:
                age = int(digits[:3])
                if PEER_MIN_AGE <= age <= 99:
                    return name, age
    return None


# ═══════════════════════ СВОДКА ПО ОЦЕНКАМ ═════════════════════════════════
#
# Считается по сырым оценкам, чтобы всё было в одном месте и читалось
# целиком. Числа рассчитаны на публикацию: людям интересно видеть, где они
# на общем фоне, и это само по себе повод вернуться в режим.


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def vibe(votes: list[dict], algo: dict[int, float] | None = None) -> dict:
    """Разбор всех выставленных оценок: распределение, согласие, крайности."""
    if not votes:
        return {"votes": 0}

    spread: dict[str, int] = {tier.key: 0 for tier in PEER_TIERS}
    by_target: dict[str, list[float]] = {}
    by_voter: dict[int, list[float]] = {}

    for row in votes:
        if row["tier"] in spread:
            spread[row["tier"]] += 1
        by_target.setdefault(row["target"], []).append(float(row["score"]))
        by_voter.setdefault(int(row["voter_id"]), []).append(float(row["score"]))

    total = len(votes)
    average = sum(float(r["score"]) for r in votes) / total

    # Анкеты, по которым набралось хотя бы три мнения
    judged = {t: v for t, v in by_target.items() if len(v) >= 3}
    per_profile = [sum(v) / len(v) for v in judged.values()]

    # Насколько люди сходятся во мнении об одном и том же человеке.
    # Маленький разброс — согласие, большой — спорная внешность.
    agreement = (
        sum(_stdev(v) for v in judged.values()) / len(judged) if judged else 0.0
    )

    # Самый спорный: там, где мнения расходятся сильнее всего
    controversial = max(
        (( _stdev(v), t) for t, v in judged.items()), default=(0.0, None)
    )

    # Щедрость: у скольких оценивающих средняя выше общей
    active = {u: v for u, v in by_voter.items() if len(v) >= 5}
    generous = sum(1 for v in active.values() if sum(v) / len(v) > average)

    result = {
        "votes": total,
        "voters": len(by_voter),
        "targets": len(by_target),
        "average": round(average, 2),
        "spread": spread,
        "judged": len(judged),
        "agreement": round(agreement, 2),
        "controversial_gap": round(controversial[0], 2),
        "generous_share": round(generous / len(active) * 100) if active else 0,
        "profile_scores": per_profile,
    }

    # Алгоритм против людей: сравниваем только тех, у кого есть и то и другое
    if algo:
        pairs = []
        for target, scores in judged.items():
            if not target.startswith("u:"):
                continue
            try:
                user_id = int(target[2:])
            except ValueError:
                continue
            if user_id in algo:
                pairs.append((algo[user_id], sum(scores) / len(scores)))

        if len(pairs) >= 3:
            algo_avg = sum(a for a, _ in pairs) / len(pairs)
            human_avg = sum(h for _, h in pairs) / len(pairs)
            result["duel"] = {
                "count": len(pairs),
                "algo": round(algo_avg, 2),
                "people": round(human_avg, 2),
                "gap": round(human_avg - algo_avg, 2),
                # У скольких люди оказались щедрее алгоритма
                "people_kinder": round(
                    sum(1 for a, h in pairs if h > a) / len(pairs) * 100
                ),
            }

    return result
