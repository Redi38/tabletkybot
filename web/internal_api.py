"""
Handler factories for the internal HTTP API (port 8080): /api/sync,
/api/scheduled-jobs, and /health. This is server-to-server traffic from the
Admin Panel and Docker healthchecks — never exposed publicly like the
Telegram webhook is.

Moved out of main.py to keep that file focused on process startup/wiring;
these three handlers had no dependency on anything else in main.py besides
a few shared imports.
"""

import asyncio
import hmac
import logging

import redis.asyncio as aioredis
from aiogram import Bot
from aiohttp import web
from sqlalchemy import select, text

from database.models import User
from middleware.logging_context import correlation_scope
from services.scheduler import (
    get_active_pending_reminders,
    scheduler,
    sync_reminders,
    sync_single_reminder,
)

logger = logging.getLogger(__name__)


def build_sync_handler(bot: Bot, session_factory, sync_secret: str):
    if not sync_secret:
        logger.warning(
            "SYNC_SECRET is not set — the internal /api/sync endpoint will reject every "
            "request until it is configured. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )

    async def handle_sync(request: web.Request) -> web.Response:
        # Server-to-server auth: the admin panel sends this secret in a header on every call.
        provided = request.headers.get("X-Sync-Secret", "")
        if not sync_secret or not hmac.compare_digest(provided, sync_secret):
            logger.warning(f"Rejected /api/sync request from {request.remote}: invalid or missing X-Sync-Secret")
            return web.json_response({"status": "error", "message": "unauthorized"}, status=401)

        try:
            data = await request.json()
            action = data.get("action")
            medicine_id = data.get("medicine_id")

            with correlation_scope(f"adminsync:{action or 'full'}:{medicine_id or '-'}"):
                if action and medicine_id:
                    logger.info(f"Received a point signal from the Admin Panel: {action} for med_{medicine_id}")
                    await sync_single_reminder(bot, session_factory, medicine_id, action)
                else:
                    logger.info("⚠️ Signal without an ID. Running a full synchronization...")
                    await sync_reminders(bot, session_factory)

            return web.json_response({"status": "success", "message": "Synchronized"})

        except (web.HTTPBadRequest, asyncio.exceptions.TimeoutError, ValueError):  # fmt: skip
            with correlation_scope("adminsync:fallback-full"):
                logger.info("⚡ Received a non-JSON signal. Running a full synchronization...")
                await sync_reminders(bot, session_factory)
            return web.json_response({"status": "success", "message": "Full synchronization completed"})

        except Exception as e:
            logger.error(f"❌ Critical error in the webhook handler: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    return handle_sync


def build_scheduled_jobs_handler(session_factory, sync_secret: str):

    async def handle_scheduled_jobs(request: web.Request) -> web.Response:
        provided = request.headers.get("X-Sync-Secret", "")
        if not sync_secret or not hmac.compare_digest(provided, sync_secret):
            logger.warning(
                f"Rejected /api/scheduled-jobs request from {request.remote}: invalid or missing X-Sync-Secret"
            )
            return web.json_response({"status": "error", "message": "unauthorized"}, status=401)

        active = await get_active_pending_reminders()

        jobs = []
        if active:
            chat_ids = {r["chat_id"] for r in active}
            async with session_factory() as session:
                result = await session.execute(select(User).where(User.id.in_(chat_ids)))
                names_by_id = {u.id: (u.full_name or u.username or str(u.id)) for u in result.scalars().all()}

            for r in active:
                jobs.append(
                    {
                        "medicine_id": r["medicine_id"],
                        "medicine_name": r["medicine_name"],
                        "chat_id": r["chat_id"],
                        "user_name": names_by_id.get(r["chat_id"], str(r["chat_id"])),
                        "sent_at": r["sent_at"],
                    }
                )

        jobs.sort(key=lambda j: j["sent_at"] or "")
        return web.json_response({"status": "success", "count": len(jobs), "jobs": jobs})

    return handle_scheduled_jobs


def build_health_handler(session_factory, redis_url: str):
    """
    Health-check endpoint for Docker healthcheck / monitoring.
    Verifies DB connectivity, Redis connectivity, and that the
    APScheduler instance is actually running.
    """

    async def handle_health(request):
        checks = {}
        healthy = True

        # DB check
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"
            healthy = False

        # Redis check
        try:
            redis_client = aioredis.from_url(redis_url)
            await redis_client.ping()
            await redis_client.close()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
            healthy = False

        # Scheduler check
        checks["scheduler"] = "running" if scheduler.running else "stopped"
        if not scheduler.running:
            healthy = False

        status_code = 200 if healthy else 503
        return web.json_response(
            {"status": "healthy" if healthy else "unhealthy", "checks": checks},
            status=status_code,
        )

    return handle_health
