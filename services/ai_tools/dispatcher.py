from sqlalchemy.ext.asyncio import AsyncSession

from .medicines import (
    execute_add_medicine_reminder,
    execute_get_my_medicines,
    execute_request_medicine_removal,
    execute_update_medicine,
)
from .prescriptions import (
    execute_add_prescription_entry,
    execute_get_my_prescriptions,
    execute_mark_prescription_bought,
    execute_request_prescription_removal,
    execute_update_prescription,
)

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
