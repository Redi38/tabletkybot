"""
Sending reminders (initial + hourly repeats), pausing/resuming them (both
the live settings-toggle path and the bot-startup restore path), and
removing all jobs (including repeats) for a given medicine.

Split by concern:
  utils.py   — small shared helpers (grid alignment, reminder keyboard,
               local-date dedup check, blocked-user cleanup safety net)
  send.py    — send_reminder / send_repeat_reminder
  remove.py  — cancel_repeat_reminder / remove_reminders
  resume.py  — pause/resume_repeat_reminders_for_user, resume_pending_reminders

Depends on redis_state.py for tracking which reminders are unacknowledged
(so the hourly repeat knows what to resend) and pending stock alerts (so a
reminder firing after an unacknowledged empty-stock alert can auto-archive
the medicine instead of sending a normal dose reminder).
"""

from .remove import cancel_repeat_reminder, cancel_repeat_reminders_for_medicine, remove_reminders
from .resume import pause_repeat_reminders_for_user, resume_pending_reminders, resume_repeat_reminders_for_user
from .send import send_reminder, send_repeat_reminder
from .utils import _handle_user_blocked, _local_today, _manual_reminder_today, _next_grid_slot, get_reminder_keyboard

__all__ = [
    "get_reminder_keyboard",
    "send_reminder",
    "send_repeat_reminder",
    "cancel_repeat_reminder",
    "cancel_repeat_reminders_for_medicine",
    "remove_reminders",
    "pause_repeat_reminders_for_user",
    "resume_repeat_reminders_for_user",
    "resume_pending_reminders",
    "_local_today",
    "_manual_reminder_today",
    "_next_grid_slot",
    "_handle_user_blocked",
]
