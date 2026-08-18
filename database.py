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
    async def add_label(self, photo_id: str, score: float, metrics: str) -> bool:
        """Сохраняет размеченный пример. False, если такой уже был."""

    @abstractmethod
    async def delete_labels_by_score(self, score: float) -> int:
        """Удаляет размеченные примеры с точно такой оценкой."""

    @abstractmethod
    async def refresh_label(self, photo_id: str, metrics: str) -> float | None:
        """Обновляет замеры у размеченного фото. Возвращает его оценку."""

    @abstractmethod
    async def label_stats(self) -> dict: ...

    @abstractmethod
    async def remember_username(self, user_id: int, username: str) -> None:
        """Запоминает @username, чтобы владелец мог адресовать команды по нему."""

    @abstractmethod
    async def user_by_username(self, username: str) -> int | None: ...

    @abstractmethod
    async def set_theme(self, user_id: int, theme: str) -> None: ...

    @abstractmethod
    async def get_theme(self, user_id: int) -> str | None: ...

    @abstractmethod
    async def set_setting(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def get_setting(self, key: str) -> str | None: ...

    @abstractmethod

    # ───────────────────── режим взаимных оценок ──────────────────────

    @abstractmethod
    async def peer_save_profile(
        self, user_id: int, name: str, age: int, photo: bytes | None,
        photo_key: str | None, terms_version: str,
    ) -> None:
        """Создаёт или обновляет анкету. Новое фото затирает предыдущее."""

    @abstractmethod
    async def peer_profile(self, user_id: int) -> dict | None: ...

    @abstractmethod
    async def peer_photo(self, user_id: int) -> bytes | None: ...

    @abstractmethod
    async def peer_delete(self, user_id: int) -> None: ...

    @abstractmethod
    async def peer_set_status(
        self, user_id: int, status: str, hidden_until: datetime | None,
        note: str | None,
    ) -> None: ...

    @abstractmethod
    async def peer_next(
        self, viewer_id: int, limit: int = 10, skip_since: datetime | None = None
    ) -> list[dict]:
        """Анкеты, которые зритель ещё не оценивал и недавно не пропускал."""

    @abstractmethod
    async def peer_vote(
        self, voter_id: int, target: str, tier: str, score: float
    ) -> bool:
        """False, если этот зритель уже оценивал эту цель."""

    @abstractmethod
    async def peer_seen(self, viewer_id: int) -> set[str]: ...

    @abstractmethod
    async def peer_skip(self, viewer_id: int, target: str) -> None:
        """Запоминает пропуск, чтобы карточка не возвращалась сразу."""

    @abstractmethod
    async def peer_skipped(self, viewer_id: int, since: datetime) -> set[str]: ...

    @abstractmethod
    async def peer_votes_since(self, voter_id: int, since: datetime) -> int: ...

    @abstractmethod
    async def peer_result(self, target: str) -> dict: ...

    @abstractmethod
    async def peer_votes_received(self, user_id: int, since: datetime) -> int:
        """Сколько оценок пришло на анкету после указанного момента."""

    @abstractmethod
    async def peer_recent_voters(
        self, user_id: int, since: datetime, limit: int = 5
    ) -> list[dict]:
        """Кто недавно оценил и какой балл поставил."""

    @abstractmethod
    async def peer_add_report(
        self, reporter_id: int, target: str, reason: str
    ) -> int: ...

    @abstractmethod
    async def peer_close_report(self, report_id: int, status: str) -> None: ...

    @abstractmethod
    async def peer_seed_add(
        self, key: str, photo: bytes, added_by: int,
        name: str | None = None, age: int | None = None,
    ) -> bool: ...

    @abstractmethod
    async def peer_seed_list(self) -> list[dict]:
        """Снимки наполнения: ключ, имя и возраст (без самих байтов)."""

    @abstractmethod
    async def peer_seed_photo(self, key: str) -> bytes | None: ...

    @abstractmethod
    async def peer_seed_update(
        self, key: str, name: str | None, age: int | None
    ) -> bool:
        """Меняет имя и возраст у снимка наполнения."""

    @abstractmethod
    async def peer_seed_delete(self, key: str) -> bool: ...

    @abstractmethod
    async def peer_seed_clear(self) -> int: ...

    @abstractmethod
    async def peer_vote_rows(self, limit: int = 50000) -> list[dict]:
        """
        Сырые оценки для сводки: кто, кого и как оценил.

        Считаем в Python, а не в SQL: у SQLite нет STDDEV, и одинаковая
        логика на обоих бэкендах здесь дороже пары миллисекунд.
        """

    @abstractmethod
    async def algo_scores(self) -> dict[int, float]:
        """Последний балл алгоритма — чтобы сравнить его с мнением людей."""

    @abstractmethod
    async def peer_stats(self, since: datetime | None = None) -> dict:
        """
        Сводка по режиму. Живые анкеты считаются отдельно от наполнения:
        смешивать их в одном числе — верный способ обмануть себя насчёт
        того, сколько людей реально пользуется режимом.
        """
    async def diagnose_labels(self) -> dict:
        """Прямая проверка таблицы разметки: что реально лежит в базе."""

    @abstractmethod
    async def export_labels(self) -> list[dict]: ...

    @abstractmethod
    async def count_purchases(self, user_id: int, prefix: str) -> int:
        """Сколько покупок с таким префиксом (нужно для докупки попыток)."""

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
    async def referral_stats(self, limit: int = 10) -> dict:
        """Сводка по приглашениям: всего, топ пригласивших, выдано XP."""

    @abstractmethod
    async def broadcast_batch(
        self, after_id: int, limit: int, without_peer: bool = False
    ) -> list[int]:
        """
        Порция получателей рассылки, отсортированная по id.

        Порциями, а не одним списком: serverless-функция живёт секунды,
        и рассылка на тысячу человек в один вызов просто оборвётся.
        """

    @abstractmethod
    async def broadcast_size(self, without_peer: bool = False) -> int: ...

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
    referred_by   INTEGER,
    theme         TEXT,
    username      TEXT
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
-- Обучающая выборка: только числа, без фотографий.
CREATE TABLE IF NOT EXISTS labels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id   TEXT    NOT NULL UNIQUE,
    score      REAL    NOT NULL,
    metrics    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

-- Общие значения: подарочные сканы на день и подобное
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Анкеты режима взаимных оценок. Фото лежит здесь же: показать его другим
-- иначе нечем. Хранится только текущее — новое затирает старое.
CREATE TABLE IF NOT EXISTS peer_profiles (
    user_id      INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    age          INTEGER NOT NULL,
    photo        BLOB,
    photo_key    TEXT,
    status       TEXT    NOT NULL DEFAULT 'active',
    hidden_until TEXT,
    hidden_note  TEXT,
    terms_version TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

-- Ключ (voter_id, target) не даёт показать одну анкету дважды.
CREATE TABLE IF NOT EXISTS peer_votes (
    voter_id   INTEGER NOT NULL,
    target     TEXT    NOT NULL,
    tier       TEXT    NOT NULL,
    score      REAL    NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (voter_id, target)
);

CREATE TABLE IF NOT EXISTS peer_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    target      TEXT    NOT NULL,
    reason      TEXT,
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL
);

-- Пул снимков для наполнения. Держим в базе, а не только в папке: файлы
-- из репозитория попадают в serverless-функцию только при верной настройке
-- сборки, и молчаливо не попасть туда — слишком частая беда.
CREATE TABLE IF NOT EXISTS peer_seed (
    key        TEXT PRIMARY KEY,
    photo      BLOB NOT NULL,
    name       TEXT,
    age        INTEGER,
    added_by   INTEGER,
    created_at TEXT NOT NULL
);

-- Пропущенные анкеты. Отдельно от оценок: пропуск не должен влиять на
-- средний балл, но и показывать ту же карточку по кругу нельзя.
CREATE TABLE IF NOT EXISTS peer_skips (
    voter_id   INTEGER NOT NULL,
    target     TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (voter_id, target)
);

CREATE INDEX IF NOT EXISTS idx_peer_votes_target ON peer_votes(target);
CREATE INDEX IF NOT EXISTS idx_peer_status ON peer_profiles(status);


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
            ("theme", "ALTER TABLE users ADD COLUMN theme TEXT"),
            ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
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

    async def add_label(self, photo_id: str, score: float, metrics: str) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO labels (photo_id, score, metrics, created_at)
                 VALUES (?, ?, ?, ?)
            """,
            (photo_id, score, metrics, _now_iso()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_labels_by_score(self, score: float) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM labels WHERE ABS(score - ?) < 0.001", (score,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def refresh_label(self, photo_id: str, metrics: str) -> float | None:
        async with self.conn.execute(
            "SELECT score FROM labels WHERE photo_id = ?", (photo_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None

        await self.conn.execute(
            "UPDATE labels SET metrics = ? WHERE photo_id = ?", (metrics, photo_id)
        )
        await self.conn.commit()
        return float(row["score"])

    async def label_stats(self) -> dict:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n, AVG(score) AS avg FROM labels"
        ) as cursor:
            row = await cursor.fetchone()
        async with self.conn.execute(
            """
              SELECT CAST(score AS INTEGER) AS bucket, COUNT(*) AS n
                FROM labels GROUP BY bucket ORDER BY bucket
            """
        ) as cursor:
            buckets = {int(r["bucket"]): int(r["n"]) for r in await cursor.fetchall()}
        return {
            "total": int(row["n"] or 0),
            "average": round(float(row["avg"] or 0), 2),
            "buckets": buckets,
        }

    async def export_labels(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT photo_id, score, metrics FROM labels ORDER BY id"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def remember_username(self, user_id: int, username: str) -> None:
        await self.ensure_user(user_id)
        await self.conn.execute(
            "UPDATE users SET username = ? WHERE user_id = ?", (username, user_id)
        )
        await self.conn.commit()

    async def user_by_username(self, username: str) -> int | None:
        async with self.conn.execute(
            "SELECT user_id FROM users WHERE LOWER(username) = LOWER(?)", (username,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["user_id"]) if row else None

    async def set_theme(self, user_id: int, theme: str) -> None:
        await self.ensure_user(user_id)
        await self.conn.execute(
            "UPDATE users SET theme = ? WHERE user_id = ?", (theme, user_id)
        )
        await self.conn.commit()

    async def get_theme(self, user_id: int) -> str | None:
        async with self.conn.execute(
            "SELECT theme FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["theme"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now_iso()),
        )
        await self.conn.commit()

    async def get_setting(self, key: str) -> str | None:
        async with self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["value"] if row else None


    # ───────────────────── режим взаимных оценок ──────────────────────

    async def peer_save_profile(
        self, user_id: int, name: str, age: int, photo: bytes | None,
        photo_key: str | None, terms_version: str,
    ) -> None:
        await self.ensure_user(user_id)
        now = _now_iso()
        if photo is None:
            await self.conn.execute(
                """
                INSERT INTO peer_profiles
                       (user_id, name, age, terms_version, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                       name = excluded.name,
                       age = excluded.age,
                       terms_version = excluded.terms_version,
                       updated_at = excluded.updated_at
                """,
                (user_id, name, age, terms_version, now, now),
            )
        else:
            await self.conn.execute(
                """
                INSERT INTO peer_profiles
                       (user_id, name, age, photo, photo_key, status,
                        hidden_until, hidden_note, terms_version,
                        created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, 'active', NULL, NULL, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                       name = excluded.name,
                       age = excluded.age,
                       photo = excluded.photo,
                       photo_key = excluded.photo_key,
                       status = 'active',
                       hidden_until = NULL,
                       hidden_note = NULL,
                       terms_version = excluded.terms_version,
                       updated_at = excluded.updated_at
                """,
                (user_id, name, age, photo, photo_key, terms_version, now, now),
            )
        await self.conn.commit()

    async def peer_profile(self, user_id: int) -> dict | None:
        async with self.conn.execute(
            """
            SELECT user_id, name, age, photo_key, status, hidden_until,
                   hidden_note, terms_version
              FROM peer_profiles WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def peer_photo(self, user_id: int) -> bytes | None:
        async with self.conn.execute(
            "SELECT photo FROM peer_profiles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return bytes(row["photo"]) if row and row["photo"] else None

    async def peer_delete(self, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM peer_profiles WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

    async def peer_set_status(
        self, user_id: int, status: str, hidden_until: datetime | None,
        note: str | None,
    ) -> None:
        # Фото стираем сразу: скрытая анкета не должна оставлять снимок
        # в базе дольше, чем нужно, а после снятия скрытия его загрузят заново.
        await self.conn.execute(
            """
            UPDATE peer_profiles
               SET status = ?, hidden_until = ?, hidden_note = ?,
                   photo = NULL, photo_key = NULL, updated_at = ?
             WHERE user_id = ?
            """,
            (
                status,
                hidden_until.isoformat(timespec="seconds") if hidden_until else None,
                note,
                _now_iso(),
                user_id,
            ),
        )
        await self.conn.commit()

    async def peer_next(
        self, viewer_id: int, limit: int = 10, skip_since: datetime | None = None
    ) -> list[dict]:
        moment = (
            skip_since or datetime.now(timezone.utc)
        ).isoformat(timespec="seconds")
        async with self.conn.execute(
            """
            SELECT p.user_id, p.name, p.age, p.photo_key
              FROM peer_profiles p
             WHERE p.status = 'active'
               AND p.photo IS NOT NULL
               AND p.user_id != ?
               AND NOT EXISTS (
                     SELECT 1 FROM peer_votes v
                      WHERE v.voter_id = ? AND v.target = 'u:' || p.user_id
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM peer_skips s
                      WHERE s.voter_id = ? AND s.target = 'u:' || p.user_id
                        AND s.created_at >= ?
                   )
             ORDER BY RANDOM() LIMIT ?
            """,
            (viewer_id, viewer_id, viewer_id, moment, limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def peer_vote(
        self, voter_id: int, target: str, tier: str, score: float
    ) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO peer_votes
                   (voter_id, target, tier, score, created_at)
                 VALUES (?, ?, ?, ?, ?)
            """,
            (voter_id, target, tier, score, _now_iso()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def peer_seen(self, viewer_id: int) -> set[str]:
        async with self.conn.execute(
            "SELECT target FROM peer_votes WHERE voter_id = ?", (viewer_id,)
        ) as cursor:
            return {r["target"] for r in await cursor.fetchall()}

    async def peer_skip(self, viewer_id: int, target: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO peer_skips (voter_id, target, created_at) VALUES (?, ?, ?)
            ON CONFLICT(voter_id, target) DO UPDATE SET created_at = excluded.created_at
            """,
            (viewer_id, target, _now_iso()),
        )
        await self.conn.commit()

    async def peer_skipped(self, viewer_id: int, since: datetime) -> set[str]:
        async with self.conn.execute(
            "SELECT target FROM peer_skips WHERE voter_id = ? AND created_at >= ?",
            (viewer_id, since.isoformat(timespec="seconds")),
        ) as cursor:
            return {r["target"] for r in await cursor.fetchall()}

    async def peer_votes_since(self, voter_id: int, since: datetime) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM peer_votes WHERE voter_id = ? AND created_at >= ?",
            (voter_id, since.isoformat(timespec="seconds")),
        ) as cursor:
            return int((await cursor.fetchone())["n"])

    async def peer_result(self, target: str) -> dict:
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS n, AVG(score) AS avg FROM peer_votes
             WHERE target = ?
            """,
            (target,),
        ) as cursor:
            row = await cursor.fetchone()
        async with self.conn.execute(
            "SELECT tier, COUNT(*) AS n FROM peer_votes WHERE target = ? GROUP BY tier",
            (target,),
        ) as cursor:
            spread = {r["tier"]: int(r["n"]) for r in await cursor.fetchall()}
        return {
            "count": int(row["n"] or 0),
            "average": round(float(row["avg"] or 0), 2),
            "spread": spread,
        }

    async def peer_votes_received(self, user_id: int, since: datetime) -> int:
        async with self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM peer_votes
             WHERE target = ? AND created_at > ?
            """,
            (f"u:{user_id}", since.isoformat(timespec="seconds")),
        ) as cursor:
            return int((await cursor.fetchone())["n"])

    async def peer_recent_voters(
        self, user_id: int, since: datetime, limit: int = 5
    ) -> list[int]:
        async with self.conn.execute(
            """
              SELECT v.voter_id, v.tier FROM peer_votes v
                JOIN peer_profiles p ON p.user_id = v.voter_id
               WHERE v.target = ? AND v.created_at > ?
                 AND p.status = 'active' AND p.photo IS NOT NULL
            ORDER BY v.created_at DESC LIMIT ?
            """,
            (f"u:{user_id}", since.isoformat(timespec="seconds"), limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def peer_add_report(
        self, reporter_id: int, target: str, reason: str
    ) -> int:
        cursor = await self.conn.execute(
            """
            INSERT INTO peer_reports (reporter_id, target, reason, created_at)
                 VALUES (?, ?, ?, ?)
            """,
            (reporter_id, target, reason, _now_iso()),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def peer_close_report(self, report_id: int, status: str) -> None:
        await self.conn.execute(
            "UPDATE peer_reports SET status = ? WHERE id = ?", (status, report_id)
        )
        await self.conn.commit()

    async def peer_seed_add(
        self, key: str, photo: bytes, added_by: int,
        name: str | None = None, age: int | None = None,
    ) -> bool:
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO peer_seed
                   (key, photo, name, age, added_by, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, photo, name, age, added_by, _now_iso()),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def peer_seed_list(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT key, name, age FROM peer_seed ORDER BY created_at"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def peer_seed_photo(self, key: str) -> bytes | None:
        async with self.conn.execute(
            "SELECT photo FROM peer_seed WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return bytes(row["photo"]) if row else None

    async def peer_seed_update(
        self, key: str, name: str | None, age: int | None
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE peer_seed SET name = ?, age = ? WHERE key = ?", (name, age, key)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def peer_seed_delete(self, key: str) -> bool:
        cursor = await self.conn.execute("DELETE FROM peer_seed WHERE key = ?", (key,))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def peer_seed_clear(self) -> int:
        cursor = await self.conn.execute("DELETE FROM peer_seed")
        await self.conn.commit()
        return cursor.rowcount

    async def peer_vote_rows(self, limit: int = 50000) -> list[dict]:
        async with self.conn.execute(
            """
              SELECT voter_id, target, tier, score FROM peer_votes
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def algo_scores(self) -> dict[int, float]:
        async with self.conn.execute(
            "SELECT user_id, last_overall FROM users WHERE ratings_count > 0"
        ) as cursor:
            return {
                int(r["user_id"]): float(r["last_overall"])
                for r in await cursor.fetchall()
            }

    async def peer_stats(self, since: datetime | None = None) -> dict:
        moment = (since or datetime.now(timezone.utc)).isoformat(timespec="seconds")

        async with self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(status = 'active') AS active,
                   SUM(status = 'hidden') AS hidden,
                   SUM(status = 'banned') AS banned,
                   SUM(status = 'active' AND photo IS NOT NULL) AS with_photo,
                   SUM(created_at >= ?) AS fresh
              FROM peer_profiles
            """,
            (moment,),
        ) as cursor:
            row = await cursor.fetchone()

        async with self.conn.execute("SELECT COUNT(*) AS n FROM peer_votes") as cursor:
            votes = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM peer_votes WHERE created_at >= ?", (moment,)
        ) as cursor:
            votes_recent = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            "SELECT COUNT(DISTINCT voter_id) AS n FROM peer_votes WHERE created_at >= ?",
            (moment,),
        ) as cursor:
            voters_recent = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            """
            SELECT COUNT(DISTINCT target) AS n FROM peer_votes
             WHERE target LIKE 'u:%'
            """
        ) as cursor:
            rated_profiles = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM peer_reports WHERE status = 'open'"
        ) as cursor:
            reports = int((await cursor.fetchone())["n"])

        async with self.conn.execute("SELECT COUNT(*) AS n FROM peer_seed") as cursor:
            seed = int((await cursor.fetchone())["n"])

        return {
            "profiles": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "hidden": int(row["hidden"] or 0),
            "banned": int(row["banned"] or 0),
            "with_photo": int(row["with_photo"] or 0),
            "fresh": int(row["fresh"] or 0),
            "rated_profiles": rated_profiles,
            "votes": votes,
            "votes_recent": votes_recent,
            "voters_recent": voters_recent,
            "open_reports": reports,
            "seed": seed,
        }
    async def diagnose_labels(self) -> dict:
        info = {"backend": "sqlite", "target": self._path}
        try:
            async with self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='labels'"
            ) as cursor:
                info["table_exists"] = bool(await cursor.fetchone())
            async with self.conn.execute("SELECT COUNT(*) AS n FROM labels") as cursor:
                info["rows"] = int((await cursor.fetchone())["n"])
            async with self.conn.execute(
                "SELECT photo_id, score FROM labels ORDER BY id DESC LIMIT 3"
            ) as cursor:
                info["last"] = [
                    f"{r['photo_id'][:10]}…={r['score']}" for r in await cursor.fetchall()
                ]
        except Exception as error:  # noqa: BLE001
            info["error"] = f"{type(error).__name__}: {error}"
        return info

    async def count_purchases(self, user_id: int, prefix: str) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM purchases WHERE user_id = ? AND guide_id LIKE ?",
            (user_id, prefix + "%"),
        ) as cursor:
            return int((await cursor.fetchone())["n"])

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

    async def referral_stats(self, limit: int = 10) -> dict:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by IS NOT NULL"
        ) as cursor:
            total = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            """
              SELECT referred_by AS inviter, COUNT(*) AS n
                FROM users WHERE referred_by IS NOT NULL
            GROUP BY referred_by ORDER BY n DESC, inviter LIMIT ?
            """,
            (limit,),
        ) as cursor:
            top = [(int(r["inviter"]), int(r["n"])) for r in await cursor.fetchall()]

        async with self.conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS n FROM xp_events
             WHERE key LIKE 'referral:%' OR key LIKE 'refbonus:%'
            """
        ) as cursor:
            xp = int((await cursor.fetchone())["n"])

        async with self.conn.execute(
            "SELECT COUNT(DISTINCT referred_by) AS n FROM users WHERE referred_by IS NOT NULL"
        ) as cursor:
            inviters = int((await cursor.fetchone())["n"])

        return {"total": total, "inviters": inviters, "xp": xp, "top": top}

    async def broadcast_batch(
        self, after_id: int, limit: int, without_peer: bool = False
    ) -> list[int]:
        extra = (
            "AND NOT EXISTS (SELECT 1 FROM peer_profiles p WHERE p.user_id = u.user_id)"
            if without_peer
            else ""
        )
        async with self.conn.execute(
            f"""
              SELECT u.user_id FROM users u
               WHERE u.user_id > ? {extra}
            ORDER BY u.user_id LIMIT ?
            """,
            (after_id, limit),
        ) as cursor:
            return [int(r["user_id"]) for r in await cursor.fetchall()]

    async def broadcast_size(self, without_peer: bool = False) -> int:
        extra = (
            "WHERE NOT EXISTS (SELECT 1 FROM peer_profiles p WHERE p.user_id = u.user_id)"
            if without_peer
            else ""
        )
        async with self.conn.execute(
            f"SELECT COUNT(*) AS n FROM users u {extra}"
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
    referred_by   BIGINT,
    theme         TEXT,
    username      TEXT
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

CREATE TABLE IF NOT EXISTS labels (
    id         BIGSERIAL PRIMARY KEY,
    photo_id   TEXT   NOT NULL UNIQUE,
    score      DOUBLE PRECISION NOT NULL,
    metrics    TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS peer_profiles (
    user_id      BIGINT PRIMARY KEY,
    name         TEXT    NOT NULL,
    age          INTEGER NOT NULL,
    photo        BYTEA,
    photo_key    TEXT,
    status       TEXT    NOT NULL DEFAULT 'active',
    hidden_until TIMESTAMPTZ,
    hidden_note  TEXT,
    terms_version TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS peer_votes (
    voter_id   BIGINT NOT NULL,
    target     TEXT   NOT NULL,
    tier       TEXT   NOT NULL,
    score      DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (voter_id, target)
);

CREATE TABLE IF NOT EXISTS peer_reports (
    id          BIGSERIAL PRIMARY KEY,
    reporter_id BIGINT NOT NULL,
    target      TEXT   NOT NULL,
    reason      TEXT,
    status      TEXT   NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS peer_seed (
    key        TEXT PRIMARY KEY,
    photo      BYTEA NOT NULL,
    name       TEXT,
    age        INTEGER,
    added_by   BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS peer_skips (
    voter_id   BIGINT NOT NULL,
    target     TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (voter_id, target)
);

CREATE INDEX IF NOT EXISTS idx_peer_votes_target ON peer_votes(target);
CREATE INDEX IF NOT EXISTS idx_peer_status ON peer_profiles(status);


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
ALTER TABLE users ADD COLUMN IF NOT EXISTS theme        TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS username     TEXT;
ALTER TABLE peer_seed ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE peer_seed ADD COLUMN IF NOT EXISTS age  INTEGER;

CREATE INDEX IF NOT EXISTS idx_users_username ON users(LOWER(username));

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

    async def add_label(self, photo_id: str, score: float, metrics: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO labels (photo_id, score, metrics) VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING RETURNING id
                """,
                photo_id, score, metrics,
            )
        return row is not None

    async def delete_labels_by_score(self, score: float) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM labels WHERE ABS(score - $1) < 0.001", score
            )
        return int(result.split()[-1]) if result else 0

    async def refresh_label(self, photo_id: str, metrics: str) -> float | None:
        async with self.pool.acquire() as conn:
            score = await conn.fetchval(
                "SELECT score FROM labels WHERE photo_id = $1", photo_id
            )
            if score is None:
                return None
            await conn.execute(
                "UPDATE labels SET metrics = $1 WHERE photo_id = $2", metrics, photo_id
            )
        return float(score)

    async def label_stats(self) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS n, AVG(score) AS avg FROM labels")
            rows = await conn.fetch(
                """
                  SELECT FLOOR(score)::int AS bucket, COUNT(*) AS n
                    FROM labels GROUP BY bucket ORDER BY bucket
                """
            )
        return {
            "total": int(row["n"] or 0),
            "average": round(float(row["avg"] or 0), 2),
            "buckets": {int(r["bucket"]): int(r["n"]) for r in rows},
        }

    async def export_labels(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT photo_id, score, metrics FROM labels ORDER BY id")
        return [dict(r) for r in rows]

    async def remember_username(self, user_id: int, username: str) -> None:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET username = $1 WHERE user_id = $2", username, user_id
            )

    async def user_by_username(self, username: str) -> int | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT user_id FROM users WHERE LOWER(username) = LOWER($1)", username
            )
        return int(value) if value else None

    async def set_theme(self, user_id: int, theme: str) -> None:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET theme = $1 WHERE user_id = $2", theme, user_id
            )

    async def get_theme(self, user_id: int) -> str | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT theme FROM users WHERE user_id = $1", user_id
            )

    async def set_setting(self, key: str, value: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()
                """,
                key, value,
            )

    async def get_setting(self, key: str) -> str | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT value FROM settings WHERE key = $1", key)


    # ───────────────────── режим взаимных оценок ──────────────────────

    async def peer_save_profile(
        self, user_id: int, name: str, age: int, photo: bytes | None,
        photo_key: str | None, terms_version: str,
    ) -> None:
        await self.ensure_user(user_id)
        async with self.pool.acquire() as conn:
            if photo is None:
                await conn.execute(
                    """
                    INSERT INTO peer_profiles (user_id, name, age, terms_version)
                         VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) DO UPDATE SET
                           name = EXCLUDED.name,
                           age = EXCLUDED.age,
                           terms_version = EXCLUDED.terms_version,
                           updated_at = NOW()
                    """,
                    user_id, name, age, terms_version,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO peer_profiles
                           (user_id, name, age, photo, photo_key, status,
                            terms_version)
                         VALUES ($1, $2, $3, $4, $5, 'active', $6)
                    ON CONFLICT (user_id) DO UPDATE SET
                           name = EXCLUDED.name,
                           age = EXCLUDED.age,
                           photo = EXCLUDED.photo,
                           photo_key = EXCLUDED.photo_key,
                           status = 'active',
                           hidden_until = NULL,
                           hidden_note = NULL,
                           terms_version = EXCLUDED.terms_version,
                           updated_at = NOW()
                    """,
                    user_id, name, age, photo, photo_key, terms_version,
                )

    async def peer_profile(self, user_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, name, age, photo_key, status, hidden_until,
                       hidden_note, terms_version
                  FROM peer_profiles WHERE user_id = $1
                """,
                user_id,
            )
        return dict(row) if row else None

    async def peer_photo(self, user_id: int) -> bytes | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT photo FROM peer_profiles WHERE user_id = $1", user_id
            )
        return bytes(value) if value else None

    async def peer_delete(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM peer_profiles WHERE user_id = $1", user_id)

    async def peer_set_status(
        self, user_id: int, status: str, hidden_until: datetime | None,
        note: str | None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE peer_profiles
                   SET status = $1, hidden_until = $2, hidden_note = $3,
                       photo = NULL, photo_key = NULL, updated_at = NOW()
                 WHERE user_id = $4
                """,
                status, hidden_until, note, user_id,
            )

    async def peer_next(
        self, viewer_id: int, limit: int = 10, skip_since: datetime | None = None
    ) -> list[dict]:
        moment = skip_since or datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.user_id, p.name, p.age, p.photo_key
                  FROM peer_profiles p
                 WHERE p.status = 'active'
                   AND p.photo IS NOT NULL
                   AND p.user_id <> $1
                   AND NOT EXISTS (
                         SELECT 1 FROM peer_votes v
                          WHERE v.voter_id = $1
                            AND v.target = 'u:' || p.user_id::text
                       )
                   AND NOT EXISTS (
                         SELECT 1 FROM peer_skips s
                          WHERE s.voter_id = $1
                            AND s.target = 'u:' || p.user_id::text
                            AND s.created_at >= $3
                       )
                 ORDER BY RANDOM() LIMIT $2
                """,
                viewer_id, limit, moment,
            )
        return [dict(r) for r in rows]

    async def peer_vote(
        self, voter_id: int, target: str, tier: str, score: float
    ) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO peer_votes (voter_id, target, tier, score)
                     VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING RETURNING target
                """,
                voter_id, target, tier, score,
            )
        return row is not None

    async def peer_seen(self, viewer_id: int) -> set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target FROM peer_votes WHERE voter_id = $1", viewer_id
            )
        return {r["target"] for r in rows}

    async def peer_skip(self, viewer_id: int, target: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO peer_skips (voter_id, target) VALUES ($1, $2)
                ON CONFLICT (voter_id, target) DO UPDATE SET created_at = NOW()
                """,
                viewer_id, target,
            )

    async def peer_skipped(self, viewer_id: int, since: datetime) -> set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target FROM peer_skips WHERE voter_id = $1 AND created_at >= $2",
                viewer_id, since,
            )
        return {r["target"] for r in rows}

    async def peer_votes_since(self, voter_id: int, since: datetime) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM peer_votes WHERE voter_id = $1 AND created_at >= $2",
                voter_id, since,
            )
        return int(value or 0)

    async def peer_result(self, target: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n, AVG(score) AS avg FROM peer_votes WHERE target = $1",
                target,
            )
            rows = await conn.fetch(
                "SELECT tier, COUNT(*) AS n FROM peer_votes WHERE target = $1 GROUP BY tier",
                target,
            )
        return {
            "count": int(row["n"] or 0),
            "average": round(float(row["avg"] or 0), 2),
            "spread": {r["tier"]: int(r["n"]) for r in rows},
        }

    async def peer_votes_received(self, user_id: int, since: datetime) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM peer_votes WHERE target = $1 AND created_at > $2",
                f"u:{user_id}", since,
            )
        return int(value or 0)

    async def peer_recent_voters(
        self, user_id: int, since: datetime, limit: int = 5
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                  SELECT v.voter_id, v.tier FROM peer_votes v
                    JOIN peer_profiles p ON p.user_id = v.voter_id
                   WHERE v.target = $1 AND v.created_at > $2
                     AND p.status = 'active' AND p.photo IS NOT NULL
                ORDER BY v.created_at DESC LIMIT $3
                """,
                f"u:{user_id}", since, limit,
            )
        return [dict(r) for r in rows]

    async def peer_add_report(
        self, reporter_id: int, target: str, reason: str
    ) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                """
                INSERT INTO peer_reports (reporter_id, target, reason)
                     VALUES ($1, $2, $3) RETURNING id
                """,
                reporter_id, target, reason,
            )
        return int(value or 0)

    async def peer_close_report(self, report_id: int, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE peer_reports SET status = $1 WHERE id = $2", status, report_id
            )

    async def peer_seed_add(
        self, key: str, photo: bytes, added_by: int,
        name: str | None = None, age: int | None = None,
    ) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                """
                INSERT INTO peer_seed (key, photo, name, age, added_by)
                     VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING RETURNING key
                """,
                key, photo, name, age, added_by,
            )
        return row is not None

    async def peer_seed_list(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, name, age FROM peer_seed ORDER BY created_at"
            )
        return [dict(r) for r in rows]

    async def peer_seed_photo(self, key: str) -> bytes | None:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT photo FROM peer_seed WHERE key = $1", key
            )
        return bytes(value) if value else None

    async def peer_seed_update(
        self, key: str, name: str | None, age: int | None
    ) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE peer_seed SET name = $1, age = $2 WHERE key = $3", name, age, key
            )
        return result.endswith("1")

    async def peer_seed_delete(self, key: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM peer_seed WHERE key = $1", key)
        return result.endswith("1")

    async def peer_seed_clear(self) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM peer_seed")
        return int(result.split()[-1]) if result else 0

    async def peer_vote_rows(self, limit: int = 50000) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                  SELECT voter_id, target, tier, score FROM peer_votes
                ORDER BY created_at DESC LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def algo_scores(self) -> dict[int, float]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id, last_overall FROM users WHERE ratings_count > 0"
            )
        return {int(r["user_id"]): float(r["last_overall"]) for r in rows}

    async def peer_stats(self, since: datetime | None = None) -> dict:
        moment = since or datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status = 'active') AS active,
                       COUNT(*) FILTER (WHERE status = 'hidden') AS hidden,
                       COUNT(*) FILTER (WHERE status = 'banned') AS banned,
                       COUNT(*) FILTER (
                           WHERE status = 'active' AND photo IS NOT NULL
                       ) AS with_photo,
                       COUNT(*) FILTER (WHERE created_at >= $1) AS fresh
                  FROM peer_profiles
                """,
                moment,
            )
            votes = await conn.fetchval("SELECT COUNT(*) FROM peer_votes")
            votes_recent = await conn.fetchval(
                "SELECT COUNT(*) FROM peer_votes WHERE created_at >= $1", moment
            )
            voters_recent = await conn.fetchval(
                "SELECT COUNT(DISTINCT voter_id) FROM peer_votes WHERE created_at >= $1",
                moment,
            )
            rated_profiles = await conn.fetchval(
                "SELECT COUNT(DISTINCT target) FROM peer_votes WHERE target LIKE 'u:%'"
            )
            reports = await conn.fetchval(
                "SELECT COUNT(*) FROM peer_reports WHERE status = 'open'"
            )
            seed = await conn.fetchval("SELECT COUNT(*) FROM peer_seed")

        return {
            "profiles": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "hidden": int(row["hidden"] or 0),
            "banned": int(row["banned"] or 0),
            "with_photo": int(row["with_photo"] or 0),
            "fresh": int(row["fresh"] or 0),
            "rated_profiles": int(rated_profiles or 0),
            "votes": int(votes or 0),
            "votes_recent": int(votes_recent or 0),
            "voters_recent": int(voters_recent or 0),
            "open_reports": int(reports or 0),
            "seed": int(seed or 0),
        }
    async def diagnose_labels(self) -> dict:
        # Хост показываем без логина и пароля — по нему видно, та ли база
        host = self._dsn.split("@")[-1].split("/")[0] if "@" in self._dsn else "?"
        info = {"backend": "postgres", "target": host}
        try:
            async with self.pool.acquire() as conn:
                info["table_exists"] = bool(
                    await conn.fetchval("SELECT to_regclass('public.labels')")
                )
                info["rows"] = int(await conn.fetchval("SELECT COUNT(*) FROM labels") or 0)
                rows = await conn.fetch(
                    "SELECT photo_id, score FROM labels ORDER BY id DESC LIMIT 3"
                )
                info["last"] = [f"{r['photo_id'][:10]}…={r['score']}" for r in rows]
                info["db"] = await conn.fetchval("SELECT current_database()")
        except Exception as error:  # noqa: BLE001
            info["error"] = f"{type(error).__name__}: {error}"
        return info

    async def count_purchases(self, user_id: int, prefix: str) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM purchases WHERE user_id = $1 AND guide_id LIKE $2",
                user_id, prefix + "%",
            )
        return int(value or 0)

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

    async def referral_stats(self, limit: int = 10) -> dict:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL"
            )
            inviters = await conn.fetchval(
                "SELECT COUNT(DISTINCT referred_by) FROM users WHERE referred_by IS NOT NULL"
            )
            rows = await conn.fetch(
                """
                  SELECT referred_by AS inviter, COUNT(*) AS n
                    FROM users WHERE referred_by IS NOT NULL
                GROUP BY referred_by ORDER BY n DESC, inviter LIMIT $1
                """,
                limit,
            )
            xp = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0) FROM xp_events
                 WHERE key LIKE 'referral:%' OR key LIKE 'refbonus:%'
                """
            )

        return {
            "total": int(total or 0),
            "inviters": int(inviters or 0),
            "xp": int(xp or 0),
            "top": [(int(r["inviter"]), int(r["n"])) for r in rows],
        }

    async def broadcast_batch(
        self, after_id: int, limit: int, without_peer: bool = False
    ) -> list[int]:
        extra = (
            "AND NOT EXISTS (SELECT 1 FROM peer_profiles p WHERE p.user_id = u.user_id)"
            if without_peer
            else ""
        )
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                  SELECT u.user_id FROM users u
                   WHERE u.user_id > $1 {extra}
                ORDER BY u.user_id LIMIT $2
                """,
                after_id, limit,
            )
        return [int(r["user_id"]) for r in rows]

    async def broadcast_size(self, without_peer: bool = False) -> int:
        extra = (
            "WHERE NOT EXISTS (SELECT 1 FROM peer_profiles p WHERE p.user_id = u.user_id)"
            if without_peer
            else ""
        )
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(f"SELECT COUNT(*) FROM users u {extra}")
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
