from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import btn_variants, get_text
from services.report_service import create_csv_report, create_excel_report

router = Router()
# Main reports menu
@router.message(F.text.in_(btn_variants("btn_report")))
async def report_menu_handler(message: Message, session: AsyncSession) -> None:
    """Opens the inline menu for choosing a report."""
    if not message.from_user:
        return
    lang = await crud.get_user_language(session, message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(lang, "btn_gen_excel"), callback_data="report_excel", style="primary"),
            InlineKeyboardButton(text=get_text(lang, "btn_gen_csv"), callback_data="report_csv", style="primary")
        ]
    ])
    await message.answer(get_text(lang, "report_menu_title"), reply_markup=kb, parse_mode="HTML")
# ── Helper function ─────────────────────────────────────────────────────
async def _generate_and_send_report(call: CallbackQuery, session: AsyncSession, bot: Bot, report_type: str) -> None:
    """Universal function for generating and sending reports."""
    if not call.from_user or not isinstance(call.message, Message):
        return
    user = await crud.get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.full_name)
    lang = str(user.language) if user.language else "ua"
    records = await crud.get_medicine_records_for_report(session, call.from_user.id)
    if not records:
        await call.message.answer(get_text(lang, "report_empty"))
        await call.answer()
        return
    await bot.send_chat_action(call.message.chat.id, "upload_document")
    user_tz = str(user.timezone) if user.timezone else "Europe/Kyiv"
    # Choose the generator function and file extension based on the type
    if report_type == "excel":
        buffer = create_excel_report(records, lang, user_name=str(user.full_name), user_tz=user_tz)
        file_ext = "xlsx"
    else:
        buffer = create_csv_report(records, lang, user_name=str(user.full_name), user_tz=user_tz)
        file_ext = "csv"
    filename = f"med_report_{datetime.now().strftime('%Y-%m-%d')}.{file_ext}"
    caption = get_text(lang, "report_caption", date=datetime.now().strftime('%d.%m.%Y %H:%M'), count=len(records))
    await call.message.answer_document(
        document=BufferedInputFile(buffer.read(), filename=filename),
        caption=caption,
        parse_mode="HTML"
    )
    await call.answer()
# Button handlers
@router.callback_query(F.data == "report_excel")
async def process_report_excel(call: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Generates an Excel report."""
    await _generate_and_send_report(call, session, bot, report_type="excel")
@router.callback_query(F.data == "report_csv")
async def process_report_csv(call: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Generates a CSV report."""
    await _generate_and_send_report(call, session, bot, report_type="csv")
