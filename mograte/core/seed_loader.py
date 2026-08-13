"""Загрузка демо-анкет из папки seed_photos.

Два режима:

1. Есть manifest.json — берём имя, возраст и происхождение фото оттуда.
   Так и надо делать: поле source/license фиксирует, на каком основании
   вы показываете это фото людям.

2. Манифеста нет — имя и возраст берутся из имени файла в формате
   `Имя_Возраст.jpg` (например `Алина_23.jpg`). Удобно для быстрого
   старта, но происхождение фото при этом нигде не зафиксировано.

Загрузка идемпотентна: повторный запуск не плодит дубликаты, а фото,
исчезнувшие из папки, снимаются с показа.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, db

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
NAME_AGE_RE = re.compile(r"^(?P<name>.+?)[_\-\s](?P<age>\d{2})$")


def _parse_filename(stem: str) -> tuple[str, int] | None:
    m = NAME_AGE_RE.match(stem.strip())
    if not m:
        return None
    age = int(m.group("age"))
    if not (config.MIN_AGE <= age <= config.MAX_AGE):
        return None
    return m.group("name").strip(), age


async def load(verbose: bool = True) -> dict:
    """Синхронизирует папку с таблицей демо-анкет."""
    folder = config.SEED_DIR
    folder.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in raw if isinstance(raw, list) else raw.get("profiles", []):
                if "file" in item:
                    manifest[item["file"]] = item
        except (ValueError, OSError) as exc:
            if verbose:
                print(f"[seed] манифест не прочитан: {exc}")

    added, updated, skipped = 0, 0, []
    slugs: list[str] = []

    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in IMAGE_EXT or not path.is_file():
            continue

        meta = manifest.get(path.name)
        if meta:
            name = str(meta.get("name", path.stem))
            age = int(meta.get("age", 0))
            gender = meta.get("gender")
            source = meta.get("source")
            license_ = meta.get("license")
        else:
            parsed = _parse_filename(path.stem)
            if not parsed:
                skipped.append(path.name)
                continue
            name, age = parsed
            gender = source = license_ = None

        if not (config.MIN_AGE <= age <= config.MAX_AGE):
            skipped.append(f"{path.name} (возраст {age})")
            continue

        slug = path.name
        slugs.append(slug)
        existed = await db.get_seed_by_slug(slug)
        await db.upsert_seed(
            slug,
            display_name=name,
            age=age,
            gender=gender,
            file_name=path.name,
            source=source,
            license=license_,
            active=1,
        )
        if existed:
            updated += 1
        else:
            added += 1

    await db.deactivate_seeds_except(slugs)

    result = {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "total_active": await db.count_seeds(),
    }

    if verbose:
        print(
            f"[seed] добавлено {added}, обновлено {updated}, "
            f"активно {result['total_active']}"
        )
        if skipped:
            print("[seed] пропущены (не разобрано имя или возраст):")
            for s in skipped:
                print(f"       {s}")
        if not manifest and result["total_active"]:
            print(
                "[seed] манифест не найден — происхождение фото нигде не записано. "
                "См. seed_photos/README.md"
            )
    return result
