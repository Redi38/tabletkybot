"""
Reminder scheduling, split by concern.

Module map:
  redis_state.py    — Redis-backed state: pending (unacknowledged) reminders,
                       pending stock alerts, action locks. No APScheduler
                       dependency.
  jobs.py            — the APScheduler instance, sending reminders (initial +
                        hourly repeats), adding/removing per-medicine jobs,
                        and DB<->scheduler sync. Depends on redis_state.py.
  prescriptions.py     — prescription expiry reminders and auto-archiving.
                          Independent of jobs.py and redis_state.py.
"""

from .jobs import (
    add_reminders_for_medicine,
    cancel_repeat_reminder,
    get_reminder_keyboard,
    remove_reminders,
    resume_pending_reminders,
    scheduler,
    send_reminder,
    send_repeat_reminder,
    start_scheduler,
    stop_scheduler,
    sync_reminders,
    sync_single_reminder,
)
from .prescriptions import (
    archive_expired_prescriptions,
    check_prescription_reminders,
    get_prescription_alert_keyboard,
)
from .redis_state import (
    acquire_action_lock,
    clear_stock_alert_pending,
    get_active_pending_reminders,
    get_stock_alert_pending,
    init_redis,
    save_stock_alert_pending,
)

__all__ = [
    "scheduler",
    "start_scheduler",
    "stop_scheduler",
    "get_reminder_keyboard",
    "send_reminder",
    "send_repeat_reminder",
    "cancel_repeat_reminder",
    "resume_pending_reminders",
    "remove_reminders",
    "add_reminders_for_medicine",
    "sync_reminders",
    "sync_single_reminder",
    "init_redis",
    "get_active_pending_reminders",
    "save_stock_alert_pending",
    "get_stock_alert_pending",
    "clear_stock_alert_pending",
    "acquire_action_lock",
    "get_prescription_alert_keyboard",
    "check_prescription_reminders",
    "archive_expired_prescriptions",
]
