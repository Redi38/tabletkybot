"""CRUD operations for medicines, schedules, and intake records."""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Medicine, MedicineRecord, MedicineSchedule, User


def _medicine_with_schedules():
    """Base query for Medicine with schedules eagerly loaded."""
    return select(Medicine).options(selectinload(Medicine.schedules))


# ─── Medicines ──────────────────────────────────────────────────────────────
async def add_medicine(
    session: AsyncSession,
    user_id: int,
    name: str,
    form: str,
    dosage: str,
    schedules_list: list[str],
    course_duration: int,
    stock_amount: int | None = None,
    low_stock_threshold: int | None = 5,
) -> Medicine:
    """
    Adds a medicine and creates several schedule records.
    schedules_list: list of times, e.g. ["08:00", "20:00"]
    """
    medicine = Medicine(
        user_id=user_id,
        name=name,
        form=form,
        dosage=dosage,
        course_duration=course_duration,
        stock_amount=stock_amount,
        low_stock_threshold=low_stock_threshold,
    )
    session.add(medicine)
    await session.flush()

    schedules = [MedicineSchedule(medicine_id=medicine.id, scheduled_time=t.strip()) for t in schedules_list]
    session.add_all(schedules)
    await session.flush()

    result = await session.execute(_medicine_with_schedules().where(Medicine.id == medicine.id))
    return result.scalar_one()


async def get_user_medicines(session: AsyncSession, user_id: int, active_only: bool = True) -> list[Medicine]:
    stmt = _medicine_with_schedules().where(Medicine.user_id == user_id).execution_options(populate_existing=True)
    if active_only:
        stmt = stmt.where(Medicine.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_medicine_by_id(session: AsyncSession, medicine_id: int) -> Medicine | None:
    result = await session.execute(
        _medicine_with_schedules().where(Medicine.id == medicine_id).execution_options(populate_existing=True)
    )
    medicine: Medicine | None = result.scalar_one_or_none()
    return medicine


async def update_medicine_field(session: AsyncSession, medicine_id: int, field: str, value) -> bool:
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id))
    medicine: Medicine | None = result.scalar_one_or_none()
    if not medicine:
        return False
    setattr(medicine, field, value)
    await session.flush()
    return True


async def update_medicine_schedules(session: AsyncSession, medicine_id: int, new_schedules: list[str]) -> bool:
    await session.execute(delete(MedicineSchedule).where(MedicineSchedule.medicine_id == medicine_id))
    schedules = [MedicineSchedule(medicine_id=medicine_id, scheduled_time=t.strip()) for t in new_schedules]
    session.add_all(schedules)
    await session.flush()
    return True


async def delete_medicine(session: AsyncSession, medicine_id: int) -> bool:
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id))
    medicine: Medicine | None = result.scalar_one_or_none()
    if not medicine:
        return False
    await session.delete(medicine)
    await session.flush()
    return True


async def record_medicine_taken(session: AsyncSession, medicine_id: int, status: str = "taken") -> dict:
    """
    Records the fact that a dose was taken/skipped.
    Subtracts 1 day from the course and 1 unit from stock (if status is taken).
    Returns a dict with information about the remaining amounts.
    """
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id))
    medicine: Medicine | None = result.scalar_one_or_none()

    if not medicine:
        return {"success": False}

    remaining_days = medicine.course_duration or 0
    record = MedicineRecord(medicine_id=medicine_id, status=status, remaining_days=remaining_days)
    session.add(record)

    if status == "taken":
        if remaining_days > 0:
            medicine.course_duration = remaining_days - 1
            remaining_days = medicine.course_duration
        if medicine.stock_amount is not None and medicine.stock_amount > 0:
            medicine.stock_amount -= 1

    await session.flush()
    return {
        "success": True,
        "remaining_days": remaining_days,
        "stock_amount": medicine.stock_amount,
        "low_stock_threshold": medicine.low_stock_threshold,
    }


async def add_stock(session: AsyncSession, medicine_id: int, amount_to_add: int) -> int | None:
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id))
    medicine: Medicine | None = result.scalar_one_or_none()

    if not medicine:
        return None

    current = medicine.stock_amount or 0

    new_stock = current + amount_to_add

    medicine.stock_amount = new_stock
    await session.flush()

    return new_stock


async def get_archived_medicines(session: AsyncSession, user_id: int) -> list[Medicine]:
    result = await session.execute(
        _medicine_with_schedules().where(Medicine.user_id == user_id, Medicine.is_active.is_(False))
    )
    return list(result.scalars().all())


async def get_stale_active_medicines(session: AsyncSession, cutoff: datetime) -> list[tuple[Medicine, User, datetime]]:
    """
    Returns (medicine, user, last_activity_at) for every active medicine
    whose last recorded dose (MedicineRecord.taken_at, "taken" or "skipped"
    both count as a response) is older than `cutoff` — or, for a medicine
    with no records at all yet, whose creation date is older than `cutoff`.
    last_activity_at is that reference timestamp, returned so callers can
    report how many days it's been.

    Used by the inactivity sweep (services/scheduler/jobs/reminders/
    inactivity_sweep.py) to catch medicines that are ALREADY stale — unlike
    the day-to-day check in send_reminder()/send_repeat_reminder(), which
    can only measure inactivity starting from when it began running (it
    relies on Redis's pending-reminder state, overwritten by every resend),
    this reads the durable intake log instead, so it also works
    retroactively for medicines that were overdue before the sweep existed.
    """
    last_taken_subq = (
        select(MedicineRecord.medicine_id, func.max(MedicineRecord.taken_at).label("last_taken_at"))
        .group_by(MedicineRecord.medicine_id)
        .subquery()
    )
    last_activity = func.coalesce(last_taken_subq.c.last_taken_at, Medicine.created_at).label("last_activity_at")
    stmt = (
        select(Medicine, User, last_activity)
        .join(User, Medicine.user_id == User.id)
        .outerjoin(last_taken_subq, last_taken_subq.c.medicine_id == Medicine.id)
        .where(Medicine.is_active.is_(True), last_activity < cutoff)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]
