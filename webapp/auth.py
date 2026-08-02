"""
Авторизация мини-аппа через Telegram initData.

Telegram подписывает данные пользователя ключом, производным от токена бота.
Проверка подписи доказывает, что запрос пришёл из настоящего Telegram, а
user_id не подделан. Это отменяет необходимость в логинах, паролях и почте:
пользователь уже аутентифицирован мессенджером.

Алгоритм — из документации Telegram Mini Apps:
    secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
    hash   = HMAC_SHA256(key=secret, msg=data_check_string)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

# initData считается протухшим через сутки — защита от переигрывания
# перехваченной строки.
MAX_AUTH_AGE = 86_400


class AuthError(Exception):
    """initData не прошёл проверку."""


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str
    username: str
    photo_url: str
    language_code: str

    @property
    def display_name(self) -> str:
        full = " ".join(part for part in (self.first_name, self.last_name) if part)
        return full or (f"@{self.username}" if self.username else "аноним")


def _data_check_string(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(pairs))


def verify_init_data(init_data: str, bot_token: str, max_age: int = MAX_AUTH_AGE) -> TelegramUser:
    """Проверяет подпись initData и возвращает пользователя."""
    if not init_data:
        raise AuthError("initData пуст")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    received_hash = ""
    payload: list[tuple[str, str]] = []

    for key, value in pairs:
        if key == "hash":
            received_hash = value
        elif key != "signature":
            payload.append((key, value))

    if not received_hash:
        raise AuthError("в initData нет подписи")

    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret, _data_check_string(payload).encode(), hashlib.sha256
    ).hexdigest()

    # Сравнение постоянного времени — чтобы подпись нельзя было подобрать
    # по времени ответа сервера.
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("подпись не совпала")

    fields = dict(payload)

    auth_date = fields.get("auth_date", "")
    if not auth_date.isdigit():
        raise AuthError("нет даты авторизации")
    if time.time() - int(auth_date) > max_age:
        raise AuthError("сессия устарела, перезапусти приложение")

    try:
        user = json.loads(fields.get("user", "{}"))
    except json.JSONDecodeError as error:
        raise AuthError("не разобрать данные пользователя") from error

    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise AuthError("нет id пользователя")

    return TelegramUser(
        id=user_id,
        first_name=str(user.get("first_name") or ""),
        last_name=str(user.get("last_name") or ""),
        username=str(user.get("username") or ""),
        photo_url=str(user.get("photo_url") or ""),
        language_code=str(user.get("language_code") or ""),
    )
