"""
Хранилище с двумя бэкендами.

    sqlite:///looksmax.db        локальная разработка и обычный VPS
    postgresql://user:pass@host  serverless (Vercel + Neon/Supabase)

Выбор делает create_database() по схеме URL. Интерфейс общий, поэтому бот и
мини-апп не знают, где лежат данные.

Почему на serverless нужен Postgres: файловая система там эфемерна и не
разделяется между вызовами функции, так что SQLite потеряет данные при первом
же холодном старте.

Не хранятся ни фотографии, ни юзернеймы, ни почта: только Telegram ID и числа.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ─────────────────────────────── модели ────────────────────────────────────


@dataclass
class UserStats:
    count: int
    best: float
    average: float
    last: float


@dataclass
class Audience:
    total: int            # всего пользователей в базе
    declared: int         # указали возраст
    with_reports: int     # собрали хотя бы один отчёт
    by_age: dict[int, int]


@dataclass
class HistoryPoint:
    label: str      # ключ периода: 2026-08-02 / 2026-W31 / 2026-08
    value: float    # средний балл за период
    count: int      # сколько отчётов попало в период


PERIODS = ("day", "week", "month")

# Записи о виденных альбомах живут недолго — нужны только чтобы не выдать
# три отчёта на пачку из трёх фото.
ALBUM_TTL = 300.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────── интерфейс ─────────────────────────────────


class BaseDatabase(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def ping(self) -> None:
        """Дешёвый запрос: проверить, что база действительно отвечает."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def ensure_user(self, user_id: int) -> None: ...

    @abstractmethod
    async def set_declared_age(self, user_id: int, age: int) -> None: ...

    @abstractmethod
    async def get_declared_age(self, user_id: int) -> int | None: ...

    @abstractmethod
    async def save_rating(self, user_id: int, report_id: str, overall: float) -> bool: ...

    @abstractmethod
    async def get_stats(self, user_id: int) -> UserStats | None: ...

    @abstractmethod
    async def history(
        self, user_id: int, period: str = "day", limit: int = 30
    ) -> list[HistoryPoint]: ...

    # Состояние, которое раньше жило в памяти процесса. На serverless память
    # между вызовами не сохраняется, поэтому оно переехало в базу.

    @abstractmethod
    async def start_demo(self, user_id: int, ttl_seconds: float) -> None: ...

    @abstractmethod
    async def stop_demo(self, user_id: int) -> None: ...

    @abstractmethod
    async def demo_seconds_left(self, user_id: int) -> float: ...

    @abstractmethod
    async def claim_album(self, media_group_id: str) -> bool:
        """True, если этот альбом видим впервые."""

    @abstractmethod
    async def audience(self) -> Audience:
        """Агрегированный портрет аудитории: только суммы, без привязки к людям."""

    @abstractmethod
    async def get_habits(self, user_id: int, since: str) -> dict[str, int]:
        """Отметки привычек по дням, начиная с даты since (YYYY-MM-DD)."""

    @abstractmethod
    async def set_habit_mask(self, user_id: int, day: str, mask: int) -> None: ...

    @abstractmethod
    async def award_xp(self, user_id: int, key: str, amount: int) -> bool:
        """Начисляет XP. False, если событие с таким ключом уже было."""

    @abstractmethod
    async def xp_balance(self, user_id: int) -> tuple[int, int]:
        """(заработано всего, потрачено)."""

    @abstractmethod
    async def purchase(self, user_id: int, guide_id: str, price: int) -> bool: ...

    @abstractmethod
    async def purchased(self, user_id: int) -> set[str]: ...

    @abstractmethod
    async def set_ref_code(self, user_id: int, code: str) -> None: ...

    @abstractmethod
    async def get_ref_code(self, user_id: int) -> str | None: ...

    @abstractmethod
    async def user_by_ref_code(self, code: str) -> int | None: ...

    @abstractmethod
    async def referrer_of(self, user_id: int) -> int | None: ...

    @abstractmethod
    async def bind_referrer(self, user_id: int, referrer_id: int) -> bool: ...

    @abstractmethod
    async def referral_count(self, user_id: int) -> int: ...

    @abstractmethod
    async def audience(self, active_since: datetime) -> dict:
        """Сводка по аудитории: всего, с возрастом, активные, гистограмма лет."""

    @abstractmethod
    async def count_ratings_since(self, user_id: int, since: datetime) -> int:
        """
        Сколько отчётов собрано начиная с момента since.

        Принимает именно datetime, а не строку: asyncpg строго типизирован и
        отвергает строку там, где колонка объявлена как timestamptz.
        """


# ─────────────────────────────── SQLite ────────────────────────────────────

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    ratings_count INTEGER NOT NULL DEFAULT 0,
    best_overall  REAL    NOT NULL DEFAULT 0,
    last_overall  REAL    NOT NULL DEFAULT 0,
    sum_overall   REAL    NOT NULL DEFAULT 0,
    declared_age  INTEGER,
    onboarded_at  TEXT,
    ref_code      TEXT UNIQUE,
    referred_by   INTEGER
);

CREATE TABLE IF NOT EXISTS ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    report_id  TEXT    NOT NULL,
    overall    REAL    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_sessions (
    user_id    INTEGER PRIMARY KEY,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_albums (
    album_key  TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS habits (
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    mask    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- Ключ события уникален, поэтому повторное начисление за то же действие
-- в тот же день просто не проходит. Идемпотентность без блокировок.
CREATE TABLE IF NOT EXISTS xp_events (
    user_id    INTEGER NOT NULL,
    key        TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS purchases (
    user_id    INTEGER NOT NULL,
    guide_id   TEXT    NOT NULL,
    price      INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, guide_id)
);

CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_unique ON ratings(user_id, report_id);
"""

SQLITE_BUCKETS = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}


class SQLiteDatabase(BaseDatabase):
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn = None

    async def ping(self) -> None:
        async with self.conn.execute("SELECT 1"):
            pass

    async def connect(self) -> None:
        import aiosqlite

        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SQLITE_SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(users)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}

        if "display_name" in columns:
            await self.conn.execute("ALTER TABLE users DROP COLUMN display_name")

        for column, ddl in (
            ("declared_age", "ALTER TABLE users ADD COLUMN declared_age INTEGER"),
            ("onboarded_at", "ALTER TABLE users ADD COLUMN onboarded_at TEXT"),
            ("ref_code", "ALTER TABLE users ADD COLUMN ref_code TEXT"),
            ("referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER"),
        ):
            if column not in columns:
                await self.conn.execute(ddl)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            raise RuntimeError("База не подключена: вызови connect().")
        return self._conn

    async def ensure_user(self, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, _now_iso()),
        )
        await self.conn.commit()

    async def set_declared_age(self, user_id: int, age: int) -> None:
        await self.ensure_user(user_id)
        await self.conn.execute(
            """
            UPDATE users
               SET declared_age = ?, onboarded_at = COALESCE(onboarded_at, ?)
             WHERE user_id = ?
            """,
            (age, _now_iso(), user_id),
        )
        await self.conn.commit()

    async def get_declared_age(self, user_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT declared_age FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["declared_age"]) if row and row["declared_age"] else None

    async def save_rating(self, user_id: int, report_id: str, overall: float) -> bool:
        await self.ensure_user(user_id)
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO ratings (user_id, report_id, overall, created_at)
                 VALUES (?, ?, ?, ?)
            """,
            (user_id, report_id, overall, _now_iso()),
        )
        is_new = cursor.rowcount > 0

        if is_new:
            await self.conn.execute(
                """
                UPDATE users
                   SET ratings_count = ratings_count + 1,
                       sum_overall   = sum_overall + ?,
                       last_overall  = ?,
                       best_overall  = MAX(best_overall, ?)
                 WHERE user_id = ?
                """,
                (overall, overall, overall, user_id),
            )

        await self.conn.commit()
        return is_new

    async def get_stats(self, user_id: int) -> UserStats | None:
        async with self.conn.execute(
            """
            SELECT ratings_count, best_overall, last_overall, sum_overall
              FROM users WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None or row["ratings_count"] == 0:
            return None

        count = int(row["ratings_count"])
        return UserStats(
            count=count,
            best=round(float(row["best_overall"]), 1),
            average=round(float(row["sum_overall"]) / count, 1),
            last=round(float(row["last_overall"]), 1),
        )

    async def history(
        self, user_id: int, period: str = "day", limit: int = 30
    ) -> list[HistoryPoint]:
        fmt = SQLITE_BUCKETS.get(period)
        if fmt is None:
            raise ValueError(f"неизвестный период: {period}")

        async with self.conn.execute(
            f"""
              SELECT strftime('{fmt}', created_at) AS bucket,
                     AVG(overall) AS value, COUNT(*) AS cnt
                FROM ratings WHERE user_id = ?
            GROUP BY bucket ORDER BY bucket DESC LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

        return [
            HistoryPoint(row["bucket"], round(float(row["value"]), 1), int(row["cnt"]))
            for row in reversed(rows)
        ]

    async def start_demo(self, user_id: int, ttl_seconds: float) -> None:
        await self.conn.execute(
            """
            INSERT INTO demo_sessions (user_id, expires_at) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET expires_at = excluded.expires_at
            """,
            (user_id, time.time() + ttl_seconds),
        )
        await self.conn.commit()

    async def stop_demo(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM demo_sessions WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def demo_seconds_left(self, user_id: int) -> float:
        async with self.conn.execute(
            "SELECT expires_at FROM demo_sessions WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return 0.0
        return max(0.0, float(row["expires_at"]) - time.time())

    async def claim_album(self, media_group_id: str) -> bool:
        now = time.time()
        await self.conn.execute(
            "DELETE FROM seen_albums WHERE created_at < ?", (now - ALBUM_TTL,)
        )
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO seen_albums (album_key, created_at) VALUES (?, ?)",
            (media_group_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def audience(self) -> Audience:
        async def scalar(sql: str) -> int:
            async with self.conn.execute(sql) as cursor:
                row = await cursor.fetchone()
            return int(row["n"]) if row else 0

        total = await scalar("SELECT COUNT(*) AS n FROM users")
        with_reports = await scalar(
            "SELECT COUNT(*) AS n FROM users WHERE ratings_count > 0"
        )

        async with self.conn.execute(
            """
              SELECT declared_age AS age, COUNT(*) AS n
                FROM users WHERE declared_age IS NOT NULL
            GROUP BY declared_age ORDER BY declared_age
            """
        ) as cursor:
            rows = await cursor.fetchall()

        by_age = {int(row["age"]): int(row["n"]) for row in rows}
        return Audience(total, sum(by_age.values()), with_reports, by_age)

    async def get_habits(self, user_id: int, since: str) -> dict[str, int]:
        async with self.conn.execute(
            "SELECT day, mask FROM habits WHERE user_id = ? AND day >= ?",
            (user_id, since),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["day"]: int(row["mask"]) for row in rows}

    async def set_habit_mask(self, user_id: int, day: str, mask: int) -> None:
        await self.ensure_user(user_id)
        await self.conn.execute(
            """
            INSERT INTO habits (user_id, day, mask) VALUES (?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET mask = excluded.mask
            """,
            (user_id, day, mask),
        )
        await self.conn.commit()

    async def award_xp(self, user_id: int, key: str, amount: int) -> bool:
        await self.ensure_user(user_id)
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO xp_events (user_id, key, amount, created_at)
                 VALUES (?, ?, ?, ?)
            """,
            (user_id, key, amount, _now_iso()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def xp_balance(self, user_id: int) -> tuple[int, int]:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS n FROM xp_events WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            earned = int((await cursor.fetchone())["n"])
        async with self.conn.execute(
            "SELECT COALESCE(SUM(price), 0) AS n FROM purchases WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            spent = int((await cursor.fetchone())["n"])
        return earned, spent

    async def purchase(self, user_id: int, guide_id: str, price: int) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO purchases (user_id, guide_id, price, created_at)
                 VALUES (?, ?, ?, ?)
            """,
            (user_id, guide_id, price, _now_iso()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def purchased(self, user_id: int) -> set[str]:
        async with self.conn.execute(
            "SELECT guide_id FROM purchases WHERE user_id = ?", (user_id,)
        ) as cursor:
            return {row["guide_id"] for row in await cursor.fetchall()}

    async def set_ref_code(self, user_id: int, code: str) -> None:
        await self.ensure_user(user_id)
        await self.conn.execute(
            "UPDATE users SET ref_code = ? WHERE user_id = ? AND ref_code IS NULL",
            (code, user_id),
        )
        await self.conn.commit()

    async def get_ref_code(self, user_id: int) -> str | None:
        async with self.conn.execute(
            "SELECT ref_code FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["ref_code"] if row and row["ref_code"] else None

    async def user_by_ref_code(self, code: str) -> int | None:
        async with self.conn.execute(
            "SELECT user_id FROM users WHERE ref_code = ?", (code,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["user_id"]) if row else None

    async def referrer_of(self, user_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT referred_by FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["referred_by"]) if row and row["referred_by"] else None

    async def bind_referrer(self, user_id: int, referrer_id: int) -> bool:
        await self.ensure_user(user_id)
        cursor = await self.conn.execute(
            "UPDATE users SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL",
            (referrer_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def referral_count(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?", (user_id,)
        ) as cursor:
            return int((await cursor.fetchone())["n"])

    async def audience(self, active_since: datetime) -> dict:
        moment = active_since.isoformat(timespec="seconds")
        async with self.conn.execute(
            """
            SELECT COUNT(*)                                          AS total,
                   SUM(declared_age IS NOT NULL)                     AS with_age,
                   SUM(ratings_count > 0)                            AS with_report
              FROM users
            """
        ) as cursor:
            row = await cursor.fetchone()

        async with self.conn.execute(
            """
              SELECT declared_age AS age, COUNT(*) AS n
                FROM users WHERE declared_age IS NOT NULL
            GROUP BY declared_age ORDER BY declared_age
            """
        ) as cursor:
            ages = {int(r["age"]): int(r["n"]) for r in await cursor.fetchall()}

        async with self.conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM ratings WHERE created_at >= ?",
            (moment,),
        ) as cursor:
            active = int((await cursor.fetchone())["n"])

        return {
            "total": int(row["total"] or 0),
            "with_age": int(row["with_age"] or 0),
            "with_report": int(row["with_report"] or 0),
            "active": active,
            "ages": ages,
        }

    async def count_ratings_since(self, user_id: int, since: datetime) -> int:
        # В SQLite created_at хранится строкой ISO, поэтому приводим сами.
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM ratings WHERE user_id = ? AND created_at >= ?",
            (user_id, since.isoformat(timespec="seconds")),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0


# ────────────────────────────── PostgreSQL ─────────────────────────────────

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       BIGINT PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ratings_count INTEGER NOT NULL DEFAULT 0,
    best_overall  DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_overall  DOUBLE PRECISION NOT NULL DEFAULT 0,
    sum_overall   DOUBLE PRECISION NOT NULL DEFAULT 0,
    declared_age  INTEGER,
    onboarded_at  TIMESTAMPTZ,
    ref_code      TEXT UNIQUE,
    referred_by   BIGINT
);

CREATE TABLE IF NOT EXISTS ratings (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    report_id  TEXT   NOT NULL,
    overall    DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS demo_sessions (
    user_id    BIGINT PRIMARY KEY,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_albums (
    album_key  TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS habits (
    user_id BIGINT  NOT NULL,
    day     TEXT    NOT NULL,
    mask    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS xp_events (
    user_id    BIGINT  NOT NULL,
    key        TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS purchases (
    user_id    BIGINT  NOT NULL,
    guide_id   TEXT    NOT NULL,
    price      INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, guide_id)
);

CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_unique ON ratings(user_id, report_id);
"""

# CREATE TABLE IF NOT EXISTS не добавляет колонки к таблице, которая уже
# существует. Поэтому каждое новое поле нужно отдельно дописывать сюда —
# иначе на боевой базе оно просто не появится, а код упадёт на первом же
# обращении к нему. Все команды идемпотентны, повторный запуск безопасен.
PG_MIGRATIONS = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS declared_age INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarded_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code     TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by  BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code);
CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by);
"""

PG_BUCKETS = {"day": "YYYY-MM-DD", "week": 'IYYY-"W"IW', "month": "YYYY-MM"}


# Параметры, которые понимает psql, но не понимает asyncpg. Он не отбрасывает
# их, а отправляет в Postgres как настройки сервера — и соединение падает с
# «unrecognized configuration parameter». Neon и Supabase кладут
# channel_binding в строку, которую дают кнопкой «Copy snippet».
UNSUPPORTED_DSN_PARAMS = frozenset({"channel_binding", "gssencmode", "sslnegotiation"})


def sanitize_dsn(dsn: str) -> str:
    """Приводит строку подключения к тому, что переваривает asyncpg."""
    dsn = dsn.strip().replace("postgres://", "postgresql://", 1)
    parts = urlparse(dsn)

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in UNSUPPORTED_DSN_PARAMS
    ]

    return urlunparse(parts._replace(query=urlencode(kept)))


class PostgresDatabase(BaseDatabase):
    def __init__(self, dsn: str) -> None:
        self._dsn = sanitize_dsn(dsn)
        self._pool = None

    async def ping(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    async def connect(self) -> None:
        import asyncpg

        # statement_cache_size=0 обязателен при работе через pgbouncer в
        # transaction mode (пулер Neon и Supabase): иначе подготовленные
        # выражения разъезжаются между соединениями.
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=0,
            max_size=4,
            statement_cache_size=0,
            command_timeout=15,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(PG_SCHEMA)
            await conn.execute(PG_MIGRATIONS)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self):
        if self._pool is None:
            raise RuntimeError("База не подключена: вызови connect().")
        return self._pool

    async def ensure_user(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                user_id,
            )

    async def set_declared_age(self, user_id: int, age: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, declared_age, onboarded_at)
                     VALUES ($1, $2, NOW())
                ON CONFLICT (user_id) DO UPDATE
                   SET declared_age = EXCLUDED.declared_age,
                       onboarded_at = COALESCE(users.onboarded_at, NOW())
                """,
                user_id,
                age,
            )

    async def get_declared_age(self, user_id: int) -> int | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT declared_age FROM users WHERE user_id = $1", user_id
            )
        return int(value) if value else None

    async def save_rating(self, user_id: int, report_id: str, overall: float) -> bool:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO ratings (user_id, report_id, overall)
                         VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, report_id) DO NOTHING
                      RETURNING id
                    """,
                    user_id,
                    report_id,
                    overall,
                )
                if inserted is None:
                    return False

                await conn.execute(
                    """
                    UPDATE users
                       SET ratings_count = ratings_count + 1,
                           sum_overall   = sum_overall + $1,
                           last_overall  = $1,
                           best_overall  = GREATEST(best_overall, $1)
                     WHERE user_id = $2
                    """,
                    overall,
                    user_id,
                )
        return True

    async def get_stats(self, user_id: int) -> UserStats | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT ratings_count, best_overall, last_overall, sum_overall
                  FROM users WHERE user_id = $1
                """,
                user_id,
            )

        if row is None or row["ratings_count"] == 0:
            return None

        count = int(row["ratings_count"])
        return UserStats(
            count=count,
            best=round(float(row["best_overall"]), 1),
            average=round(float(row["sum_overall"]) / count, 1),
            last=round(float(row["last_overall"]), 1),
        )

    async def history(
        self, user_id: int, period: str = "day", limit: int = 30
    ) -> list[HistoryPoint]:
        fmt = PG_BUCKETS.get(period)
        if fmt is None:
            raise ValueError(f"неизвестный период: {period}")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                  SELECT to_char(created_at, '{fmt}') AS bucket,
                         AVG(overall) AS value, COUNT(*) AS cnt
                    FROM ratings WHERE user_id = $1
                GROUP BY bucket ORDER BY bucket DESC LIMIT $2
                """,
                user_id,
                limit,
            )

        return [
            HistoryPoint(row["bucket"], round(float(row["value"]), 1), int(row["cnt"]))
            for row in reversed(rows)
        ]

    async def start_demo(self, user_id: int, ttl_seconds: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO demo_sessions (user_id, expires_at) VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET expires_at = EXCLUDED.expires_at
                """,
                user_id,
                time.time() + ttl_seconds,
            )

    async def stop_demo(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM demo_sessions WHERE user_id = $1", user_id)

    async def demo_seconds_left(self, user_id: int) -> float:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT expires_at FROM demo_sessions WHERE user_id = $1", user_id
            )
        return 0.0 if value is None else max(0.0, float(value) - time.time())

    async def claim_album(self, media_group_id: str) -> bool:
        now = time.time()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM seen_albums WHERE created_at < $1", now - ALBUM_TTL
            )
            claimed = await conn.fetchval(
                """
                INSERT INTO seen_albums (album_key, created_at) VALUES ($1, $2)
                ON CONFLICT DO NOTHING RETURNING album_key
                """,
                media_group_id,
                now,
            )
        return claimed is not None

    async def audience(self) -> Audience:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM users")
            with_reports = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE ratings_count > 0"
            )
            rows = await conn.fetch(
                """
                  SELECT declared_age AS age, COUNT(*) AS n
                    FROM users WHERE declared_age IS NOT NULL
                GROUP BY declared_age ORDER BY declared_age
                """
            )

        by_age = {int(row["age"]): int(row["n"]) for row in rows}
        return Audience(
            int(total or 0), sum(by_age.values()), int(with_reports or 0), by_age
        )

    async def get_habits(self, user_id: int, since: str) -> dict[str, int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT day, mask FROM habits WHERE user_id = $1 AND day >= $2",
                user_id,
                since,
            )
        return {row["day"]: int(row["mask"]) for row in rows}

    async def set_habit_mask(self, user_id: int, day: str, mask: int) -> None:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO habits (user_id, day, mask) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, day) DO UPDATE SET mask = EXCLUDED.mask
                """,
                user_id,
                day,
                mask,
            )

    async def award_xp(self, user_id: int, key: str, amount: int) -> bool:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO xp_events (user_id, key, amount) VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING RETURNING key
                """,
                user_id, key, amount,
            )
        return row is not None

    async def xp_balance(self, user_id: int) -> tuple[int, int]:
        async with self.pool.acquire() as conn:
            earned = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM xp_events WHERE user_id = $1",
                user_id,
            )
            spent = await conn.fetchval(
                "SELECT COALESCE(SUM(price), 0) FROM purchases WHERE user_id = $1",
                user_id,
            )
        return int(earned or 0), int(spent or 0)

    async def purchase(self, user_id: int, guide_id: str, price: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO purchases (user_id, guide_id, price) VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING RETURNING guide_id
                """,
                user_id, guide_id, price,
            )
        return row is not None

    async def purchased(self, user_id: int) -> set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT guide_id FROM purchases WHERE user_id = $1", user_id
            )
        return {row["guide_id"] for row in rows}

    async def set_ref_code(self, user_id: int, code: str) -> None:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET ref_code = $1 WHERE user_id = $2 AND ref_code IS NULL",
                code, user_id,
            )

    async def get_ref_code(self, user_id: int) -> str | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT ref_code FROM users WHERE user_id = $1", user_id
            )

    async def user_by_ref_code(self, code: str) -> int | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT user_id FROM users WHERE ref_code = $1", code
            )
        return int(value) if value else None

    async def referrer_of(self, user_id: int) -> int | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT referred_by FROM users WHERE user_id = $1", user_id
            )
        return int(value) if value else None

    async def bind_referrer(self, user_id: int, referrer_id: int) -> bool:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE users SET referred_by = $1
                 WHERE user_id = $2 AND referred_by IS NULL
                """,
                referrer_id, user_id,
            )
        return result.endswith("1")

    async def referral_count(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referred_by = $1", user_id
            )
        return int(value or 0)

    async def audience(self, active_since: datetime) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)                                        AS total,
                       COUNT(declared_age)                             AS with_age,
                       COUNT(*) FILTER (WHERE ratings_count > 0)       AS with_report
                  FROM users
                """
            )
            age_rows = await conn.fetch(
                """
                  SELECT declared_age AS age, COUNT(*) AS n
                    FROM users WHERE declared_age IS NOT NULL
                GROUP BY declared_age ORDER BY declared_age
                """
            )
            active = await conn.fetchval(
                "SELECT COUNT(DISTINCT user_id) FROM ratings WHERE created_at >= $1",
                active_since,
            )

        return {
            "total": int(row["total"] or 0),
            "with_age": int(row["with_age"] or 0),
            "with_report": int(row["with_report"] or 0),
            "active": int(active or 0),
            "ages": {int(r["age"]): int(r["n"]) for r in age_rows},
        }

    async def count_ratings_since(self, user_id: int, since: datetime) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM ratings WHERE user_id = $1 AND created_at >= $2",
                user_id,
                since,
            )
        return int(value or 0)


# ─────────────────────────────── фабрика ───────────────────────────────────


def create_database(url: str) -> BaseDatabase:
    """
    postgresql://... или postgres://...  → PostgresDatabase
    всё остальное                        → SQLiteDatabase
    """
    scheme = urlparse(url).scheme

    if scheme in ("postgres", "postgresql"):
        return PostgresDatabase(url)

    if scheme == "sqlite":
        path = url.replace("sqlite:///", "", 1).replace("sqlite://", "", 1)
        return SQLiteDatabase(path or "looksmax.db")

    return SQLiteDatabase(url)


# Совместимость со старым импортом `from database import Database`
Database = SQLiteDatabase
