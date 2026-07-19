"""
Admin panel composition root: logging setup, config/engine/session factory,
the FastAPI app instance, health check, and the sqladmin Admin instance.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqladmin.application import Admin as BaseAdmin
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request as StarletteRequest

from admin.auth import AdminAuth
from config import load_config
from database import crud

# ─── Admin panel file logging ──────
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_admin_file_handler = RotatingFileHandler(
    filename=os.path.join(LOG_DIR, "admin.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_admin_file_handler.setFormatter(_log_formatter)


for _logger_name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_logger_name).addHandler(_admin_file_handler)
logging.getLogger().setLevel(logging.INFO)

logger = logging.getLogger(__name__)

config = load_config()
engine = create_async_engine(config.database_url, echo=False)

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

# Initialize the FastAPI application
app = FastAPI(
    title="MedBot Admin Panel",
    description="Medication bot management panel",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = "static/favicon.ico"
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return FileResponse("static/favicon.ico")


@app.get("/health")
async def health_check():
    """
    Health-check endpoint for Docker healthcheck / monitoring.
    Verifies DB connectivity and Redis connectivity.
    """
    checks = {}
    healthy = True

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        healthy = False

    try:
        redis_client = aioredis.from_url(config.redis_url)
        await redis_client.ping()
        await redis_client.close()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        content={"status": "healthy" if healthy else "unhealthy", "checks": checks},
        status_code=status_code,
    )


# ─── Custom Admin ─────
class DashboardAdmin(BaseAdmin):
    async def index(self, request: StarletteRequest):
        async with SessionLocal() as session:
            stats = await crud.get_global_intake_stats(session)

        return await self.templates.TemplateResponse(
            request,
            "sqladmin/index.html",
            context={"stats": stats},
        )


# Initialize SQLAdmin (our subclass instead of the base Admin)
authentication_backend = AdminAuth(
    secret_key=config.admin_panel_session_secret,
    username=config.admin_panel_username,
    password_hash=config.admin_panel_password_hash,
)
admin = DashboardAdmin(
    app,
    engine,
    title="MedBot Dashboard",
    templates_dir="templates",
    authentication_backend=authentication_backend,
)

if not config.admin_panel_password_hash:
    logger.warning(
        "ADMIN_PANEL_PASSWORD_HASH is not set — the admin panel login form will reject "
        "every attempt until a password hash is configured. Generate one with: "
        "`python -m admin.auth`."
    )


# ─── Register ModelViews, custom pages, and their API routes ────────────────
from admin import dashboard, logs_viewer, model_views, sync  # noqa: E402

admin.add_view(model_views.UserAdmin)
admin.add_view(model_views.MedicineAdmin)
admin.add_view(model_views.MedicineScheduleAdmin)
admin.add_view(model_views.MedicineRecordAdmin)
admin.add_view(model_views.PrescriptionAdmin)
admin.add_view(model_views.ChatHistoryAdmin)
admin.add_view(dashboard.AIMetricsView)
admin.add_view(sync.ReminderQueueView)
admin.add_view(logs_viewer.LogsView)
