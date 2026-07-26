"""Small helpers shared across handler modules."""

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_or_create_user
from database.models import User
from locales.texts import user_lang


async def get_user_and_language(session: AsyncSession, message: Message) -> tuple[User, str]:
    """
    Ensures a User row exists for message.from_user (creating it on first
    contact) and resolves their stored language preference. This exact
    get_or_create_user + user_lang pairing used to be duplicated in every
    handler that greets or replies to a user.
    """
    assert message.from_user is not None
    user = await get_or_create_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    return user, user_lang(user)
