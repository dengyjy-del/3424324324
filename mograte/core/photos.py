"""Приём и хранение фотографий анкет.

Фото кладём на диск, потому что мини-апп не умеет открывать
Telegram file_id — браузеру нужен обычный URL. file_id тоже храним,
чтобы бот пересылал фото без повторной заливки.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from . import config

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}


class PhotoError(Exception):
    """Фото не приняли. Текст сообщения показывается пользователю."""


def _ensure_dir() -> None:
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def save_bytes(raw: bytes, user_id: int) -> str:
    """Сохраняет фото и возвращает имя файла относительно MEDIA_DIR."""
    if len(raw) > config.MAX_PHOTO_BYTES:
        mb = config.MAX_PHOTO_BYTES // (1024 * 1024)
        raise PhotoError(f"Файл больше {mb} МБ. Пришлите фото поменьше.")

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 — Pillow бросает разное
        raise PhotoError("Это не похоже на изображение. Пришлите фото в JPEG или PNG.") from exc

    if img.format not in ALLOWED_FORMATS:
        raise PhotoError("Поддерживаются JPEG, PNG и WEBP.")

    # exif_transpose разворачивает снятое на телефон фото по метаданным;
    # заодно EXIF (в том числе геометки) не попадает в сохранённый файл.
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail((config.PHOTO_MAX_SIDE, config.PHOTO_MAX_SIDE), Image.LANCZOS)

    _ensure_dir()
    name = f"{user_id}_{uuid.uuid4().hex[:12]}.jpg"
    img.save(config.MEDIA_DIR / name, "JPEG", quality=88, optimize=True)
    return name


def remove(file_name: str | None) -> None:
    if not file_name:
        return
    path = config.MEDIA_DIR / Path(file_name).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def path_for(file_name: str) -> Path:
    return config.MEDIA_DIR / Path(file_name).name
