from datetime import date, datetime, timedelta
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
    except (TypeError, ValueError):
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


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_medicines",
            "description": (
                "Get the list of the user's active medicines with their intake "
                "schedule, dosage, and remaining course."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_prescriptions",
            "description": (
                "Get the list of the user's active prescriptions — medicine name, "
                "expiration date, how much has already been purchased out of the allowed amount."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_medicine_reminder",
            "description": "Add a new medicine with reminders. duration_days — a realistic course length in days (1-365).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "form": {"type": "string"},
                    "dosage": {"type": "string"},
                    "times": {"type": "array", "items": {"type": "string"}, "description": "Time in HH:MM format"},
                    "duration_days": {"type": "integer", "description": "From 1 to 365 days"},
                    "stock_amount": {"type": "integer"},
                },
                "required": ["name", "form", "dosage", "times", "duration_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_medicine",
            "description": "Change a parameter of an already added medicine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "field": {
                        "type": "string",
                        "enum": ["name", "form", "dosage", "stock_amount", "low_stock_threshold"],
                    },
                    "value": {"type": "string"},
                },
                "required": ["medicine_name", "field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_medicine_removal",
            "description": (
                "Call this when the user wants to archive OR delete a medicine. "
                "This does NOT perform the action immediately — the user will receive a message with buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {"medicine_name": {"type": "string"}},
                "required": ["medicine_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_prescription_entry",
            "description": "Add a new prescription for a medicine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "issued_date": {"type": "string", "description": "Issue date, DD.MM.YY"},
                    "valid_from_date": {"type": "string", "description": "Start date of validity, DD.MM.YY"},
                    "duration_days": {"type": "integer", "enum": [30, 60]},
                    "max_quantity": {"type": "integer"},
                    "reminder_days_before": {"type": "integer"},
                },
                "required": ["medicine_name", "issued_date", "valid_from_date", "duration_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_prescription",
            "description": "Change a parameter of an already added prescription.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "field": {"type": "string", "enum": ["max_quantity", "reminder_days_before", "notes"]},
                    "value": {"type": "string"},
                },
                "required": ["medicine_name", "field", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_prescription_bought",
            "description": "Mark the purchase of a certain quantity of units under a prescription.",
            "parameters": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "amount": {"type": "integer"},
                },
                "required": ["medicine_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_prescription_removal",
            "description": (
                "Call this when the user wants to archive OR delete a prescription. "
                "This does NOT perform the action immediately — the user will receive a message with buttons."
            ),
            "parameters": {
                "type": "object",
                "properties": {"medicine_name": {"type": "string"}},
                "required": ["medicine_name"],
            },
        },
    },
]


# ─── Reads ─────────────────────────────────────────────────────────────

async def execute_get_my_medicines(session: AsyncSession, user_id: int, args: dict) -> dict:
    medicines = await crud.get_user_medicines(session, user_id, active_only=True)
    if not medicines:
        return {"medicines": [], "note": "No active medicines found for this user."}
    return {"medicines": [
        {
            "name": m.name, "form": m.form, "dosage": m.dosage,
            "schedule": [s.scheduled_time for s in m.schedules],
            "remaining_doses": m.course_duration, "stock_amount": m.stock_amount,
        }
        for m in medicines
    ]}


async def execute_get_my_prescriptions(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescriptions = await crud.get_user_prescriptions(session, user_id, active_only=True)
    if not prescriptions:
        return {"prescriptions": [], "note": "No active prescriptions found for this user."}
    return {"prescriptions": [
        {
            "medicine_name": p.medicine_name,
            "valid_from": p.valid_from.isoformat(), "expires_at": p.expires_at.isoformat(),
            "max_quantity": p.max_quantity, "purchased_quantity": p.purchased_quantity,
            "is_fully_purchased": p.is_fully_purchased,
        }
        for p in prescriptions
    ]}


# ─── Writes (executed immediately) ─────────────────────────────────────

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
        session=session, user_id=user_id,
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
    value = args.get("value")

    if field in ("stock_amount", "low_stock_threshold"):
        value = _to_int(value, min_value=0, max_value=100000)
        if value is None:
            return {"error": f"Field {field} must be an integer between 0 and 100000."}
    elif field in ("name", "form", "dosage"):
        value = str(value)[:150]

    await crud.update_medicine_field(session, medicine.id, field, value)
    return {"success": True, "medicine_name": medicine.name, "updated_field": field, "new_value": value}


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
        session=session, user_id=user_id,
        medicine_name=str(args.get("medicine_name", ""))[:150],
        issued_at=issued, valid_from=valid_from, expires_at=expires_at,
        max_quantity=max_quantity,
        reminder_days_before=reminder_days_before,
    )
    return {
        "success": True, "medicine_name": prescription.medicine_name,
        "valid_from": valid_from.isoformat(), "expires_at": expires_at.isoformat(),
    }


async def execute_update_prescription(session: AsyncSession, user_id: int, args: dict) -> dict:
    prescription = await _find_prescription(session, user_id, args.get("medicine_name", ""))
    if not prescription:
        return {"error": f"Prescription for '{args.get('medicine_name')}' not found or the name is ambiguous."}

    field = args.get("field")
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


# ─── Confirmation requests (nothing is deleted, only the target is found) ─────

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


# ─── Dispatcher ────────────────────────────────────────────────────────────

TOOL_EXECUTORS = {
    "get_my_medicines": execute_get_my_medicines,
    "get_my_prescriptions": execute_get_my_prescriptions,
    "add_medicine_reminder": execute_add_medicine_reminder,
    "update_medicine": execute_update_medicine,
    "add_prescription_entry": execute_add_prescription_entry,
    "update_prescription": execute_update_prescription,
    "mark_prescription_bought": execute_mark_prescription_bought,
    "request_medicine_removal": execute_request_medicine_removal,
    "request_prescription_removal": execute_request_prescription_removal,
}


async def execute_tool(tool_name: str, session: AsyncSession, user_id: int, arguments: dict | None = None) -> dict:
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return await executor(session, user_id, arguments or {})
    except Exception as e:

        await session.rollback()
        return {"error": f"Error executing {tool_name}: {e}"}
