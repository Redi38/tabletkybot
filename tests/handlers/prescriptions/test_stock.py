"""
Tests for handlers/prescriptions/stock.py: adding a purchased quantity to
a medicine's stock (decline / pack size -> pick medicine -> stock updated),
and the finish-archive/keep-active follow-up after a full purchase.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.states import AddPurchaseToStock
from handlers.prescriptions.stock import (
    finish_archive,
    finish_keep,
    stock_add_declined,
    stock_add_start,
    stock_medicine_picked,
    stock_pack_size_entered,
)


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
    message.delete = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_medicine(db_session, user_id=1, name="Ibuprofen", stock_amount=10):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name=name,
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=5,
        stock_amount=stock_amount,
    )
    await db_session.commit()
    return medicine


async def _add_prescription(db_session, user_id=1):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 1, 31),
    )
    await db_session.commit()
    return prescription


class TestStockAddDeclined:
    async def test_deletes_the_prompt_message(self):
        call, message = _fake_call(1, "presc_stock_no")

        await stock_add_declined(call)

        message.delete.assert_awaited_once()
        call.answer.assert_awaited_once()

    async def test_survives_delete_failing(self):
        call, message = _fake_call(1, "presc_stock_no")
        message.delete.side_effect = RuntimeError("message too old")

        await stock_add_declined(call)

        call.answer.assert_awaited_once()


class TestStockAddStart:
    async def test_asks_for_pack_size_and_stores_amount(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_stock_yes_5_10")
        state = _FakeState()

        await stock_add_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == AddPurchaseToStock.waiting_pack_size
        assert (await state.get_data())["purchased_amount"] == 10


class TestStockPackSizeEntered:
    async def test_invalid_pack_size_shows_error(self, db_session):
        await _add_medicine(db_session)
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "purchased_amount": 10})

        await stock_pack_size_entered(message, state, db_session)

        message.answer.assert_awaited_once()
        assert "reply_markup" not in message.answer.call_args.kwargs

    async def test_no_active_medicines_clears_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "purchased_amount": 5})

        await stock_pack_size_entered(message, state, db_session)

        assert await state.get_data() == {}

    async def test_valid_pack_size_shows_medicine_picker(self, db_session):
        await _add_medicine(db_session, name="Ibuprofen")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "purchased_amount": 2})

        await stock_pack_size_entered(message, state, db_session)

        data = await state.get_data()
        assert data["total"] == 20  # 2 packs * 10 units
        assert state.state == AddPurchaseToStock.waiting_medicine_choice
        assert message.answer.call_args.kwargs["reply_markup"] is not None


class TestStockMedicinePicked:
    async def test_adds_the_computed_total_to_the_chosen_medicines_stock(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=10)
        call, message = _fake_call(1, f"presc_stock_pick_{medicine.id}")
        state = _FakeState(data={"lang": "en", "total": 20})

        await stock_medicine_picked(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 30
        message.edit_text.assert_awaited_once()
        assert await state.get_data() == {}

    async def test_unknown_medicine_still_reports_gracefully(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_stock_pick_999")
        state = _FakeState(data={"lang": "en", "total": 20})

        await stock_medicine_picked(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert await state.get_data() == {}


class TestFinishArchive:
    async def test_archives_the_prescription(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, f"presc_finish_archive_{prescription.id}")

        await finish_archive(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_active is False
        message.edit_text.assert_awaited_once()

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_finish_archive_999")

        await finish_archive(call, db_session)

        message.edit_text.assert_not_awaited()


class TestFinishKeep:
    async def test_shows_kept_active_confirmation(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_finish_keep_5")

        await finish_keep(call, db_session)

        message.edit_text.assert_awaited_once()
        call.answer.assert_awaited_once()
