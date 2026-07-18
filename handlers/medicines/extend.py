"""Handlers for extending/restoring a medicine course."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import crud
from locales.texts import get_text
from services.scheduler import add_reminders_for_medicine

from .states import ExtendMedicine
from .utils import _valid_medicine_ctx, parse_int

router = Router()


# ── Course extension ─────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("med_restore_ask_") | F.data.startswith("med_extend_ask_"))
async def extend_course_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, _ = ctx
    await state.update_data(medicine_id=medicine_id, lang=lang)
    await msg.edit_text(get_text(lang, "ask_extend_days"), parse_mode="HTML")
    await state.set_state(ExtendMedicine.waiting_for_days)


@router.message(ExtendMedicine.waiting_for_days)
async def extend_course_save(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    session_factory: async_sessionmaker,
) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ua")
    days = parse_int(message.text.strip()) if message.text else None
    if days is None:
        await message.answer(get_text(lang, "err_duration"))
        return

    medicine_id = data["medicine_id"]
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if not medicine or not message.from_user:
        return

    schedules_count = len(medicine.schedules) if medicine.schedules else 1
    await crud.update_medicine_field(session, medicine_id, "course_duration", days * schedules_count)
    await crud.update_medicine_field(session, medicine_id, "is_active", True)

    tz = await crud.get_user_timezone(session, message.from_user.id)
    add_reminders_for_medicine(bot, medicine, str(tz), message.from_user.id, lang, session_factory=session_factory)
    await state.clear()
    await message.answer(get_text(lang, "med_restored", name=str(medicine.name), days=days), parse_mode="HTML")
