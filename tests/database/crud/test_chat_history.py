"""Tests for database/crud/chat_history.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

import database.crud as crud


class TestChatHistory:
    async def test_add_and_get_roundtrip(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "Hello there")
        await crud.add_chat_message(db_session, 1, "assistant", "Hi! How can I help?")

        history = await crud.get_chat_history(db_session, 1, limit=10)

        assert len(history) == 2
        # returned in chronological order (oldest first)
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello there"
        assert history[1]["role"] == "assistant"

    async def test_keep_last_trims_old_messages(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        for i in range(5):
            await crud.add_chat_message(db_session, 1, "user", f"msg {i}", keep_last=3)

        history = await crud.get_chat_history(db_session, 1, limit=10)
        assert len(history) == 3
        # the 3 most recent should be kept, in order
        assert [h["content"] for h in history] == ["msg 2", "msg 3", "msg 4"]

    async def test_clear_chat_history_removes_all(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "Hello")

        await crud.clear_chat_history(db_session, 1)

        history = await crud.get_chat_history(db_session, 1)
        assert history == []

    async def test_content_is_encrypted_at_rest(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "a very private message")
        await db_session.commit()

        from sqlalchemy import text

        raw = await db_session.execute(text("SELECT content FROM chat_history"))
        raw_value = raw.scalar_one()

        assert raw_value != "a very private message"
