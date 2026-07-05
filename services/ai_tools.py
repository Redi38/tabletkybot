from sqlalchemy.ext.asyncio import AsyncSession
from database import crud


# Схеми tools — те, що бачить LLM і на основі чого вирішує, коли викликати

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_medicines",
            "description": (
                "Отримати список активних ліків користувача з розкладом прийому, "
                "дозуванням та залишком курсу. Використовуй, коли юзер питає "
                "'які ліки я приймаю', 'мій розклад', 'скільки залишилось приймати' тощо."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_prescriptions",
            "description": (
                "Отримати список активних рецептів користувача — назва препарату, "
                "дата закінчення дії, скільки вже куплено з дозволеної кількості. "
                "Використовуй, коли юзер питає про рецепти, 'коли закінчується рецепт', "
                "'скільки ще можна купити по рецепту' тощо."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# Виконавці — реальна Python-логіка за кожним tool

async def execute_get_my_medicines(session: AsyncSession, user_id: int) -> dict:
    medicines = await crud.get_user_medicines(session, user_id, active_only=True)
    if not medicines:
        return {"medicines": [], "note": "У користувача немає активних препаратів."}

    result = []
    for med in medicines:
        result.append({
            "name": med.name,
            "form": med.form,
            "dosage": med.dosage,
            "schedule": [s.scheduled_time for s in med.schedules],
            "remaining_doses": med.course_duration,
            "stock_amount": med.stock_amount,
        })
    return {"medicines": result}


async def execute_get_my_prescriptions(session: AsyncSession, user_id: int) -> dict:
    prescriptions = await crud.get_user_prescriptions(session, user_id, active_only=True)
    if not prescriptions:
        return {"prescriptions": [], "note": "У користувача немає активних рецептів."}

    result = []
    for p in prescriptions:
        result.append({
            "medicine_name": p.medicine_name,
            "valid_from": p.valid_from.isoformat(),
            "expires_at": p.expires_at.isoformat(),
            "max_quantity": p.max_quantity,
            "purchased_quantity": p.purchased_quantity,
            "is_fully_purchased": p.is_fully_purchased,
        })
    return {"prescriptions": result}


# Диспетчер — викликається з ai_service.py при отриманні tool_calls від LLM

TOOL_EXECUTORS = {
    "get_my_medicines": execute_get_my_medicines,
    "get_my_prescriptions": execute_get_my_prescriptions,
}


async def execute_tool(tool_name: str, session: AsyncSession, user_id: int) -> dict:
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"error": f"Невідомий tool: {tool_name}"}
    try:
        return await executor(session, user_id)
    except Exception as e:
        return {"error": f"Помилка виконання {tool_name}: {e}"}
