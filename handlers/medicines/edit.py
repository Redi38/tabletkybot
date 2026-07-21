"""Handlers for editing an existing medicine's fields."""

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import crud
from locales.texts import data_lang, get_text
from services.scheduler import add_reminders_for_medicine

from .keyboards import medicine_menu_kb
from .states import EditMedicine
from .utils import _valid_medicine_ctx, parse_int, parse_times

router = Router()
logger = logging.getLogger(__name__)


# ── Editing ───────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("edit_med_"))
async def edit_medicine_menu(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx

    buttons = [
        [
            InlineKeyboardButton(
                text=get_text(lang, "btn_mark_taken_now"),
                callback_data=f"mark_taken_now_{medicine_id}",
                style="success",
            )
        ],
        [InlineKeyboardButton(text=get_text(lang, "btn_edit_name"), callback_data=f"edit_field_name_{medicine_id}")],
        [
            InlineKeyboardButton(
                text=get_text(lang, "btn_edit_dosage"), callback_data=f"edit_field_dosage_{medicine_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=get_text(lang, "btn_edit_time"), callback_data=f"edit_field_schedules_{medicine_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=get_text(lang, "btn_edit_duration"), callback_data=f"edit_field_course_duration_{medicine_id}"
            )
        ],
    ]
    if medicine.stock_amount is not None:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_edit_stock"), callback_data=f"edit_field_stock_amount_{medicine_id}"
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_edit_threshold"),
                    callback_data=f"edit_field_low_stock_threshold_{medicine_id}",
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_enable_stock"), callback_data=f"edit_field_stock_amount_{medicine_id}"
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")])
    await msg.edit_text(
        get_text(lang, "edit_what"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("edit_field_"))
async def edit_field_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, _ = ctx
    field = "_".join(str(call.data).split("_")[2:-1])
    await state.update_data(medicine_id=medicine_id, field=field, lang=lang)
    await msg.edit_text(get_text(lang, "edit_enter_new", field=field), parse_mode="HTML")
    await state.set_state(EditMedicine.waiting_value)


@router.message(EditMedicine.waiting_value)
async def edit_field_save(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    session_factory: async_sessionmaker,
) -> None:
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    medicine_id, field, lang = data["medicine_id"], data["field"], data_lang(data)
    new_value = message.text.strip()

    if field == "schedules":
        parsed_times = parse_times(new_value)
        if not parsed_times:
            await message.answer(get_text(lang, "err_time"), parse_mode="HTML")
            return
        medicine = await crud.get_medicine_by_id(session, medicine_id)
        if medicine:
            old_count = len(medicine.schedules) if medicine.schedules else 1
            new_count = len(parsed_times)
            new_total = int((medicine.course_duration / old_count) * new_count)
            await crud.update_medicine_field(session, medicine_id, "course_duration", new_total)
        await crud.update_medicine_schedules(session, medicine_id, parsed_times)
    else:
        final_value: str | int = new_value
        if field in ("course_duration", "stock_amount", "low_stock_threshold"):
            val = parse_int(new_value)
            if val is None:
                err_key = {"course_duration": "err_duration", "stock_amount": "err_stock"}.get(field, "err_threshold")
                await message.answer(get_text(lang, err_key))
                return
            if field == "course_duration":
                medicine = await crud.get_medicine_by_id(session, medicine_id)
                count = len(medicine.schedules) if medicine and medicine.schedules else 1
                final_value = val * count
            else:
                final_value = val
        await crud.update_medicine_field(session, medicine_id, field, final_value)

    await session.commit()
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if medicine:
        tz = await crud.get_user_timezone(session, message.from_user.id)
        add_reminders_for_medicine(bot, medicine, str(tz), message.from_user.id, lang, session_factory=session_factory)

    logger.info(
        f"User {message.from_user.id} (@{message.from_user.username}) edited field '{field}' of medicine (id={medicine_id})"
    )

    await state.clear()
    await message.answer(get_text(lang, "edit_success"), reply_markup=medicine_menu_kb(lang))
