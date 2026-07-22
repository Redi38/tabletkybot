"""CRUD operations for User accounts (language/timezone preferences)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from locales.texts import DEFAULT_LANG


# ─── Users ────────────────────────────────────────────────────────────
async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is not None:
        return user

    new_user = User(id=user_id, username=username, full_name=full_name)
    session.add(new_user)
    await session.flush()
    await session.refresh(new_user)
    return new_user


async def get_all_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User))
    return list(result.scalars().all())


async def _get_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    return user


async def update_user_timezone(session: AsyncSession, user_id: int, new_timezone: str) -> None:
    user = await _get_user(session, user_id)
    if user:
        user.timezone = new_timezone
        await session.flush()


async def update_user_language(session: AsyncSession, user_id: int, language: str) -> None:
    user = await _get_user(session, user_id)
    if user:
        user.language = language
        await session.flush()


async def get_user_language(session: AsyncSession, user_id: int) -> str:
    user = await _get_user(session, user_id)
    return str(user.language) if user and user.language else DEFAULT_LANG


async def get_user_timezone(session: AsyncSession, user_id: int) -> str:
    user = await _get_user(session, user_id)
    return str(user.timezone) if user and user.timezone else "Europe/Kyiv"


async def get_repeat_reminders_enabled(session: AsyncSession, user_id: int) -> bool:
    user = await _get_user(session, user_id)
    return bool(user.repeat_reminders_enabled) if user else True


async def toggle_repeat_reminders(session: AsyncSession, user_id: int) -> bool:
    """Flip the repeat-reminders flag and return the new value."""
    user = await _get_user(session, user_id)
    if user is None:
        return True
    user.repeat_reminders_enabled = not user.repeat_reminders_enabled
    await session.flush()
    return bool(user.repeat_reminders_enabled)
