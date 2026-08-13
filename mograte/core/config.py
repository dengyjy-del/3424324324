"""Конфигурация режима оценивания.

Все значения читаются из окружения, чтобы модуль можно было положить
в существующего бота без правки кода.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int_list(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.append(int(chunk))
    return out


# --- Telegram -------------------------------------------------------------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# Кому уходят жалобы. Можно указать личные id админов через запятую
# и/или один общий чат модерации.
ADMIN_IDS: list[int] = _int_list(os.getenv("ADMIN_IDS", ""))
MOD_CHAT_ID: int | None = (
    int(os.getenv("MOD_CHAT_ID")) if os.getenv("MOD_CHAT_ID", "").lstrip("-").isdigit() else None
)

# --- Хранилище ------------------------------------------------------------
# Та же база, что у основного бота: на Vercel это Postgres.
DATABASE_URL: str = os.getenv("DATABASE_URL", "") or os.getenv(
    "RATE_DB_PATH", str(BASE_DIR / "mograte.db")
)
DB_PATH: str = str(BASE_DIR / "mograte.db")
MEDIA_DIR: Path = Path(os.getenv("RATE_MEDIA_DIR", str(BASE_DIR / "media" / "photos")))
SEED_DIR: Path = Path(os.getenv("RATE_SEED_DIR", str(BASE_DIR / "seed_photos")))

# --- WebApp ---------------------------------------------------------------
WEBAPP_HOST: str = os.getenv("RATE_WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT: int = int(os.getenv("RATE_WEBAPP_PORT", "8080"))
# Публичный https-адрес мини-аппа. Telegram открывает только https.
WEBAPP_URL: str = os.getenv("RATE_WEBAPP_URL", "")

# --- Правила режима -------------------------------------------------------
MIN_AGE: int = 18
MAX_AGE: int = 99
NAME_MIN_LEN: int = 2
NAME_MAX_LEN: int = 24

# Версия юридических документов. Поднимите её — и у всех снова
# спросят согласие перед следующей оценкой.
CONSENT_VERSION: str = os.getenv("RATE_CONSENT_VERSION", "2026-07-16")

# Ссылки на полные документы (показываются в дисклеймере).
TERMS_URL: str = os.getenv("RATE_TERMS_URL", "")
PRIVACY_URL: str = os.getenv("RATE_PRIVACY_URL", "")
SUPPORT_HANDLE: str = os.getenv("RATE_SUPPORT", "@ascendlab_help")

# --- Лента ----------------------------------------------------------------
# Если живых анкет к показу меньше этого числа — подмешиваем анкеты
# из папки seed_photos.
MIN_LIVE_POOL: int = int(os.getenv("RATE_MIN_LIVE_POOL", "5"))
# Доля сид-анкет в выдаче, когда живых мало (0..1).
SEED_RATIO_WHEN_THIN: float = float(os.getenv("RATE_SEED_RATIO", "0.6"))
# Дневной лимит оценок для обычного пользователя. 0 — без лимита.
DAILY_VOTE_LIMIT: int = int(os.getenv("RATE_DAILY_LIMIT", "0"))

# --- Модерация ------------------------------------------------------------
HIDE_HOURS: int = int(os.getenv("RATE_HIDE_HOURS", "24"))
# Сколько жалоб подряд автоматически прячут анкету до решения модератора.
AUTOHIDE_REPORTS: int = int(os.getenv("RATE_AUTOHIDE_REPORTS", "3"))

# --- Фото -----------------------------------------------------------------
MAX_PHOTO_BYTES: int = int(os.getenv("RATE_MAX_PHOTO_BYTES", str(8 * 1024 * 1024)))
PHOTO_MAX_SIDE: int = int(os.getenv("RATE_PHOTO_MAX_SIDE", "1280"))


def validate() -> list[str]:
    """Возвращает список проблем конфигурации. Пусто — всё в порядке."""
    problems: list[str] = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN не задан")
    if not ADMIN_IDS and MOD_CHAT_ID is None:
        problems.append("Не задан ни ADMIN_IDS, ни MOD_CHAT_ID — жалобы будет некуда слать")
    if not WEBAPP_URL:
        problems.append("RATE_WEBAPP_URL не задан — кнопка мини-аппа не появится (бот будет работать)")
    return problems
