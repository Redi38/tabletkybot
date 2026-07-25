from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from database import crud

from .helpers import _find_prescription, _parse_date_flexible, _to_int


async def execute_get_my_prescriptions(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescriptions = await crud.get_user_prescriptions(session, user_id, active_only=True)
    if not prescriptions:
        return {"prescriptions": [], "note": "No active prescriptions found for this user."}
    return {
        "prescriptions": [
            {
                "medicine_name": p.medicine_name,
                "valid_from": p.valid_from.isoformat(),
                "expires_at": p.expires_at.isoformat(),
                "max_quantity": p.max_quantity,
                "purchased_quantity": p.purchased_quantity,
                "is_fully_purchased": p.is_fully_purchased,
            }
            for p in prescriptions
        ]
    }


async def execute_add_prescription_entry(session: AsyncSession, user_id: int, args: dict) -> dict:
    issued = _parse_date_flexible(args.get("issued_date", ""))
    valid_from = _parse_date_flexible(args.get("valid_from_date", ""))

    if not issued or not valid_from:
        return {"error": "Could not parse the dates. Format: DD.MM.YY."}

    duration_days = _to_int(args.get("duration_days"))
    if duration_days not in (30, 60):
        return {"error": "duration_days must be exactly 30 or 60."}

    max_quantity = None
    if args.get("max_quantity") is not None:
        max_quantity = _to_int(args.get("max_quantity"), min_value=1, max_value=100000)
        if max_quantity is None:
            return {"error": "max_quantity must be an integer between 1 and 100000."}

    reminder_days_before = _to_int(args.get("reminder_days_before", 3), min_value=0, max_value=90)
    if reminder_days_before is None:
        reminder_days_before = 3

    expires_at = valid_from + timedelta(days=duration_days)

    prescription = await crud.add_prescription(
        session=session,
        user_id=user_id,
        medicine_name=str(args.get("medicine_name", ""))[:150],
        valid_from=valid_from,
        expires_at=expires_at,
        max_quantity=max_quantity,
        reminder_days_before=reminder_days_before,
    )
    return {
        "success": True,
        "medicine_name": prescription.medicine_name,
        "valid_from": valid_from.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


async def execute_update_prescription(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescription = await _find_prescription(session, user_id, args.get("medicine_name", ""))
    if not prescription:
        return {"error": f"Prescription for '{args.get('medicine_name')}' not found or the name is ambiguous."}

    field = args.get("field")
    if not field:
        return {"error": "field is required"}
    value = args.get("value")

    if field == "max_quantity":
        value = _to_int(value, min_value=1, max_value=100000)
        if value is None:
            return {"error": "max_quantity must be an integer between 1 and 100000."}
    elif field == "reminder_days_before":
        value = _to_int(value, min_value=0, max_value=90)
        if value is None:
            return {"error": "reminder_days_before must be an integer between 0 and 90."}
    elif field == "notes":
        value = str(value)[:500]

    await crud.update_prescription_field(session, prescription.id, field, value)
    return {"success": True, "medicine_name": prescription.medicine_name, "updated_field": field, "new_value": value}


async def execute_mark_prescription_bought(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescription = await _find_prescription(session, user_id, args.get("medicine_name", ""))
    if not prescription:
        return {"error": f"Prescription for '{args.get('medicine_name')}' not found or the name is ambiguous."}

    amount = _to_int(args.get("amount"), min_value=1, max_value=100000)
    if amount is None:
        return {"error": "amount must be an integer between 1 and 100000."}

    if prescription.max_quantity is not None:
        remaining = prescription.max_quantity - prescription.purchased_quantity
        if amount > remaining:
            return {"error": f"Prescription limit exceeded. Only {remaining} unit(s) remaining."}

    result = await crud.mark_prescription_purchased(session, prescription.id, amount)
    return {"success": True, **result}


async def execute_request_prescription_removal(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescription = await _find_prescription(session, user_id, args.get("medicine_name", ""))
    if not prescription:
        return {"error": f"Prescription for '{args.get('medicine_name')}' not found or the name is ambiguous."}
    return {
        "requires_confirmation": True,
        "target_type": "prescription",
        "target_id": prescription.id,
        "target_name": prescription.medicine_name,
    }
