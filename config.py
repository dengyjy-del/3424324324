"""Конфигурация из переменных окружения / .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Токены всех ботов: первый — основной, остальные — зеркала.
    # bot_token оставлен отдельным полем, чтобы не переписывать код,
    # который знает только про одного бота.
    bot_token: str
    bot_tokens: tuple[str, ...]
    rejected_token_vars: tuple[str, ...]
    brand_name: str
    database_url: str
    score_salt: str
    cooldown_seconds: float
    scan_delay: float

    # обязательная подписка
    channel_id: str          # "@username" или "-100..."; пусто = гейт выключен
    channel_url: str         # ссылка для кнопки «Подписаться»
    channel_title: str       # как канал называется в тексте
    chat_id: str             # чат/группа; пусто = не проверяется
    chat_url: str            # ссылка-приглашение в чат

    # скрытый режим
    demo_code: str
    demo_ttl_minutes: float
    admin_ids: tuple[int, ...]
    peer_ids: tuple[int, ...]
    peer_open: bool

    # мини-апп
    webhook_secret: str      # заголовок, которым Telegram подписывает вебхук
    setup_key: str           # ключ для /api/setup
    webapp_url: str          # публичный https-адрес; пусто = приложение выключено
    webapp_host: str
    webapp_port: int
    min_age: int
    daily_scan_limit: int

    @property
    def webapp_enabled(self) -> bool:
        return bool(self.webapp_url)

    @property
    def is_serverless(self) -> bool:
        """Vercel выставляет VERCEL=1 во всех окружениях сборки и рантайма."""
        return bool(os.getenv("VERCEL"))

    @property
    def uses_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))

    # ─────────────────────────── зеркала ────────────────────────────────

    @property
    def mirror_tokens(self) -> tuple[str, ...]:
        """Все токены, кроме основного."""
        return self.bot_tokens[1:]

    @property
    def bot_ids(self) -> tuple[str, ...]:
        """Публичные id ботов — числа до двоеточия в токене."""
        return tuple(bot_id_of(token) for token in self.bot_tokens)

    @property
    def primary_id(self) -> str:
        return bot_id_of(self.bot_token)

    def token_for(self, bot_id: str | int) -> str:
        """Токен по id бота. Пустая строка — такого бота в настройках нет."""
        wanted = str(bot_id).strip()
        for token in self.bot_tokens:
            if bot_id_of(token) == wanted:
                return token
        return ""

    @property
    def gate_sources(self) -> tuple[str, ...]:
        return tuple(x for x in (self.channel_id, self.chat_id) if x)

    @property
    def gate_enabled(self) -> bool:
        return bool(self.gate_sources)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def peer_allowed(self, user_id: int) -> bool:
        """
        Кому виден ChadMatch. Отдельно от админских прав: тестировщику нужен
        доступ к разделу, а не к модерации, режиму съёмки и разметке.
        """
        return self.peer_open or self.is_admin(user_id) or user_id in self.peer_ids

    def demo_allowed_for(self, user_id: int) -> bool:
        """
        Если ADMIN_IDS заполнен — демо доступен только этим ID.
        Если пуст — работает по одному лишь коду (удобно на старте,
        но код станет рабочим для любого, кто его узнает).
        """
        return not self.admin_ids or user_id in self.admin_ids


_CREDENTIALS = re.compile(r"://[^:/@\s]+:[^@/\s]+@")


def redact_secrets(text: str) -> str:
    """Прячет логин и пароль из строк подключения, если они попали в текст."""
    return _CREDENTIALS.sub("://***:***@", text)


# ───────────────────────── токены ботов и зеркал ───────────────────────────

# Формат токена BotFather: <числовой id>:<секрет>.
_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

# До скольких пронумерованных переменных заглядываем: BOT_TOKEN_2 … BOT_TOKEN_50.
MAX_NUMBERED_TOKENS = 50


def bot_id_of(token: str) -> str:
    """
    Числовая часть токена — публичный id бота.

    Секретом не является: именно он подставляется в адрес вебхука, чтобы
    понять, какому из ботов пришёл апдейт.
    """
    return token.split(":", 1)[0].strip()


def mask_token(token: str) -> str:
    """Как показывать токен в диагностике: id открыт, секрет скрыт."""
    return f"{bot_id_of(token)}:***" if token else ""


def _split_tokens(raw: str) -> list[str]:
    """Одна переменная может содержать несколько токенов через запятую."""
    return [chunk.strip() for chunk in re.split(r"[\s,;]+", raw or "") if chunk.strip()]


def _token_variables() -> list[str]:
    """
    Имена переменных, в которых ищем токены, в порядке приоритета.

    Поддержаны оба стиля, потому что удобство зависит от количества зеркал:
    список в одной переменной проще вставить, отдельные переменные проще
    отключать по одной.
    """
    names = ["BOT_TOKEN", "BOT_TOKENS", "MIRROR_TOKENS", "MIRROR_BOT_TOKENS"]
    names += [f"BOT_TOKEN_{i}" for i in range(2, MAX_NUMBERED_TOKENS + 1)]
    names += [f"MIRROR_TOKEN_{i}" for i in range(1, MAX_NUMBERED_TOKENS + 1)]
    return names


def collect_tokens() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Собирает токены из окружения.

    Возвращает (токены, имена переменных с мусором). Первым идёт основной
    бот — от него зависит проверка подписки и адрес мини-аппа. Дубли
    отбрасываются по id: один и тот же бот, вписанный дважды, получил бы два
    вебхука, а победил бы последний.
    """
    tokens: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for name in _token_variables():
        for chunk in _split_tokens(os.getenv(name) or ""):
            if not _TOKEN_RE.match(chunk):
                rejected.append(name)
                continue
            key = bot_id_of(chunk)
            if key in seen:
                continue
            seen.add(key)
            tokens.append(chunk)

    return tuple(tokens), tuple(dict.fromkeys(rejected))


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_ids(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, "") or ""
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.append(int(chunk))
    return tuple(ids)


def _missing_var(name: str, extra: str = "") -> str:
    """Подсказка зависит от того, где запущен код: в Vercel .env не существует."""
    if os.getenv("VERCEL"):
        text = (
            f"Не задана переменная {name}.\n"
            "Добавь её в Vercel: проект → Settings → Environment Variables → "
            "Add New. Отметь Production, Preview и Development, сохрани и "
            "передеплой проект — переменные подхватываются только при сборке."
        )
    else:
        text = (
            f"Не задана переменная {name}.\n"
            "Добавь её в файл .env рядом с bot.py."
        )
    return f"{text}\n{extra}" if extra else text


def _database_url() -> str:
    """
    Vercel и хостинги Postgres кладут строку подключения в разные переменные.
    Берём первую попавшуюся, иначе — локальный SQLite.
    """
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "DB_URL"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return os.getenv("DB_PATH", "looksmax.db")


def _webapp_url() -> str:
    """
    Публичный адрес приложения.

    Порядок важен. VERCEL_URL — это адрес конкретного деплоя вида
    project-a1b2c3-team.vercel.app: он меняется при каждой сборке, а Vercel
    закрывает такие адреса защитой доступа, из-за чего Telegram не может
    достучаться до вебхука. Постоянный домен лежит в
    VERCEL_PROJECT_PRODUCTION_URL, его и берём.
    """
    for name in ("WEBAPP_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        value = (os.getenv(name) or "").strip().rstrip("/")
        if not value:
            continue
        return value if value.startswith("http") else f"https://{value}"

    return ""


def load_config() -> Config:
    tokens, rejected = collect_tokens()
    if not tokens:
        extra = "Токен выдаёт @BotFather в Telegram."
        if rejected:
            extra += (
                " Значения в "
                + ", ".join(rejected)
                + " на токен не похожи: нужен формат 123456789:AA... целиком, "
                "без кавычек, пробелов и лишних символов."
            )
        raise RuntimeError(_missing_var("BOT_TOKEN", extra))

    token = tokens[0]
    if rejected:
        # Не роняем запуск из-за одного кривого зеркала: остальные должны
        # работать. Но молчать нельзя — иначе зеркало «просто не отвечает».
        import logging

        logging.getLogger("looksmax.config").warning(
            "Пропущены переменные с некорректными токенами: %s. "
            "Проверь формат: 123456789:AA...",
            ", ".join(rejected),
        )

    channel_id = (os.getenv("CHANNEL_ID") or "").strip()
    channel_url = (os.getenv("CHANNEL_URL") or "").strip()

    if channel_id and not channel_url:
        if channel_id.startswith("@"):
            channel_url = f"https://t.me/{channel_id[1:]}"
        else:
            raise RuntimeError(
                _missing_var(
                    "CHANNEL_URL",
                    "Она обязательна, потому что CHANNEL_ID задан числом. "
                    "Укажи ссылку-приглашение канала (https://t.me/...) — либо "
                    "впиши в CHANNEL_ID @username вместо числа.",
                )
            )

    demo_code = (os.getenv("DEMO_CODE") or "").strip()

    return Config(
        bot_token=token,
        bot_tokens=tokens,
        rejected_token_vars=rejected,
        brand_name=(os.getenv("BRAND_NAME") or "LOOKSCORE").strip(),
        database_url=_database_url(),
        # Соль влияет на все оценки. Поменяешь — все отчёты пересчитаются.
        score_salt=os.getenv("SCORE_SALT", "looksmax-v1"),
        cooldown_seconds=_get_float("COOLDOWN_SECONDS", 3.0),
        scan_delay=_get_float("SCAN_DELAY", 0.75),
        channel_id=channel_id,
        channel_url=channel_url,
        channel_title=(os.getenv("CHANNEL_TITLE") or "канал").strip(),
        chat_id=(os.getenv("CHAT_ID") or "").strip(),
        chat_url=(os.getenv("CHAT_URL") or "").strip(),
        demo_code=demo_code,
        demo_ttl_minutes=_get_float("DEMO_TTL_MINUTES", 30.0),
        admin_ids=_get_ids("ADMIN_IDS"),
        peer_ids=_get_ids("PEER_IDS"),
        peer_open=(os.getenv("PEER_OPEN") or "").strip().lower() in ("1", "true", "yes"),
        webhook_secret=(os.getenv("WEBHOOK_SECRET") or "").strip(),
        setup_key=(os.getenv("SETUP_KEY") or "").strip(),
        webapp_url=_webapp_url(),
        webapp_host=(os.getenv("WEBAPP_HOST") or "0.0.0.0").strip(),
        webapp_port=int(_get_float("WEBAPP_PORT", 8080)),
        min_age=int(_get_float("MIN_AGE", 16)),
        daily_scan_limit=int(_get_float("DAILY_SCAN_LIMIT", 5)),
    )
