"""
Tests for handlers/report.py: the reports menu and the Excel/CSV
generate-and-send flow (including the empty-records path).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Chat, Message

from database import crud
from database.models import Medicine, MedicineRecord
from handlers.report import process_report_csv, process_report_excel, report_menu_handler


def _fake_message(user_id: int):
    message = create_autospec(Message, instance=True)
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.answer = AsyncMock()
    return message


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.chat = MagicMock(spec=Chat, id=user_id)
    message.answer = AsyncMock()
    message.answer_document = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


def _fake_bot():
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    return bot


async def _add_medicine_record(db_session, user_id: int):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = Medicine(user_id=user_id, name="Aspirin", dosage="500mg")
    db_session.add(medicine)
    await db_session.flush()
    db_session.add(MedicineRecord(medicine_id=medicine.id, status="taken", remaining_days=3))
    await db_session.flush()


class TestReportMenuHandler:
    async def test_shows_excel_and_csv_options(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(1)

        await report_menu_handler(message, db_session)

        message.answer.assert_awaited_once()
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert callback_data == ["report_excel", "report_csv"]

    async def test_noop_when_no_from_user(self, db_session):
        message = _fake_message(1)
        message.from_user = None

        await report_menu_handler(message, db_session)

        message.answer.assert_not_awaited()


class TestProcessReportExcel:
    async def test_sends_empty_message_when_no_records(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "report_excel")
        bot = _fake_bot()

        await process_report_excel(call, db_session, bot)

        message.answer.assert_awaited_once()
        message.answer_document.assert_not_awaited()
        call.answer.assert_awaited_once()
        bot.send_chat_action.assert_not_awaited()

    async def test_generates_and_sends_excel_document(self, db_session):
        await _add_medicine_record(db_session, 1)
        call, message = _fake_call(1, "report_excel")
        bot = _fake_bot()

        await process_report_excel(call, db_session, bot)

        bot.send_chat_action.assert_awaited_once_with(1, "upload_document")
        message.answer_document.assert_awaited_once()
        sent_file = message.answer_document.call_args.kwargs["document"]
        assert sent_file.filename.endswith(".xlsx")
        call.answer.assert_awaited_once()

    async def test_noop_when_message_not_a_message_instance(self, db_session):
        await _add_medicine_record(db_session, 1)
        call, message = _fake_call(1, "report_excel")
        call.message = None
        bot = _fake_bot()

        await process_report_excel(call, db_session, bot)

        message.answer_document.assert_not_awaited()
        bot.send_chat_action.assert_not_awaited()

    async def test_falls_back_to_default_timezone_when_unset(self, db_session):
        await _add_medicine_record(db_session, 1)
        call, message = _fake_call(1, "report_excel")
        bot = _fake_bot()

        await process_report_excel(call, db_session, bot)

        message.answer_document.assert_awaited_once()


class TestProcessReportCsv:
    async def test_sends_empty_message_when_no_records(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "report_csv")
        bot = _fake_bot()

        await process_report_csv(call, db_session, bot)

        message.answer.assert_awaited_once()
        message.answer_document.assert_not_awaited()

    async def test_generates_and_sends_csv_document(self, db_session):
        await _add_medicine_record(db_session, 1)
        call, message = _fake_call(1, "report_csv")
        bot = _fake_bot()

        await process_report_csv(call, db_session, bot)

        bot.send_chat_action.assert_awaited_once_with(1, "upload_document")
        message.answer_document.assert_awaited_once()
        sent_file = message.answer_document.call_args.kwargs["document"]
        assert sent_file.filename.endswith(".csv")
        call.answer.assert_awaited_once()

    async def test_caption_includes_record_count(self, db_session):
        await _add_medicine_record(db_session, 1)
        call, message = _fake_call(1, "report_csv")
        bot = _fake_bot()

        await process_report_csv(call, db_session, bot)

        caption = message.answer_document.call_args.kwargs["caption"]
        assert "1" in caption
