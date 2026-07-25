from sqlalchemy.ext.asyncio import AsyncSession

from database import crud

from .helpers import _find_medicine, _to_int


async def execute_get_my_medicines(session: AsyncSession, user_id: int, args: dict) -> dict:
    medicines = await crud.get_user_medicines(session, user_id, active_only=True)
    if not medicines:
        return {"medicines": [], "note": "No active medicines found for this user."}
    return {
        "medicines": [
            {
                "name": m.name,
                "form": m.form,
                "dosage": m.dosage,
                "schedule": [s.scheduled_time for s in m.schedules],
                "remaining_doses": m.course_duration,
                "stock_amount": m.stock_amount,
            }
            for m in medicines
        ]
    }


async def execute_add_medicine_reminder(session: AsyncSession, user_id: int, args: dict) -> dict:
    times = args.get("times") or []
    if not times or not isinstance(times, list):
        return {"error": "A non-empty list of times (times) is required."}

    duration_days = _to_int(args.get("duration_days"), min_value=1, max_value=365)
    if duration_days is None:
        return {"error": "duration_days must be an integer between 1 and 365."}

    stock_amount = None
    if args.get("stock_amount") is not None:
        stock_amount = _to_int(args.get("stock_amount"), min_value=0, max_value=100000)
        if stock_amount is None:
            return {"error": "stock_amount must be an integer between 0 and 100000."}

    course_duration = duration_days * len(times)

    medicine = await crud.add_medicine(
        session=session,
        user_id=user_id,
        name=str(args.get("name", ""))[:150],
        form=str(args.get("form", ""))[:64],
        dosage=str(args.get("dosage", ""))[:64],
        schedules_list=[str(t) for t in times],
        course_duration=course_duration,
        stock_amount=stock_amount,
    )
    return {"success": True, "medicine_name": medicine.name, "schedule": times, "duration_days": duration_days}


async def execute_update_medicine(session: AsyncSession, user_id: int, args: dict) -> dict:
    medicine = await _find_medicine(session, user_id, args.get("medicine_name", ""))
    if not medicine:
        return {"error": f"Medicine '{args.get('medicine_name')}' not found or the name is ambiguous."}

    field = args.get("field")
    if not field:
        return {"error": "field is required"}
    value = args.get("value")

    if field in ("stock_amount", "low_stock_threshold"):
        value = _to_int(value, min_value=0, max_value=100000)
        if value is None:
            return {"error": f"Field {field} must be an integer between 0 and 100000."}
    elif field in ("name", "form", "dosage"):
        value = str(value)[:150]

    await crud.update_medicine_field(session, medicine.id, field, value)
    return {"success": True, "medicine_name": medicine.name, "updated_field": field, "new_value": value}


async def execute_request_medicine_removal(session: AsyncSession, user_id: int, args: dict) -> dict:
    medicine = await _find_medicine(session, user_id, args.get("medicine_name", ""))
    if not medicine:
        return {"error": f"Medicine '{args.get('medicine_name')}' not found or the name is ambiguous."}
    return {
        "requires_confirmation": True,
        "target_type": "medicine",
        "target_id": medicine.id,
        "target_name": medicine.name,
    }
