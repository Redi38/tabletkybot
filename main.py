import asyncio
import logging
import os
import ssl
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import load_config
from database.db import init_db
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
    archive_expired_prescriptions,
    check_prescription_reminders,
    init_redis,
    resume_pending_reminders,
    scheduler,
    start_scheduler,
    stop_scheduler,
    sync_reminders,
)
from web.internal_api import build_health_handler, build_scheduled_jobs_handler, build_sync_handler

# ─── Logging: simultaneously to docker logs and to a file (for the Admin Panel) ──
_default_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_DIR = os.getenv("LOG_DIR", _default_log_dir)
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


async def _set_webhook_with_retry(
    bot: Bot,
    *,
    url: str,
    cert_data: bytes,
    secret_token: str,
    drop_pending_updates: bool,
    allowed_updates,
    max_attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
) -> None:
    """Call bot.set_webhook with exponential backoff.

    Telegram's API (or the network path to it) can be briefly unavailable right
    when the container starts. Without a retry, that single failed call would
    raise out of main() and crash the whole process before it ever handles an
    update. We keep trying with a capped exponential backoff instead of giving
    up after the first hiccup.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            await bot.set_webhook(
                url=url,
                certificate=types.BufferedInputFile(cert_data, filename="webhook.pem"),
                secret_token=secret_token,
                drop_pending_updates=drop_pending_updates,
                allowed_updates=allowed_updates,
            )
            return
        except TelegramRetryAfter as e:
            # Telegram explicitly tells us how long to wait.
            delay = float(e.retry_after)
            logger.warning(f"⚠️ set_webhook rate-limited by Telegram, retrying in {delay:.0f}s (attempt {attempt})")
        except TelegramNetworkError as e:
            if attempt >= max_attempts:
                logger.critical(f"❌ set_webhook failed after {attempt} attempts, giving up: {e}")
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                f"⚠️ set_webhook network error (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay:.0f}s"
            )
        await asyncio.sleep(delay)


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

    async def _timed_job(name: str, coro) -> None:
        start = asyncio.get_running_loop().time()
        try:
            await coro
        finally:
            elapsed = asyncio.get_running_loop().time() - start
            logger.info(f"Job '{name}' finished in {elapsed:.2f}s")

    async def _tagged_sync_reminders(bot, session_factory):
        with correlation_scope("job:sync_reminders_hourly"):
            await _timed_job("sync_reminders_hourly", sync_reminders(bot, session_factory))

    async def _tagged_check_prescriptions(bot, session_factory):
        with correlation_scope("job:check_prescription_reminders"):
            await _timed_job("check_prescription_reminders", check_prescription_reminders(bot, session_factory))

    async def _tagged_archive_expired_prescriptions(bot, session_factory):
        with correlation_scope("job:archive_expired_prescriptions"):
            await _timed_job("archive_expired_prescriptions", archive_expired_prescriptions(bot, session_factory))

    async def _tagged_backup(config):
        with correlation_scope("job:db_backup_daily"):
            await _timed_job("db_backup_daily", run_database_backup(config))

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
        _tagged_archive_expired_prescriptions,
        trigger="cron",
        hour=0,
        minute=10,
        timezone="UTC",
        id="presc_archive_expired_daily",
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

    # Read the certificate for Telegram
    with open(config.webhook_cert, "rb") as f:
        cert_data = f.read()

    await _set_webhook_with_retry(
        bot,
        url=config.webhook_url,
        cert_data=cert_data,
        secret_token=config.webhook_secret,
        drop_pending_updates=config.webhook_drop_pending_updates,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info(f"✅ Webhook set: {config.webhook_url}")

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
    except (KeyboardInterrupt, SystemExit):  # fmt: skip
        logger.info("Bot stopped by user")
