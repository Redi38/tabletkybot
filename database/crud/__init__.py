"""
Database CRUD operations, split by domain.

Module map:
  users.py         — user accounts (language/timezone preferences)
  medicines.py      — medicines, schedules, intake records
  stats.py           — reports, dashboard, and adherence statistics
  chat_history.py     — AI conversation history (sliding window)
  prescriptions.py     — prescriptions
  ai_metrics.py         — AI usage metrics (latency, tool usage, error rates)
"""

from .ai_metrics import get_ai_metrics_summary, get_recent_ai_metrics, log_ai_metric
from .chat_history import add_chat_message, clear_chat_history, get_chat_history
from .medicines import (
    add_medicine,
    add_stock,
    delete_medicine,
    get_archived_medicines,
    get_medicine_by_id,
    get_user_medicines,
    record_medicine_taken,
    update_medicine_field,
    update_medicine_schedules,
)
from .prescriptions import (
    add_prescription,
    archive_prescription,
    delete_prescription,
    get_expired_active_prescriptions,
    get_prescription_by_id,
    get_prescriptions_needing_reminder,
    get_user_archived_prescriptions,
    get_user_prescriptions,
    mark_prescription_purchased,
    mark_prescription_reminder_sent,
    restore_prescription,
    update_prescription_field,
)
from .stats import (
    get_dashboard_stats,
    get_global_intake_stats,
    get_medicine_intake_stats,
    get_medicine_records_for_report,
)
from .users import (
    get_all_users,
    get_or_create_user,
    get_user_language,
    get_user_timezone,
    update_user_language,
    update_user_timezone,
)

__all__ = [
    "get_or_create_user",
    "get_all_users",
    "update_user_timezone",
    "update_user_language",
    "get_user_language",
    "get_user_timezone",
    "add_medicine",
    "get_user_medicines",
    "get_medicine_by_id",
    "update_medicine_field",
    "update_medicine_schedules",
    "delete_medicine",
    "record_medicine_taken",
    "add_stock",
    "get_archived_medicines",
    "get_medicine_records_for_report",
    "get_medicine_intake_stats",
    "get_global_intake_stats",
    "get_dashboard_stats",
    "add_chat_message",
    "get_chat_history",
    "clear_chat_history",
    "add_prescription",
    "get_user_prescriptions",
    "get_prescription_by_id",
    "update_prescription_field",
    "mark_prescription_purchased",
    "archive_prescription",
    "delete_prescription",
    "get_prescriptions_needing_reminder",
    "mark_prescription_reminder_sent",
    "get_expired_active_prescriptions",
    "get_user_archived_prescriptions",
    "restore_prescription",
    "log_ai_metric",
    "get_ai_metrics_summary",
    "get_recent_ai_metrics",
]
