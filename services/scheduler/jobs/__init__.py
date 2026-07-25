"""
Core reminder job scheduling, split by concern.

Module map:
  core.py       — the APScheduler instance, start/stop lifecycle, and
                   shared job-id helpers.
  reminders.py  — sending reminders (initial + hourly repeats), resuming
                   them after a restart, and removing all jobs for a
                   given medicine. Depends on redis_state.py.
  sync.py       — adding per-medicine cron jobs and full/single sync
                   between the database and the in-memory job queue.
                   Depends on reminders.py.
"""

from .core import (
    _med_job_id,
    _next_schedule_id_for_today,
    scheduler,
    start_scheduler,
    stop_scheduler,
)
from .reminders import (
    _local_today,
    _manual_reminder_today,
    cancel_repeat_reminder,
    get_reminder_keyboard,
    remove_reminders,
    resume_pending_reminders,
    send_reminder,
    send_repeat_reminder,
)
from .sync import add_reminders_for_medicine, sync_reminders, sync_single_reminder

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
    "_med_job_id",
    "_next_schedule_id_for_today",
    "_local_today",
    "_manual_reminder_today",
]
