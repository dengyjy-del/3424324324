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
from collections.abc import Iterable
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
    # id бота, чьей подписью открыт мини-апп. У зеркал он свой, и по нему
    # приложение подставляет в ссылки «поделиться» того бота, через которого
    # человек реально пришёл. Хранится именно id, а не токен: секрету незачем
    # ездить по коду приложения.
    bot_id: str = ""

    @property
    def display_name(self) -> str:
        full = " ".join(part for part in (self.first_name, self.last_name) if part)
        return full or (f"@{self.username}" if self.username else "аноним")


def _data_check_string(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(pairs))


def verify_init_data(
    init_data: str,
    bot_token: str | Iterable[str],
    max_age: int = MAX_AUTH_AGE,
) -> TelegramUser:
    """
    Проверяет подпись initData и возвращает пользователя.

    Токенов может быть несколько: у каждого зеркала свой, а мини-апп у всех
    один. Какой именно бот открыл приложение, заранее неизвестно — Telegram
    этого в initData не пишет, — поэтому подпись сверяется со всеми токенами
    до первого совпадения. Это дёшево: HMAC на 20 токенов считается за
    доли миллисекунды.
    """
    tokens = [bot_token] if isinstance(bot_token, str) else [t for t in bot_token if t]
    if not tokens:
        raise AuthError("на сервере не настроен ни один токен бота")

    if not init_data:
        raise AuthError("initData пуст")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    received_hash = ""
    with_signature: list[tuple[str, str]] = []
    without_signature: list[tuple[str, str]] = []

    for key, value in pairs:
        if key == "hash":
            received_hash = value
            continue
        with_signature.append((key, value))
        if key != "signature":
            without_signature.append((key, value))

    if not received_hash:
        raise AuthError("в initData нет подписи")

    # Поле signature (Ed25519-подпись для сторонней проверки) Telegram добавил
    # позже самого hash, и в разных версиях клиента оно то участвует в расчёте
    # HMAC, то нет. Оба набора приходят от Telegram, поэтому принимаем любой
    # совпавший — иначе часть пользователей не сможет войти.
    checks = [
        _data_check_string(candidate).encode()
        for candidate in (without_signature, with_signature)
    ]

    matched_token = ""
    for token in tokens:
        secret = hmac.new(
            b"WebAppData", token.strip().encode(), hashlib.sha256
        ).digest()
        for check in checks:
            expected = hmac.new(secret, check, hashlib.sha256).hexdigest()
            # Сравнение постоянного времени: подпись нельзя подобрать по
            # времени ответа сервера.
            if hmac.compare_digest(expected, received_hash):
                matched_token = token
                break
        if matched_token:
            break

    if not matched_token:
        raise AuthError(
            "Подпись Telegram не совпала. Приложение открыто через бота, "
            f"чьего токена нет на сервере (сейчас настроено токенов: "
            f"{len(tokens)}). Добавь токен этого бота в переменную BOT_TOKENS "
            "и передеплой проект."
        )

    fields = dict(with_signature)

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
        bot_id=matched_token.split(":", 1)[0],
    )
