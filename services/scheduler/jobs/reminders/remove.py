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
from ..core import _MED_JOB_PREFIX, _repeat_job_id, scheduler
from .utils import _manual_reminder_today

logger = logging.getLogger(__name__)


async def cancel_repeat_reminder(chat_id: int, medicine_id: int, schedule_id: int | None) -> None:
    job_id = _repeat_job_id(medicine_id, schedule_id, chat_id)
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Repeat reminder {job_id} cancelled")
    except Exception:
        pass
    await _delete_pending_reminder(chat_id, medicine_id, schedule_id)


async def cancel_repeat_reminders_for_medicine(chat_id: int, medicine_id: int) -> None:
    """
    Cancels every unacknowledged dose's repeat reminder for this medicine at
    once — used by the self-service "mark as taken" flows, which record a
    dose without going through a specific reminder message/schedule_id, so
    there's no single dose to target.
    """
    from ...redis_state import _get_pending_reminders_for_chat

    for medicine_id_, schedule_id, _data in await _get_pending_reminders_for_chat(chat_id):
        if medicine_id_ == medicine_id:
            await cancel_repeat_reminder(chat_id, medicine_id, schedule_id)


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
