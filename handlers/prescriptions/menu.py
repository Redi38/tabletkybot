"""Top-level prescriptions menu navigation handlers."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import btn_variants, get_text

from .keyboards import prescription_menu_kb
from .utils import _base_ctx

router = Router()


@router.message(F.text.in_(btn_variants("btn_prescriptions")))
async def prescriptions_menu(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(
        get_text(language, "presc_menu_title"),
        reply_markup=prescription_menu_kb(language),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "presc_menu")
async def back_to_presc_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.clear()
    await msg.edit_text(get_text(lang, "presc_menu_title"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "presc_back")
async def back_to_main_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, _ = ctx
    await state.clear()
    user = await crud.get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.full_name)
    lang = str(user.language) if user.language else "ua"
    await msg.edit_text(get_text(lang, "start_text", name=str(user.full_name)), parse_mode="HTML")
    await call.answer()
