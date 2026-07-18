"""Shared helpers used across the medicines handler modules."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Medicine
from locales.texts import get_text


def is_valid_time(time_str: str) -> bool:
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False


def parse_times(times_str: str) -> list[str] | None:
    raw = [t.strip() for t in times_str.replace(";", ",").split(",")]
    valid = [t for t in raw if is_valid_time(t)]
    return valid if valid and len(valid) == len(raw) else None


def parse_int(val_str: str) -> int | None:
    try:
        val = int(val_str)
        return val if val >= 0 else None
    except ValueError:
        return None


async def _safe_edit_text(msg: Message, text: str, **kwargs) -> None:
    """
    Wraps msg.edit_text to swallow Telegram's "message is not modified" error,
    which happens when a duplicate button tap (double-tap, network retry)
    tries to set exactly the same text/markup that a previous, already-processed
    tap set moments earlier. Any other TelegramBadRequest is re-raised as-is.
    """
    try:
        await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _base_ctx(call: CallbackQuery, session: AsyncSession) -> tuple[Message, str] | None:
    if not isinstance(call.message, Message) or not call.from_user:
        return None
    lang = await crud.get_user_language(session, call.from_user.id)
    return call.message, lang


async def _valid_medicine_ctx(call: CallbackQuery, session: AsyncSession) -> tuple[Message, str, int, Medicine] | None:
    base = await _base_ctx(call, session)
    if not base or not call.data:
        return None
    msg, lang = base
    try:
        medicine_id = int(str(call.data).split("_")[-1])
    except ValueError:
        return None
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if not medicine:
        await call.answer(get_text(lang, "med_not_found"), show_alert=True)
        return None
    return msg, lang, medicine_id, medicine
