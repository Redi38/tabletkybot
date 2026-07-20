"""
Talking to the bot process from the admin panel: notifying it of DB changes
so its in-memory APScheduler jobs stay in sync, and proxying its live
reminder queue for the "Reminder Queue" sidebar page.

Self-contained: loads its own config rather than importing it from
admin.app, so it has no dependency on the composition root beyond the
`app` FastAPI instance (needed to register routes) and `logger`.
"""

import logging
import os

import aiohttp
from sqladmin import BaseView, expose
from starlette.requests import Request

from admin.app import app
from config import load_config

logger = logging.getLogger(__name__)
config = load_config()


async def notify_bot(action_name: str, medicine_id: int):
    """Sends a POST request with point data (JSON) to the bot's internal API."""
    base_url = os.getenv("WEBHOOK_BASE_URL", "http://bot:8080")
    webhook_url = f"{base_url}/api/sync"

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                webhook_url,
                json={"action": action_name, "medicine_id": medicine_id},
                headers={"X-Sync-Secret": config.sync_secret},
                timeout=aiohttp.ClientTimeout(total=2),
            )
            logger.info(f"Bot notified: {action_name} for medicine ID {medicine_id}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to contact the bot for synchronization: {e}")


@app.get("/api/admin/scheduled-jobs")
async def get_scheduled_jobs() -> dict:
    """
    Proxies to the bot's internal /api/scheduled-jobs — the admin panel runs
    in a separate process/container and has no direct access to the bot's
    in-memory APScheduler queue, so this is the only way to see it.
    """
    base_url = os.getenv("WEBHOOK_BASE_URL", "http://bot:8080")
    url = f"{base_url}/api/scheduled-jobs"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"X-Sync-Secret": config.sync_secret},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return {"status": "error", "message": data.get("message", "Failed to fetch scheduled jobs")}
                return data
    except Exception as e:
        logger.warning(f"Failed to fetch scheduled jobs from the bot: {e}")
        return {"status": "error", "message": f"Could not reach the bot: {e}"}


class ReminderQueueView(BaseView):
    """
    Custom page in the Admin Panel sidebar. Data is loaded via JS through
    /api/admin/scheduled-jobs, which proxies to the bot's live in-memory
    APScheduler queue — this is what's actually about to fire, not just
    what's configured in the Intake Schedules table.
    """

    name = "Reminder Queue"
    icon = "fa-solid fa-bell"

    @expose("/reminders-view", methods=["GET"])
    async def reminders_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request,
            "sqladmin/reminders.html",
            context={"request": request},
        )
