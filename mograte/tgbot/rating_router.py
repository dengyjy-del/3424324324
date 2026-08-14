"""Роутер режима оценивания для aiogram 3.

Подключение в существующем боте:

    from mograte.tgbot.rating_router import router as rating_router
    dp.include_router(rating_router)
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message

from ..core import config, db, feed, grades, moderation, photos, texts
from . import keyboards as kb
from .notify import send_report_to_mods

log = logging.getLogger(__name__)
router = Router(name="mograte.rating")


class Reg(StatesGroup):
    name = State()
    age = State()
    gender = State()
    photo = State()
    report_comment = State()
    edit_name = State()
    edit_photo = State()


# --- Вход -----------------------------------------------------------------

@router.message(Command("rate"))
@router.message(F.text == "Оценивать")
async def cmd_rate(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _route(message, message.from_user.id, state)


@router.message(Command("myprofile"))
@router.message(F.text == "Моя анкета")
async def cmd_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_profile(message, message.from_user.id)


async def _route(message: Message, user_id: int, state: FSMContext) -> None:
    """Ведёт человека по онбордингу до ленты."""
    try:
        await feed.gate(user_id)
    except feed.NotReady as nr:
        await _handle_not_ready(message, user_id, state, nr.reason)
        return
    await send_next_card(message, user_id)


async def _handle_not_ready(
    message: Message, user_id: int, state: FSMContext, reason: str
) -> None:
    if reason == "consent":
        await _ask_consent(message)
        return
    if reason == "profile":
        await _start_registration(message, state)
        return
    if reason in {"photo", "reupload"}:
        await state.set_state(Reg.photo)
        await message.answer(texts.NOT_READY[reason] + "\n\nПришлите фото одним сообщением.")
        return
    if reason == "hidden":
        prof = await db.get_profile(user_id)
        until = prof["hidden_until"] if prof else 0
        extra = ""
        if until:
            hours = max(1, (until - db.now()) // 3600)
            extra = f"\n\nОсталось примерно {hours} ч."
        await message.answer(texts.NOT_READY["hidden"] + extra)
        return
    await message.answer(texts.NOT_READY.get(reason, "Режим сейчас недоступен."))


# --- Согласие -------------------------------------------------------------

async def _ask_consent(message: Message) -> None:
    body = f"<b>{texts.DISCLAIMER_TITLE}</b>\n\n{texts.DISCLAIMER_BODY}"
    links = texts.disclaimer_links()
    if links:
        body += f"\n\n{links}"
    await message.answer(body, reply_markup=kb.consent_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "rate:consent:yes")
async def on_consent_yes(call: CallbackQuery, state: FSMContext) -> None:
    await db.save_consent(call.from_user.id, source="bot")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Условия приняты")
    prof = await db.get_profile(call.from_user.id)
    if prof is None or prof["status"] == "draft":
        await _start_registration(call.message, state)
    else:
        await _route(call.message, call.from_user.id, state)


@router.callback_query(F.data == "rate:consent:no")
async def on_consent_no(call: CallbackQuery) -> None:
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Без согласия режим оценок недоступен. Остальные функции бота работают как обычно."
    )
    await call.answer()


# --- Регистрация анкеты ---------------------------------------------------

async def _start_registration(message: Message, state: FSMContext) -> None:
    await state.set_state(Reg.name)
    await message.answer(
        "Создаём анкету.\n\nКак вас показывать другим? Напишите имя — "
        f"его увидят все, кто будет оценивать (от {config.NAME_MIN_LEN} "
        f"до {config.NAME_MAX_LEN} символов).",
        reply_markup=kb.remove(),
    )


@router.message(StateFilter(Reg.name), F.text)
async def reg_name(message: Message, state: FSMContext) -> None:
    ok, result = texts.validate_name(message.text)
    if not ok:
        await message.answer(result)
        return
    await state.update_data(display_name=result)
    await state.set_state(Reg.age)
    await message.answer(f"Сколько вам лет? Режим доступен с {config.MIN_AGE}.")


@router.message(StateFilter(Reg.age), F.text)
async def reg_age(message: Message, state: FSMContext) -> None:
    ok, result = texts.validate_age(message.text)
    if not ok:
        await message.answer(str(result))
        return
    await state.update_data(age=int(result))
    await state.set_state(Reg.gender)
    await message.answer("Ваш пол?", reply_markup=kb.gender_kb())


@router.callback_query(StateFilter(Reg.gender), F.data.startswith("rate:gender:"))
async def reg_gender(call: CallbackQuery, state: FSMContext) -> None:
    gender = call.data.split(":")[-1]
    await state.update_data(gender=gender)
    await state.set_state(Reg.photo)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Теперь фото для анкеты.\n\n"
        "Пришлите снимок, на котором видно ваше лицо. "
        "Только своё фото — чужие снимки и изображения других людей запрещены."
    )
    await call.answer()


@router.message(StateFilter(Reg.photo, Reg.edit_photo), F.photo)
async def reg_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    photo = message.photo[-1]
    try:
        raw = await _download(bot, photo.file_id)
        file_name = await photos.save(raw, message.from_user.id)
    except photos.PhotoError as exc:
        await message.answer(str(exc))
        return
    except Exception:  # noqa: BLE001
        log.exception("не удалось сохранить фото")
        await message.answer("Не получилось сохранить фото. Попробуйте другое.")
        return

    current = await state.get_state()
    prof = await db.get_profile(message.from_user.id)

    if prof and prof["photo_path"]:
        await photos.remove(prof["photo_path"])

    if current == Reg.edit_photo.state or prof:
        await db.upsert_profile(
            message.from_user.id,
            photo_file_id=photo.file_id,
            photo_path=file_name,
            status="active",
            needs_reupload=0,
            hidden_until=0,
        )
    else:
        data = await state.get_data()
        await db.upsert_profile(
            message.from_user.id,
            display_name=data.get("display_name", ""),
            age=data.get("age", 0),
            gender=data.get("gender"),
            photo_file_id=photo.file_id,
            photo_path=file_name,
            status="active",
            needs_reupload=0,
        )

    await state.clear()
    await message.answer(
        "Анкета готова и участвует в показах.\n\n"
        "Теперь можно оценивать других. Каждая анкета показывается один раз.",
        reply_markup=kb.main_kb(),
    )
    await send_next_card(message, message.from_user.id)


@router.message(StateFilter(Reg.photo, Reg.edit_photo))
async def reg_photo_wrong(message: Message) -> None:
    await message.answer(
        "Нужно именно фото. Отправьте изображение как фотографию, а не файлом или текстом."
    )


async def _download(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


# --- Лента ----------------------------------------------------------------

async def send_next_card(message: Message, user_id: int) -> None:
    try:
        await feed.gate(user_id)
    except feed.NotReady as nr:
        await message.answer(texts.NOT_READY.get(nr.reason, "Режим сейчас недоступен."))
        return

    try:
        card = await feed.next_card(user_id)
    except feed.FeedEmpty:
        await message.answer(texts.FEED_EMPTY, reply_markup=kb.main_kb())
        return

    caption = f"<b>{_esc(card.display_name)}</b>, {card.age}"
    markup = kb.card_kb(card.kind, card.target_id)

    if card.kind == "live" and card.photo_file_id:
        try:
            await message.answer_photo(card.photo_file_id, caption=caption, reply_markup=markup)
            return
        except Exception:  # noqa: BLE001 — file_id мог протухнуть
            log.warning("file_id не сработал, отправляю файл с диска")

    data = (
        await photos.load(card.photo_path)
        if card.kind == "live"
        else photos.read_seed(card.photo_path)
    )
    if data is None:
        log.error("фото анкеты не найдено: %s/%s", card.kind, card.target_id)
        await send_next_card(message, user_id)
        return
    await message.answer_photo(
        BufferedInputFile(data, filename="p.jpg"), caption=caption, reply_markup=markup
    )


@router.callback_query(F.data.startswith("rate:v:"))
async def on_vote(call: CallbackQuery) -> None:
    _, _, kind, target_id, grade = call.data.split(":")
    if not grades.is_valid(grade):
        await call.answer("Неизвестная оценка")
        return

    fresh = await feed.vote(call.from_user.id, kind, int(target_id), grade)
    await call.answer(grades.label(grade) if fresh else "Уже оценено")

    try:
        await call.message.edit_caption(
            caption=f"{call.message.caption}\n\nВаша оценка: <b>{grades.label(grade)}</b>",
            reply_markup=None,
        )
    except Exception:  # noqa: BLE001 — подпись могла не измениться
        await call.message.edit_reply_markup(reply_markup=None)

    await send_next_card(call.message, call.from_user.id)


@router.callback_query(F.data.startswith("rate:skip:"))
async def on_skip(call: CallbackQuery) -> None:
    await call.answer("Пропущено")
    await call.message.edit_reply_markup(reply_markup=None)
    await send_next_card(call.message, call.from_user.id)


@router.callback_query(F.data == "rate:next")
async def on_next(call: CallbackQuery) -> None:
    await call.answer()
    await send_next_card(call.message, call.from_user.id)


# --- Жалобы ---------------------------------------------------------------

@router.callback_query(F.data.startswith("rate:rep:"))
async def on_report_start(call: CallbackQuery) -> None:
    _, _, kind, target_id = call.data.split(":")
    if await db.already_reported(call.from_user.id, kind, int(target_id)):
        await call.answer("Вы уже жаловались на эту анкету", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=kb.report_reasons_kb(kind, int(target_id)))
    await call.answer("Выберите причину")


@router.callback_query(F.data.startswith("rate:repcancel:"))
async def on_report_cancel(call: CallbackQuery) -> None:
    _, _, kind, target_id = call.data.split(":")
    await call.message.edit_reply_markup(reply_markup=kb.card_kb(kind, int(target_id)))
    await call.answer()


@router.callback_query(F.data.startswith("rate:repr:"))
async def on_report_reason(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    _, _, kind, target_id, reason = call.data.split(":")
    await state.set_state(Reg.report_comment)
    await state.update_data(rep_kind=kind, rep_target=int(target_id), rep_reason=reason)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await call.message.answer(
        "Опишите проблему одним сообщением — это поможет модератору.\n\n"
        "Если добавить нечего, напишите «-»."
    )


@router.message(StateFilter(Reg.report_comment), F.text)
async def on_report_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()

    comment = message.text.strip()
    if comment in {"-", "—", "нет"}:
        comment = None

    result = await moderation.file_report(
        reporter_id=message.from_user.id,
        kind=data["rep_kind"],
        target_id=data["rep_target"],
        reason=data["rep_reason"],
        comment=comment,
    )

    await send_report_to_mods(bot, result.report_id)

    note = "Жалоба отправлена модератору."
    if result.autohidden:
        note += " Анкета скрыта из показа до решения."
    await message.answer(note)
    await send_next_card(message, message.from_user.id)


# --- Своя анкета ----------------------------------------------------------

async def show_profile(message: Message, user_id: int) -> None:
    stats = await db.my_stats(user_id)
    prof = stats["profile"]

    if prof is None or prof["status"] in {"draft", "deleted"}:
        await message.answer(
            "У вас пока нет анкеты в режиме оценок. Нажмите «Оценивать», чтобы создать.",
            reply_markup=kb.main_kb(),
        )
        return

    if prof["status"] == "banned":
        await message.answer(texts.NOT_READY["banned"])
        return

    tier = grades.tier_label(prof["votes_weight"], prof["votes_count"])
    lines = [
        f"<b>{_esc(prof['display_name'])}</b>, {prof['age']}",
        f"Ваш тир: <b>{tier}</b>",
        f"Оценок получено: {prof['votes_count']}",
        f"Оценок поставлено: {stats['given']}",
    ]

    if prof["votes_count"]:
        parts = [
            f"{grades.label(code)} — {stats['breakdown'].get(code, 0)}"
            for code in grades.CODES
            if stats["breakdown"].get(code)
        ]
        if parts:
            lines.append("\n" + " · ".join(parts))

    if prof["status"] == "hidden":
        hours = max(1, (prof["hidden_until"] - db.now()) // 3600) if prof["hidden_until"] else 0
        lines.append(
            f"\n⚠️ Анкета скрыта модератором"
            + (f", осталось около {hours} ч." if hours else " до решения.")
        )
    elif prof["status"] == "awaiting_photo" or prof["needs_reupload"]:
        lines.append("\n⚠️ Чтобы вернуться в показ, загрузите новое фото.")

    text = "\n".join(lines)
    if prof["photo_file_id"] and prof["status"] not in {"awaiting_photo"}:
        await message.answer_photo(
            prof["photo_file_id"], caption=text, reply_markup=kb.profile_kb(prof["status"])
        )
    else:
        await message.answer(text, reply_markup=kb.profile_kb(prof["status"]))


@router.callback_query(F.data == "rate:editphoto")
async def on_edit_photo(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.edit_photo)
    await call.answer()
    await call.message.answer("Пришлите новое фото для анкеты.")


@router.callback_query(F.data == "rate:editname")
async def on_edit_name(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.edit_name)
    await call.answer()
    await call.message.answer("Напишите новое имя для анкеты.")


@router.message(StateFilter(Reg.edit_name), F.text)
async def on_new_name(message: Message, state: FSMContext) -> None:
    ok, result = texts.validate_name(message.text)
    if not ok:
        await message.answer(result)
        return
    await db.upsert_profile(message.from_user.id, display_name=result)
    await state.clear()
    await message.answer("Имя обновлено.", reply_markup=kb.main_kb())


@router.callback_query(F.data == "rate:delete:ask")
async def on_delete_ask(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(
        "Удалить анкету? Показы прекратятся, фото будет удалено, "
        "полученные оценки не сохранятся.",
        reply_markup=kb.confirm_delete_kb(),
    )


@router.callback_query(F.data == "rate:delete:yes")
async def on_delete_yes(call: CallbackQuery) -> None:
    prof = await db.get_profile(call.from_user.id)
    if prof:
        await photos.remove(prof["photo_path"])
    await db.delete_profile(call.from_user.id)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Анкета удалена")
    await call.message.answer(
        "Анкета удалена, показы прекращены. Создать новую можно в любой момент.",
        reply_markup=kb.main_kb(),
    )


@router.callback_query(F.data == "rate:delete:no")
async def on_delete_no(call: CallbackQuery) -> None:
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Оставили как есть")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
