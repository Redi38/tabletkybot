from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from database import crud
from services.report_service import create_excel_report, create_csv_report
from locales.texts import get_text, TEXTS, btn_variants

router = Router()


# Головне меню звітів
@router.message(F.text.in_(btn_variants("btn_report")))
async def report_menu_handler(message: Message, session: AsyncSession) -> None:
    """Відкриває Inline-меню вибору звітів."""
    if not message.from_user: return
    lang = await crud.get_user_language(session, message.from_user.id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(lang, "btn_gen_excel"), callback_data="report_excel", style="primary"),
            InlineKeyboardButton(text=get_text(lang, "btn_gen_csv"), callback_data="report_csv", style="primary")
        ]
    ])

    await message.answer(get_text(lang, "report_menu_title"), reply_markup=kb, parse_mode="HTML")


# ── Допоміжна функція ─────────────────────────────────────────────────────
async def _generate_and_send_report(call: CallbackQuery, session: AsyncSession, bot: Bot, report_type: str) -> None:
    """Універсальна функція для створення та відправки звітів."""
    if not call.from_user or not isinstance(call.message, Message): return

    user = await crud.get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.full_name)
    lang = str(user.language) if user.language else "ua"
    records = await crud.get_medicine_records_for_report(session, call.from_user.id)

    if not records:
        await call.message.answer(get_text(lang, "report_empty"))
        await call.answer()
        return

    await bot.send_chat_action(call.message.chat.id, "upload_document")

    user_tz = str(user.timezone) if user.timezone else "Europe/Kyiv"

    # Вибираємо функцію генерації та розширення файлу залежно від типу
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


# Обробники кнопок
@router.callback_query(F.data == "report_excel")
async def process_report_excel(call: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Генерує Excel звіт."""
    await _generate_and_send_report(call, session, bot, report_type="excel")


@router.callback_query(F.data == "report_csv")
async def process_report_csv(call: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Генерує CSV звіт."""
    await _generate_and_send_report(call, session, bot, report_type="csv")
