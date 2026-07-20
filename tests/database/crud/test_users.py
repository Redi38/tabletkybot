"""Tests for database/crud/users.py against a real (in-memory SQLite) async session.

See the `db_session` fixture in conftest.py.
"""

import database.crud as crud


class TestUsers:
    async def test_get_or_create_user_creates_new(self, db_session):
        user = await crud.get_or_create_user(
            db_session,
            user_id=1,
            username="redi",
            full_name="Redi Test",
        )
        assert user.id == 1
        assert user.username == "redi"
        assert user.language == "ua"  # default

    async def test_get_or_create_user_returns_existing(self, db_session):
        first = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
        second = await crud.get_or_create_user(db_session, 1, "someone_else", "Different Name")

        assert second.id == first.id
        # Should NOT overwrite the existing user's data with the new args
        assert second.username == "redi"

    async def test_get_all_users_returns_all(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.get_or_create_user(db_session, 2, "b", "B")

        users = await crud.get_all_users(db_session)
        assert {u.id for u in users} == {1, 2}

    async def test_update_user_timezone(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.update_user_timezone(db_session, 1, "America/New_York")

        tz = await crud.get_user_timezone(db_session, 1)
        assert tz == "America/New_York"

    async def test_update_user_timezone_nonexistent_user_no_error(self, db_session):
        # Should silently no-op rather than raising
        await crud.update_user_timezone(db_session, 999, "America/New_York")

    async def test_get_user_timezone_defaults_when_not_set(self, db_session):
        # A fresh user has a DB-level default of "Europe/Kyiv" set at insert time
        await crud.get_or_create_user(db_session, 1, "a", "A")
        tz = await crud.get_user_timezone(db_session, 1)
        assert tz == "Europe/Kyiv"

    async def test_get_user_language_defaults_for_unknown_user(self, db_session):
        # No such user at all -> falls back to "ua"
        lang = await crud.get_user_language(db_session, 999)
        assert lang == "ua"
