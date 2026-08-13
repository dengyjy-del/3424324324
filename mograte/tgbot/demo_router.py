"""Роутер режима съёмки для раздела оценок.

Диалог: /demo_card → фото → ник → оценка → готовая карточка.

Подключается ПЕРЕД основным роутером бота, иначе фото перехватит
обработчик отчётов, а текст — обработчик кода.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..core import grades
from . import demo_card

log = logging.getLogger(__name__)
router = Router(name="mograte.demo_card")


class DemoCard(StatesGroup):
    photo = State()
    nick = State()
    grade = State()


def _admin_ids(config) -> set[int]:
    return set(getattr(config, "admin_ids", ()) or ())


async def _allowed(user_id: int, config, demo) -> tuple[bool, str]:
    """Пускаем только владельца и только при включённом режиме съёмки."""
    if user_id not in _admin_ids(config):
        return False, demo_card.DENIED
    if demo is not None and not await demo.is_active(user_id):
        return False, demo_card.NOT_IN_DEMO
    return True, ""


@router.message(Command("demo_card"))
async def cmd_demo_card(message: Message, state: FSMContext, config, demo) -> None:
    user = message.from_user
    if user is None:
        return

    ok, reason = await _allowed(user.id, config, demo)
    if not ok:
        await message.answer(reason)
        return

    await state.clear()
    await state.set_state(DemoCard.photo)
    await message.answer(demo_card.ASK_PHOTO)


@router.message(StateFilter(DemoCard.photo), F.photo)
async def got_photo(message: Message, state: FSMContext) -> None:
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(DemoCard.nick)
    await message.answer(demo_card.ASK_NICK)


@router.message(StateFilter(DemoCard.photo))
async def need_photo(message: Message) -> None:
    await message.answer("Нужно фото — отправь картинку, а не текст или файл.")


@router.message(StateFilter(DemoCard.nick), F.text)
async def got_nick(message: Message, state: FSMContext) -> None:
    ok, result = demo_card.validate_nickname(message.text)
    if not ok:
        await message.answer(result)
        return
    await state.update_data(nickname=result)
    await state.set_state(DemoCard.grade)
    await message.answer(demo_card.ASK_GRADE, reply_markup=demo_card.grade_keyboard())


@router.callback_query(StateFilter(DemoCard.grade), F.data == "demo:cancel")
async def cancelled(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(demo_card.CANCELLED)
    await call.answer()


@router.callback_query(StateFilter(DemoCard.grade), F.data.startswith("demo:g:"))
async def got_grade(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.rsplit(":", 1)[-1]
    if not grades.is_valid(code):
        await call.answer("Неизвестная оценка")
        return

    data = await state.get_data()
    await state.clear()

    photo_id = data.get("photo_file_id")
    nickname = data.get("nickname")
    if not photo_id or not nickname:
        await call.answer("Карточка потерялась, начни заново: /demo_card", show_alert=True)
        return

    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()

    # Собственно карточка — то, что попадёт в кадр.
    await call.message.answer_photo(
        photo_id,
        caption=demo_card.caption(nickname, code),
        reply_markup=demo_card.card_keyboard(),
    )

    await demo_card.register(call.from_user.id, nickname, code)
    await call.message.answer(demo_card.RIGHTS_REMINDER)


@router.callback_query(F.data.in_({"demo:rate", "demo:write"}))
async def card_buttons(call: CallbackQuery) -> None:
    """Кнопки на карточке — реквизит: нажатие ничего не меняет."""
    await call.answer("Кнопка для кадра", show_alert=False)
