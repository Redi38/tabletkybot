from datetime import datetime, timedelta, timezone, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload
from database.models import User, Medicine, MedicineSchedule, MedicineRecord, ChatHistory, Prescription


# ─── Допоміжні функції ──────────────────────────────────────────────────────
def _medicine_with_schedules():
    """Базовий запит для Medicine із завантаженими розкладами."""
    return select(Medicine).options(selectinload(Medicine.schedules))

# ─── Користувачі ────────────────────────────────────────────────────────────
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
    return str(user.language) if user and user.language else "uk"

async def get_user_timezone(session: AsyncSession, user_id: int) -> str:
    user = await _get_user(session, user_id)
    return str(user.timezone) if user and user.timezone else "Europe/Kyiv"

# ─── Ліки ──────────────────────────────────────────────────────────────────
async def add_medicine(
        session: AsyncSession, user_id: int, name: str, form: str,
        dosage: str, schedules_list: list[str], course_duration: int,
        stock_amount: int | None = None, low_stock_threshold: int | None = 5
) -> Medicine:
    """
    Додає ліки та створює кілька записів розкладу.
    schedules_list: список часу, наприклад ["08:00", "20:00"]
    """
    medicine = Medicine(
        user_id=user_id, name=name, form=form, dosage=dosage,
        course_duration=course_duration,
        stock_amount=stock_amount, low_stock_threshold=low_stock_threshold
    )
    session.add(medicine)
    await session.flush()

    schedules = [
        MedicineSchedule(medicine_id=medicine.id, scheduled_time=t.strip())
        for t in schedules_list
    ]
    session.add_all(schedules)
    await session.flush()

    result = await session.execute(
        _medicine_with_schedules().where(Medicine.id == medicine.id)
    )
    return result.scalar_one()

async def get_user_medicines(
        session: AsyncSession, user_id: int, active_only: bool = True
) -> list[Medicine]:
    stmt = (
        _medicine_with_schedules()
        .where(Medicine.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if active_only:
        stmt = stmt.where(Medicine.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())

async def get_medicine_by_id(session: AsyncSession, medicine_id: int) -> Medicine | None:
    result = await session.execute(
        _medicine_with_schedules()
        .where(Medicine.id == medicine_id)
        .execution_options(populate_existing=True)
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

async def update_medicine_schedules(
        session: AsyncSession, medicine_id: int, new_schedules: list[str]
) -> bool:
    await session.execute(
        delete(MedicineSchedule).where(MedicineSchedule.medicine_id == medicine_id)
    )
    schedules = [
        MedicineSchedule(medicine_id=medicine_id, scheduled_time=t.strip())
        for t in new_schedules
    ]
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

async def record_medicine_taken(
        session: AsyncSession, medicine_id: int, status: str = "taken"
) -> dict:
    """
    Записує факт прийому/пропуску.
    Віднімає 1 день курсу і віднімає 1 з аптечки (якщо статус taken).
    Повертає словник з інформацією про залишки.
    """
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id))
    medicine: Medicine | None = result.scalar_one_or_none()

    if not medicine:
        return {"success": False}

    remaining_days = medicine.course_duration or 0
    record = MedicineRecord(
        medicine_id=medicine_id, status=status, remaining_days=remaining_days
    )
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
        _medicine_with_schedules().where(
            Medicine.user_id == user_id, Medicine.is_active.is_(False)
        )
    )
    return list(result.scalars().all())

# ─── Звіти та Статистика ────────────────────────────────────────────────────
async def get_medicine_records_for_report(
        session: AsyncSession, user_id: int
) -> list[tuple]:
    stmt = (
        select(
            Medicine.name, Medicine.dosage, MedicineRecord.remaining_days,
            MedicineRecord.taken_at, MedicineRecord.status
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

async def get_global_intake_stats(session: AsyncSession) -> dict[str, int]:
    stmt = select(MedicineRecord.status, func.count(MedicineRecord.id)).group_by(MedicineRecord.status)
    result = await session.execute(stmt)
    counts = {str(status): int(count) for status, count in result.all()}
    return {"taken": counts.get("taken", 0), "skipped": counts.get("skipped", 0)}

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

# ─── Історія діалогу з ШІ ───────────────────────────────────────────────────
async def add_chat_message(
        session: AsyncSession, user_id: int, role: str, content: str
) -> None:
    session.add(ChatHistory(user_id=user_id, role=role, content=content))
    await session.flush()

async def get_chat_history(
        session: AsyncSession, user_id: int, limit: int = 10
) -> list[dict]:
    result = await session.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return [{"role": str(m.role), "content": str(m.content)} for m in messages]

async def clear_chat_history(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(ChatHistory).where(ChatHistory.user_id == user_id))
    await session.flush()

# ─── Рецепти ─────────────────────────────────────────────────────────────────
async def add_prescription(
        session: AsyncSession, user_id: int, medicine_name: str,
        issued_at: date, valid_from: date, expires_at: date,
        medicine_id: int | None = None, dosage: str | None = None,
        max_quantity: int | None = None, reminder_days_before: int = 3,
        notes: str | None = None,
) -> Prescription:
    prescription = Prescription(
        user_id=user_id, medicine_id=medicine_id, medicine_name=medicine_name,
        dosage=dosage,
        issued_at=issued_at, valid_from=valid_from, expires_at=expires_at,
        max_quantity=max_quantity, reminder_days_before=reminder_days_before,
        notes=notes,
    )
    session.add(prescription)
    await session.flush()
    await session.refresh(prescription)
    return prescription
 
 
async def get_user_prescriptions(
        session: AsyncSession, user_id: int, active_only: bool = True
) -> list[Prescription]:
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
    """Додає amount до purchased_quantity. Повертає стан для показу юзеру."""
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
    Повертає всі активні, ще не повністю викуплені рецепти без надісланого
    нагадування, разом з юзером (для timezone/language). Точну перевірку
    "чи саме сьогодні той день" робить scheduler, бо вона залежить від
    часового поясу конкретного юзера.
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
    Повертає всі активні рецепти, термін дії яких вже минув (expires_at < сьогодні),
    разом з юзером — для автоархівації та сповіщення.
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
        session: AsyncSession, prescription_id: int,
        issued_at: date, valid_from: date, expires_at: date,
        max_quantity: int | None,
) -> bool:
    prescription = await get_prescription_by_id(session, prescription_id)
    if not prescription:
        return False
    prescription.issued_at = issued_at
    prescription.valid_from = valid_from
    prescription.expires_at = expires_at
    prescription.max_quantity = max_quantity
    prescription.purchased_quantity = 0
    prescription.is_fully_purchased = False
    prescription.reminder_sent = False
    prescription.is_active = True
    await session.flush()
    return True
