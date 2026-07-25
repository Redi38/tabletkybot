from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database.models import MedicineSchedule

scheduler = AsyncIOScheduler()

_MED_JOB_PREFIX = "med_"


def _med_job_id(medicine_id: int, schedule_id: int) -> str:
    return f"{_MED_JOB_PREFIX}{medicine_id}_{schedule_id}"


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()


def _next_schedule_id_for_today(schedules: list[MedicineSchedule], tz_name: str) -> int | None:
    """
    Finds the soonest schedule (by time-of-day) that hasn't happened yet
    today, in the user's own timezone. Used when a reminder is sent
    manually (from the Admin Panel) so the one-time "already reminded"
    suppression can target that specific upcoming dose slot.
    Returns None if every schedule for today has already passed (nothing
    left to suppress).
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Kyiv")
    now_time = datetime.now(tz).time()
    candidates = []
    for sched in schedules:
        try:
            hour, minute = map(int, sched.scheduled_time.split(":"))
        except ValueError:
            continue
        if (hour, minute) > (now_time.hour, now_time.minute):
            candidates.append((hour, minute, sched.id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]
