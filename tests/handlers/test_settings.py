"""
Tests for handlers/settings.py: the settings menu, name/timezone/language
editing, feedback forwarding, and the repeat-reminders on/off toggle.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database import crud
from handlers.settings import (
    edit_lang_start,
    edit_name_save,
    edit_name_start,
    edit_tz_save,
    edit_tz_start,
    feedback_save,
    feedback_start,
    settings_keyboard,
    settings_menu,
    toggle_repeat_reminders,
)


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
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()),
        ):
            await toggle_repeat_reminders(call, state, db_session, bot, MagicMock())

        assert await crud.get_repeat_reminders_enabled(db_session, 1) is False

    async def test_second_toggle_flips_it_back(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()),
        ):
            call1, _ = _fake_call(1, "toggle_repeat_reminders")
            await toggle_repeat_reminders(call1, state, db_session, bot, MagicMock())
            call2, _ = _fake_call(1, "toggle_repeat_reminders")
            await toggle_repeat_reminders(call2, state, db_session, bot, MagicMock())

        assert await crud.get_repeat_reminders_enabled(db_session, 1) is True

    async def test_edits_the_settings_message_in_place(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()),
        ):
            await toggle_repeat_reminders(call, state, db_session, bot, MagicMock())

        message.edit_text.assert_awaited_once()

    async def test_acknowledges_the_callback(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()),
        ):
            await toggle_repeat_reminders(call, state, db_session, bot, MagicMock())

        call.answer.assert_awaited_once()

    async def test_new_button_label_reflects_the_flipped_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()),
        ):
            await toggle_repeat_reminders(call, state, db_session, bot, MagicMock())

        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        assert "toggle_repeat_reminders" in callback_data

    async def test_turning_off_pauses_active_repeats(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "toggle_repeat_reminders")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()) as mock_pause,
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()) as mock_resume,
        ):
            await toggle_repeat_reminders(call, state, db_session, bot, MagicMock())

        mock_pause.assert_awaited_once_with(1)
        mock_resume.assert_not_awaited()

    async def test_turning_back_on_resumes_active_repeats(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        state = _fake_state()
        bot = create_autospec(Bot, instance=True)
        session_factory = MagicMock()

        with (
            patch("handlers.settings.pause_repeat_reminders_for_user", AsyncMock()),
            patch("handlers.settings.resume_repeat_reminders_for_user", AsyncMock()) as mock_resume,
        ):
            call1, _ = _fake_call(1, "toggle_repeat_reminders")
            await toggle_repeat_reminders(call1, state, db_session, bot, session_factory)
            call2, _ = _fake_call(1, "toggle_repeat_reminders")
            await toggle_repeat_reminders(call2, state, db_session, bot, session_factory)

        mock_resume.assert_awaited_once_with(bot, 1, session_factory)


def _fake_text_message(user_id: int, text: str):
    message = create_autospec(Message, instance=True)
    message.text = text
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.answer = AsyncMock()
    return message


def _fake_state_with_data(data: dict):
    state = create_autospec(FSMContext, instance=True)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    return state


class TestEditNameStart:
    async def test_prompts_for_new_name_and_sets_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "set_name")
        state = _fake_state()

        await edit_name_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        state.set_state.assert_awaited_once()

    async def test_noop_when_message_missing(self, db_session):
        call, _ = _fake_call(1, "set_name")
        call.message = None
        state = _fake_state()

        await edit_name_start(call, state, db_session)

        state.set_state.assert_not_awaited()


class TestEditNameSave:
    async def test_updates_the_stored_name(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Old Name")
        message = _fake_text_message(1, "New Name")
        state = _fake_state_with_data({"lang": "en"})

        await edit_name_save(message, state, db_session)

        user = await crud.get_or_create_user(db_session, 1, "tester", "Old Name")
        assert user.full_name == "New Name"
        state.clear.assert_awaited_once()
        message.answer.assert_awaited_once()

    async def test_noop_when_text_missing(self, db_session):
        message = _fake_text_message(1, "")
        message.text = None
        state = _fake_state_with_data({"lang": "en"})

        await edit_name_save(message, state, db_session)

        state.clear.assert_not_awaited()


class TestEditTzStart:
    async def test_prompts_for_new_timezone_and_sets_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "set_tz")
        state = _fake_state()

        await edit_tz_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        state.set_state.assert_awaited_once()


class TestEditTzSave:
    async def test_rejects_unresolvable_place(self, db_session, mock_bot):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_text_message(1, "Nowhereville")
        state = _fake_state_with_data({"lang": "en"})

        with patch("handlers.settings.resolve_timezone_from_place", AsyncMock(return_value=None)):
            await edit_tz_save(message, state, db_session, mock_bot)

        message.answer.assert_awaited_once()
        state.clear.assert_not_awaited()
        user = await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        assert user.timezone is None

    async def test_updates_timezone_and_reschedules_medicines(self, db_session, mock_bot):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_text_message(1, "Kyiv")
        state = _fake_state_with_data({"lang": "en"})

        with (
            patch("handlers.settings.resolve_timezone_from_place", AsyncMock(return_value="Europe/Kyiv")),
            patch("handlers.settings.add_reminders_for_medicine") as mock_add_reminders,
        ):
            await edit_tz_save(message, state, db_session, mock_bot)

        user = await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        assert user.timezone == "Europe/Kyiv"
        state.clear.assert_awaited_once()
        message.answer.assert_awaited_once()
        mock_add_reminders.assert_not_called()  # no active medicines for this user


class TestFeedbackStart:
    async def test_prompts_for_feedback_and_sets_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "set_feedback")
        state = _fake_state()

        await feedback_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        state.set_state.assert_awaited_once()


class TestFeedbackSave:
    def _config(self, admin_chat_id):
        return Config(bot_token="t", webhook_host="https://example.com", admin_chat_id=admin_chat_id)

    async def test_warns_when_admin_chat_id_not_configured(self, mock_bot):
        message = _fake_text_message(1, "great bot!")
        state = _fake_state_with_data({"lang": "en"})

        await feedback_save(message, state, mock_bot, self._config(None))

        state.clear.assert_awaited_once()
        mock_bot.send_message.assert_not_awaited()
        message.answer.assert_awaited_once()

    async def test_forwards_feedback_to_admin_on_success(self, mock_bot):
        message = _fake_text_message(1, "great bot!")
        state = _fake_state_with_data({"lang": "en"})

        await feedback_save(message, state, mock_bot, self._config(12345))

        mock_bot.send_message.assert_awaited_once()
        assert mock_bot.send_message.call_args.kwargs["chat_id"] == 12345
        message.answer.assert_awaited_once()

    async def test_falls_back_to_error_message_when_send_fails(self, mock_bot):
        message = _fake_text_message(1, "great bot!")
        state = _fake_state_with_data({"lang": "en"})
        mock_bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))

        await feedback_save(message, state, mock_bot, self._config(12345))

        message.answer.assert_awaited_once()

    async def test_noop_when_text_missing(self, mock_bot):
        message = _fake_text_message(1, "")
        message.text = None
        state = _fake_state_with_data({"lang": "en"})

        await feedback_save(message, state, mock_bot, self._config(12345))

        state.clear.assert_not_awaited()


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
