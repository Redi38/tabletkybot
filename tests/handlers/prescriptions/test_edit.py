"""
Tests for handlers/prescriptions/edit.py: editing an existing
prescription's valid_from date, duration, or max_quantity.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.edit import (
    edit_duration_save,
    edit_duration_start,
    edit_menu,
    edit_quantity_save,
    edit_quantity_start,
    edit_valid_from_save,
    edit_valid_from_start,
)
from handlers.prescriptions.states import EditPrescription


class _FakeState:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self.state = None

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self._data = {}
        self.state = None


def _fake_message(text: str | None, user_id: int = 1):
    message = create_autospec(Message, instance=True)
    message.text = text
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.answer = AsyncMock()
    return message


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_prescription(db_session, user_id=1, valid_from=date(2026, 1, 1), expires_at=date(2026, 1, 31)):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name="Amoxicillin",
        valid_from=valid_from,
        expires_at=expires_at,
    )
    await db_session.commit()
    return prescription


class TestEditMenu:
    async def test_shows_the_edit_field_keyboard(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_edit_{prescription.id}")

        await edit_menu(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None
        call.answer.assert_awaited_once()

    async def test_no_op_when_prescription_not_found(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_edit_999")

        await edit_menu(call, db_session)

        message.edit_text.assert_not_awaited()
        call.answer.assert_awaited_once()  # alert from _valid_prescription_ctx


class TestEditValidFromFlow:
    async def test_start_sets_state_with_prescription_id(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_ef_valid_{prescription.id}")
        state = _FakeState()

        await edit_valid_from_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == EditPrescription.valid_from
        assert (await state.get_data())["prescription_id"] == prescription.id

    async def test_invalid_date_shows_error_without_saving(self, db_session):
        prescription = await _add_prescription(db_session)
        message = _fake_message("not-a-date")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await edit_valid_from_save(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.valid_from == date(2026, 1, 1)  # unchanged

    async def test_valid_date_shifts_valid_from_and_preserves_duration(self, db_session):
        prescription = await _add_prescription(
            db_session, valid_from=date(2026, 1, 1), expires_at=date(2026, 1, 31)
        )  # 30-day course
        message = _fake_message("15.02.2026")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await edit_valid_from_save(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.valid_from == date(2026, 2, 15)
        assert refreshed.expires_at == date(2026, 3, 17)  # still a 30-day course
        assert await state.get_data() == {}

    async def test_prescription_deleted_meanwhile_clears_state_without_error(self, db_session):
        await _add_prescription(db_session)
        message = _fake_message("15.02.2026")
        state = _FakeState(data={"lang": "en", "prescription_id": 999})

        await edit_valid_from_save(message, state, db_session)

        assert await state.get_data() == {}
        message.answer.assert_not_awaited()


class TestEditDurationFlow:
    async def test_start_shows_duration_options(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_ef_duration_{prescription.id}")

        await edit_duration_start(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None

    async def test_save_updates_expires_at_relative_to_valid_from(self, db_session):
        prescription = await _add_prescription(db_session, valid_from=date(2026, 1, 1), expires_at=date(2026, 1, 31))
        call, message = _fake_call(1, f"presc_edur_60_{prescription.id}")

        await edit_duration_save(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.expires_at == date(2026, 3, 2)  # valid_from + 60 days
        message.edit_text.assert_awaited_once()

    async def test_save_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_edur_60_999")

        await edit_duration_save(call, db_session)

        message.edit_text.assert_not_awaited()


class TestEditQuantityFlow:
    async def test_start_sets_state(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_ef_quantity_{prescription.id}")
        state = _FakeState()

        await edit_quantity_start(call, state, db_session)

        assert state.state == EditPrescription.quantity
        assert (await state.get_data())["prescription_id"] == prescription.id

    async def test_dash_clears_the_quantity_limit(self, db_session):
        prescription = await _add_prescription(db_session)
        await crud.update_prescription_field(db_session, prescription.id, "max_quantity", 30)
        message = _fake_message("-")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await edit_quantity_save(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.max_quantity is None

    async def test_invalid_quantity_shows_error_without_saving(self, db_session):
        prescription = await _add_prescription(db_session)
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await edit_quantity_save(message, state, db_session)

        assert await state.get_data() != {}  # state not cleared, still mid-flow

    async def test_valid_quantity_saves_and_clears_state(self, db_session):
        prescription = await _add_prescription(db_session)
        message = _fake_message("45")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await edit_quantity_save(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.max_quantity == 45
        assert await state.get_data() == {}
