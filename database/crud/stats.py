"""Reports, dashboard, and adherence statistics queries."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Medicine, MedicineRecord, Prescription, User


# ─── Reports ────────────────────────────────────────────────────
async def get_medicine_records_for_report(session: AsyncSession, user_id: int) -> list[tuple]:
    stmt = (
        select(
            Medicine.name,
            Medicine.dosage,
            MedicineRecord.remaining_days,
            MedicineRecord.taken_at,
            MedicineRecord.status,
        )
        .join(MedicineRecord, Medicine.id == MedicineRecord.medicine_id)
        .where(Medicine.user_id == user_id)
        .order_by(MedicineRecord.taken_at)
    )
    result = await session.execute(stmt)
    return [tuple(row) for row in result.all()]


async def get_medicine_intake_stats(session: AsyncSession, user_id: int) -> dict[str, int]:
    stmt = (
        select(MedicineRecord.status, func.count(MedicineRecord.id))
        .join(Medicine, Medicine.id == MedicineRecord.medicine_id)
        .where(Medicine.user_id == user_id)
        .group_by(MedicineRecord.status)
    )
    result = await session.execute(stmt)
    counts = {str(status): int(count) for status, count in result.all()}
    taken = counts.get("taken", 0)
    skipped = counts.get("skipped", 0)
    return {"total": taken + skipped, "taken": taken, "skipped": skipped}


async def get_global_intake_stats(session: AsyncSession) -> dict:
    stmt = select(MedicineRecord.status, func.count(MedicineRecord.id)).group_by(MedicineRecord.status)
    result = await session.execute(stmt)
    counts = {str(status): int(count) for status, count in result.all()}
    taken = counts.get("taken", 0)
    skipped = counts.get("skipped", 0)
    total = taken + skipped
    adherence_rate = round((taken / total * 100), 1) if total else 0.0

    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()

    total_active_medicines = (
        await session.execute(select(func.count(Medicine.id)).where(Medicine.is_active.is_(True)))
    ).scalar_one()

    active_prescriptions = (
        await session.execute(select(func.count(Prescription.id)).where(Prescription.is_active.is_(True)))
    ).scalar_one()

    return {
        "taken": taken,
        "skipped": skipped,
        "adherence_rate": adherence_rate,
        "total_users": total_users,
        "total_active_medicines": total_active_medicines,
        "active_prescriptions": active_prescriptions,
    }


async def get_dashboard_stats(session: AsyncSession, period: str = "all") -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    period_map = {"24h": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    date_filter = now - period_map[period] if period in period_map else None

    pie_stmt = select(MedicineRecord.status, func.count(MedicineRecord.id))
    if date_filter:
        pie_stmt = pie_stmt.where(MedicineRecord.taken_at >= date_filter)
    pie_stmt = pie_stmt.group_by(MedicineRecord.status)
    pie_result = await session.execute(pie_stmt)
    pie_counts = {str(s): int(c) for s, c in pie_result.all()}

    hour_stmt = select(
        func.extract("hour", MedicineRecord.taken_at).label("hour"),
        func.count(MedicineRecord.id).label("cnt"),
    ).where(MedicineRecord.status == "taken")
    if date_filter:
        hour_stmt = hour_stmt.where(MedicineRecord.taken_at >= date_filter)
    hour_stmt = hour_stmt.group_by("hour")
    hour_result = await session.execute(hour_stmt)

    hourly_data = [0] * 24
    for row in hour_result.all():
        if row.hour is not None:
            hourly_data[int(row.hour)] = int(row.cnt)

    return {
        "pie": {"taken": pie_counts.get("taken", 0), "skipped": pie_counts.get("skipped", 0)},
        "hourly": hourly_data,
    }
