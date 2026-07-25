from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from database import crud


def _parse_date_flexible(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(value, min_value: int | None = None, max_value: int | None = None) -> int | None:
    """
    Safe conversion to int with bounds checking. Returns None if the value
    can't be converted or is out of range — so we NEVER let a string
    or an extreme number through into the DB (as happened with course_duration).
    """
    try:
        result = int(value)
    except (TypeError, ValueError):  # fmt: skip
        return None
    if min_value is not None and result < min_value:
        return None
    if max_value is not None and result > max_value:
        return None
    return result


async def _find_medicine(session: AsyncSession, user_id: int, identifier: str):
    medicines = await crud.get_user_medicines(session, user_id, active_only=True)
    identifier_lower = identifier.strip().lower()
    matches = [m for m in medicines if m.name.lower() == identifier_lower]
    if not matches:
        matches = [m for m in medicines if identifier_lower in m.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


async def _find_prescription(session: AsyncSession, user_id: int, identifier: str):
    prescriptions = await crud.get_user_prescriptions(session, user_id, active_only=True)
    identifier_lower = identifier.strip().lower()
    matches = [p for p in prescriptions if p.medicine_name.lower() == identifier_lower]
    if not matches:
        matches = [p for p in prescriptions if identifier_lower in p.medicine_name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None
