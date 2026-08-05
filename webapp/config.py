"""Конфигурация из переменных окружения / .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    brand_name: str
    database_url: str
    score_salt: str
    cooldown_seconds: float
    scan_delay: float

    # обязательная подписка
    channel_id: str          # "@username" или "-100..."; пусто = гейт выключен
    channel_url: str         # ссылка для кнопки «Подписаться»
    channel_title: str       # как канал называется в тексте

    # скрытый режим
    demo_code: str
    demo_ttl_minutes: float
    admin_ids: tuple[int, ...]

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

    @property
    def gate_enabled(self) -> bool:
        return bool(self.channel_id)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

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
    """На Vercel домен известен только в рантайме — через VERCEL_URL."""
    explicit = (os.getenv("WEBAPP_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit

    vercel = (os.getenv("VERCEL_URL") or "").strip()
    return f"https://{vercel}" if vercel else ""


def load_config() -> Config:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            _missing_var("BOT_TOKEN", "Токен выдаёт @BotFather в Telegram.")
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
        brand_name=(os.getenv("BRAND_NAME") or "LOOKSCORE").strip(),
        database_url=_database_url(),
        # Соль влияет на все оценки. Поменяешь — все отчёты пересчитаются.
        score_salt=os.getenv("SCORE_SALT", "looksmax-v1"),
        cooldown_seconds=_get_float("COOLDOWN_SECONDS", 3.0),
        scan_delay=_get_float("SCAN_DELAY", 0.75),
        channel_id=channel_id,
        channel_url=channel_url,
        channel_title=(os.getenv("CHANNEL_TITLE") or "канал").strip(),
        demo_code=demo_code,
        demo_ttl_minutes=_get_float("DEMO_TTL_MINUTES", 30.0),
        admin_ids=_get_ids("ADMIN_IDS"),
        webhook_secret=(os.getenv("WEBHOOK_SECRET") or "").strip(),
        setup_key=(os.getenv("SETUP_KEY") or "").strip(),
        webapp_url=_webapp_url(),
        webapp_host=(os.getenv("WEBAPP_HOST") or "0.0.0.0").strip(),
        webapp_port=int(_get_float("WEBAPP_PORT", 8080)),
        min_age=int(_get_float("MIN_AGE", 16)),
        daily_scan_limit=int(_get_float("DAILY_SCAN_LIMIT", 3)),
    )
