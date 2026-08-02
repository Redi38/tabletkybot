"""
Cancelling a single repeat reminder, and removing ALL scheduled jobs
(daily + repeat) for a given medicine — used on delete/archive and when a
medicine is auto-archived due to an unacknowledged empty-stock alert.
"""

import asyncio
import logging

from ...redis_state import (
    _delete_pending_reminder,
    _delete_pending_reminders_for_medicine,
    _delete_stock_alerts_for_medicine,
)
from ..core import _MED_JOB_PREFIX, scheduler
from .utils import _manual_reminder_today

logger = logging.getLogger(__name__)


async def cancel_repeat_reminder(chat_id: int, medicine_id: int) -> None:
    try:
        scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
        logger.info(f"Repeat reminder repeat_{medicine_id}_{chat_id} cancelled")
    except Exception:
        pass
    await _delete_pending_reminder(chat_id, medicine_id)


def remove_reminders(medicine_id: int) -> None:
    prefix = f"{_MED_JOB_PREFIX}{medicine_id}_"
    repeat_prefix = f"repeat_{medicine_id}_"
    removed = 0

    for job in scheduler.get_jobs():
        if job.id.startswith(prefix) or job.id.startswith(repeat_prefix):
            try:
                scheduler.remove_job(job.id)
                removed += 1
            except Exception as e:
                logger.error(f"Error removing reminder {job.id}: {e}")

    for key in [k for k in _manual_reminder_today if k[0] == medicine_id]:
        _manual_reminder_today.pop(key, None)

    try:
        asyncio.create_task(_delete_pending_reminders_for_medicine(medicine_id))
        asyncio.create_task(_delete_stock_alerts_for_medicine(medicine_id))
    except RuntimeError:
        logger.warning(f"No active event loop to clean up Redis for med_{medicine_id}")

    if removed:
        logger.info(f"Removed {removed} schedules (including repeats) for medicine ID {medicine_id}")
