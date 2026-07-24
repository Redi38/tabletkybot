"""
Tests for handlers/errors.py: the global exception handler — silent vs.
logged-and-notify branches by exception type, message vs. callback_query
delivery, language fallback, and swallowing delivery failures.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InaccessibleMessage, Message
from sqlalchemy.exc import SQLAlchemyError

from database import crud
from handlers.errors import global_error_handler


def _fake_event(exception, *, message=None, callback_query=None):
    event = MagicMock()
    event.exception = exception
    event.update.event_type = "message" if message else "callback_query"
    event.update.message = message
    event.update.callback_query = callback_query
    return event


def _fake_message(user_id: int = 1):
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(id=user_id)
    message.answer = AsyncMock()
    return message


def _fake_callback_query(user_id: int = 1, message=None):
    call = MagicMock()
    call.from_user = MagicMock(id=user_id)
    call.message = message
    call.answer = AsyncMock()
    return call


class TestSilentlyIgnoredExceptions:
    """These exception types are noise (rate limits, blocked bot, bad
    input) and must never reach the user or trigger a reply."""

    async def test_retry_after_returns_without_notifying(self, db_session):
        message = _fake_message()
        event = _fake_event(TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=5), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_not_awaited()

    async def test_forbidden_error_returns_without_notifying(self, db_session):
        message = _fake_message()
        event = _fake_event(TelegramForbiddenError(method=MagicMock(), message="blocked"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_not_awaited()

    async def test_bad_request_returns_without_notifying(self, db_session):
        message = _fake_message()
        event = _fake_event(TelegramBadRequest(method=MagicMock(), message="bad"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_not_awaited()


class TestNotifyingExceptions:
    """Everything else gets logged and the user gets a generic error
    message, regardless of the specific exception type."""

    async def test_database_error_still_notifies_the_user(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        event = _fake_event(SQLAlchemyError("db exploded"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()

    async def test_unexpected_exception_still_notifies_the_user(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        event = _fake_event(RuntimeError("boom"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()

    async def test_connection_error_still_notifies_the_user(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        event = _fake_event(ConnectionError("unreachable"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()


class TestDeliveryTarget:
    async def test_message_event_replies_via_message_answer(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        event = _fake_event(RuntimeError("boom"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()

    async def test_callback_query_with_real_message_replies_via_message_answer(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        inner_message = _fake_message(user_id=1)
        call = _fake_callback_query(user_id=1, message=inner_message)
        event = _fake_event(RuntimeError("boom"), callback_query=call)

        await global_error_handler(event, db_session)

        inner_message.answer.assert_awaited_once()
        call.answer.assert_not_awaited()

    async def test_callback_query_with_inaccessible_message_uses_alert(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        inaccessible = MagicMock(spec=InaccessibleMessage)
        call = _fake_callback_query(user_id=1, message=inaccessible)
        event = _fake_event(RuntimeError("boom"), callback_query=call)

        await global_error_handler(event, db_session)

        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True


class TestLanguageFallback:
    async def test_uses_default_language_when_session_is_none(self):
        message = _fake_message(user_id=1)
        event = _fake_event(RuntimeError("boom"), message=message)

        await global_error_handler(event, None)

        message.answer.assert_awaited_once()

    async def test_falls_back_to_default_when_lookup_raises(self, db_session, monkeypatch):
        monkeypatch.setattr("handlers.errors.get_user_language", AsyncMock(side_effect=RuntimeError("db down")))
        message = _fake_message(user_id=1)
        event = _fake_event(RuntimeError("boom"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()

    async def test_no_user_id_available_still_notifies_with_default_language(self, db_session):
        message = _fake_message()
        message.from_user = None
        event = _fake_event(RuntimeError("boom"), message=message)

        await global_error_handler(event, db_session)

        message.answer.assert_awaited_once()


class TestDeliveryFailureIsSwallowed:
    async def test_telegram_api_error_while_notifying_does_not_propagate(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        message.answer = AsyncMock(side_effect=TelegramAPIError(method=MagicMock(), message="can't deliver"))
        event = _fake_event(RuntimeError("boom"), message=message)

        # Must not raise even though the notification itself fails.
        await global_error_handler(event, db_session)

    async def test_non_telegram_error_while_notifying_still_propagates(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(user_id=1)
        message.answer = AsyncMock(side_effect=RuntimeError("unexpected"))
        event = _fake_event(RuntimeError("boom"), message=message)

        with pytest.raises(RuntimeError):
            await global_error_handler(event, db_session)
