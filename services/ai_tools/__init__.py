"""
AI tool schemas and executors used by the AI agent's function-calling loop.

This package was split from a single ai_tools.py module for readability.
Everything that used to be importable from `services.ai_tools` is still
importable from here, so existing imports keep working unchanged:

    from services.ai_tools import TOOL_SCHEMAS, execute_tool
"""

from .dispatcher import TOOL_EXECUTORS, execute_tool
from .helpers import _find_medicine, _find_prescription, _parse_date_flexible, _to_int
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
from .schemas import TOOL_SCHEMAS

__all__ = [
    "TOOL_SCHEMAS",
    "TOOL_EXECUTORS",
    "execute_tool",
    "_parse_date_flexible",
    "_to_int",
    "_find_medicine",
    "_find_prescription",
    "execute_get_my_medicines",
    "execute_get_my_prescriptions",
    "execute_add_medicine_reminder",
    "execute_update_medicine",
    "execute_add_prescription_entry",
    "execute_update_prescription",
    "execute_mark_prescription_bought",
    "execute_request_medicine_removal",
    "execute_request_prescription_removal",
]
