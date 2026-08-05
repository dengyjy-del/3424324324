"""Хендлеры: команды, приём фото, инлайн-кнопки, режим съёмки."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, User

import keyboards
import rating
import texts
from access import DemoState, SubscriptionGate
from config import Config
from database import BaseDatabase as Database
from rating import generate_report

router = Router(name="looksmax")

def display_name(user: User | None) -> str:
    if user is None:
        return "аноним"
    if user.username:
        return f"@{user.username}"
    return user.full_name or "аноним"


# ────────────────────────────── команды ────────────────────────────────────


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


@router.message(F.photo)
async def handle_photo(
    message: Message, db: Database, config: Config, demo: DemoState
) -> None:
    """
    Бот больше не считает оценку сам.

    Замеры лица вычисляются по 478 точкам прямо в браузере мини-аппа, и
    воспроизвести их на стороне бота невозможно: библиотека распознавания
    весит больше, чем помещается в serverless-функцию. Пока бот считал по
    своей формуле, одно и то же фото давало в боте и в приложении разные
    баллы — а это ровно то, из-за чего к оценкам теряют доверие.

    Поэтому источник оценки теперь один — приложение.
    """
    user = message.from_user
    if user is None:
        return

    if message.media_group_id and not await db.claim_album(message.media_group_id):
        return

    if config.webapp_url:
        await message.answer(
            texts.PHOTO_TO_APP,
            reply_markup=keyboards.open_app(config.webapp_url),
        )
    else:
        await message.answer(texts.PHOTO_NO_APP)


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
