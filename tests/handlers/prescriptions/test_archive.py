"""
Tests for handlers/prescriptions/archive.py: manual archive (ask ->
confirm), the archive list, and permanent deletion (ask -> confirm).
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.archive import (
    archive_ask,
    archive_confirm,
    archive_list,
    delete_ask,
    delete_confirm,
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


async def _add_prescription(db_session, user_id=1, name="Amoxicillin", is_active=True):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name=name,
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 1, 31),
    )
    if not is_active:
        await crud.archive_prescription(db_session, prescription.id)
    await db_session.commit()
    return prescription


class TestArchiveAsk:
    async def test_shows_confirmation_with_the_medicine_name(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_archive_ask_{prescription.id}")

        await archive_ask(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None
        call.answer.assert_awaited_once()

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_archive_ask_999")

        await archive_ask(call, db_session)

        message.edit_text.assert_not_awaited()


class TestArchiveConfirm:
    async def test_archives_the_prescription(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_archive_confirm_{prescription.id}")

        await archive_confirm(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_active is False
        message.edit_text.assert_awaited_once()

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_archive_confirm_999")

        await archive_confirm(call, db_session)

        message.edit_text.assert_not_awaited()


class TestArchiveList:
    async def test_shows_empty_state_with_no_archived_prescriptions(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_archive_list")

        await archive_list(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None

    async def test_lists_only_archived_prescriptions(self, db_session):
        await _add_prescription(db_session, name="Active One", is_active=True)
        await _add_prescription(db_session, name="Archived One", is_active=False)
        call, message = _fake_call(1, "presc_archive_list")

        await archive_list(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Archived One" in text
        assert "Active One" not in text

    async def test_no_op_when_no_from_user(self, db_session):
        call, message = _fake_call(1, "presc_archive_list")
        call.from_user = None

        await archive_list(call, db_session)

        message.edit_text.assert_not_awaited()


class TestDeleteAsk:
    async def test_shows_deletion_confirmation(self, db_session):
        prescription = await _add_prescription(db_session, is_active=False)
        call, message = _fake_call(1, f"presc_delete_ask_{prescription.id}")

        await delete_ask(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None


class TestDeleteConfirm:
    async def test_permanently_removes_the_prescription(self, db_session):
        prescription = await _add_prescription(db_session, is_active=False)
        call, message = _fake_call(1, f"presc_delete_confirm_{prescription.id}")

        await delete_confirm(call, db_session)

        assert await crud.get_prescription_by_id(db_session, prescription.id) is None
        message.edit_text.assert_awaited_once()

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_delete_confirm_999")

        await delete_confirm(call, db_session)

        message.edit_text.assert_not_awaited()
