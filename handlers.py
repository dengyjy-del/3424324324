"""Хендлеры: команды, приём фото, инлайн-кнопки, режим съёмки."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    Message,
    User,
)

import keyboards
import peer
import rating
import texts
from access import DemoState, SubscriptionGate
from config import Config
from database import BaseDatabase as Database
from rating import generate_report

logger = logging.getLogger(__name__)

router = Router(name="looksmax")

def display_name(user: User | None) -> str:
    if user is None:
        return "аноним"
    if user.username:
        return f"@{user.username}"
    return user.full_name or "аноним"


# ────────────────────────────── команды ────────────────────────────────────


async def _remember(message: Message, db: Database) -> None:
    """Сохраняет @username: без него адресные команды работают только по ID."""
    user = message.from_user
    if user is not None and user.username:
        with contextlib.suppress(Exception):
            await db.remember_username(user.id, user.username)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is not None:
        await db.ensure_user(user.id)
    name = user.first_name if user and user.first_name else "друг"
    await message.answer(
        texts.start(name), reply_markup=keyboards.main_menu(config.webapp_url)
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP, reply_markup=keyboards.back_menu())


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(texts.ABOUT, reply_markup=keyboards.back_menu())


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database) -> None:
    await _send_stats(message, db, message.from_user)


@router.message(Command("ref"))
async def cmd_ref(message: Message, db: Database, config: Config) -> None:
    """
    /ref — свой код, /ref КОД — ввести чужой.

    Логика начисления живёт в webapp.server, чтобы правила в боте и в
    приложении не разъехались.
    """
    from webapp.server import apply_ref_code, make_ref_code

    user = message.from_user
    if user is None:
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) == 1:
        code = await db.get_ref_code(user.id)
        if not code:
            await db.set_ref_code(user.id, make_ref_code(user.id, config.score_salt))
            code = await db.get_ref_code(user.id)
        invited = await db.referral_count(user.id)
        await message.answer(texts.referral(code or "—", invited))
        return

    result = await apply_ref_code(db, user.id, parts[1])
    if result["ok"]:
        await message.answer(texts.ref_applied(result["bonus"]))
    else:
        await message.answer(f"❌ {result['error']}")


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    if message.from_user is not None:
        await message.answer(texts.whoami(message.from_user.id))


@router.message(Command("audience"))
async def cmd_audience(message: Message, db: Database, config: Config) -> None:
    """Портрет аудитории. Только для ID из ADMIN_IDS."""
    user = message.from_user
    if user is None:
        return
    if not config.is_admin(user.id):
        await message.answer(texts.NEED_PHOTO)
        return

    data = await db.audience()
    await message.answer(
        texts.audience_card(
            data.total, data.declared, data.with_reports, data.by_age
        )
    )


@router.message(Command("audience"))
async def cmd_audience(message: Message, db: Database, config: Config) -> None:
    """Сводка по аудитории. Только для ID из ADMIN_IDS."""
    user = message.from_user
    if user is None:
        return

    if not config.admin_ids or not config.is_admin(user.id):
        await message.answer(
            "🚫 Команда только для владельца.\n"
            "Впиши свой ID (его покажет /myid) в переменную ADMIN_IDS."
        )
        return

    days = 7
    since = datetime.now(timezone.utc) - timedelta(days=days)
    await message.answer(texts.audience(await db.audience(since), days))


@router.message(Command("checkgate"))
async def cmd_checkgate(
    message: Message, config: Config, gate_bot: Bot | None = None
) -> None:
    """
    Проверка гейта по шагам. Только для ID из ADMIN_IDS.

    Нужна, потому что при ошибке проверки источник пропускается молча —
    иначе кривой конфиг заблокировал бы всех пользователей разом. Побочный
    эффект: снаружи неработающая проверка выглядит как выключенная.

    Проверяем от имени основного бота — именно он ходит в канал за статусом
    подписки, даже когда команду прислали в зеркало.
    """
    user = message.from_user
    if user is None:
        return

    if not config.admin_ids or not config.is_admin(user.id):
        await message.answer("🚫 Команда только для владельца.")
        return

    checker = gate_bot or message.bot
    sources = [("Канал", config.channel_id), ("Чат", config.chat_id)]
    lines = ["🔎 <b>ПРОВЕРКА ДОСТУПА</b>", texts.LINE]

    if len(config.bot_tokens) > 1:
        try:
            who = await checker.get_me()
            lines.append(
                f"Ботов подключено: <b>{len(config.bot_tokens)}</b> "
                f"(основной + {len(config.bot_tokens) - 1} зеркал)"
            )
            lines.append(f"Подписку проверяет: @{texts.safe(who.username or '—')}")
            lines.append("<i>Админом канала нужен только он.</i>")
            lines.append(texts.LINE)
        except TelegramAPIError:
            pass

    for label, chat_id in sources:
        if not chat_id:
            lines.append(f"{label}: <i>переменная не задана</i>")
            continue

        lines.append(f"<b>{label}</b> — <code>{texts.safe(chat_id)}</code>")
        try:
            chat = await checker.get_chat(chat_id)
            lines.append(f"  ✅ найден: {texts.safe(chat.title or '—')}")
        except TelegramAPIError as error:
            lines.append(f"  ❌ не найден: {texts.safe(str(error)[:90])}")
            continue

        try:
            me = await checker.get_chat_member(chat_id, checker.id)
            ok = me.status in ("administrator", "creator")
            lines.append(
                "  ✅ бот администратор" if ok
                else f"  ❌ бот не админ (статус: {me.status})"
            )
        except TelegramAPIError as error:
            lines.append(f"  ❌ статус бота неизвестен: {texts.safe(str(error)[:70])}")
            continue

        try:
            member = await checker.get_chat_member(chat_id, user.id)
            lines.append(f"  ✅ проверка участника работает (твой статус: {member.status})")
        except TelegramAPIError as error:
            lines.append(f"  ❌ участника не проверить: {texts.safe(str(error)[:70])}")

    # Частая ошибка: в CHAT_ID кладут ID канала. Тогда проверка идёт по
    # одному и тому же месту дважды, и подписка на чат фактически не нужна.
    if config.channel_id and config.chat_id:
        try:
            first = await checker.get_chat(config.channel_id)
            second = await checker.get_chat(config.chat_id)
            if first.id == second.id:
                lines += [
                    texts.LINE,
                    "⚠️ <b>Канал и чат — это одно и то же место.</b>",
                    "В CHAT_ID попал ID канала, поэтому подписка на чат "
                    "не проверяется. Нужен ID группы.",
                ]
        except TelegramAPIError:
            pass

    lines += [
        texts.LINE,
        "<i>Чтобы узнать ID группы: перешли сюда любое сообщение из неё.</i>",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("strict"))
async def cmd_strict(message: Message, db: Database, config: Config) -> None:
    """
    /strict -0.5 — сделать оценки строже на полбалла, /strict 0 — вернуть.

    Значение живёт в базе, поэтому применяется сразу и к боту, и к
    приложению, без передеплоя.
    """
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 1:
        current = await db.get_setting("strictness") or "0"
        await message.answer(texts.strict_now(float(current)))
        return

    try:
        value = float(parts[1].strip().replace(",", "."))
    except ValueError:
        await message.answer(texts.STRICT_USAGE)
        return

    if not -3.0 <= value <= 3.0:
        await message.answer(texts.STRICT_USAGE)
        return

    await db.set_setting("strictness", str(value))
    rating.set_strictness(value)
    await message.answer(texts.strict_set(value))


@router.message(Command("gift"))
async def cmd_gift(message: Message, db: Database, config: Config) -> None:
    """
    /gift 5 — подарить всем пользователям дополнительные попытки на сегодня.

    Работает как надбавка к суточному лимиту, а не как выдача каждому по
    отдельности: так подарок достанется и тем, кто зайдёт позже, а в полночь
    он сам сойдёт на нет вместе со сбросом счётчика.
    """
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    amount = 5
    if len(parts) > 1:
        try:
            amount = int(parts[1].strip())
        except ValueError:
            await message.answer(texts.GIFT_USAGE)
            return

    if not 0 <= amount <= 50:
        await message.answer(texts.GIFT_USAGE)
        return

    today = datetime.now(timezone.utc).date().isoformat()
    await db.set_setting(f"gift_scans:{today}", str(amount))
    await message.answer(texts.gift_done(amount, config.daily_scan_limit))


@router.message(Command("labels"))
async def cmd_labels(message: Message, db: Database, config: Config) -> None:
    """Сколько собрано размеченных примеров. Только для владельца."""
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    # Пробная запись и чтение в одном месте: показывает, где рвётся цепочка
    if (message.text or "").strip().endswith("debug"):
        info = await db.diagnose_labels()
        probe = f"probe:{int(datetime.now(timezone.utc).timestamp())}"
        try:
            written = await db.add_label(probe, 5.0, '{"probe":1}')
            after = await db.diagnose_labels()
            info["probe_written"] = written
            info["rows_after_probe"] = after.get("rows")
        except Exception as error:  # noqa: BLE001
            info["probe_error"] = f"{type(error).__name__}: {str(error)[:100]}"

        lines = ["🔬 <b>ДИАГНОСТИКА РАЗМЕТКИ</b>", texts.LINE]
        if info.get("backend") == "sqlite" and config.webapp_url:
            lines += [
                "⚠️ <b>Бот и приложение на разных базах.</b>",
                "Бот пишет в локальный файл, приложение — в свою базу. "
                "Разметка из приложения сюда не попадёт.",
                texts.LINE,
            ]
        for key, value in info.items():
            shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            lines.append(f"<code>{key}</code>: {texts.safe(shown[:110] or '—')}")
        await message.answer("\n".join(lines))
        return

    # Ошибку показываем, а не прячем: раньше сбой чтения выглядел как
    # «разметки нет», и было непонятно, потерялись данные или нет.
    try:
        stats = await db.label_stats()
        rows = await db.export_labels()
    except Exception as error:  # noqa: BLE001
        logger.exception("Не удалось прочитать разметку")
        await message.answer(
            "⚠️ <b>Не удалось прочитать разметку</b>\n"
            f"<code>{texts.safe(type(error).__name__)}: "
            f"{texts.safe(str(error)[:120])}</code>"
        )
        return

    if not stats["total"]:
        await message.answer(texts.LABELS_EMPTY)
        return

    await message.answer(texts.label_stats(stats))

    # Выгрузка файлом: его можно прислать мне для переобучения модели
    payload = json.dumps(rows, ensure_ascii=False, indent=1).encode()
    await message.answer_document(
        BufferedInputFile(payload, filename="labels.json"),
        caption="Выгрузка разметки. Пришли этот файл для переобучения модели.",
    )


@router.message(Command("referrals"))
async def cmd_referrals(message: Message, db: Database, config: Config) -> None:
    """Сводка по приглашениям. Только для ID из ADMIN_IDS."""
    user = message.from_user
    if user is None:
        return

    if not config.admin_ids or not config.is_admin(user.id):
        await message.answer(
            "🚫 Команда только для владельца.\n"
            "Впиши свой ID (его покажет /myid) в переменную ADMIN_IDS."
        )
        return

    await message.answer(texts.referral_stats(await db.referral_stats(10)))


@router.message(Command("demo_off"))
async def cmd_demo_off(message: Message, demo: DemoState) -> None:
    user = message.from_user
    if user is None:
        return
    if not await demo.is_active(user.id):
        await message.answer(texts.DEMO_NOT_ACTIVE)
        return
    await demo.disable(user.id)
    await message.answer(texts.DEMO_OFF)


# ──────────────────────────────── фото ─────────────────────────────────────


# ═══════════════════ РЕЖИМ ВЗАИМНЫХ ОЦЕНОК ═════════════════════════════════
#
# В боте режим повторяет приложение один в один: анкета, очередь, жалобы.
# Состояние нигде не копится — на serverless между вызовами не сохраняется
# ничего, кроме базы, поэтому анкета создаётся одним сообщением: фото плюс
# подпись «имя, возраст».


def _peer_open(config: Config, user_id: int) -> bool:
    """Кому виден ChadMatch: владельцу, списку PEER_IDS или всем."""
    return config.peer_allowed(user_id)


async def _peer_state_for(db: Database, user_id: int) -> dict:
    profile = await db.peer_profile(user_id)
    used = await db.peer_votes_since(
        user_id, datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    state = {"votes_left": max(0, peer.DAILY_VOTE_LIMIT - used), "profile": None}
    if profile:
        result = await db.peer_result(f"u:{user_id}")
        hidden = profile.get("hidden_until")
        state["profile"] = {
            "name": profile["name"],
            "age": profile["age"],
            "status": profile["status"],
            "hidden_until": hidden.isoformat() if isinstance(hidden, datetime) else hidden,
            "hidden_note": profile.get("hidden_note"),
            "votes": result["count"],
            "average": result["average"],
            "tier": (
                peer.tier_for_score(result["average"]).title
                if result["count"] >= 3 else None
            ),
        }
    return state


@router.message(Command("peer"))
async def cmd_peer(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or not _peer_open(config, user.id):
        return
    await _remember(message, db)
    await message.answer(texts.peer_intro(await _peer_state_for(db, user.id)))


@router.message(Command("peer_delete"))
async def cmd_peer_delete(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or not _peer_open(config, user.id):
        return
    await db.peer_delete(user.id)
    await message.answer(texts.PEER_DELETED)


@router.message(Command("seed"))
async def cmd_seed(message: Message, db: Database, config: Config) -> None:
    """
    /seed on — следующие фото уходят в пул наполнения, /seed off — обратно.
    /seed — сколько снимков в пуле и что видно серверу.
    """
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    from webapp.server import seed_diagnostics

    parts = (message.text or "").split()
    mode = parts[1].lower() if len(parts) > 1 else ""

    if mode in ("on", "вкл"):
        await db.set_setting(f"seedmode:{user.id}", "1")
        await message.answer(texts.SEED_ON)
        return

    if mode in ("off", "выкл"):
        await db.set_setting(f"seedmode:{user.id}", "")
        await message.answer(texts.SEED_OFF)
        return

    if mode in ("clear", "очистить"):
        removed = await db.peer_seed_clear()
        await message.answer(f"🗑 Удалено снимков из пула: <b>{removed}</b>")
        return

    await message.answer(
        texts.seed_status(len(await db.peer_seed_list()), seed_diagnostics())
    )


@router.message(Command("peer_stats"))
async def cmd_peer_stats(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return
    await message.answer(texts.peer_admin_stats(await db.peer_stats()))


def _parse_caption(caption: str) -> tuple[str, int] | None:
    """Разбирает подпись вида «Макс, 19»."""
    parts = [p.strip() for p in (caption or "").replace(";", ",").split(",")]
    if len(parts) < 2:
        return None
    name = peer.clean_name(parts[0])
    digits = "".join(ch for ch in parts[1] if ch.isdigit())
    if not digits:
        return None
    return name, int(digits[:3])


async def _peer_save_from_message(
    message: Message, db: Database, user: User
) -> bool:
    """Создаёт анкету из фото с подписью. True, если получилось."""
    parsed = _parse_caption(message.caption or "")
    if parsed is None:
        await message.answer(texts.PEER_BAD_CAPTION)
        return True

    name, age = parsed
    problem = peer.name_error(name) or peer.age_error(age)
    if problem:
        await message.answer(f"❌ {texts.safe(problem)}")
        return True

    existing = await db.peer_profile(user.id)
    if existing and existing.get("status") == "banned":
        await message.answer(texts.PEER_BANNED_NOTICE)
        return True

    photo = message.photo[-1]
    payload = await message.bot.download(photo.file_id)
    data = payload.read()

    await db.peer_save_profile(
        user.id, name, age, data, photo.file_unique_id, peer.TERMS_VERSION
    )
    await _remember(message, db)
    await message.answer(texts.PEER_SAVED)
    return True


async def _send_next_card(message: Message, db: Database, user_id: int) -> None:
    """Показывает следующую анкету из очереди."""
    profile = await db.peer_profile(user_id)
    if not profile or profile.get("status") != "active":
        await message.answer(texts.PEER_NO_PROFILE)
        return

    used = await db.peer_votes_since(
        user_id,
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
    )
    if used >= peer.DAILY_VOTE_LIMIT:
        await message.answer("Лимит оценок на сегодня исчерпан.")
        return

    rows = await db.peer_next(user_id, limit=1)
    if rows:
        row = rows[0]
        data = await db.peer_photo(row["user_id"])
        if data:
            await message.answer_photo(
                BufferedInputFile(data, filename="p.jpg"),
                caption=texts.peer_card(row["name"], row["age"]),
                reply_markup=keyboards.peer_vote(f"u:{row['user_id']}"),
            )
            return

    # Живых анкет не нашлось — берём снимок из папки наполнения
    seed = await _next_seed(db, user_id)
    if seed is None:
        await message.answer(texts.PEER_EMPTY)
        return

    target, photo, name, age = seed
    await message.answer_photo(
        photo,
        caption=texts.peer_card(name, age),
        reply_markup=keyboards.peer_vote(target),
    )


async def _next_seed(db: Database, user_id: int):
    """
    Снимок наполнения, который этот человек ещё не видел.

    Сначала пул из базы, потом папка репозитория: пул надёжнее, файлы из
    репозитория доезжают до функции не на всех конфигурациях сборки.
    """
    from webapp.server import seed_files, seed_photo_from_folder

    seen = await db.peer_seen(user_id)

    from webapp.server import _filler_identity

    for row in await db.peer_seed_list():
        key = row["key"]
        if f"pool:{key}" in seen:
            continue
        data = await db.peer_seed_photo(key)
        if data:
            name, age = _filler_identity(key, row.get("name"), row.get("age"))
            return f"pool:{key}", BufferedInputFile(data, filename="p.jpg"), name, age

    for file_name in seed_files():
        if f"seed:{file_name}" in seen:
            continue
        data = seed_photo_from_folder(file_name)
        if data:
            name, age = _filler_identity(file_name, None, None)
            return f"seed:{file_name}", BufferedInputFile(data, filename=file_name), name, age

    return None


@router.message(Command("rate"))
async def cmd_rate(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or not _peer_open(config, user.id):
        return
    await _send_next_card(message, db, user.id)


@router.callback_query(F.data == "prate")
async def cb_peer_rate(callback: CallbackQuery, db: Database, config: Config) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None or not _peer_open(config, user.id):
        return
    await _send_next_card(callback.message, db, user.id)


@router.callback_query(F.data == "pnext")
async def cb_peer_next(callback: CallbackQuery, db: Database, config: Config) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None or not _peer_open(config, user.id):
        return
    with contextlib.suppress(TelegramAPIError):
        await callback.message.delete()
    await _send_next_card(callback.message, db, user.id)


@router.callback_query(F.data.startswith("pv:"))
async def cb_peer_vote(callback: CallbackQuery, db: Database, config: Config) -> None:
    user = callback.from_user
    if callback.message is None or not _peer_open(config, user.id):
        await callback.answer()
        return

    _, tier_key, target = callback.data.split(":", 2)
    chosen = peer.TIER_BY_KEY.get(tier_key)
    if chosen is None or target == f"u:{user.id}":
        await callback.answer("Не получилось", show_alert=True)
        return

    used = await db.peer_votes_since(
        user.id,
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
    )
    if used >= peer.DAILY_VOTE_LIMIT:
        await callback.answer("Лимит на сегодня", show_alert=True)
        return

    fresh = await db.peer_vote(user.id, target, chosen.key, chosen.score)
    if fresh:
        with contextlib.suppress(Exception):
            await db.award_xp(user.id, f"peervote:{target}", 1)

    await callback.answer(
        texts.peer_voted(chosen.title, max(0, peer.DAILY_VOTE_LIMIT - used - 1))
    )
    with contextlib.suppress(TelegramAPIError):
        await callback.message.delete()
    await _send_next_card(callback.message, db, user.id)


@router.callback_query(F.data.startswith("pr:"))
async def cb_peer_report(callback: CallbackQuery, config: Config) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None or not _peer_open(config, user.id):
        return
    target = callback.data.split(":", 1)[1]
    with contextlib.suppress(TelegramAPIError):
        await callback.message.edit_reply_markup(
            reply_markup=keyboards.peer_report_reasons(target)
        )


@router.callback_query(F.data.startswith("prr:"))
async def cb_peer_reason(
    callback: CallbackQuery, db: Database, config: Config
) -> None:
    user = callback.from_user
    if callback.message is None or not _peer_open(config, user.id):
        await callback.answer()
        return

    _, reason, target = callback.data.split(":", 2)
    report_id = await db.peer_add_report(user.id, target, reason)

    from webapp.server import notify_report

    await notify_report(callback.message.bot, db, config, report_id, user.id, target, reason)
    await callback.answer(texts.PEER_REPORT_SENT, show_alert=True)
    with contextlib.suppress(TelegramAPIError):
        await callback.message.delete()
    await _send_next_card(callback.message, db, user.id)


# ─────────────────────────── модерация жалоб ───────────────────────────────


@router.callback_query(F.data.startswith("rep:"))
async def cb_moderate(callback: CallbackQuery, db: Database, config: Config) -> None:
    """Решение по жалобе. Доступно только владельцу."""
    user = callback.from_user
    if not config.admin_ids or not config.is_admin(user.id):
        await callback.answer("Только для модератора", show_alert=True)
        return

    _, action, report_id, target = callback.data.split(":", 3)
    owner_id: int | None = None
    if target.startswith("u:"):
        with contextlib.suppress(ValueError):
            owner_id = int(target[2:])

    if action == "keep":
        await db.peer_close_report(int(report_id), "kept")
        await callback.answer("Оставлено")
    elif action == "del" and owner_id:
        await db.peer_set_status(owner_id, "banned", None, "Нарушение правил")
        await db.peer_close_report(int(report_id), "banned")
        with contextlib.suppress(TelegramAPIError):
            await callback.message.bot.send_message(owner_id, texts.PEER_BANNED_NOTICE)
        await callback.answer("Анкета удалена", show_alert=True)
    elif action == "hide" and owner_id:
        until = datetime.now(timezone.utc) + timedelta(hours=peer.HIDE_HOURS)
        note = "Жалоба подтверждена модератором."
        await db.peer_set_status(owner_id, "hidden", until, note)
        await db.peer_close_report(int(report_id), "hidden")
        with contextlib.suppress(TelegramAPIError):
            await callback.message.bot.send_message(
                owner_id, texts.peer_hidden_notice(peer.HIDE_HOURS, note)
            )
        await callback.answer("Скрыто на 24 часа", show_alert=True)
    else:
        await callback.answer("Снимок из папки — анкеты нет", show_alert=True)
        await db.peer_close_report(int(report_id), "kept")

    with contextlib.suppress(TelegramAPIError):
        await callback.message.edit_reply_markup(reply_markup=None)


# ───────────────── адресная выдача попыток ─────────────────────────────────


@router.message(Command("give"))
async def cmd_give(message: Message, db: Database, config: Config) -> None:
    """/give <id|@username> <n> — попытки конкретному человеку."""
    user = message.from_user
    if user is None or not config.admin_ids or not config.is_admin(user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(texts.GIVE_USAGE)
        return

    who_raw, amount_raw = parts[1], parts[2]
    try:
        amount = int(amount_raw)
    except ValueError:
        await message.answer(texts.GIVE_USAGE)
        return

    if not 1 <= amount <= 50:
        await message.answer(texts.GIVE_USAGE)
        return

    target_id: int | None = None
    if who_raw.lstrip("-").isdigit():
        target_id = int(who_raw)
    else:
        target_id = await db.user_by_username(who_raw.lstrip("@"))

    if target_id is None:
        await message.answer(texts.GIVE_NOT_FOUND)
        return

    today = datetime.now(timezone.utc).date().isoformat()
    # Ключ содержит дату и порядковый номер: те же записи, что и у покупок,
    # поэтому подарок сам исчезает вместе со сбросом лимита в полночь.
    already = await db.count_purchases(target_id, f"scan:{today}:")
    for index in range(amount):
        await db.purchase(target_id, f"scan:{today}:{already + index + 1}", 0)

    await message.answer(texts.give_done(who_raw, amount))
    with contextlib.suppress(TelegramAPIError):
        await message.bot.send_message(
            target_id,
            f"🎁 Тебе начислено <b>{amount}</b> дополнительных отчётов на сегодня.",
        )


@router.message(F.photo)
async def handle_photo(
    message: Message, db: Database, config: Config, demo: DemoState
) -> None:
    """
    Обычным пользователям бот оценку не считает.

    Замеры лица снимаются по 478 точкам в браузере мини-аппа, и повторить
    их на стороне бота нельзя: библиотека распознавания не помещается в
    serverless-функцию. Пока бот считал по своей формуле, одно фото давало
    в боте и в приложении разные баллы — из-за этого к оценкам и теряли
    доверие. Поэтому источник оценки один.

    Исключение — режим съёмки: там баллы заранее известны и от замеров не
    зависят, так что для роликов бот отвечает прямо в чате.
    """
    user = message.from_user
    if user is None:
        return

    if message.media_group_id and not await db.claim_album(message.media_group_id):
        return

    # Пополнение пула наполнения: включается через /seed on
    if config.is_admin(user.id) and await db.get_setting(f"seedmode:{user.id}"):
        await _add_to_seed(message, db, user)
        return

    if await demo.is_active(user.id):
        # Подпись различает два сценария съёмки: с ней снимаем карточку
        # «тебя оценили», без неё — обычный разбор по фото.
        if (message.caption or "").strip():
            await _demo_peer_card(message, db, user)
        else:
            await _demo_report(message, user, config)
        return

    # Анкета режима оценок: фото с подписью «имя, возраст»
    if _peer_open(config, user.id) and (message.caption or "").strip():
        if await _peer_save_from_message(message, db, user):
            return

    if config.webapp_url:
        await message.answer(
            texts.PHOTO_TO_APP,
            reply_markup=keyboards.open_app(config.webapp_url),
        )
    else:
        await message.answer(texts.PHOTO_NO_APP)


async def _add_to_seed(message: Message, db: Database, user: User) -> None:
    """Кладёт присланное фото в пул наполнения."""
    photo = message.photo[-1]
    payload = await message.bot.download(photo.file_id)
    data = payload.read()

    key = hashlib.sha256(data).hexdigest()[:32]
    parsed = peer.parse_identity(message.caption or "")
    name, age = parsed if parsed else (None, None)

    fresh = await db.peer_seed_add(key, data, user.id, name, age)
    total = len(await db.peer_seed_list())

    # Альбом присылают пачкой — отвечаем коротко, чтобы не спамить
    await message.answer(
        f"{'✅ Добавлено' if fresh else '↩️ Уже было'} · в пуле: <b>{total}</b>"
    )


async def _demo_peer_card(message: Message, db: Database, user: User) -> None:
    """
    Режим съёмки для раздела оценок: показывает, как выглядит уведомление
    «тебя оценили». Ник берётся из подписи к фото, оценка — кнопкой.
    """
    nick = texts.safe((message.caption or "").strip()[:32])
    photo_id = message.photo[-1].file_id

    # Callback не вмещает file_id целиком, поэтому кладём его в настройки.
    await db.set_setting(
        f"demopeer:{user.id}", json.dumps({"photo": photo_id, "nick": nick})
    )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    line = []
    for tier in peer.PEER_TIERS:
        line.append(
            InlineKeyboardButton(
                text=f"{tier.emoji} {tier.title}", callback_data=f"dpc:{tier.key}"
            )
        )
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    await message.answer(
        texts.peer_demo_ask(nick),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("dpc:"))
async def cb_demo_peer_card(
    callback: CallbackQuery, db: Database, config: Config, demo: DemoState
) -> None:
    user = callback.from_user
    if callback.message is None or not await demo.is_active(user.id):
        await callback.answer()
        return

    tier = peer.TIER_BY_KEY.get(callback.data.split(":", 1)[1])
    raw = await db.get_setting(f"demopeer:{user.id}")
    if tier is None or not raw:
        await callback.answer("Отправь фото заново", show_alert=True)
        return

    saved = json.loads(raw)
    await callback.answer()
    with contextlib.suppress(TelegramAPIError):
        await callback.message.delete()

    me = await callback.message.bot.get_me()
    await callback.message.answer_photo(
        saved["photo"],
        caption=texts.peer_demo_card(saved["nick"], tier.title),
        reply_markup=keyboards.peer_demo_card(me.username or ""),
    )


@router.callback_query(F.data == "demo:rate")
async def cb_demo_rate(callback: CallbackQuery) -> None:
    """Кнопка на карточке съёмки: живого действия за ней нет."""
    await callback.answer("Открой приложение, чтобы оценить в ответ")


async def _demo_report(message: Message, user: User, config: Config) -> None:
    """Отчёт в чате для записи роликов: анимация, затем карточка."""
    photo_id = message.photo[-1].file_unique_id
    report = generate_report(user.id, photo_id, config.score_salt, rating.DEMO)

    with contextlib.suppress(TelegramAPIError):
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    label, percent = texts.SCAN_STAGES[0]
    status = await message.answer(texts.scan_frame(label, percent))

    for label, percent in texts.SCAN_STAGES[1:]:
        await asyncio.sleep(config.scan_delay)
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text(texts.scan_frame(label, percent))

    await asyncio.sleep(config.scan_delay)

    card = texts.report_card(report, display_name(user), show_header=False)
    markup = keyboards.report_menu(photo_id, demo=True)
    try:
        await status.edit_text(card, reply_markup=markup)
    except TelegramBadRequest:
        await message.answer(card, reply_markup=markup)


@router.my_chat_member()
async def on_added(event: ChatMemberUpdated, config: Config) -> None:
    """
    Когда бота добавляют в группу или канал, он сразу присылает владельцу ID.

    Это надёжнее пересылки: в чате обсуждений посты канала появляются
    автоматическим репостом, и Telegram указывает источником канал, а не сам
    чат. Именно из-за этого в CHAT_ID легко попадает ID канала.
    """
    chat = event.chat
    if chat.type == "private" or not config.admin_ids:
        return

    status = event.new_chat_member.status
    if status not in ("member", "administrator"):
        return

    kind = "канал" if chat.type == "channel" else "группа"
    note = (
        "Впиши в CHANNEL_ID" if chat.type == "channel" else "Впиши в CHAT_ID"
    )
    text = (
        f"➕ <b>Бот добавлен: {texts.safe(chat.title or '—')}</b>\n"
        f"Тип: {kind}\n"
        f"ID: <code>{chat.id}</code>\n\n"
        f"<i>{note}. Статус бота: {status}."
        + ("" if status == "administrator" else " Для проверки подписки нужны права администратора.")
        + "</i>"
    )

    for admin_id in config.admin_ids:
        with contextlib.suppress(TelegramAPIError):
            await event.bot.send_message(admin_id, text)


@router.message(F.forward_origin)
async def cmd_whereis(message: Message, config: Config) -> None:
    """Показывает ID источника пересланного сообщения. Только для владельца."""
    user = message.from_user
    if user is None or not config.is_admin(user.id):
        return

    origin = message.forward_origin
    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)

    if chat is None:
        await message.answer(
            "Это сообщение переслано от пользователя, а не из чата.\n"
            "<i>Перешли сообщение, отправленное в самой группе.</i>"
        )
        return

    kind = "канал" if chat.type == "channel" else "группа"
    hint = (
        "<i>Это канал. Для CHAT_ID нужен чат обсуждений — но посты канала "
        "попадают туда репостом, и пересылка такого сообщения всегда покажет "
        "канал. Надёжнее: открой чат в web.telegram.org — ID будет в адресной "
        "строке, либо добавь бота в чат заново, и он пришлёт ID сам.</i>"
        if chat.type == "channel"
        else "<i>Для проверки подписки на группу впиши это число в CHAT_ID.</i>"
    )
    await message.answer(
        f"🆔 <b>{texts.safe(chat.title or '—')}</b>\n"
        f"Тип: {kind}\n"
        f"ID: <code>{chat.id}</code>\n\n" + hint
    )


@router.message(F.document)
async def handle_document(message: Message) -> None:
    mime = (message.document.mime_type or "") if message.document else ""
    if mime.startswith("image/"):
        await message.answer(texts.DOC_AS_PHOTO)
    else:
        await message.answer(texts.NEED_PHOTO)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, config: Config, demo: DemoState) -> None:
    text = (message.text or "").strip()

    if config.demo_code and text == config.demo_code:
        await _toggle_demo(message, config, demo)
        return

    await message.answer(
        texts.NEED_PHOTO, reply_markup=keyboards.main_menu(config.webapp_url)
    )


@router.message()
async def handle_other(message: Message) -> None:
    await message.answer(texts.NEED_PHOTO)


# ─────────────────────────────── колбэки ───────────────────────────────────


@router.callback_query(F.data == keyboards.CHECK_SUB)
async def cb_check_sub(
    callback: CallbackQuery, gate: SubscriptionGate, config: Config
) -> None:
    user = callback.from_user
    if user is None or callback.message is None:
        await callback.answer()
        return

    gate.forget(user.id)

    if await gate.is_member(callback.bot, user.id):
        await callback.answer("Доступ открыт", show_alert=False)
        await callback.message.answer(
            texts.GATE_PASSED, reply_markup=keyboards.main_menu(config.webapp_url)
        )
    else:
        await callback.answer(texts.GATE_FAILED, show_alert=True)


@router.callback_query(F.data.startswith(keyboards.DETAILS_PREFIX))
async def cb_details(
    callback: CallbackQuery, config: Config, demo: DemoState
) -> None:
    await callback.answer()
    if callback.message is None or callback.data is None or callback.from_user is None:
        return

    photo_id = callback.data[len(keyboards.DETAILS_PREFIX) :]
    in_demo = await demo.is_active(callback.from_user.id)
    profile = rating.DEMO if in_demo else rating.NORMAL

    report = generate_report(
        callback.from_user.id, photo_id, config.score_salt, profile
    )

    await callback.message.answer(
        texts.report_details(report, config.score_salt, show_header=not in_demo),
        reply_markup=keyboards.back_menu() if in_demo else keyboards.details_menu(),
    )


@router.callback_query(F.data == "ref")
async def cb_ref(callback: CallbackQuery, db: Database, config: Config) -> None:
    from webapp.server import make_ref_code

    await callback.answer()
    user = callback.from_user
    if callback.message is None or user is None:
        return

    code = await db.get_ref_code(user.id)
    if not code:
        await db.set_ref_code(user.id, make_ref_code(user.id, config.score_salt))
        code = await db.get_ref_code(user.id)

    await callback.message.answer(
        texts.referral(code or "—", await db.referral_count(user.id)),
        reply_markup=keyboards.back_menu(),
    )


@router.callback_query(F.data == "refenter")
async def cb_ref_enter(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    if await db.referrer_of(callback.from_user.id) is not None:
        await callback.message.answer(texts.REF_ALREADY, reply_markup=keyboards.back_menu())
        return

    await callback.message.answer(texts.REF_HOWTO, reply_markup=keyboards.back_menu())


@router.callback_query(F.data == "peer")
async def cb_peer(callback: CallbackQuery, db: Database, config: Config) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None:
        return

    if not _peer_open(config, user.id):
        await callback.message.answer(texts.PEER_CLOSED)
        return

    await callback.message.answer(
        texts.peer_intro(await _peer_state_for(db, user.id)),
        reply_markup=keyboards.peer_menu(config.webapp_url),
    )


@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    if callback.message is not None:
        await _send_stats(callback.message, db, callback.from_user)


@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(texts.ABOUT, reply_markup=keyboards.back_menu())


@router.callback_query(F.data == "howto")
async def cb_howto(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(texts.HELP, reply_markup=keyboards.back_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, config: Config) -> None:
    await callback.answer()
    if callback.message is None:
        return
    name = (callback.from_user.first_name if callback.from_user else None) or "друг"
    await callback.message.answer(
        texts.start(name), reply_markup=keyboards.main_menu(config.webapp_url)
    )


# ─────────────────────────────── хелперы ───────────────────────────────────


async def _toggle_demo(message: Message, config: Config, demo: DemoState) -> None:
    user = message.from_user
    if user is None:
        return

    # Убираем код из чата — чтобы он не попал в кадр при записи экрана.
    with contextlib.suppress(TelegramAPIError):
        await message.delete()

    if not config.demo_allowed_for(user.id):
        await message.answer(texts.DEMO_DENIED)
        return

    if await demo.is_active(user.id):
        await message.answer(texts.demo_still_on(await demo.minutes_left(user.id)))
        return

    minutes = await demo.enable(user.id)
    await message.answer(texts.demo_on(minutes))


async def _send_stats(target: Message, db: Database, user: User | None) -> None:
    if user is None:
        return

    stats = await db.get_stats(user.id)
    if stats is None:
        await target.answer(texts.NO_STATS, reply_markup=keyboards.back_menu())
        return

    await target.answer(
        texts.user_stats(
            display_name(user), stats.count, stats.best, stats.average, stats.last
        ),
        reply_markup=keyboards.back_menu(),
    )
