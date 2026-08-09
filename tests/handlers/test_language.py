"""
Tests for handlers/language.py: the language-selection callback
(set_language).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.language import set_language
from locales.texts import DEFAULT_LANG


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.delete = AsyncMock()
    message.answer = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


class TestSetLanguage:
    """
    Regression coverage: changing language used to leave the old
    "choose your language" message (with its 3 inline buttons) sitting in
    the chat while a brand-new confirmation message was sent alongside it,
    cluttering the conversation. The old message should now be deleted.
    """

    async def test_deletes_the_language_selection_message(self, db_session):
        call, message = _fake_call(1, "lang_en")

        await set_language(call, db_session)

        message.delete.assert_awaited_once()

    async def test_sends_exactly_one_confirmation_message(self, db_session):
        call, message = _fake_call(1, "lang_en")

        await set_language(call, db_session)

        message.answer.assert_awaited_once()
        assert message.answer.call_args.kwargs["reply_markup"] is not None

    async def test_persists_the_chosen_language(self, db_session):
        call, _ = _fake_call(1, "lang_ru")

        await set_language(call, db_session)

        assert await crud.get_user_language(db_session, 1) == "ru"

    async def test_survives_delete_failing_with_telegram_bad_request(self, db_session):
        call, message = _fake_call(1, "lang_en")
        message.delete.side_effect = TelegramBadRequest(method=MagicMock(), message="message to delete not found")

        await set_language(call, db_session)

        # Deletion failing (e.g. message too old, already gone) must not stop
        # the confirmation from being sent.
        message.answer.assert_awaited_once()
        assert await crud.get_user_language(db_session, 1) == "en"

    async def test_acknowledges_the_callback(self, db_session):
        call, _ = _fake_call(1, "lang_en")

        await set_language(call, db_session)

        call.answer.assert_awaited_once()

    async def test_ignores_callback_without_data(self, db_session):
        call, message = _fake_call(1, "lang_en")
        call.data = None

        await set_language(call, db_session)

        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()


class TestSetLanguageAlreadySelected:
    """
    Regression coverage: picking the language that is already active used
    to silently re-save the same value and send a normal confirmation.
    It should now show an alert instead, and leave the selection message
    untouched.
    """

    async def test_shows_alert_instead_of_saving_again(self, db_session):
        call, _ = _fake_call(1, "lang_en")
        await set_language(call, db_session)
        call.answer.reset_mock()

        call2, message2 = _fake_call(1, "lang_en")
        await set_language(call2, db_session)

        call2.answer.assert_awaited_once()
        assert call2.answer.call_args.kwargs.get("show_alert") is True
        message2.delete.assert_not_awaited()
        message2.answer.assert_not_awaited()

    async def test_defaults_to_default_lang_when_unset(self, db_session):
        call, message = _fake_call(2, f"lang_{DEFAULT_LANG}")

        await set_language(call, db_session)

        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True
        message.delete.assert_not_awaited()
