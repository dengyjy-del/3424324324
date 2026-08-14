"""Приём и хранение фотографий анкет.

Фото лежат в базе, а не на диске. Причина: на Vercel файловая система
доступна только для чтения, а /tmp живёт до конца одного вызова функции.
Запись на диск там падает с OSError, ответ уходил 500 без JSON — и в
интерфейсе появлялось общее «Не удалось загрузить» вместо причины.

База у раздела та же, что у основного бота, поэтому фото переживают
деплой и одинаково видны боту и мини-аппу.
"""
from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from . import config

log = logging.getLogger(__name__)

# MPO отдают некоторые камеры вместо обычного JPEG.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO", "GIF", "BMP", "TIFF"}

# HEIC с айфона Pillow без плагина не читает. Подключаем pillow-heif,
# если он есть; иначе даём человеку понятный совет.
_HEIF_OK = False
try:  # pragma: no cover — зависит от окружения
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HEIF_OK = True
    ALLOWED_FORMATS.update({"HEIF", "HEIC"})
except Exception:  # noqa: BLE001 — плагина может не быть, это нормально
    pass


class PhotoError(Exception):
    """Фото не приняли. Текст показывается пользователю как есть."""


def _looks_like_heic(raw: bytes) -> bool:
    head = raw[:32]
    return b"ftyp" in head and any(
        tag in head for tag in (b"heic", b"heix", b"hevc", b"mif1", b"heim")
    )


def process(raw: bytes) -> bytes:
    """Проверяет и нормализует фото, возвращает готовый JPEG.

    На диск ничего не пишет — вызывающий сам решает, куда положить.
    """
    if not raw:
        raise PhotoError("Файл пустой. Попробуйте выбрать фото ещё раз.")

    if len(raw) > config.MAX_PHOTO_BYTES:
        mb = config.MAX_PHOTO_BYTES // (1024 * 1024)
        raise PhotoError(f"Файл больше {mb} МБ. Выберите фото поменьше.")

    if _looks_like_heic(raw) and not _HEIF_OK:
        raise PhotoError(
            "Это фото в формате HEIC — такие отдаёт айфон. В настройках "
            "камеры выберите «Наиболее совместимый» либо пришлите скриншот."
        )

    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        img = Image.open(io.BytesIO(raw))
    except PhotoError:
        raise
    except Exception as exc:  # noqa: BLE001 — Pillow бросает разное
        raise PhotoError(
            "Не удалось прочитать изображение. Пришлите фото в JPEG или PNG."
        ) from exc

    if img.format not in ALLOWED_FORMATS:
        raise PhotoError(
            f"Формат {img.format or 'неизвестный'} не поддерживается. "
            "Подойдёт JPEG, PNG или WEBP."
        )

    # exif_transpose разворачивает снимок по метаданным ориентации,
    # а заодно EXIF (включая геометки) не попадает в результат.
    img = ImageOps.exif_transpose(img) or img
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((config.PHOTO_MAX_SIDE, config.PHOTO_MAX_SIDE), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


def new_name(user_id: int) -> str:
    return f"{user_id}_{uuid.uuid4().hex[:12]}.jpg"


async def save(raw: bytes, user_id: int) -> str:
    """Проверяет фото и кладёт в базу. Возвращает имя для ссылки."""
    from . import db

    data = process(raw)
    name = new_name(user_id)
    await db.save_photo(name, user_id, data)
    return name


async def load(name: str) -> bytes | None:
    from . import db

    return await db.load_photo(name)


async def remove(name: str | None) -> None:
    if not name:
        return
    from . import db

    try:
        await db.delete_photo(name)
    except Exception:  # noqa: BLE001 — чистка не должна ронять основной поток
        log.warning("не удалось удалить фото %s", name)


# ── Демо-анкеты ────────────────────────────────────────────────────────────
# Остаются файлами в репозитории: они попадают в сборку и доступны
# на чтение даже на serverless.

def seed_path(file_name: str) -> Path:
    return config.SEED_DIR / Path(file_name).name


def read_seed(file_name: str) -> bytes | None:
    try:
        return seed_path(file_name).read_bytes()
    except OSError:
        return None
