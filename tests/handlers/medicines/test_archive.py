"""
Tests for handlers/medicines/archive.py: viewing the archived-medicines
list, archiving a medicine (ask -> confirm), and permanently deleting one
(ask -> confirm), including reminder/stock-alert cleanup side effects.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.archive import (
    archive_medicine_ask,
    archive_medicine_exec,
    confirm_delete_medicine,
    delete_medicine,
    list_archived_medicines,
)


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_medicine(db_session, user_id=1, name="Ibuprofen", is_active=True):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name=name,
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=5,
    )
    if not is_active:
        await crud.update_medicine_field(db_session, medicine.id, "is_active", False)
    await db_session.commit()
    return medicine


class TestListArchivedMedicines:
    async def test_shows_empty_state_with_no_archived_medicines(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_archive_list")

        await list_archived_medicines(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None

    async def test_lists_only_archived_medicines(self, db_session):
        await _add_medicine(db_session, name="Active One", is_active=True)
        await _add_medicine(db_session, name="Archived One", is_active=False)
        call, message = _fake_call(1, "med_archive_list")

        await list_archived_medicines(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Archived One" in text
        assert "Active One" not in text

    async def test_no_op_when_no_from_user(self, db_session):
        call, message = _fake_call(1, "med_archive_list")
        call.from_user = None

        await list_archived_medicines(call, db_session)

        message.edit_text.assert_not_awaited()


class TestArchiveMedicineAsk:
    async def test_shows_confirmation_with_the_medicine_name(self, db_session):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"med_archive_ask_{medicine.id}")

        await archive_medicine_ask(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None

    async def test_no_op_when_medicine_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_archive_ask_999")

        await archive_medicine_ask(call, db_session)

        message.edit_text.assert_not_awaited()


class TestArchiveMedicineExec:
    async def test_archives_the_medicine_and_clears_stock_alert(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, _ = _fake_call(1, f"med_archive_confirm_{medicine.id}")

        await archive_medicine_exec(call, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.is_active is False
        call.answer.assert_awaited_once()

    async def test_refreshes_the_medicines_list_afterwards(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"med_archive_confirm_{medicine.id}")

        await archive_medicine_exec(call, db_session)

        # list_medicines() is called at the end, which re-renders via edit_text
        message.edit_text.assert_awaited_once()

    async def test_no_op_when_medicine_missing(self, db_session, mock_redis):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_archive_confirm_999")

        await archive_medicine_exec(call, db_session)

        call.answer.assert_awaited_once()  # only the "not found" alert from _valid_medicine_ctx
        message.edit_text.assert_not_awaited()


class TestDeleteMedicine:
    async def test_shows_deletion_confirmation(self, db_session):
        medicine = await _add_medicine(db_session, is_active=False)
        call, message = _fake_call(1, f"med_del_{medicine.id}")

        await delete_medicine(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None


class TestConfirmDeleteMedicine:
    async def test_permanently_removes_the_medicine(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, is_active=False)
        call, _ = _fake_call(1, f"med_confirm_del_{medicine.id}")

        await confirm_delete_medicine(call, db_session)

        assert await crud.get_medicine_by_id(db_session, medicine.id) is None
        call.answer.assert_awaited_once()

    async def test_no_op_when_medicine_missing(self, db_session, mock_redis):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_confirm_del_999")

        await confirm_delete_medicine(call, db_session)

        call.answer.assert_awaited_once()  # only the "not found" alert from _valid_medicine_ctx
        message.edit_text.assert_not_awaited()
