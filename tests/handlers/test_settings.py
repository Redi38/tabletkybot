"""
Tests for handlers/settings.py: the settings menu, the language-selection
entry point, and the repeat-reminders on/off toggle.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.settings import edit_lang_start, settings_keyboard, settings_menu, toggle_repeat_reminders


def _fake_message_for_menu(user_id: int):
    message = create_autospec(Message, instance=True)
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


def _fake_state():
    state = create_autospec(FSMContext, instance=True)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    return state


class TestSettingsKeyboard:
    def test_shows_on_label_when_repeats_enabled(self):
        keyboard = settings_keyboard("en", repeat_reminders_enabled=True)
        toggle_row = keyboard.inline_keyboard[3]
        assert toggle_row[0].callback_data == "toggle_repeat_reminders"
        # "on" state should be the button that lets you turn it *off*
        assert toggle_row[0].text != settings_keyboard("en", repeat_reminders_enabled=False).inline_keyboard[3][0].text

    def test_toggle_button_always_present(self):
        for enabled in (True, False):
            keyboard = settings_keyboard("en", repeat_reminders_enabled=enabled)
            callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
            assert "toggle_repeat_reminders" in callback_data


class TestSettingsMenu:
    async def test_shows_repeat_reminders_state_matching_the_user(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message_for_menu(1)

        await settings_menu(message, db_session)

        message.answer.assert_awaited_once()
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert "toggle_repeat_reminders" in callback_data


class TestToggleRepeatReminders:
    async def test_flips_the_stored_preference(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()

        await toggle_repeat_reminders(call, state, db_session)

        assert await crud.get_repeat_reminders_enabled(db_session, 1) is False

    async def test_second_toggle_flips_it_back(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        state = _fake_state()

        call1, _ = _fake_call(1, "toggle_repeat_reminders")
        await toggle_repeat_reminders(call1, state, db_session)
        call2, _ = _fake_call(1, "toggle_repeat_reminders")
        await toggle_repeat_reminders(call2, state, db_session)

        assert await crud.get_repeat_reminders_enabled(db_session, 1) is True

    async def test_edits_the_settings_message_in_place(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()

        await toggle_repeat_reminders(call, state, db_session)

        message.edit_text.assert_awaited_once()

    async def test_acknowledges_the_callback(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()

        await toggle_repeat_reminders(call, state, db_session)

        call.answer.assert_awaited_once()

    async def test_new_button_label_reflects_the_flipped_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()

        await toggle_repeat_reminders(call, state, db_session)

        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert "toggle_repeat_reminders" in callback_data


class TestEditLangStart:
    async def test_shows_the_three_language_options(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "set_lang")
        state = _fake_state()

        await edit_lang_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert callback_data == {"lang_ua", "lang_en", "lang_ru"}
