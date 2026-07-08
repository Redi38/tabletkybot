import asyncio
import logging
import ssl
import sys
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import load_config
from database.db import init_db
from middleware.db_middleware import DatabaseMiddleware

from services.scheduler import start_scheduler, stop_scheduler, sync_reminders, sync_single_reminder, scheduler, check_prescription_reminders, init_redis, resume_pending_reminders
from handlers import start, medicines, ai_agent, report, errors, settings, prescriptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_sync_handler(bot: Bot, session_factory):
    async def handle_sync(request):
        try:
            data = await request.json()
            action = data.get("action")
            medicine_id = data.get("medicine_id")

            if action and medicine_id:
                logger.info(f"Отримано точковий сигнал від Адмін-Панелі {action} для med_{medicine_id}")
                await sync_single_reminder(bot, session_factory, medicine_id, action)
            else:
                logger.info("⚠️ Сигнал без ID. Виконую повну синхронізацію...")
                await sync_reminders(bot, session_factory)

            return web.json_response({"status": "success", "message": "Синхронізовано"})

        except (web.HTTPBadRequest, asyncio.exceptions.TimeoutError, ValueError):
            logger.info("⚡ Отримано не-JSON сигнал. Виконую повну синхронізацію...")
            await sync_reminders(bot, session_factory)
            return web.json_response({"status": "success", "message": "Повна синхронізація виконана"})

        except Exception as e:
            logger.error(f"❌ Критична помилка обробника вебхука: {e}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    return handle_sync


async def main() -> None:
    config = load_config()

    init_redis(config.redis_url)

    try:
        session_factory = await init_db(config.database_url)
        logger.info("✅ База даних ініціалізована")
    except ConnectionRefusedError:
        logger.critical("❌ ПОМИЛКА: Не вдалося підключитися до бази даних!")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ Критична помилка підключення до БД: {e}")
        sys.exit(1)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(config.redis_url)
    dp = Dispatcher(storage=storage)

    dp["config"] = config
    dp["bot"] = bot

    dp.update.middleware(DatabaseMiddleware(session_factory))

    dp.include_router(errors.router)
    dp.include_router(settings.router)
    dp.include_router(prescriptions.router)
    dp.include_router(medicines.router)
    dp.include_router(report.router)
    dp.include_router(start.router)
    dp.include_router(ai_agent.router)

    start_scheduler()
    logger.info("APScheduler запущено")

    await sync_reminders(bot, session_factory)

    await resume_pending_reminders(bot)

    scheduler.add_job(
        sync_reminders, trigger='interval', hours=1, id='db_sync_job_hourly',
        replace_existing=True, kwargs={'bot': bot, 'session_factory': session_factory}
    )

    scheduler.add_job(
        check_prescription_reminders, trigger='cron', minute=0, timezone='UTC',
        id='presc_reminder_check_hourly', replace_existing=True,
        kwargs={'bot': bot, 'session_factory': session_factory}
    )

    # Читаємо сертифікат для Telegram
    with open(config.webhook_cert, "rb") as f:
        cert_data = f.read()

    await bot.set_webhook(
        url=config.webhook_url,
        certificate=types.BufferedInputFile(cert_data, filename="webhook.pem"),
        secret_token=config.webhook_secret,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info(f"✅ Webhook встановлено: {config.webhook_url}")

    # ── Публічний HTTPS-сервер для Telegram webhook (порт 8443) ──────────
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
    logger.info(f"🌐 Публічний webhook-сервер запущено на порту {config.webhook_port}")

    # ── Внутрішній HTTP-сервер для /api/sync від admin-панелі (порт 8080) ─
    internal_app = web.Application()
    internal_app.router.add_post("/api/sync", build_sync_handler(bot, session_factory))

    internal_runner = web.AppRunner(internal_app)
    await internal_runner.setup()
    internal_site = web.TCPSite(internal_runner, "0.0.0.0", 8080)
    await internal_site.start()
    logger.info("🔧 Внутрішній sync-сервер запущено на порту 8080")

    try:
        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        stop_scheduler()
        await runner.cleanup()
        await internal_runner.cleanup()
        if bot.session:
            await bot.session.close()
        logger.info("Бот зупинено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот зупинено користувачем")
