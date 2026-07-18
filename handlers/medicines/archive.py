"""Handlers for archiving, restoring the archive list, and deleting medicines."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text
from services.scheduler import clear_stock_alert_pending, remove_reminders

from .keyboards import medicine_back_only_kb
from .listing import list_medicines
from .utils import _base_ctx, _valid_medicine_ctx

router = Router()
logger = logging.getLogger(__name__)


# ── Archive and Removal ─────────────────────────────────────────────────────
@router.callback_query(F.data == "med_archive_list")
async def list_archived_medicines(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    archived = await crud.get_archived_medicines(session, call.from_user.id)
    if not archived:
        await msg.edit_text(get_text(lang, "med_archived_empty"), reply_markup=medicine_back_only_kb(lang))
        return

    text = f"🗂 <b>{get_text(lang, 'med_archived_title')}</b>\n\n"
    buttons = []
    for med in archived:
        time_str = ", ".join(s.scheduled_time for s in med.schedules) if med.schedules else "-"
        stock_info = get_text(lang, "med_stock_info", amount=med.stock_amount) if med.stock_amount is not None else ""
        text += (
            f"💊 <b>{med.name}</b>\n   {get_text(lang, 'med_form')}: {med.form} | "
            f"{get_text(lang, 'med_dose')}: {med.dosage}\n   ⏰ {time_str}{stock_info}\n\n"
        )
        buttons.append(
            [
                InlineKeyboardButton(text=f"🔄 {med.name}", callback_data=f"med_restore_ask_{med.id}", style="primary"),
                InlineKeyboardButton(
                    text=get_text(lang, "btn_archive_del"), callback_data=f"med_del_{med.id}", style="danger"
                ),
            ]
        )
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("med_archive_ask_"))
async def archive_medicine_ask(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_archive"), callback_data=f"med_archive_confirm_{medicine_id}"
                )
            ],
            [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")],
        ]
    )
    await msg.edit_text(
        get_text(lang, "med_archive_confirm", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("med_archive_confirm_"))
async def archive_medicine_exec(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    _, lang, medicine_id, medicine = ctx
    await crud.update_medicine_field(session, medicine_id, "is_active", False)
    remove_reminders(medicine_id)
    await clear_stock_alert_pending(call.from_user.id, medicine_id)
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) archived medicine '{medicine.name}' (id={medicine_id})"
    )
    await call.answer(get_text(lang, "edit_success"))
    await list_medicines(call, session)


@router.callback_query(F.data.startswith("med_del_"))
async def delete_medicine(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx
    buttons = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_confirm_del"),
                    callback_data=f"med_confirm_del_{medicine_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text=get_text(lang, "btn_cancel_del"), callback_data="med_list", style="primary")],
        ]
    )
    await msg.edit_text(
        get_text(lang, "med_del_prompt", name=str(medicine.name)), reply_markup=buttons, parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("med_confirm_del_"))
async def confirm_delete_medicine(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    _, lang, medicine_id, medicine = ctx
    await crud.delete_medicine(session, medicine_id)
    remove_reminders(medicine_id)
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) deleted medicine '{medicine.name}' (id={medicine_id})"
    )
    await call.answer(get_text(lang, "med_deleted", name=str(medicine.name)))
    await list_medicines(call, session)
