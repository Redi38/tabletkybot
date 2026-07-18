"""Shared helpers used across the prescriptions handler modules."""

from datetime import date, datetime

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Prescription
from locales.texts import get_text


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_optional_int(text: str) -> int | None:
    """Returns None if the user sent '-' (skip), a number, or -1 on error."""
    text = text.strip()
    if text == "-":
        return None
    try:
        val = int(text)
        return val if val >= 0 else -1
    except ValueError:
        return -1


def parse_positive_int(text: str) -> int | None:
    """Returns a positive integer, or None if the input is invalid. Unlike
    parse_optional_int, there is NO "-" (skip) option here — the pack
    size is a required field."""
    try:
        val = int(text.strip())
        return val if val > 0 else None
    except ValueError:
        return None


def parse_optional_text(text: str) -> str | None:
    text = text.strip()
    return None if text == "-" else text


async def _base_ctx(call: CallbackQuery, session: AsyncSession) -> tuple[Message, str] | None:
    if not isinstance(call.message, Message) or not call.from_user:
        return None
    lang = await crud.get_user_language(session, call.from_user.id)
    return call.message, lang


async def _valid_prescription_ctx(
    call: CallbackQuery, session: AsyncSession
) -> tuple[Message, str, int, Prescription] | None:
    base = await _base_ctx(call, session)
    if not base or not call.data:
        return None
    msg, lang = base
    try:
        prescription_id = int(str(call.data).split("_")[-1])
    except ValueError:
        return None
    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        await call.answer(get_text(lang, "med_not_found"), show_alert=True)
        return None
    return msg, lang, prescription_id, prescription
