"""
Tests for handlers/prescriptions/buy.py: marking a prescription purchase
(ask amount -> validate against the pack limit -> confirm -> record it,
optionally offering to archive once fully purchased).
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.buy import buy_amount_entered, buy_ask_amount, buy_confirm
from handlers.prescriptions.states import BuyPrescription


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
    message.answer = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_prescription(db_session, user_id=1, max_quantity=None):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 1, 31),
        max_quantity=max_quantity,
    )
    await db_session.commit()
    return prescription


class TestBuyAskAmount:
    async def test_asks_without_limit_wording_when_unlimited(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=None)
        call, message = _fake_call(1, f"presc_buy_ask_{prescription.id}")
        state = _FakeState()

        await buy_ask_amount(call, state, db_session)

        message.answer.assert_awaited_once()
        assert state.state == BuyPrescription.waiting_amount
        assert (await state.get_data())["prescription_id"] == prescription.id

    async def test_asks_with_remaining_amount_when_limited(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        call, message = _fake_call(1, f"presc_buy_ask_{prescription.id}")
        state = _FakeState()

        await buy_ask_amount(call, state, db_session)

        message.answer.assert_awaited_once()
        assert state.state == BuyPrescription.waiting_amount


class TestBuyAmountEntered:
    async def test_invalid_amount_shows_error(self, db_session):
        prescription = await _add_prescription(db_session)
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await buy_amount_entered(message, state, db_session)

        message.answer.assert_awaited_once()

    async def test_zero_or_negative_amount_is_rejected(self, db_session):
        prescription = await _add_prescription(db_session)
        message = _fake_message("0")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await buy_amount_entered(message, state, db_session)

        message.answer.assert_awaited_once()

    async def test_amount_exceeding_the_remaining_limit_is_rejected(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        message = _fake_message("31")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await buy_amount_entered(message, state, db_session)

        message.answer.assert_awaited_once()
        # rejection message, not a confirm keyboard
        assert "reply_markup" not in message.answer.call_args.kwargs

    async def test_amount_within_the_limit_shows_confirm_and_clears_state(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await buy_amount_entered(message, state, db_session)

        assert message.answer.call_args.kwargs["reply_markup"] is not None
        assert await state.get_data() == {}

    async def test_unlimited_prescription_accepts_any_positive_amount(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=None)
        message = _fake_message("1000")
        state = _FakeState(data={"lang": "en", "prescription_id": prescription.id})

        await buy_amount_entered(message, state, db_session)

        assert message.answer.call_args.kwargs["reply_markup"] is not None

    async def test_prescription_deleted_meanwhile_clears_state_without_error(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "prescription_id": 999})

        await buy_amount_entered(message, state, db_session)

        assert await state.get_data() == {}


class TestBuyConfirm:
    async def test_records_the_purchase_and_shows_stock_prompt(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        call, message = _fake_call(1, f"presc_buy_confirm_{prescription.id}_10")

        await buy_confirm(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.purchased_quantity == 10
        # edit_text for the success message, answer for the stock-add prompt
        message.edit_text.assert_awaited_once()
        message.answer.assert_awaited_once()

    async def test_fully_purchasing_offers_archive_or_keep(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=10)
        call, message = _fake_call(1, f"presc_buy_confirm_{prescription.id}_10")

        await buy_confirm(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_fully_purchased is True
        # one answer() for the stock prompt, one more for the archive/keep prompt
        assert message.answer.await_count == 2

    async def test_partial_purchase_does_not_offer_archive(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        call, message = _fake_call(1, f"presc_buy_confirm_{prescription.id}_10")

        await buy_confirm(call, db_session)

        assert message.answer.await_count == 1

    async def test_no_op_when_prescription_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_buy_confirm_999_10")

        await buy_confirm(call, db_session)

        message.edit_text.assert_not_awaited()
        message.answer.assert_not_awaited()
