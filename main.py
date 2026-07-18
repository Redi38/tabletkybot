import asyncio
import hmac
import logging
import os
import ssl
import sys
import tempfile
from logging.handlers import RotatingFileHandler

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from sqlalchemy import select, text

from config import load_config
from database.db import init_db
from database.models import User
from handlers import ai_agent, errors, medicines, prescriptions, report, settings, start
from middleware.db_middleware import DatabaseMiddleware
from middleware.logging_context import (
    ActionLoggingMiddleware,
    CorrelationIdFilter,
    CorrelationIdMiddleware,
    correlation_scope,
)
from services.backup_service import run_database_backup
from services.scheduler import (
    check_prescription_reminders,
    get_active_pending_reminders,
    init_redis,
    resume_pending_reminders,
    scheduler,
    start_scheduler,
    stop_scheduler,
    sync_reminders,
    sync_single_reminder,
)

# ─── Logging: simultaneously to docker logs and to a file (for the Admin Panel) ──
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    LOG_DIR = os.path.join(tempfile.gettempdir(), "medbot_logs")
    os.makedirs(LOG_DIR, exist_ok=True)

_log_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | [%(cid)s] | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_correlation_filter = CorrelationIdFilter()

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)
_stream_handler.addFilter(_correlation_filter)

# Size-based rotation — so the log file doesn't grow endlessly and fill up the disk
_file_handler = RotatingFileHandler(
    filename=os.path.join(LOG_DIR, "bot.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
_file_handler.addFilter(_correlation_filter)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger(__name__)

logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_sync_handler(bot: Bot, session_factory, sync_secret: str):
    if not sync_secret:
        logger.warning(
            "SYNC_SECRET is not set — the internal /api/sync endpoint will reject every "
            "request until it is configured. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )

    async def handle_sync(request: web.Request) -> web.Response:
        # Server-to-server auth: the admin panel sends this secret in a
        # header on every call.
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

        except (web.HTTPBadRequest, asyncio.exceptions.TimeoutError, ValueError):
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


async def main() -> None:
    config = load_config()

    init_redis(config.redis_url)

    try:
        session_factory = await init_db(config.database_url)
        logger.info("✅ Database initialized")
    except ConnectionRefusedError:
        logger.critical("❌ ERROR: Failed to connect to the database!")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ Critical error connecting to the DB: {e}")
        sys.exit(1)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(config.redis_url)
    dp = Dispatcher(storage=storage)

    dp["config"] = config
    dp["bot"] = bot
    dp["session_factory"] = session_factory

    dp.update.middleware(CorrelationIdMiddleware())
    dp.update.middleware(ActionLoggingMiddleware())
    dp.update.middleware(DatabaseMiddleware(session_factory))

    dp.include_router(errors.router)
    dp.include_router(settings.router)
    dp.include_router(prescriptions.router)
    dp.include_router(medicines.router)
    dp.include_router(report.router)
    dp.include_router(start.router)
    dp.include_router(ai_agent.router)

    start_scheduler()
    logger.info("APScheduler started")

    with correlation_scope("job:startup_sync"):
        await sync_reminders(bot, session_factory)
        await resume_pending_reminders(bot)

    async def _tagged_sync_reminders(bot, session_factory):
        with correlation_scope("job:sync_reminders_hourly"):
            await sync_reminders(bot, session_factory)

    async def _tagged_check_prescriptions(bot, session_factory):
        with correlation_scope("job:check_prescription_reminders"):
            await check_prescription_reminders(bot, session_factory)

    async def _tagged_backup(config):
        with correlation_scope("job:db_backup_daily"):
            await run_database_backup(config)

    scheduler.add_job(
        _tagged_sync_reminders,
        trigger="interval",
        hours=1,
        id="db_sync_job_hourly",
        replace_existing=True,
        kwargs={"bot": bot, "session_factory": session_factory},
    )

    scheduler.add_job(
        _tagged_check_prescriptions,
        trigger="cron",
        minute=0,
        timezone="UTC",
        id="presc_reminder_check_hourly",
        replace_existing=True,
        kwargs={"bot": bot, "session_factory": session_factory},
    )

    scheduler.add_job(
        _tagged_backup,
        trigger="cron",
        hour=3,
        minute=0,
        timezone="Europe/Kyiv",
        id="db_backup_daily",
        replace_existing=True,
        kwargs={"config": config},
    )

    # Read the certificate for Telegram
    with open(config.webhook_cert, "rb") as f:
        cert_data = f.read()

    await bot.set_webhook(
        url=config.webhook_url,
        certificate=types.BufferedInputFile(cert_data, filename="webhook.pem"),
        secret_token=config.webhook_secret,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info(f"✅ Webhook set: {config.webhook_url}")

    # ── Public HTTPS server for the Telegram webhook (port 8443) ──────────
    app = web.Application()

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.webhook_secret,
    ).register(app, path=config.webhook_path)

    setup_application(app, dp, bot=bot)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(config.webhook_cert, config.webhook_key)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.webhook_port, ssl_context=ssl_context)
    await site.start()
    logger.info(f"🌐 Public webhook server started on port {config.webhook_port}")

    # ── Internal HTTP server for /api/sync and /health (port 8080) ────────
    internal_app = web.Application()
    internal_app.router.add_post("/api/sync", build_sync_handler(bot, session_factory, config.sync_secret))
    internal_app.router.add_get(
        "/api/scheduled-jobs", build_scheduled_jobs_handler(session_factory, config.sync_secret)
    )
    internal_app.router.add_get("/health", build_health_handler(session_factory, config.redis_url))

    internal_runner = web.AppRunner(internal_app, access_log=None)
    await internal_runner.setup()
    internal_site = web.TCPSite(internal_runner, "0.0.0.0", 8080)
    await internal_site.start()
    logger.info("🔧 Internal sync server started on port 8080")

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        stop_scheduler()
        await runner.cleanup()
        await internal_runner.cleanup()
        if bot.session:
            await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
