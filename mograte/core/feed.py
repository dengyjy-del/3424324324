"""Подбор следующей анкеты для оценки.

Правило, которое держит всю ленту: пара (зритель, анкета) попадает
в rate_seen ровно один раз. Повторов нет ни у живых анкет, ни у сидов,
независимо от того, оценил человек анкету, пропустил или пожаловался.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import config, db, grades


@dataclass
class Card:
    kind: str            # live | seed
    target_id: int
    display_name: str
    age: int
    photo_url: str       # путь для мини-аппа
    photo_path: str      # файл на диске
    photo_file_id: str | None = None   # для отправки в боте без перезаливки

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.target_id,
            "name": self.display_name,
            "age": self.age,
            "photo": self.photo_url,
        }


class FeedEmpty(Exception):
    """Анкеты кончились."""


class NotReady(Exception):
    """Человеку рано в ленту: нет анкеты, согласия или фото."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def gate(user_id: int) -> None:
    """Проверяет, можно ли пускать человека оценивать.

    Порядок проверок = порядок экранов онбординга.
    """
    if not await db.has_consent(user_id):
        raise NotReady("consent")

    prof = await db.get_profile(user_id)
    if prof is None or prof["status"] == "deleted":
        raise NotReady("profile")
    if prof["status"] == "banned":
        raise NotReady("banned")
    if prof["status"] == "draft":
        # Имя и возраст уже есть — значит, человек остановился на фото.
        # Возвращать его в начало анкеты было бы обидно.
        if prof["display_name"] and prof["age"]:
            raise NotReady("photo")
        raise NotReady("profile")
    if prof["status"] == "hidden":
        raise NotReady("hidden")
    if prof["status"] == "awaiting_photo" or prof["needs_reupload"]:
        raise NotReady("reupload")
    if not prof["photo_path"]:
        raise NotReady("photo")

    if config.DAILY_VOTE_LIMIT and not prof["priority"]:
        if await db.votes_today(user_id) >= config.DAILY_VOTE_LIMIT:
            raise NotReady("limit")


async def next_card(user_id: int) -> Card:
    """Возвращает следующую анкету и сразу помечает её показанной."""
    await db.unhide_expired()

    live_left = await db.count_live_available(user_id)
    thin = live_left < config.MIN_LIVE_POOL

    live = await db.live_candidates(user_id, limit=12)
    seeds = await db.seed_candidates(user_id, limit=12) if thin else []

    pick = _choose(live, seeds, thin)
    if pick is None:
        # Живые кончились — пробуем сиды, даже если пул не считался тонким.
        seeds = await db.seed_candidates(user_id, limit=12)
        pick = _choose([], seeds, True)
    if pick is None:
        raise FeedEmpty()

    kind, row = pick

    # Гонка: два запроса могли выхватить одну карточку. Тогда берём следующую.
    if not await db.mark_seen(user_id, kind, _target_id(kind, row)):
        return await next_card(user_id)

    return _to_card(kind, row)


def _choose(live: list[dict], seeds: list[dict], thin: bool):
    if live and seeds:
        use_seed = random.random() < config.SEED_RATIO_WHEN_THIN if thin else False
        pool, kind = (seeds, "seed") if use_seed else (live, "live")
        return kind, pool[0]
    if live:
        return "live", live[0]
    if seeds:
        return "seed", seeds[0]
    return None


def _target_id(kind: str, row: dict) -> int:
    return int(row["user_id"] if kind == "live" else row["id"])


def _to_card(kind: str, row: dict) -> Card:
    if kind == "live":
        return Card(
            kind="live",
            target_id=int(row["user_id"]),
            display_name=row["display_name"],
            age=int(row["age"]),
            photo_url=f"/media/{row['photo_path']}",
            photo_path=row["photo_path"],
            photo_file_id=row["photo_file_id"],
        )
    return Card(
        kind="seed",
        target_id=int(row["id"]),
        display_name=row["display_name"],
        age=int(row["age"]),
        photo_url=f"/seed/{row['file_name']}",
        photo_path=row["file_name"],
    )


async def vote(user_id: int, kind: str, target_id: int, grade: str) -> bool:
    if not grades.is_valid(grade):
        raise ValueError(f"неизвестная оценка: {grade}")
    return await db.add_vote(user_id, kind, target_id, grade, grades.weight(grade))
