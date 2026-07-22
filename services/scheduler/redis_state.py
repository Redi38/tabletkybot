"""
Redis-backed state for the reminder system: unacknowledged ("pending")
reminders awaiting a take/skip response, pending empty-stock alerts, and
short-lived idempotency locks for the take/skip buttons.

This is deliberately separate from job scheduling (jobs.py) — this module
only reads/writes Redis keys and has no dependency on APScheduler at all.
"""

import json
import logging
from datetime import datetime
from datetime import timezone as dt_timezone

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None

_PENDING_KEY_PREFIX = "pending_reminder:"
_PENDING_TTL_SECONDS = 60 * 60 * 48

_STOCK_ALERT_KEY_PREFIX = "stock_alert_pending:"
_STOCK_ALERT_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

_ACTION_LOCK_PREFIX = "action_lock:"
_ACTION_LOCK_TTL_SECONDS = 3


def init_redis(redis_url: str) -> None:
    global _redis_client
    _redis_client = aioredis.from_url(redis_url, decode_responses=True)


def _pending_key(chat_id: int, medicine_id: int) -> str:
    return f"{_PENDING_KEY_PREFIX}{chat_id}:{medicine_id}"


async def _save_pending_reminder(
    chat_id: int,
    medicine_id: int,
    message_id: int,
    medicine_name: str,
    course_duration: int,
    language: str,
    timezone: str,
) -> None:
    if not _redis_client:
        return
    data = {
        "message_id": message_id,
        "medicine_name": medicine_name,
        "course_duration": course_duration,
        "language": language,
        "timezone": timezone,
        "sent_at": datetime.now(dt_timezone.utc).isoformat(),
    }
    await _redis_client.set(_pending_key(chat_id, medicine_id), json.dumps(data), ex=_PENDING_TTL_SECONDS)  # type: ignore[misc]


async def _get_pending_reminder(chat_id: int, medicine_id: int) -> dict | None:
    if not _redis_client:
        return None
    raw = await _redis_client.get(_pending_key(chat_id, medicine_id))  # type: ignore[misc]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _delete_pending_reminder(chat_id: int, medicine_id: int) -> None:
    if not _redis_client:
        return
    await _redis_client.delete(_pending_key(chat_id, medicine_id))  # type: ignore[misc]


async def _get_all_pending_reminders() -> list[tuple[int, int, dict]]:
    """Returns [(chat_id, medicine_id, data), ...] — used to restore state on bot startup."""
    if not _redis_client:
        return []
    result = []
    async for key in _redis_client.scan_iter(match=f"{_PENDING_KEY_PREFIX}*"):
        try:
            _, chat_id_str, medicine_id_str = key.split(":")
            raw = await _redis_client.get(key)  # type: ignore[misc]
            if raw:
                data = json.loads(raw)
                result.append((int(chat_id_str), int(medicine_id_str), data))
        except (ValueError, json.JSONDecodeError):  # fmt: skip
            continue
    return result


async def get_active_pending_reminders() -> list[dict]:
    """
    Returns currently "active" reminders: ones already sent to the user
    (a message with take/skip buttons was delivered) that haven't been
    acknowledged yet. This is a small subset of the full reminder schedule
    — most scheduled reminders simply haven't fired yet and aren't "active"
    in this sense. Used by the admin panel's Reminder Queue page.
    """
    pending = await _get_all_pending_reminders()
    return [
        {
            "chat_id": chat_id,
            "medicine_id": medicine_id,
            "medicine_name": data.get("medicine_name"),
            "sent_at": data.get("sent_at"),
        }
        for chat_id, medicine_id, data in pending
    ]


async def _delete_pending_reminders_for_medicine(medicine_id: int) -> None:
    if not _redis_client:
        return
    pattern = f"{_PENDING_KEY_PREFIX}*:{medicine_id}"
    async for key in _redis_client.scan_iter(match=pattern):
        await _redis_client.delete(key)  # type: ignore[misc]
        logger.info(f"Deleted an orphaned pending Redis record: {key}")


def _stock_alert_key(chat_id: int, medicine_id: int) -> str:
    return f"{_STOCK_ALERT_KEY_PREFIX}{chat_id}:{medicine_id}"


async def save_stock_alert_pending(chat_id: int, medicine_id: int, medicine_name: str, language: str) -> None:
    """Marks that an 'empty stock' alert was sent and is awaiting a user action
    (restock or archive). If no action is taken before the next scheduled dose,
    the medicine will be auto-archived."""
    if not _redis_client:
        return
    data = {"medicine_name": medicine_name, "language": language}
    await _redis_client.set(_stock_alert_key(chat_id, medicine_id), json.dumps(data), ex=_STOCK_ALERT_TTL_SECONDS)  # type: ignore[misc]


async def get_stock_alert_pending(chat_id: int, medicine_id: int) -> dict | None:
    if not _redis_client:
        return None
    raw = await _redis_client.get(_stock_alert_key(chat_id, medicine_id))  # type: ignore[misc]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def clear_stock_alert_pending(chat_id: int, medicine_id: int) -> None:
    if not _redis_client:
        return
    await _redis_client.delete(_stock_alert_key(chat_id, medicine_id))  # type: ignore[misc]


async def _delete_stock_alerts_for_medicine(medicine_id: int) -> None:
    if not _redis_client:
        return
    pattern = f"{_STOCK_ALERT_KEY_PREFIX}*:{medicine_id}"
    async for key in _redis_client.scan_iter(match=pattern):
        await _redis_client.delete(key)  # type: ignore[misc]


async def acquire_action_lock(chat_id: int, medicine_id: int) -> bool:
    """
    Idempotency guard against duplicate take/skip button taps (double-tap,
    slow network causing a retry, etc.) that would otherwise process the
    same dose twice — double-decrementing stock/course_duration and/or
    causing a "message is not modified" TelegramBadRequest when the second
    tap tries to edit a message that the first tap already edited.

    Returns True if this call acquired the lock (i.e. it's the first tap
    in-flight for this medicine right now), False if another take/skip is
    already being processed for the same chat_id+medicine_id — meaning this
    is a duplicate that should be ignored.
    """
    if not _redis_client:
        return True
    key = f"{_ACTION_LOCK_PREFIX}{chat_id}:{medicine_id}"
    acquired = await _redis_client.set(key, "1", nx=True, ex=_ACTION_LOCK_TTL_SECONDS)  # type: ignore[misc]
    return bool(acquired)
