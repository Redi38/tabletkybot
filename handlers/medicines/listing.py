"""Handlers for listing active medicines and per-medicine statistics."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text
from services.report_service import get_medicine_stats_summary

from .keyboards import medicine_back_only_kb
from .utils import _base_ctx

router = Router()


# ── List and statistics ──────────────────────────────────────────────────
@router.callback_query(F.data == "med_list")
async def list_medicines(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    medicines = [m for m in await crud.get_user_medicines(session, call.from_user.id) if m.is_active]

    if not medicines:
        buttons = [
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_archive_list"), callback_data="med_archive_list", style="primary"
                )
            ],
            [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_menu")],
        ]
        await msg.edit_text(get_text(lang, "med_empty"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    text = get_text(lang, "med_list_title")
    buttons = []
    for med in medicines:
        schedules_str = ", ".join(s.scheduled_time for s in med.schedules)
        stock_info = get_text(lang, "med_stock_info", amount=med.stock_amount) if med.stock_amount is not None else ""
        remaining_info = get_text(lang, "med_remaining_doses", duration=med.course_duration)
        text += (
            f"💊 <b>{med.name}</b>\n   {get_text(lang, 'med_form')}: {med.form} | "
            f"{get_text(lang, 'med_dose')}: {med.dosage}\n   ⏰ {schedules_str} | "
            f"{remaining_info}{stock_info}\n\n"
        )
        buttons.append(
            [
                InlineKeyboardButton(text=f"✏️ {med.name}", callback_data=f"edit_med_{med.id}", style="primary"),
                InlineKeyboardButton(
                    text=get_text(lang, "btn_archive"), callback_data=f"med_archive_ask_{med.id}", style="danger"
                ),
            ]
        )

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="med_archive_list")])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_menu")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data == "med_stats")
async def medicine_stats(call: CallbackQuery, session: AsyncSession) -> None:
    """
    Per-medicine breakdown (taken/missed/% adherence/last dose), using the
    same aggregation as the Excel report's "By medicine" sheet — so the
    numbers shown here always match what the user sees in their exported
    report.
    """
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx

    records = await crud.get_medicine_records_for_report(session, call.from_user.id)
    if not records:
        await msg.edit_text(get_text(lang, "med_stats_empty"), reply_markup=medicine_back_only_kb(lang))
        return

    user_tz = await crud.get_user_timezone(session, call.from_user.id)
    stats_by_medicine = get_medicine_stats_summary(records, user_tz)

    lines = [get_text(lang, "med_stats_title"), ""]
    for stat in stats_by_medicine:
        pct = stat["pct"]
        indicator = "🟢" if pct >= 80 else "🟡" if pct >= 50 else "🔴"
        last_str = stat["last_dt"].strftime("%d.%m.%Y %H:%M") if stat["last_dt"] else "—"
        lines.append(
            f"{indicator} <b>{stat['name']}</b> ({stat['dosage']})\n"
            f"   ✅ {stat['taken']} / ⏭️ {stat['missed']} — <b>{pct:.1f}%</b>\n"
            f"   {get_text(lang, 'med_stats_last')}: {last_str}\n"
        )

    text = "\n".join(lines)
    await msg.edit_text(text, reply_markup=medicine_back_only_kb(lang), parse_mode="HTML")
