"""Тексты сообщений и рендеринг отчётов (HTML parse mode)."""

from __future__ import annotations

from html import escape

from rating import Report, pick_tip, render_bar

LINE = "━━━━━━━━━━━━━━━━━━"

# Название бота. Подставляется из BRAND_NAME при старте — менять здесь не нужно.
BRAND = "LOOKSCORE"


def configure(brand_name: str) -> None:
    """Вызывается один раз при запуске."""
    global BRAND
    BRAND = brand_name

DISCLAIMER_SHORT = "<i>Развлекательный формат. Оценки генерируются алгоритмом.</i>"

DISCLAIMER_FULL = (
    "<i>⚠️ Бот развлекательный. Оценки формируются алгоритмом и не являются "
    "экспертным или медицинским заключением.</i>"
)


def safe(value: str | None, fallback: str = "аноним") -> str:
    return escape(value) if value else fallback


# ───────────────────────────── статичные ───────────────────────────────────


def start(name: str) -> str:
    return (
        f"🧬 <b>{BRAND}</b>\n"
        "<i>система оценки лицевой эстетики</i>\n"
        f"{LINE}\n"
        f"Привет, <b>{safe(name)}</b>.\n"
        "Разбираю фото по <b>10 параметрам</b> и выдаю общий балл, тир и "
        "персональный разбор зон роста.\n"
        f"{LINE}\n"
        "<b>КАК ПОЛУЧИТЬ ОТЧЁТ</b>\n"
        "<blockquote>1. Пришли фото анфас\n"
        "2. Ровный свет, без фильтров\n"
        "3. Отчёт придёт через ~10 секунд</blockquote>\n"
        "<b>ЧТО ОЦЕНИВАЮ</b>\n"
        "<blockquote expandable>👁 Канторальный наклон\n"
        "🦅 Посадка глаз\n"
        "🗿 Линия челюсти\n"
        "📐 Гониальный угол\n"
        "💎 Скуловые кости\n"
        "⚡ Проекция подбородка\n"
        "🔻 Гармония носа\n"
        "⚖️ Симметрия лица\n"
        "✨ Качество кожи\n"
        "📊 Пропорции лица</blockquote>\n"
        "📸 <b>Отправь фото — и начнём.</b>\n"
        f"{LINE}\n"
        "🔥 <b>ChadMatch</b> — второй режим: выкладываешь своё фото и "
        "оцениваешь чужие, тебя оценивают в ответ. Живые люди, не алгоритм.\n"
        "<blockquote>Кнопка ниже или команда /peer</blockquote>"
        f"{DISCLAIMER_SHORT}"
    )


HELP = (
    "📖 <b>СПРАВКА</b>\n"
    f"{LINE}\n"
    "<b>Команды</b>\n"
    "<blockquote>/start — главный экран\n"
    "/peer — ChadMatch: оценки от людей\n"
    "/rate — оценивать анкеты\n"
    "/stats — твоя статистика\n"
    "/about — о боте и приватности\n"
    "/help — это сообщение</blockquote>\n"
    "<b>Два режима</b>\n"
    "<blockquote>🧬 <b>Разбор</b> — алгоритм считает геометрию лица по фото. "
    "Снимок остаётся на твоём устройстве.\n"
    "🔥 <b>ChadMatch</b> — тебя оценивают живые люди. Здесь фото видят "
    "другие, поэтому режим работает с 18 лет.</blockquote>\n"
    "<b>Как получить адекватный отчёт</b>\n"
    "<blockquote>• фото анфас, лицо целиком в кадре\n"
    "• дневной или мягкий боковой свет\n"
    "• нейтральное выражение\n"
    "• без фильтров и бьютификации\n"
    "• камера на уровне глаз</blockquote>\n"
    "<b>Почему повторное фото даёт то же самое?</b>\n"
    "Отчёт привязан к файлу: одно фото — один результат.\n"
    f"{DISCLAIMER_SHORT}"
)


ABOUT = (
    "ℹ️ <b>О БОТЕ</b>\n"
    f"{LINE}\n"
    "<b>Формат.</b> Развлекательный проект. Оценки формируются алгоритмом и не "
    "являются экспертным, медицинским или косметологическим заключением.\n"
    "<b>Приватность.</b> В режиме разбора бот <b>не хранит</b> фото — замеры "
    "считаются на твоём устройстве, в базу уходят только числа.\n"
    "<b>ChadMatch.</b> Здесь иначе: снимок анкеты хранится, потому что его "
    "показывают другим. Хранится только текущий, удаляется вместе с анкетой. "
    "Режим работает с 18 лет.\n"
    "<b>Стабильность.</b> Одно фото — один результат. Накрутить повторной "
    "отправкой не выйдет.\n"
    "<b>Здравый смысл.</b> Сон, уход, спорт и уверенность влияют на то, как ты "
    "выглядишь, сильнее любого рейтинга из интернета. Если самооценка всерьёз "
    "завязана на цифру — это повод поговорить с живым человеком, а не с ботом.\n"
    "<i>18+</i>"
)


NEED_PHOTO = (
    "📸 <b>Нужно фото</b>\n"
    "Пришли изображение лица анфас — соберу отчёт.\n"
    "<blockquote>Ровный свет · без фильтров · камера на уровне глаз</blockquote>"
    "🔥 А если хочешь оценок от живых людей — это /peer"
)


DOC_AS_PHOTO = (
    "📎 <b>Это файл, а не фото</b>\n"
    "Отправь картинку как <b>фото</b> (значок галереи), иначе превью и сжатие "
    "работают некорректно."
)


COOLDOWN = "⏳ <b>Не так быстро.</b> Модуль анализа остывает — пара секунд."

NO_STATS = "📊 <b>СТАТИСТИКА</b>\nПока пусто. Пришли первое фото."

# ────────────────────────── подписка на канал ──────────────────────────────


def gate(channel_title: str) -> str:
    return (
        "🔒 <b>ДОСТУП ЗАКРЫТ</b>\n"
        f"{LINE}\n"
        f"Анализ доступен подписчикам <b>{safe(channel_title, 'канала')}</b>.\n"
        "<blockquote>1. Жми «Подписаться»\n"
        "2. Возвращайся и жми «Я подписался»\n"
        "3. Отправляй фото</blockquote>\n"
        "👇 <b>Два клика — и доступ открыт.</b>"
    )


GATE_FAILED = "Подписка не найдена. Подпишись на канал и жми проверку ещё раз."

GATE_PASSED = (
    "✅ <b>ДОСТУП ОТКРЫТ</b>\n"
    f"{LINE}\n"
    "Подписка подтверждена. Отправляй фото анфас — соберу полный отчёт.\n"
    "<blockquote>Ровный свет · без фильтров · камера на уровне глаз</blockquote>"
)


# ───────────────────────── анимация анализа ────────────────────────────────

SCAN_STAGES: tuple[tuple[str, int], ...] = (
    ("Инициализация модуля", 8),
    ("Поиск лицевых маркеров", 27),
    ("Замер угловых характеристик", 51),
    ("Оценка текстур и пропорций", 74),
    ("Сборка отчёта", 93),
    ("Готово", 100),
)


def scan_frame(label: str, percent: int) -> str:
    return (
        "🔬 <b>АНАЛИЗ</b>\n"
        f"<code>{render_bar(percent / 10)}</code>  <b>{percent}%</b>\n"
        f"<i>{label}…</i>"
    )


# ─────────────────────────────── отчёт ─────────────────────────────────────


def report_card(report: Report, name: str, show_header: bool = True) -> str:
    lines = [f"🧬 <b>{BRAND}</b>"]

    if show_header:
        lines.append(f"<i>отчёт #{report.report_id} · {safe(name)}</i>")

    lines += [
        LINE,
        "<b>ОБЩИЙ БАЛЛ</b>",
        f"<code>{report.bar}</code>  <b>{report.overall}</b><i>/10</i>",
        f"{report.tier.emoji} <b>{report.tier.code}</b> — {report.tier.title}",
        f"📈 Выше, чем у <b>{report.percentile}%</b> пользователей",
        LINE,
        "📋 <b>ДЕТАЛИЗАЦИЯ</b>",
    ]

    lines += [
        f"{s.parameter.emoji} {s.parameter.title} — <b>{s.value}</b>"
        for s in report.scores
    ]

    lines += [LINE, f"💬 <i>{report.tier.comment}</i>"]

    return "\n".join(lines)


def report_details(report: Report, salt: str, show_header: bool = True) -> str:
    strong = report.strongest(3)
    weak = report.weakest(3)

    strong_block = "\n".join(
        f"{s.parameter.emoji} {s.parameter.title} — <b>{s.value}</b>" for s in strong
    )
    weak_block = "\n".join(
        f"{s.parameter.emoji} {s.parameter.title} — <b>{s.value}</b>" for s in weak
    )
    tips_block = "\n\n".join(
        f"{s.parameter.emoji} <b>{s.parameter.title}</b>\n{pick_tip(report, s, salt)}"
        for s in weak
    )

    lines = ["🔬 <b>РАЗБОР ПРОФИЛЯ</b>"]
    if show_header:
        lines.append(f"<i>отчёт #{report.report_id}</i>")

    lines += [
        LINE,
        "💪 <b>СИЛЬНЫЕ СТОРОНЫ</b>",
        f"<blockquote>{strong_block}</blockquote>",
        "🎯 <b>ЗОНЫ РОСТА</b>",
        f"<blockquote>{weak_block}</blockquote>",
        LINE,
        "🧭 <b>ЧТО МОЖНО УСИЛИТЬ</b>",
        f"<blockquote expandable>{tips_block}</blockquote>",
        LINE,
        "🚀 <b>ПОТЕНЦИАЛ</b>",
        f"<code>{report.potential_bar}</code>  <b>{report.potential}</b><i>/10</i>",
        f"<i>Запас роста: +{round(report.potential - report.overall, 1)} при "
        "системной работе над сном, уходом, спортом и стилем.</i>",
        DISCLAIMER_FULL,
    ]

    return "\n".join(lines)


# ───────────────────────────── статистика ──────────────────────────────────


def user_stats(name: str, count: int, best: float, average: float, last: float) -> str:
    from rating import tier_for

    tier = tier_for(best)
    return (
        "📊 <b>ТВОЯ СТАТИСТИКА</b>\n"
        f"<i>{safe(name)}</i>\n"
        f"{LINE}\n"
        f"🗂 Отчётов собрано: <b>{count}</b>\n"
        f"🏅 Лучший: <code>{render_bar(best)}</code>  <b>{best}</b>\n"
        f"📉 Средний: <code>{render_bar(average)}</code>  <b>{average}</b>\n"
        f"🕒 Последний: <code>{render_bar(last)}</code>  <b>{last}</b>\n"
        f"{LINE}\n"
        f"Текущий тир: {tier.emoji} <b>{tier.code}</b> — {tier.title}"
    )


# ───────────────────────────── демо-режим ──────────────────────────────────


def demo_on(minutes: float) -> str:
    return (
        "🎬 <b>РЕЖИМ СЪЁМКИ ВКЛЮЧЁН</b>\n"
        f"{LINE}\n"
        "Оценки: <b>2.0–3.5</b>\n"
        "Шапка с номером отчёта и юзернеймом скрыта\n"
        "Результаты не идут в статистику и топ\n"
        f"{LINE}\n"
        f"⏱ Автовыключение через <b>{int(minutes)} мин</b>.\n"
        "Выключить сразу: /demo_off\n"
        "<i>Режим действует только в этом чате и только для тебя.</i>"
    )


def demo_still_on(minutes_left: int) -> str:
    return (
        f"🎬 <b>Режим съёмки активен.</b> Осталось ~{minutes_left} мин.\n"
        "Выключить: /demo_off"
    )


DEMO_OFF = (
    "⏹ <b>Режим съёмки выключен.</b>\nОценки вернулись в обычный диапазон."
)

DEMO_NOT_ACTIVE = "Режим съёмки и так выключен."

DEMO_DENIED = (
    "🚫 <b>Недостаточно прав.</b>\n"
    "Режим съёмки доступен только ID из <code>ADMIN_IDS</code>."
)


def whoami(user_id: int) -> str:
    return (
        "🆔 <b>Твой Telegram ID</b>\n"
        f"<code>{user_id}</code>\n"
        "<i>Впиши его в ADMIN_IDS в .env, чтобы закрыть режим съёмки "
        "от посторонних.</i>"
    )


# ───────────────────────── портрет аудитории ───────────────────────────────

AGE_GROUPS = (
    ("13–15", 13, 15),
    ("16–17", 16, 17),
    ("18–20", 18, 20),
    ("21–23", 21, 23),
    ("24+", 24, 200),
)


def audience_card(total: int, declared: int, with_reports: int, by_age: dict) -> str:
    if not by_age:
        return (
            "👥 <b>АУДИТОРИЯ</b>\n"
            f"{LINE}\n"
            f"Пользователей в базе: <b>{total}</b>\n"
            "Возраст пока никто не указал."
        )

    groups = [
        (label, sum(n for age, n in by_age.items() if low <= age <= high))
        for label, low, high in AGE_GROUPS
    ]
    peak = max(count for _, count in groups) or 1

    lines = [
        "👥 <b>АУДИТОРИЯ</b>",
        LINE,
        f"Всего пользователей: <b>{total}</b>",
        f"Указали возраст: <b>{declared}</b>",
        f"Собрали хотя бы один отчёт: <b>{with_reports}</b>",
        LINE,
        "<b>ПО ВОЗРАСТУ</b>",
    ]

    for label, count in groups:
        share = count / declared * 100 if declared else 0
        bar = "█" * max(0, round(count / peak * 12))
        lines.append(
            f"<code>{label:>5} {bar:<12}</code> <b>{count}</b> <i>({share:.0f}%)</i>"
        )

    ordered = sorted(age for age, n in by_age.items() for _ in range(n))
    median = ordered[len(ordered) // 2]
    average = sum(ordered) / len(ordered)

    lines += [
        LINE,
        f"Медиана: <b>{median}</b> лет · средний: <b>{average:.1f}</b>",
        f"Диапазон: <b>{min(by_age)}–{max(by_age)}</b>",
        "",
        "<i>Возраст указывают сами пользователи при первом входе. "
        "Данные агрегированные, привязки к конкретным людям нет.</i>",
    ]
    return "\n".join(lines)


# ───────────────────────── аудитория (для владельца) ───────────────────────

AGE_GROUPS = (
    ("13–15", 13, 15),
    ("16–17", 16, 17),
    ("18–20", 18, 20),
    ("21–23", 21, 23),
    ("24+", 24, 200),
)


def _bar(value: int, peak: int, width: int = 12) -> str:
    if peak <= 0:
        return ""
    return "█" * max(1, round(value / peak * width)) if value else ""


def audience(data: dict, days: int) -> str:
    ages: dict[int, int] = data["ages"]
    total = data["total"]
    with_age = data["with_age"]

    if not ages:
        return (
            "👥 <b>АУДИТОРИЯ</b>\n"
            f"{LINE}\n"
            f"Пользователей: <b>{total}</b>\n"
            "Возраст пока никто не указал."
        )

    # медиана по указанным возрастам
    flat = sorted(age for age, count in ages.items() for _ in range(count))
    median = flat[len(flat) // 2]
    average = sum(flat) / len(flat)

    groups = []
    peak = 0
    for label, low, high in AGE_GROUPS:
        count = sum(n for age, n in ages.items() if low <= age <= high)
        groups.append((label, count))
        peak = max(peak, count)

    lines = [
        "👥 <b>АУДИТОРИЯ</b>",
        LINE,
        f"Всего в боте: <b>{total}</b>",
        f"Указали возраст: <b>{with_age}</b>",
        f"Собрали хотя бы отчёт: <b>{data['with_report']}</b>",
        f"Активны за {days} дн.: <b>{data['active']}</b>",
        LINE,
        "<b>ВОЗРАСТНЫЕ ГРУППЫ</b>",
    ]

    for label, count in groups:
        share = round(count / with_age * 100) if with_age else 0
        lines.append(
            f"<code>{label:<6}</code> {_bar(count, peak)} <b>{count}</b> ({share}%)"
        )

    minors = sum(n for age, n in ages.items() if age < 18)
    lines += [
        LINE,
        f"Медиана: <b>{median}</b> лет · средний <b>{average:.1f}</b>",
        f"Младше 18: <b>{round(minors / with_age * 100) if with_age else 0}%</b>",
        LINE,
        "<b>ПО ГОДАМ</b>",
    ]

    top = max(ages.values())
    for age in sorted(ages):
        lines.append(f"<code>{age:>3}</code> {_bar(ages[age], top)} {ages[age]}")

    lines += ["", "<i>Возраст указывают сами пользователи при первом входе.</i>"]
    return "\n".join(lines)


# ───────────────────────── реферальная программа ───────────────────────────


def referral(code: str, invited: int) -> str:
    from engagement import XP_REFERRAL, XP_REFERRAL_BONUS

    return (
        "🎁 <b>ПРИГЛАШАЙ ДРУЗЕЙ</b>\n"
        f"{LINE}\n"
        "Твой код:\n"
        f"<code>{safe(code)}</code>\n"
        f"{LINE}\n"
        f"За каждого, кто введёт его — <b>+{XP_REFERRAL} XP</b> тебе\n"
        f"Другу при вводе — <b>+{XP_REFERRAL_BONUS} XP</b>\n"
        f"{LINE}\n"
        f"Приглашено: <b>{invited}</b>\n"
        "<i>Друг вводит команду /ref и код через пробел. "
        "XP тратятся на платные гайды в приложении.</i>"
    )


def ref_applied(bonus: int) -> str:
    return (
        "✅ <b>Код принят</b>\n"
        f"Тебе начислено <b>+{bonus} XP</b>. Пригласивший тоже получил награду.\n"
        "<i>Копи XP за ежедневные привычки и открывай гайды в приложении.</i>"
    )


REF_HOWTO = (
    "🔑 <b>ВВОД КОДА</b>\n"
    f"{LINE}\n"
    "Отправь команду с кодом друга одним сообщением:\n"
    "<code>/ref КОД</code>\n"
    f"{LINE}\n"
    "<i>Например: /ref 7ENVAY\n"
    "Код можно ввести только один раз. Свой собственный не подойдёт.</i>"
)

REF_ALREADY = (
    "✅ Код приглашения ты уже вводил.\n"
    "<i>Свой код и статистику смотри во вкладке «Друзья» в приложении.</i>"
)


def referral_stats(data: dict) -> str:
    top = data["top"]

    lines = [
        "🎁 <b>РЕФЕРАЛЫ</b>",
        LINE,
        f"Ввели чужой код: <b>{data['total']}</b>",
        f"Кто-то пригласил: <b>{data['inviters']}</b> чел.",
        f"Выдано XP по программе: <b>{data['xp']}</b>",
    ]

    if not top:
        lines += [LINE, "<i>Пока никто не вводил код приглашения.</i>"]
        return "\n".join(lines)

    lines += [LINE, "<b>ТОП ПРИГЛАСИВШИХ</b>"]
    medals = ("🥇", "🥈", "🥉")

    for index, (user_id, count) in enumerate(top):
        mark = medals[index] if index < len(medals) else f"<code>{index + 1:>2}.</code>"
        lines.append(f"{mark} <code>{user_id}</code> — <b>{count}</b>")

    lines += [
        LINE,
        "<i>Юзернеймы не хранятся, поэтому показаны Telegram ID. "
        "Найти человека можно, переслав его сообщение боту.</i>",
    ]
    return "\n".join(lines)


PHOTO_TO_APP = (
    "📸 <b>Разбор — в приложении</b>\n"
    f"{LINE}\n"
    "Приложение находит лицо и снимает <b>478 точек</b> прямо на твоём "
    "устройстве: углы, пропорции, симметрия. Отсюда и баллы.\n"
    "Бот такое посчитать не может, поэтому оценка живёт в одном месте — "
    "чтобы одно фото не давало два разных результата.\n"
    f"{LINE}\n"
    "👇 Открывай и загружай фото там."
)

PHOTO_NO_APP = (
    "📸 Разбор фото работает в приложении.\n"
    "<i>Оно пока не настроено — загляни позже.</i>"
)


LABELS_EMPTY = (
    "🏷 <b>РАЗМЕТКА</b>\n"
    "Пока пусто. Открой приложение и пролистай вкладки до раздела «Разметка» — "
    "он виден только тебе.\n"
    "<i>Фотографии никуда не отправляются: замеры считает браузер, "
    "на сервер уходят только числа.</i>"
)


def label_stats(data: dict) -> str:
    total = data["total"]
    lines = [
        "🏷 <b>РАЗМЕТКА</b>",
        LINE,
        f"Собрано примеров: <b>{total}</b>",
        f"Средний балл: <b>{data['average']}</b>",
        LINE,
        "<b>ПО БАЛЛАМ</b>",
    ]

    buckets = data["buckets"]
    peak = max(buckets.values()) if buckets else 1
    for score in range(0, 10):
        count = buckets.get(score, 0)
        bar = "█" * round(count / peak * 14) if count else ""
        lines.append(f"<code>{score}-{score + 1}</code> {bar} {count}")

    # Ниже трёхсот примеров модель на 22 признаках остаётся неустойчивой
    left = max(0, 300 - total)
    lines += [
        LINE,
        f"До надёжной модели: ещё <b>{left}</b>" if left
        else "✅ Примеров достаточно для переобучения",
        "<i>Ровнее всего работает, когда баллы распределены равномерно, "
        "а не собраны в середине.</i>",
    ]
    return "\n".join(lines)


GIFT_USAGE = (
    "🎁 <b>ПОДАРОК ВСЕМ</b>\n"
    "Укажи, сколько попыток добавить: <code>/gift 5</code>\n"
    "<i>От 0 до 50. Ноль отменяет подарок.</i>"
)


def gift_done(amount: int, base: int) -> str:
    if amount == 0:
        return (
            "🎁 <b>Подарок отменён</b>\n"
            f"Лимит вернулся к обычным {base} отчётам в сутки."
        )
    return (
        "🎁 <b>ПОДАРОК ВЫДАН</b>\n"
        f"{LINE}\n"
        f"Всем пользователям сегодня доступно <b>{base + amount}</b> отчётов "
        f"вместо {base}.\n"
        f"{LINE}\n"
        "<i>Действует до полуночи по UTC, дальше лимит вернётся к обычному. "
        "Отменить раньше: /gift 0</i>"
    )


STRICT_USAGE = (
    "🎚 <b>СТРОГОСТЬ ОЦЕНОК</b>\n"
    "<code>/strict -0.5</code> — опустить все оценки на полбалла\n"
    "<code>/strict 0.3</code> — поднять\n"
    "<code>/strict 0</code> — вернуть как размечено\n"
    "<i>Допустимо от -3 до 3.</i>"
)


def strict_now(value: float) -> str:
    if not value:
        return (
            "🎚 <b>Строгость: обычная</b>\n"
            "Оценки идут ровно по твоей разметке.\n"
            f"{LINE}\n{STRICT_USAGE}"
        )
    return (
        f"🎚 <b>Строгость: {value:+.1f}</b>\n"
        f"Все оценки {'ниже' if value < 0 else 'выше'} размеченных на "
        f"{abs(value):.1f}.\n"
        f"{LINE}\n{STRICT_USAGE}"
    )


def strict_set(value: float) -> str:
    if not value:
        return "🎚 <b>Строгость сброшена.</b> Оценки идут ровно по разметке."
    return (
        f"🎚 <b>Строгость: {value:+.1f}</b>\n"
        f"Все новые отчёты станут {'строже' if value < 0 else 'мягче'} на "
        f"{abs(value):.1f} балла.\n"
        "<i>Применяется сразу, в боте и в приложении.</i>"
    )


# ═══════════════════ РЕЖИМ ВЗАИМНЫХ ОЦЕНОК ═════════════════════════════════


def peer_intro(state: dict) -> str:
    from peer import CONSENT_TEXT, PEER_MIN_AGE

    profile = state.get("profile")
    if not profile:
        return (
            "👥 <b>ВЗАИМНЫЕ ОЦЕНКИ</b>\n"
            f"{LINE}\n"
            "Выкладываешь своё фото — оцениваешь чужие, тебя оценивают в ответ.\n"
            f"{LINE}\n"
            f"{safe(CONSENT_TEXT)}\n"
            f"{LINE}\n"
            "<b>Как создать анкету:</b>\n"
            "Отправь фото, а в подписи к нему — имя и возраст через запятую:\n"
            "<code>Макс, 19</code>\n\n"
            f"<i>Отправляя фото, ты подтверждаешь, что тебе есть "
            f"{PEER_MIN_AGE}, что на снимке ты сам и что согласен с правилами.</i>"
        )

    if profile["status"] == "hidden":
        until = (profile.get("hidden_until") or "")[:16].replace("T", " ")
        return (
            "🙈 <b>Анкета скрыта</b>\n"
            f"{LINE}\n"
            f"{safe(profile.get('hidden_note') or 'Решение модератора.')}\n"
            f"Скрыта до <b>{safe(until)}</b>\n"
            f"{LINE}\n"
            "<i>Чтобы вернуться, отправь новое фото с подписью "
            "«имя, возраст».</i>"
        )

    if profile["status"] == "banned":
        return "🚫 <b>Анкета заблокирована.</b>\nВопросы — в поддержку."

    lines = [
        "👥 <b>ТВОЯ АНКЕТА</b>",
        LINE,
        f"Имя: <b>{safe(profile['name'])}</b>",
        f"Возраст: <b>{profile['age']}</b>",
        f"Оценок получено: <b>{profile['votes']}</b>",
    ]
    if profile.get("tier"):
        lines.append(f"Средняя: <b>{profile['average']}</b> · {profile['tier']}")
    else:
        lines.append("<i>Средняя появится после трёх оценок</i>")

    lines += [
        LINE,
        f"Оценок можно поставить сегодня: <b>{state['votes_left']}</b>",
        "",
        "/rate — оценивать анкеты",
        "/peer_delete — удалить анкету",
        "",
        "<i>Новое фото с подписью «имя, возраст» заменит текущее.</i>",
    ]
    return "\n".join(lines)


PEER_BAD_CAPTION = (
    "❌ Не разобрал подпись.\n"
    "Нужно имя и возраст через запятую: <code>Макс, 19</code>"
)

PEER_SAVED = (
    "✅ <b>Анкета сохранена</b>\n"
    "Теперь её увидят другие. Оценивай чужие — /rate"
)

PEER_NO_PROFILE = (
    "Сначала своя анкета: отправь фото с подписью <code>Имя, возраст</code>.\n"
    "<i>Так честнее — оценивают те, кого можно оценить в ответ.</i>"
)

PEER_EMPTY = (
    "На сегодня анкеты закончились.\n"
    "<i>Загляни позже — новые появляются постоянно.</i>"
)

PEER_DELETED = "🗑 Анкета и фото удалены."

PEER_REPORT_SENT = "🚩 Жалоба отправлена модератору. Спасибо."


def peer_card(name: str, age: int) -> str:
    if not name:
        return "Оцени по шкале ниже 👇"
    who = f"<b>{safe(name)}</b>"
    if age:
        who += f", {age}"
    return f"{who}\n\nОцени по шкале ниже 👇"


def peer_voted(tier_title: str, left: int) -> str:
    return f"Оценка «{safe(tier_title)}» учтена. Осталось сегодня: {left}"


def peer_hidden_notice(hours: int, note: str) -> str:
    return (
        "🙈 <b>Твоя анкета скрыта</b>\n"
        f"{LINE}\n"
        f"{safe(note)}\n"
        f"Скрытие снимается через <b>{hours} ч</b>.\n"
        f"{LINE}\n"
        "<i>Чтобы вернуться, потребуется загрузить новое фото.</i>"
    )


PEER_BANNED_NOTICE = (
    "🚫 <b>Твоя анкета удалена модератором</b>\n"
    "Причина — нарушение правил режима оценок."
)


def peer_admin_stats(data: dict) -> str:
    return (
        "👥 <b>РЕЖИМ ОЦЕНОК</b>\n"
        f"{LINE}\n"
        f"Анкет всего: <b>{data['profiles']}</b>\n"
        f"Активных: <b>{data['active']}</b>\n"
        f"Скрыто: <b>{data['hidden']}</b> · заблокировано: <b>{data['banned']}</b>\n"
        f"Оценок выставлено: <b>{data['votes']}</b>\n"
        f"Открытых жалоб: <b>{data['open_reports']}</b>"
    )


GIVE_USAGE = (
    "🎯 <b>ПОПЫТКИ АДРЕСНО</b>\n"
    "<code>/give 630046207 5</code> — по ID\n"
    "<code>/give @username 5</code> — по юзернейму\n"
    "<i>От 1 до 50. Юзернейм работает, если человек уже писал боту.</i>"
)


def give_done(who: str, amount: int) -> str:
    return (
        f"🎯 <b>Выдано {amount} попыток</b>\n"
        f"Кому: {safe(who)}\n"
        "<i>Действуют до полуночи по UTC.</i>"
    )


GIVE_NOT_FOUND = (
    "❌ Не нашёл такого пользователя.\n"
    "<i>По юзернейму получится, только если он уже писал боту. "
    "Надёжнее — по числовому ID.</i>"
)


def peer_demo_ask(nick: str) -> str:
    return (
        f"🎬 <b>Режим съёмки</b>\n"
        f"Ник: <b>{safe(nick)}</b>\n"
        "Выбери оценку, которую он тебе «поставил»:"
    )


def peer_demo_card(nick: str, tier_title: str) -> str:
    return f"<b>{safe(nick)}</b> оценил(а) тебя на <b>{safe(tier_title)}</b>"


SEED_ON = (
    "🌱 <b>Пополнение пула включено</b>\n"
    "Присылай фотографии — каждая уйдёт в очередь на оценку.\n"
    "Можно альбомом.\n"
    "<blockquote>Только те снимки, которые ты вправе показывать, "
    "и только людей 18+.</blockquote>"
    "<i>Выключить: /seed off</i>"
)

SEED_OFF = (
    "🌱 <b>Пополнение выключено.</b>\n"
    "Фотографии снова обрабатываются как обычно."
)


def seed_status(pool: int, folder: dict) -> str:
    files = folder.get("files") or []
    found = folder.get("found")

    lines = [
        "🌱 <b>ПУЛ НАПОЛНЕНИЯ</b>",
        LINE,
        f"Снимков в базе: <b>{pool}</b>",
        f"Снимков в папке: <b>{len(files)}</b>",
        LINE,
    ]

    if found:
        lines.append(f"Папка найдена: <code>{safe(found)}</code>")
    else:
        lines += [
            "⚠️ <b>Папка не видна функции.</b>",
            "Проверенные пути:",
        ]
        lines += [f"<code>{safe(path)}</code>" for path in folder.get("checked", [])]
        lines.append(
            "<i>Это обычное дело на serverless: файлы репозитория попадают "
            "в функцию не всегда. Надёжный путь — /seed on и прислать "
            "фотографии боту, они лягут в базу.</i>"
        )

    lines += [
        LINE,
        "<b>Команды</b>",
        "<blockquote>/seed on — принимать фото в пул\n"
        "/seed off — выключить\n"
        "/seed clear — очистить пул</blockquote>",
    ]
    return "\n".join(lines)


PEER_CLOSED = (
    "🔥 <b>ChadMatch скоро откроется</b>\n"
    "Режим взаимных оценок пока в закрытом тестировании.\n"
    "<i>Следи за каналом — сообщим о запуске.</i>"
)
