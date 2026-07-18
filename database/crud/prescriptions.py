"""CRUD operations for prescriptions."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Prescription, User


# ─── Prescriptions ─────────────────────────────────────────────────────────────
async def add_prescription(
    session: AsyncSession,
    user_id: int,
    medicine_name: str,
    valid_from: date,
    expires_at: date,
    max_quantity: int | None = None,
    reminder_days_before: int = 3,
) -> Prescription:
    prescription = Prescription(
        user_id=user_id,
        medicine_name=medicine_name,
        valid_from=valid_from,
        expires_at=expires_at,
        max_quantity=max_quantity,
        reminder_days_before=reminder_days_before,
    )
    session.add(prescription)
    await session.flush()
    await session.refresh(prescription)
    return prescription


async def get_user_prescriptions(session: AsyncSession, user_id: int, active_only: bool = True) -> list[Prescription]:
    stmt = select(Prescription).where(Prescription.user_id == user_id)
    if active_only:
        stmt = stmt.where(Prescription.is_active.is_(True))
    stmt = stmt.order_by(Prescription.expires_at)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_prescription_by_id(session: AsyncSession, prescription_id: int) -> Prescription | None:
    result = await session.execute(select(Prescription).where(Prescription.id == prescription_id))
    return result.scalar_one_or_none()


async def update_prescription_field(session: AsyncSession, prescription_id: int, field: str, value) -> bool:
    prescription = await get_prescription_by_id(session, prescription_id)
    if not prescription:
        return False
    setattr(prescription, field, value)
    await session.flush()
    return True


async def mark_prescription_purchased(session: AsyncSession, prescription_id: int, amount: int) -> dict:
    """Adds amount to purchased_quantity. Returns the state to show to the user."""
    prescription = await get_prescription_by_id(session, prescription_id)
    if not prescription:
        return {"success": False}

    prescription.purchased_quantity = (prescription.purchased_quantity or 0) + amount

    if prescription.max_quantity is not None and prescription.purchased_quantity >= prescription.max_quantity:
        prescription.is_fully_purchased = True

    await session.flush()
    return {
        "success": True,
        "medicine_name": prescription.medicine_name,
        "purchased_quantity": prescription.purchased_quantity,
        "max_quantity": prescription.max_quantity,
        "is_fully_purchased": prescription.is_fully_purchased,
    }


async def archive_prescription(session: AsyncSession, prescription_id: int) -> bool:
    return await update_prescription_field(session, prescription_id, "is_active", False)


async def delete_prescription(session: AsyncSession, prescription_id: int) -> bool:
    prescription = await get_prescription_by_id(session, prescription_id)
    if not prescription:
        return False
    await session.delete(prescription)
    await session.flush()
    return True


async def get_prescriptions_needing_reminder(session: AsyncSession) -> list[tuple[Prescription, User]]:
    """
    Returns all active, not-yet-fully-purchased prescriptions that haven't had
    a reminder sent, together with the user (for timezone/language). The exact
    check of "whether today is the day" is done by the scheduler, since it
    depends on the specific user's timezone.
    """
    stmt = (
        select(Prescription, User)
        .join(User, Prescription.user_id == User.id)
        .where(
            Prescription.is_active.is_(True),
            Prescription.is_fully_purchased.is_(False),
            Prescription.reminder_sent.is_(False),
        )
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def mark_prescription_reminder_sent(session: AsyncSession, prescription_id: int) -> None:
    await update_prescription_field(session, prescription_id, "reminder_sent", True)


async def get_expired_active_prescriptions(session: AsyncSession) -> list[tuple[Prescription, User]]:
    """
    Returns all active prescriptions whose validity period has already expired
    (expires_at < today), together with the user — for auto-archiving and notification.
    """
    today = date.today()
    stmt = (
        select(Prescription, User)
        .join(User, Prescription.user_id == User.id)
        .where(
            Prescription.is_active.is_(True),
            Prescription.expires_at < today,
        )
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def get_user_archived_prescriptions(session: AsyncSession, user_id: int) -> list[Prescription]:
    stmt = (
        select(Prescription)
        .where(Prescription.user_id == user_id, Prescription.is_active.is_(False))
        .order_by(Prescription.expires_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def restore_prescription(
    session: AsyncSession,
    prescription_id: int,
    valid_from: date,
    expires_at: date,
    max_quantity: int | None,
) -> bool:
    prescription = await get_prescription_by_id(session, prescription_id)
    if not prescription:
        return False
    prescription.valid_from = valid_from
    prescription.expires_at = expires_at
    prescription.max_quantity = max_quantity
    prescription.purchased_quantity = 0
    prescription.is_fully_purchased = False
    prescription.reminder_sent = False
    prescription.is_active = True
    await session.flush()
    return True
