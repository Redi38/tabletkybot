"""
Tests for handlers/prescriptions/restore.py: restoring an archived
prescription with a fresh valid_from/duration/quantity, which also
resets its purchase progress.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.restore import restore_duration, restore_quantity, restore_start, restore_valid_from
from handlers.prescriptions.states import RestorePrescription


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


async def _add_archived_prescription(db_session, user_id=1):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name="Amoxicillin",
        valid_from=date(2025, 1, 1),
        expires_at=date(2025, 1, 31),
    )
    await crud.mark_prescription_purchased(db_session, prescription.id, 30)
    await crud.archive_prescription(db_session, prescription.id)
    await db_session.commit()
    return prescription


class TestRestoreStart:
    async def test_asks_for_valid_from_and_sets_state(self, db_session):
        prescription = await _add_archived_prescription(db_session)
        call, message = _fake_call(1, f"presc_restore_{prescription.id}")
        state = _FakeState()

        await restore_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == RestorePrescription.valid_from
        assert (await state.get_data())["prescription_id"] == prescription.id

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_restore_999")
        state = _FakeState()

        await restore_start(call, state, db_session)

        message.edit_text.assert_not_awaited()


class TestRestoreValidFrom:
    async def test_invalid_date_shows_error_and_stays(self):
        message = _fake_message("not-a-date")
        state = _FakeState(data={"lang": "en"})

        await restore_valid_from(message, state)

        assert "valid_from" not in await state.get_data()
        assert state.state is None

    async def test_valid_date_shows_duration_choice(self):
        message = _fake_message("01.06.2026")
        state = _FakeState(data={"lang": "en"})

        await restore_valid_from(message, state)

        data = await state.get_data()
        assert data["valid_from"] == "2026-06-01"
        assert state.state == RestorePrescription.duration
        assert message.answer.call_args.kwargs["reply_markup"] is not None


class TestRestoreDuration:
    async def test_30_days_computes_expiry_and_asks_for_quantity(self):
        call, message = _fake_call(1, "presc_dur_30")
        state = _FakeState(data={"lang": "en", "valid_from": "2026-06-01"})

        await restore_duration(call, state)

        data = await state.get_data()
        assert data["expires"] == "2026-07-01"
        assert state.state == RestorePrescription.quantity
        message.edit_text.assert_awaited_once()


class TestRestoreQuantity:
    async def test_invalid_quantity_shows_error_without_saving(self, db_session):
        prescription = await _add_archived_prescription(db_session)
        message = _fake_message("not-a-number")
        state = _FakeState(
            data={
                "lang": "en",
                "prescription_id": prescription.id,
                "valid_from": "2026-06-01",
                "expires": "2026-07-01",
            }
        )

        await restore_quantity(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_active is False  # still archived, restore did not go through

    async def test_dash_restores_with_no_quantity_limit(self, db_session):
        prescription = await _add_archived_prescription(db_session)
        message = _fake_message("-")
        state = _FakeState(
            data={
                "lang": "en",
                "prescription_id": prescription.id,
                "valid_from": "2026-06-01",
                "expires": "2026-07-01",
            }
        )

        await restore_quantity(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_active is True
        assert refreshed.max_quantity is None

    async def test_restore_resets_purchase_progress(self, db_session):
        prescription = await _add_archived_prescription(db_session)  # was fully purchased before archiving
        message = _fake_message("30")
        state = _FakeState(
            data={
                "lang": "en",
                "prescription_id": prescription.id,
                "valid_from": "2026-06-01",
                "expires": "2026-07-01",
            }
        )

        await restore_quantity(message, state, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.valid_from == date(2026, 6, 1)
        assert refreshed.expires_at == date(2026, 7, 1)
        assert refreshed.max_quantity == 30
        assert refreshed.purchased_quantity == 0
        assert refreshed.is_fully_purchased is False

    async def test_clears_state_after_restoring(self, db_session):
        prescription = await _add_archived_prescription(db_session)
        message = _fake_message("-")
        state = _FakeState(
            data={
                "lang": "en",
                "prescription_id": prescription.id,
                "valid_from": "2026-06-01",
                "expires": "2026-07-01",
            }
        )

        await restore_quantity(message, state, db_session)

        assert await state.get_data() == {}
        assert state.state is None
