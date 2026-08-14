"""Хранилище режима оценивания.

Работает на том же DATABASE_URL, что и остальной бот: SQLite локально,
Postgres на Vercel. Это принципиально — на serverless локальный файл
не переживает деплой, и данные разъехались бы с основной базой.

Наружу торчит один и тот же набор функций независимо от бэкенда.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable

from . import config

_backend: "_Backend | None" = None


# ─────────────────────────── схема ──────────────────────────────────────────
# Пишем в диалекте SQLite; для Postgres типы подменяются автоматически.

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS rate_profiles (
        user_id        BIGINT PRIMARY KEY,
        display_name   TEXT    NOT NULL DEFAULT '',
        age            INTEGER NOT NULL DEFAULT 0,
        gender         TEXT,
        photo_file_id  TEXT,
        photo_path     TEXT,
        status         TEXT    NOT NULL DEFAULT 'draft',
        hidden_until   BIGINT  NOT NULL DEFAULT 0,
        needs_reupload INTEGER NOT NULL DEFAULT 0,
        votes_count    INTEGER NOT NULL DEFAULT 0,
        votes_weight   INTEGER NOT NULL DEFAULT 0,
        shows_count    INTEGER NOT NULL DEFAULT 0,
        priority       INTEGER NOT NULL DEFAULT 0,
        created_at     BIGINT  NOT NULL,
        updated_at     BIGINT  NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rate_feed ON rate_profiles(status, shows_count)",
    """
    CREATE TABLE IF NOT EXISTS rate_consents (
        user_id     BIGINT NOT NULL,
        version     TEXT   NOT NULL,
        accepted_at BIGINT NOT NULL,
        source      TEXT   NOT NULL,
        PRIMARY KEY (user_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_seed_profiles (
        id           SERIAL PRIMARY KEY,
        slug         TEXT    NOT NULL UNIQUE,
        display_name TEXT    NOT NULL,
        age          INTEGER NOT NULL,
        gender       TEXT,
        file_name    TEXT    NOT NULL,
        source       TEXT,
        license      TEXT,
        active       INTEGER NOT NULL DEFAULT 1,
        votes_count  INTEGER NOT NULL DEFAULT 0,
        votes_weight INTEGER NOT NULL DEFAULT 0,
        created_at   BIGINT  NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_seen (
        viewer_id   BIGINT NOT NULL,
        target_kind TEXT   NOT NULL,
        target_id   BIGINT NOT NULL,
        seen_at     BIGINT NOT NULL,
        PRIMARY KEY (viewer_id, target_kind, target_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_votes (
        id          SERIAL PRIMARY KEY,
        voter_id    BIGINT  NOT NULL,
        target_kind TEXT    NOT NULL,
        target_id   BIGINT  NOT NULL,
        grade       TEXT    NOT NULL,
        weight      INTEGER NOT NULL,
        created_at  BIGINT  NOT NULL,
        UNIQUE (voter_id, target_kind, target_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rate_votes_day ON rate_votes(voter_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS rate_reports (
        id          SERIAL PRIMARY KEY,
        reporter_id BIGINT NOT NULL,
        target_kind TEXT   NOT NULL,
        target_id   BIGINT NOT NULL,
        reason      TEXT   NOT NULL,
        comment     TEXT,
        snapshot    TEXT   NOT NULL,
        status      TEXT   NOT NULL DEFAULT 'new',
        action      TEXT,
        admin_id    BIGINT,
        created_at  BIGINT NOT NULL,
        resolved_at BIGINT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rate_reports_open ON rate_reports(status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS rate_mod_log (
        id          SERIAL PRIMARY KEY,
        admin_id    BIGINT NOT NULL,
        target_kind TEXT   NOT NULL,
        target_id   BIGINT NOT NULL,
        action      TEXT   NOT NULL,
        details     TEXT,
        created_at  BIGINT NOT NULL
    )
    """,
    # Журнал карточек, собранных в режиме съёмки. Нужен, чтобы отличать
    # постановочный материал от настоящих оценок при разборе полётов.
    """
    CREATE TABLE IF NOT EXISTS rate_demo_log (
        id         SERIAL PRIMARY KEY,
        admin_id   BIGINT NOT NULL,
        nickname   TEXT   NOT NULL,
        grade      TEXT   NOT NULL,
        created_at BIGINT NOT NULL
    )
    """,
    # Фото анкет. На serverless диск только для чтения, поэтому
    # единственное надёжное место — та же база.
    """
    CREATE TABLE IF NOT EXISTS rate_photos (
        name       TEXT   PRIMARY KEY,
        owner_id   BIGINT NOT NULL,
        data       BLOB   NOT NULL,
        created_at BIGINT NOT NULL
    )
    """,
    # Telegram не даёт узнать id по @username. Запоминаем сами, когда
    # человек пишет боту, — иначе /tries @ник работать не сможет.
    """
    CREATE TABLE IF NOT EXISTS rate_usernames (
        username TEXT   PRIMARY KEY,
        user_id  BIGINT NOT NULL,
        seen_at  BIGINT NOT NULL
    )
    """,
]


def now() -> int:
    return int(time.time())


# ─────────────────────────── бэкенды ────────────────────────────────────────

class _Backend:
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def one(self, sql: str, args: Iterable[Any] = ()) -> dict | None: ...
    async def all(self, sql: str, args: Iterable[Any] = ()) -> list[dict]: ...
    async def run(self, sql: str, args: Iterable[Any] = ()) -> int:
        """Возвращает число затронутых строк."""
        ...
    async def insert(self, sql: str, args: Iterable[Any] = ()) -> int:
        """INSERT с возвратом id новой строки."""
        ...


class _Sqlite(_Backend):
    def __init__(self, path: str) -> None:
        self._path = path
        self._db = None

    def _fix(self, sql: str) -> str:
        return sql.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")

    async def connect(self) -> None:
        import aiosqlite

        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA:
            await self._db.execute(self._fix(stmt))
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def one(self, sql, args=()):
        async with self._db.execute(sql, tuple(args)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def all(self, sql, args=()):
        async with self._db.execute(sql, tuple(args)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def run(self, sql, args=()):
        cur = await self._db.execute(sql, tuple(args))
        await self._db.commit()
        return cur.rowcount

    async def insert(self, sql, args=()):
        cur = await self._db.execute(sql, tuple(args))
        await self._db.commit()
        return int(cur.lastrowid)


class _Postgres(_Backend):
    """Postgres-бэкенд.

    Плейсхолдеры ? переписываются в $1, $2… Строки в кавычках при этом
    не трогаем: знак вопроса внутри текста не должен стать параметром.
    """

    _LITERAL = re.compile(r"'(?:[^']|'')*'")

    def __init__(self, url: str) -> None:
        self._url = url.replace("postgres://", "postgresql://", 1)
        self._pool = None

    @classmethod
    def _fix(cls, sql: str) -> str:
        parts, last = [], 0
        for m in cls._LITERAL.finditer(sql):
            parts.append(("code", sql[last:m.start()]))
            parts.append(("lit", m.group(0)))
            last = m.end()
        parts.append(("code", sql[last:]))

        n = 0
        out = []
        for kind, chunk in parts:
            if kind == "lit":
                out.append(chunk)
                continue
            buf = []
            for ch in chunk:
                if ch == "?":
                    n += 1
                    buf.append(f"${n}")
                else:
                    buf.append(ch)
            out.append("".join(buf))
        sql = "".join(out)
        return sql.replace("INSERT OR IGNORE", "INSERT").replace("RANDOM()", "RANDOM()")

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            for stmt in SCHEMA:
                await conn.execute(stmt.replace("BLOB", "BYTEA"))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def one(self, sql, args=()):
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(self._fix(sql), *args)
            return dict(row) if row else None

    async def all(self, sql, args=()):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(self._fix(sql), *args)
            return [dict(r) for r in rows]

    async def run(self, sql, args=()):
        async with self._pool.acquire() as conn:
            status = await conn.execute(self._fix(sql), *args)
        tail = status.rsplit(" ", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    async def insert(self, sql, args=()):
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval(self._fix(sql) + " RETURNING id", *args))


async def connect(database_url: str | None = None) -> _Backend:
    global _backend
    if _backend is None:
        url = database_url or config.DATABASE_URL
        if url.startswith(("postgres://", "postgresql://")):
            _backend = _Postgres(url)
        else:
            _backend = _Sqlite(url.replace("sqlite:///", "").replace("sqlite://", "") or config.DB_PATH)
        await _backend.connect()
    return _backend


async def close() -> None:
    global _backend
    if _backend is not None:
        await _backend.close()
        _backend = None


def _is_pg() -> bool:
    return isinstance(_backend, _Postgres)


async def _one(sql, args=()):
    return await (await connect()).one(sql, args)


async def _all(sql, args=()):
    return await (await connect()).all(sql, args)


async def _run(sql, args=()):
    return await (await connect()).run(sql, args)


def _ignore(sql: str, conflict: str) -> str:
    """INSERT, который молча пропускает дубликат, на обоих диалектах."""
    if _is_pg():
        return sql.replace("INSERT OR IGNORE", "INSERT") + f" ON CONFLICT ({conflict}) DO NOTHING"
    return sql


# ─────────────────────────── согласия ───────────────────────────────────────

async def has_consent(user_id: int, version: str | None = None) -> bool:
    version = version or config.CONSENT_VERSION
    row = await _one(
        "SELECT 1 AS x FROM rate_consents WHERE user_id=? AND version=?", (user_id, version)
    )
    return row is not None


async def save_consent(user_id: int, source: str, version: str | None = None) -> None:
    version = version or config.CONSENT_VERSION
    await connect()
    await _run(
        _ignore(
            "INSERT OR IGNORE INTO rate_consents(user_id, version, accepted_at, source)"
            " VALUES (?,?,?,?)",
            "user_id, version",
        ),
        (user_id, version, now(), source),
    )


# ─────────────────────────── анкеты ─────────────────────────────────────────

async def get_profile(user_id: int) -> dict | None:
    return await _one("SELECT * FROM rate_profiles WHERE user_id=?", (user_id,))


async def upsert_profile(user_id: int, **fields: Any) -> None:
    existing = await get_profile(user_id)
    ts = now()
    if existing is None:
        cols = {
            "user_id": user_id,
            "display_name": fields.get("display_name", ""),
            "age": fields.get("age", 0),
            "gender": fields.get("gender"),
            "photo_file_id": fields.get("photo_file_id"),
            "photo_path": fields.get("photo_path"),
            "status": fields.get("status", "draft"),
            "created_at": ts,
            "updated_at": ts,
        }
        marks = ",".join("?" * len(cols))
        await _run(
            f"INSERT INTO rate_profiles({','.join(cols)}) VALUES ({marks})",
            tuple(cols.values()),
        )
        return
    if not fields:
        return
    fields["updated_at"] = ts
    sets = ",".join(f"{k}=?" for k in fields)
    await _run(f"UPDATE rate_profiles SET {sets} WHERE user_id=?", (*fields.values(), user_id))


async def set_status(user_id: int, status: str, **extra: Any) -> None:
    await upsert_profile(user_id, status=status, **extra)


async def delete_profile(user_id: int) -> None:
    """Мягкое удаление: строку держим, на неё ссылаются жалобы и журнал."""
    await _run(
        "UPDATE rate_profiles SET status='deleted', photo_file_id=NULL, photo_path=NULL,"
        " display_name='', age=0, hidden_until=0, needs_reupload=0, updated_at=?"
        " WHERE user_id=?",
        (now(), user_id),
    )


async def unhide_expired() -> list[int]:
    ts = now()
    rows = await _all(
        "SELECT user_id FROM rate_profiles"
        " WHERE status='hidden' AND hidden_until>0 AND hidden_until<=?",
        (ts,),
    )
    ids = [int(r["user_id"]) for r in rows]
    for uid in ids:
        await _run(
            "UPDATE rate_profiles SET status='awaiting_photo', hidden_until=0,"
            " needs_reupload=1, photo_file_id=NULL, photo_path=NULL, updated_at=?"
            " WHERE user_id=?",
            (ts, uid),
        )
    return ids


# ─────────────────────────── сид-анкеты ─────────────────────────────────────

async def upsert_seed(slug: str, **fields: Any) -> None:
    row = await _one("SELECT id FROM rate_seed_profiles WHERE slug=?", (slug,))
    if row is None:
        await _run(
            "INSERT INTO rate_seed_profiles"
            "(slug, display_name, age, gender, file_name, source, license, active, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                slug,
                fields.get("display_name", slug),
                int(fields.get("age", 0)),
                fields.get("gender"),
                fields.get("file_name", ""),
                fields.get("source"),
                fields.get("license"),
                int(fields.get("active", 1)),
                now(),
            ),
        )
        return
    if fields:
        sets = ",".join(f"{k}=?" for k in fields)
        await _run(f"UPDATE rate_seed_profiles SET {sets} WHERE slug=?", (*fields.values(), slug))


async def get_seed(seed_id: int) -> dict | None:
    return await _one("SELECT * FROM rate_seed_profiles WHERE id=?", (seed_id,))


async def get_seed_by_slug(slug: str) -> dict | None:
    return await _one("SELECT * FROM rate_seed_profiles WHERE slug=?", (slug,))


async def deactivate_seeds_except(slugs: list[str]) -> None:
    if not slugs:
        await _run("UPDATE rate_seed_profiles SET active=0")
        return
    marks = ",".join("?" * len(slugs))
    await _run(f"UPDATE rate_seed_profiles SET active=0 WHERE slug NOT IN ({marks})", tuple(slugs))


async def count_seeds() -> int:
    row = await _one("SELECT COUNT(*) AS c FROM rate_seed_profiles WHERE active=1")
    return int(row["c"]) if row else 0


# ─────────────────────────── лента ──────────────────────────────────────────

async def live_candidates(viewer_id: int, limit: int) -> list[dict]:
    return await _all(
        """
        SELECT p.* FROM rate_profiles p
        WHERE p.status='active' AND p.user_id <> ? AND p.photo_path IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM rate_seen s
              WHERE s.viewer_id=? AND s.target_kind='live' AND s.target_id=p.user_id)
        ORDER BY p.priority DESC, p.shows_count ASC, RANDOM()
        LIMIT ?
        """,
        (viewer_id, viewer_id, limit),
    )


async def seed_candidates(viewer_id: int, limit: int) -> list[dict]:
    return await _all(
        """
        SELECT s.* FROM rate_seed_profiles s
        WHERE s.active=1
          AND NOT EXISTS (
              SELECT 1 FROM rate_seen v
              WHERE v.viewer_id=? AND v.target_kind='seed' AND v.target_id=s.id)
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (viewer_id, limit),
    )


async def count_live_available(viewer_id: int) -> int:
    row = await _one(
        """
        SELECT COUNT(*) AS c FROM rate_profiles p
        WHERE p.status='active' AND p.user_id <> ? AND p.photo_path IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM rate_seen s
              WHERE s.viewer_id=? AND s.target_kind='live' AND s.target_id=p.user_id)
        """,
        (viewer_id, viewer_id),
    )
    return int(row["c"]) if row else 0


async def mark_seen(viewer_id: int, kind: str, target_id: int) -> bool:
    """Отмечает показ. False — анкету уже показывали (значит, гонка)."""
    affected = await _run(
        _ignore(
            "INSERT OR IGNORE INTO rate_seen(viewer_id, target_kind, target_id, seen_at)"
            " VALUES (?,?,?,?)",
            "viewer_id, target_kind, target_id",
        ),
        (viewer_id, kind, target_id, now()),
    )
    if affected and kind == "live":
        await _run(
            "UPDATE rate_profiles SET shows_count=shows_count+1 WHERE user_id=?", (target_id,)
        )
    return bool(affected)


# ─────────────────────────── оценки ─────────────────────────────────────────

async def add_vote(voter_id: int, kind: str, target_id: int, grade: str, weight: int) -> bool:
    affected = await _run(
        _ignore(
            "INSERT OR IGNORE INTO rate_votes"
            "(voter_id, target_kind, target_id, grade, weight, created_at)"
            " VALUES (?,?,?,?,?,?)",
            "voter_id, target_kind, target_id",
        ),
        (voter_id, kind, target_id, grade, weight, now()),
    )
    if not affected:
        return False
    if kind == "live":
        await _run(
            "UPDATE rate_profiles SET votes_count=votes_count+1, votes_weight=votes_weight+?"
            " WHERE user_id=?",
            (weight, target_id),
        )
    else:
        await _run(
            "UPDATE rate_seed_profiles SET votes_count=votes_count+1, votes_weight=votes_weight+?"
            " WHERE id=?",
            (weight, target_id),
        )
    return True


async def votes_today(voter_id: int) -> int:
    row = await _one(
        "SELECT COUNT(*) AS c FROM rate_votes WHERE voter_id=? AND created_at>=?",
        (voter_id, now() - 24 * 3600),
    )
    return int(row["c"]) if row else 0


async def my_stats(user_id: int) -> dict:
    prof = await get_profile(user_id)
    given = await _one("SELECT COUNT(*) AS c FROM rate_votes WHERE voter_id=?", (user_id,))
    breakdown = await _all(
        "SELECT grade, COUNT(*) AS c FROM rate_votes"
        " WHERE target_kind='live' AND target_id=? GROUP BY grade",
        (user_id,),
    )
    return {
        "profile": prof,
        "given": int(given["c"]) if given else 0,
        "breakdown": {r["grade"]: int(r["c"]) for r in breakdown},
    }


# ─────────────────────────── жалобы ─────────────────────────────────────────

async def add_report(reporter_id, kind, target_id, reason, comment, snapshot) -> int:
    return await (await connect()).insert(
        "INSERT INTO rate_reports"
        "(reporter_id, target_kind, target_id, reason, comment, snapshot, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            reporter_id,
            kind,
            target_id,
            reason,
            comment,
            json.dumps(snapshot, ensure_ascii=False),
            now(),
        ),
    )


async def get_report(report_id: int) -> dict | None:
    row = await _one("SELECT * FROM rate_reports WHERE id=?", (report_id,))
    if not row:
        return None
    try:
        row["snapshot"] = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        row["snapshot"] = {}
    return row


async def open_reports_count(kind: str, target_id: int) -> int:
    row = await _one(
        "SELECT COUNT(DISTINCT reporter_id) AS c FROM rate_reports"
        " WHERE target_kind=? AND target_id=? AND status='new'",
        (kind, target_id),
    )
    return int(row["c"]) if row else 0


async def already_reported(reporter_id: int, kind: str, target_id: int) -> bool:
    row = await _one(
        "SELECT 1 AS x FROM rate_reports WHERE reporter_id=? AND target_kind=? AND target_id=?",
        (reporter_id, kind, target_id),
    )
    return row is not None


async def resolve_report(report_id: int, admin_id: int, action: str) -> None:
    await _run(
        "UPDATE rate_reports SET status='resolved', action=?, admin_id=?, resolved_at=?"
        " WHERE id=?",
        (action, admin_id, now(), report_id),
    )


async def resolve_reports_for(kind: str, target_id: int, admin_id: int, action: str) -> None:
    await _run(
        "UPDATE rate_reports SET status='resolved', action=?, admin_id=?, resolved_at=?"
        " WHERE target_kind=? AND target_id=? AND status='new'",
        (action, admin_id, now(), kind, target_id),
    )


async def pending_reports(limit: int = 20) -> list[dict]:
    return await _all(
        "SELECT * FROM rate_reports WHERE status='new' ORDER BY created_at ASC LIMIT ?", (limit,)
    )


async def log_action(admin_id, kind, target_id, action, details=None) -> None:
    await _run(
        "INSERT INTO rate_mod_log(admin_id, target_kind, target_id, action, details, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (admin_id, kind, target_id, action, details, now()),
    )


async def log_demo_card(admin_id: int, nickname: str, grade: str) -> None:
    """Пишем каждую постановочную карточку — чтобы постанову можно было отличить."""
    await _run(
        "INSERT INTO rate_demo_log(admin_id, nickname, grade, created_at) VALUES (?,?,?,?)",
        (admin_id, nickname, grade, now()),
    )


async def demo_cards_count(admin_id: int | None = None) -> int:
    if admin_id is None:
        row = await _one("SELECT COUNT(*) AS c FROM rate_demo_log")
    else:
        row = await _one("SELECT COUNT(*) AS c FROM rate_demo_log WHERE admin_id=?", (admin_id,))
    return int(row["c"]) if row else 0


# ─────────────────────────── сводка ─────────────────────────────────────────

async def global_stats() -> dict:
    async def c(sql: str) -> int:
        row = await _one(sql)
        return int(row["c"]) if row else 0

    return {
        "active": await c("SELECT COUNT(*) AS c FROM rate_profiles WHERE status='active'"),
        "hidden": await c("SELECT COUNT(*) AS c FROM rate_profiles WHERE status='hidden'"),
        "awaiting": await c("SELECT COUNT(*) AS c FROM rate_profiles WHERE status='awaiting_photo'"),
        "deleted": await c("SELECT COUNT(*) AS c FROM rate_profiles WHERE status='deleted'"),
        "seeds": await c("SELECT COUNT(*) AS c FROM rate_seed_profiles WHERE active=1"),
        "votes": await c("SELECT COUNT(*) AS c FROM rate_votes"),
        "reports_open": await c("SELECT COUNT(*) AS c FROM rate_reports WHERE status='new'"),
        "demo_cards": await c("SELECT COUNT(*) AS c FROM rate_demo_log"),
    }


# ─────────────────────────── фото ───────────────────────────────────────────

async def save_photo(name: str, owner_id: int, data: bytes) -> None:
    await _run(
        "INSERT INTO rate_photos(name, owner_id, data, created_at) VALUES (?,?,?,?)",
        (name, owner_id, data, now()),
    )


async def load_photo(name: str) -> bytes | None:
    row = await _one("SELECT data FROM rate_photos WHERE name=?", (name,))
    if not row:
        return None
    data = row["data"]
    # SQLite отдаёт bytes, asyncpg — bytes; memoryview встречается у драйверов
    # с нулевым копированием, поэтому приводим явно.
    return bytes(data) if data is not None else None


async def delete_photo(name: str) -> None:
    await _run("DELETE FROM rate_photos WHERE name=?", (name,))


async def photos_bytes_total() -> int:
    """Сколько места занято фото — для диагностики."""
    if _is_pg():
        row = await _one("SELECT COALESCE(SUM(LENGTH(data)),0) AS c FROM rate_photos")
    else:
        row = await _one("SELECT COALESCE(SUM(LENGTH(data)),0) AS c FROM rate_photos")
    return int(row["c"]) if row else 0


# ─────────────────────────── username -> id ─────────────────────────────────

async def remember_username(user_id: int, username: str | None) -> None:
    """Запоминает @username, когда человек пишет боту.

    Ник может смениться и переехать к другому человеку, поэтому старую
    запись с тем же ником перетираем, а не дополняем.
    """
    if not username:
        return
    key = username.lstrip("@").lower()
    if not key:
        return
    await _run("DELETE FROM rate_usernames WHERE username=?", (key,))
    await _run(
        "INSERT INTO rate_usernames(username, user_id, seen_at) VALUES (?,?,?)",
        (key, user_id, now()),
    )


async def resolve_username(username: str) -> int | None:
    key = username.lstrip("@").lower()
    row = await _one("SELECT user_id FROM rate_usernames WHERE username=?", (key,))
    return int(row["user_id"]) if row else None
