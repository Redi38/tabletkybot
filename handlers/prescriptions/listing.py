"""Handler for listing active prescriptions."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text

from .utils import _base_ctx

router = Router()


@router.callback_query(F.data == "presc_list")
async def list_prescriptions(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    prescriptions = await crud.get_user_prescriptions(session, call.from_user.id)

    if not prescriptions:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="presc_archive_list")],
                [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_menu")],
            ]
        )
        await msg.edit_text(get_text(lang, "presc_empty"), reply_markup=kb, parse_mode="HTML")
        return

    text = get_text(lang, "presc_list_title")
    buttons = []
    for p in prescriptions:
        max_str = str(p.max_quantity) if p.max_quantity is not None else "∞"
        text += (
            f"📝 <b>{p.medicine_name}</b>\n"
            f"   📅 {get_text(lang, 'presc_valid_from_label')}: {p.valid_from.strftime('%d.%m.%Y')}\n"
            f"   📅 {get_text(lang, 'presc_valid_until')}: <b>{p.expires_at.strftime('%d.%m.%Y')}</b>\n"
            f"   🛒 {get_text(lang, 'presc_purchased_label')}: {p.purchased_quantity}/{max_str}\n\n"
        )
        row = [InlineKeyboardButton(text=f"✏️ {p.medicine_name}", callback_data=f"presc_edit_{p.id}", style="primary")]
        if not p.is_fully_purchased:
            row.append(
                InlineKeyboardButton(
                    text=get_text(lang, "btn_mark_bought"), callback_data=f"presc_buy_ask_{p.id}", style="success"
                )
            )
        row.append(
            InlineKeyboardButton(
                text=get_text(lang, "btn_archive_presc"), callback_data=f"presc_archive_ask_{p.id}", style="danger"
            )
        )
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="presc_archive_list")])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_menu")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
