"""Handlers for manual archiving (with confirmation), the archive list, and deletion."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text

from .keyboards import archived_prescription_row, back_to_list_kb, prescription_menu_kb
from .utils import _base_ctx, _valid_prescription_ctx

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("presc_archive_ask_"))
async def archive_ask(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_confirm_archive"),
                    callback_data=f"presc_archive_confirm_{prescription_id}",
                    style="danger",
                ),
                InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_list"),
            ]
        ]
    )
    await msg.edit_text(
        get_text(lang, "presc_archive_confirm_q", name=prescription.medicine_name),
        reply_markup=kb,
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_archive_confirm_"))
async def archive_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await crud.archive_prescription(session, prescription_id)
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) archived prescription '{prescription.medicine_name}' (id={prescription_id})"
    )
    await msg.edit_text(
        get_text(lang, "presc_archived", name=prescription.medicine_name),
        reply_markup=back_to_list_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


# ── Prescription archive ────────────────────────────────────────────────────
@router.callback_query(F.data == "presc_archive_list")
async def archive_list(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    archived = await crud.get_user_archived_prescriptions(session, call.from_user.id)

    if not archived:
        await msg.edit_text(
            get_text(lang, "presc_archive_empty"), reply_markup=back_to_list_kb(lang), parse_mode="HTML"
        )
        return

    text = get_text(lang, "presc_archive_title")
    buttons = []
    for p in archived:
        text += f"📝 <b>{p.medicine_name}</b> — {p.expires_at.strftime('%d.%m.%Y')}\n"
        buttons.append(archived_prescription_row(p.id, lang))

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_list")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


# ── Deletion (with confirmation) ─────────────────────────────────────────
@router.callback_query(F.data.startswith("presc_delete_ask_"))
async def delete_ask(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_confirm_delete"),
                    callback_data=f"presc_delete_confirm_{prescription_id}",
                    style="danger",
                ),
                InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_archive_list"),
            ]
        ]
    )
    await msg.edit_text(
        get_text(lang, "presc_delete_confirm_q", name=prescription.medicine_name),
        reply_markup=kb,
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_delete_confirm_"))
async def delete_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    name = prescription.medicine_name
    await crud.delete_prescription(session, prescription_id)
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) permanently deleted prescription '{name}' (id={prescription_id})"
    )
    await msg.edit_text(
        get_text(lang, "presc_deleted", name=name), reply_markup=prescription_menu_kb(lang), parse_mode="HTML"
    )
    await call.answer()
