"""Tests for middleware/logging_context.py: ActionLoggingMiddleware."""

from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from middleware.logging_context import ActionLoggingMiddleware

CHAT = Chat(id=1, type="private")
USER = User(id=42, is_bot=False, first_name="Test", username="tester")


@pytest.mark.asyncio
async def test_logs_button_press(caplog):
    mw = ActionLoggingMiddleware()
    call = CallbackQuery(
        id="1",
        from_user=USER,
        chat_instance="x",
        data="med_list",
        message=Message(message_id=1, date=0, chat=CHAT),
    )
    update = Update(update_id=1, callback_query=call)
    handler = AsyncMock(return_value="ok")

    with caplog.at_level("INFO", logger="bot.actions"):
        result = await mw(handler, update, {})

    assert result == "ok"
    handler.assert_awaited_once_with(update, {})
    assert "med_list" in caplog.text
    assert "42" in caplog.text
    assert "tester" in caplog.text


@pytest.mark.asyncio
async def test_logs_text_message(caplog):
    mw = ActionLoggingMiddleware()
    msg = Message(message_id=1, date=0, chat=CHAT, from_user=USER, text="Ibuprofen")
    update = Update(update_id=2, message=msg)
    handler = AsyncMock(return_value=None)

    with caplog.at_level("INFO", logger="bot.actions"):
        await mw(handler, update, {})

    assert "Ibuprofen" in caplog.text


@pytest.mark.asyncio
async def test_truncates_long_message_text(caplog):
    mw = ActionLoggingMiddleware()
    long_text = "A" * 200
    msg = Message(message_id=1, date=0, chat=CHAT, from_user=USER, text=long_text)
    update = Update(update_id=3, message=msg)
    handler = AsyncMock(return_value=None)

    with caplog.at_level("INFO", logger="bot.actions"):
        await mw(handler, update, {})

    logged_line = [r for r in caplog.records if r.name == "bot.actions"][0].message
    assert "A" * 81 not in logged_line  # never logs more than _MAX_TEXT_LEN + ellipsis
    assert "…" in logged_line


@pytest.mark.asyncio
async def test_does_not_swallow_or_alter_handler_result():
    mw = ActionLoggingMiddleware()
    update = Update(update_id=4, message=Message(message_id=1, date=0, chat=CHAT, from_user=USER, text="hi"))
    handler = AsyncMock(return_value={"key": "value"})

    result = await mw(handler, update, {"some": "data"})

    assert result == {"key": "value"}
    handler.assert_awaited_once_with(update, {"some": "data"})


@pytest.mark.asyncio
async def test_ignores_updates_with_no_message_or_callback(caplog):
    """E.g. an edited_message or other update subtype we don't specifically log."""
    mw = ActionLoggingMiddleware()
    update = Update(update_id=5)
    handler = AsyncMock(return_value=None)

    with caplog.at_level("INFO", logger="bot.actions"):
        await mw(handler, update, {})

    assert caplog.text == ""
