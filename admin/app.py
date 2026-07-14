import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiohttp
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqladmin import BaseView, ModelView, action, expose
from sqladmin.application import Admin as BaseAdmin
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request as StarletteRequest
from wtforms.validators import AnyOf, DataRequired, Length, NumberRange, Regexp

from config import load_config
from database import crud
from database.models import ChatHistory, Medicine, MedicineRecord, MedicineSchedule, Prescription, User, AIMetric

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


@app.get('/favicon.ico', include_in_schema=False)
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


templates = Jinja2Templates(directory="templates")


# ─── Custom Admin ─────
class DashboardAdmin(BaseAdmin):
    async def index(self, request: StarletteRequest):
        async with SessionLocal() as session:
            stats = await crud.get_global_intake_stats(session)

        return await self.templates.TemplateResponse(
            request, "sqladmin/index.html", context={"stats": stats},
        )


# Initialize SQLAdmin (our subclass instead of the base Admin)
admin = DashboardAdmin(app, engine, title="MedBot Dashboard", templates_dir="templates")


# ─── Function to notify the bot of changes ──────────────────────────────────
async def notify_bot(action_name: str, medicine_id: int):
    """Sends a POST request with point data (JSON) to the bot's internal API."""
    base_url = os.getenv("WEBHOOK_BASE_URL", "http://bot:8080")
    webhook_url = f"{base_url}/api/sync"

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                webhook_url,
                json={"action": action_name, "medicine_id": medicine_id},
                timeout=aiohttp.ClientTimeout(total=2),
            )
            logger.info(f"Bot notified: {action_name} for medicine ID {medicine_id}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to contact the bot for synchronization: {e}")


# ─── Model display configuration in the Admin Panel ──────────────────────
class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.full_name, User.username, User.language, User.timezone, User.created_at]

    column_searchable_list = ["full_name", "username"]
    column_filters = [StaticValuesFilter(User.language, values=[("ua", "Ukrainian"), ("en", "English"), ("ru", "Russian")])]
    column_default_sort = ("created_at", True)

    form_args = dict(
        full_name=dict(validators=[
            DataRequired(message="Full name is required"),
            Length(min=2, max=100, message="Name must be between 2 and 100 characters")
        ]),
        timezone=dict(validators=[DataRequired(message="Timezone cannot be empty")]),
    )


class MedicineAdmin(ModelView, model=Medicine):
    name = "Medicine"
    name_plural = "Medicines"
    icon = "fa-solid fa-pills"
    column_list = [Medicine.id, Medicine.user, Medicine.name, Medicine.dosage,
                   Medicine.course_duration, Medicine.stock_amount, Medicine.is_active]

    column_searchable_list = ["form", "user.full_name", "user.username"]
    column_filters = [BooleanFilter(Medicine.is_active)]
    column_details_exclude_list = [Medicine.records, Medicine.schedules]

    form_args = dict(
        name=dict(validators=[
            DataRequired(message="Medicine name cannot be empty"),
            Length(max=150)
        ]),
        form=dict(validators=[DataRequired(message="Specify the form (e.g. tablets)")]),
        dosage=dict(validators=[DataRequired(message="Specify the dosage (e.g. 500 mg)")]),
        course_duration=dict(validators=[NumberRange(min=1, message="Course duration (doses) must be a number greater than 0")]),
        stock_amount=dict(validators=[NumberRange(min=0, message="Stock must be >= 0 (or left empty)")]),
        low_stock_threshold=dict(validators=[NumberRange(min=0, message="Threshold must be >= 0 (or left empty)")])
    )

    async def after_model_change(self, data, model, is_created, request):
        await notify_bot("update", model.id)

    async def after_model_delete(self, model, request):
        await notify_bot("delete", model.id)

    @action(
        name="send_reminder_now",
        label="Send reminder",
        confirmation_message="Send a reminder about this medicine to the user right now?",
        add_in_detail=True,
        add_in_list=True,
    )
    async def send_reminder_now(self, request):
        pks = request.query_params.get("pks", "")
        sent, failed = 0, 0
        for pk in pks.split(","):
            if not pk:
                continue
            try:
                await notify_bot("send_now", int(pk))
                sent += 1
            except Exception as e:
                logger.error(f"Failed to send reminder for medicine {pk}: {e}")
                failed += 1

        from starlette.responses import RedirectResponse
        referer = request.headers.get("referer", "/admin/medicine/list")
        return RedirectResponse(url=referer, status_code=303)


class MedicineScheduleAdmin(ModelView, model=MedicineSchedule):
    name = "Schedule"
    name_plural = "Intake Schedules"
    icon = "fa-solid fa-clock"
    column_list = [MedicineSchedule.id, MedicineSchedule.medicine, MedicineSchedule.scheduled_time]

    column_searchable_list = ["scheduled_time"]

    form_args = dict(
        scheduled_time=dict(validators=[
            DataRequired(message="Time is required"),
            Regexp(r"^(?:[01]\d|2[0-3]):[0-5]\d$", message="Time must be in HH:MM format (e.g. 08:30 or 20:00)")
        ])
    )

    async def after_model_change(self, data, model, is_created, request):
        await notify_bot("update", model.medicine_id)

    async def after_model_delete(self, model, request):
        await notify_bot("delete", model.medicine_id)


class MedicineRecordAdmin(ModelView, model=MedicineRecord):
    name = "Intake Record"
    name_plural = "Intake History"
    icon = "fa-solid fa-clipboard-check"
    column_list = [MedicineRecord.id, MedicineRecord.medicine, MedicineRecord.status, MedicineRecord.taken_at,
                   MedicineRecord.remaining_days]
    column_default_sort = ("taken_at", True)
    column_filters = [StaticValuesFilter(MedicineRecord.status, values=[("taken", "Taken"), ("skipped", "Skipped")])]

    form_args = dict(
        status=dict(validators=[
            DataRequired(message="Status is required"),
            AnyOf(["taken", "skipped"], message="Status can only be 'taken' or 'skipped'")
        ]),
        remaining_days=dict(validators=[NumberRange(min=0, message="Remaining days cannot be negative")])
    )


class PrescriptionAdmin(ModelView, model=Prescription):
    name = "Prescription"
    name_plural = "Prescriptions"
    icon = "fa-solid fa-file-prescription"
    column_list = [
        Prescription.id, Prescription.user, Prescription.medicine_name,
        Prescription.valid_from, Prescription.expires_at, Prescription.max_quantity,
        Prescription.purchased_quantity, Prescription.is_fully_purchased,
        Prescription.reminder_days_before, Prescription.reminder_sent, Prescription.is_active,
    ]
    column_searchable_list = ["medicine_name", "user.full_name"]
    column_default_sort = ("expires_at", False)
    column_filters = [BooleanFilter(Prescription.is_active), BooleanFilter(Prescription.is_fully_purchased)]

    form_args = dict(
        medicine_name=dict(validators=[
            DataRequired(message="Medicine name is required"),
            Length(max=150)
        ]),
        max_quantity=dict(validators=[NumberRange(min=0, message="Must be >= 0 (or empty)")]),
        purchased_quantity=dict(validators=[NumberRange(min=0, message="Cannot be negative")]),
        reminder_days_before=dict(validators=[NumberRange(min=0, max=90, message="From 0 to 90 days")]),
    )


class ChatHistoryAdmin(ModelView, model=ChatHistory):
    """
    Read-only view of the conversation with the AI agent.
    """
    name = "AI Message"
    name_plural = "AI Chat History"
    icon = "fa-solid fa-robot"
    column_list = [ChatHistory.id, ChatHistory.user, ChatHistory.role, ChatHistory.content, ChatHistory.created_at]
    column_details_list = [ChatHistory.id, ChatHistory.user, ChatHistory.role, ChatHistory.content, ChatHistory.created_at]
    column_searchable_list = ["user.full_name", "user.username"]
    column_filters = [StaticValuesFilter(ChatHistory.role, values=[("user", "user"), ("assistant", "assistant")])]
    column_default_sort = ("created_at", True)

    column_formatters = {
        ChatHistory.content: lambda m, a: (m.content[:80] + "…") if m.content and len(m.content) > 80 else m.content,
    }

    can_create = False
    can_edit = False
    can_export = True


admin.add_view(UserAdmin)
admin.add_view(MedicineAdmin)
admin.add_view(MedicineScheduleAdmin)
admin.add_view(MedicineRecordAdmin)
admin.add_view(PrescriptionAdmin)
admin.add_view(ChatHistoryAdmin)


# ─── Log viewer ─────────────────────────────────────────────────────
LOG_FILES = {
    "bot": os.path.join(LOG_DIR, "bot.log"),
    "admin": os.path.join(LOG_DIR, "admin.log"),
}

_MAX_LINES = 1000


def _tail_lines(path: str, max_lines: int, chunk_size: int = 65536) -> list[str]:
    """
    Efficiently reads the last max_lines lines of a file WITHOUT loading the
    whole file into memory — reads chunks from the end of the file until
    enough lines have been collected.
    """
    if not os.path.exists(path):
        return []

    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        data = b""
        read_size = 0

        while read_size < file_size and data.count(b"\n") <= max_lines:
            read_size = min(read_size + chunk_size, file_size)
            f.seek(file_size - read_size)
            data = f.read(read_size)

        lines = data.decode("utf-8", errors="replace").splitlines()
        return lines[-max_lines:]


def _log_line_matches(line: str, level: str = "", search: str = "") -> bool:
    """Shared filter predicate used by both the JSON viewer and the download endpoint."""
    if level and f"| {level.upper()} |" not in line:
        return False
    if search and search.lower() not in line.lower():
        return False
    return True


@app.get("/api/admin/logs")
async def get_admin_logs(
        source: str = "bot", lines: int = 200, level: str = "", search: str = ""
) -> dict:
    """
    JSON with the latest log lines.
    source: "bot" | "admin"
    lines: how many lines to return (hard-capped at _MAX_LINES)
    level: "" | "INFO" | "WARNING" | "ERROR" — log level filter
    search: arbitrary text to search for (case-insensitive substring)
    """
    path = LOG_FILES.get(source)
    if not path:
        return {"error": "invalid source", "lines": []}

    lines = max(1, min(lines, _MAX_LINES))

    raw_lines = _tail_lines(path, max_lines=lines * 3 if (level or search) else lines)

    if level or search:
        raw_lines = [ln for ln in raw_lines if _log_line_matches(ln, level, search)]

    return {"source": source, "lines": raw_lines[-lines:]}


@app.get("/api/admin/logs/download")
async def download_logs(source: str = "bot", level: str = "", search: str = ""):
    """
    Downloads the full log file (optionally filtered by level/search) as a
    plain text attachment. Unlike /api/admin/logs, this is not capped by
    _MAX_LINES — it streams the whole matching content so nothing is lost
    when investigating an incident.
    """
    path = LOG_FILES.get(source)
    if not path:
        raise HTTPException(status_code=400, detail="Invalid log source")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Log file not found")

    def iter_file():
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if not level and not search:
                # Whole file, streamed in chunks — avoids loading it all into memory
                while chunk := f.read(65536):
                    yield chunk
            else:
                for line in f:
                    if _log_line_matches(line, level, search):
                        yield line

    suffix_parts = [source]
    if level:
        suffix_parts.append(level.lower())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"logs_{'_'.join(suffix_parts)}_{timestamp}.log"

    return StreamingResponse(
        iter_file(),
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class AIMetricsView(BaseView):
    name = "AI Metrics"
    icon = "fa-solid fa-chart-line"

    @expose("/admin/ai-metrics-view", methods=["GET"])
    async def ai_metrics_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request, "sqladmin/ai_metrics.html", context={"request": request},
        )


class LogsView(BaseView):
    """
    Custom page in the Admin Panel sidebar. Data is loaded via JS
    through /api/admin/logs — the page itself only renders the template.
    """
    name = "Logs"
    icon = "fa-solid fa-file-lines"

    @expose("/admin/logs-view", methods=["GET"])
    async def logs_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request, "sqladmin/logs.html", context={"request": request},
        )


admin.add_view(AIMetricsView)
admin.add_view(LogsView)


# ─── Dashboard and Statistics Routes ─────────────────────────────────────
@app.get("/admin/dashboard")
async def admin_dashboard(request: Request):
    """Renders the main dashboard page with charts."""
    async with SessionLocal() as session:
        stats = await crud.get_global_intake_stats(session)

    return await admin.templates.TemplateResponse(
        request, "sqladmin/index.html", context={"stats": stats},
    )


@app.get("/api/admin/stats")
async def get_admin_stats(period: str = "all"):
    """API endpoint that returns dynamic JSON for the charts depending on the selected period."""
    async with SessionLocal() as session:
        stats = await crud.get_dashboard_stats(session, period)
        return stats

@app.get("/api/admin/ai-metrics")
async def get_ai_metrics(period: str = "24h"):
    async with SessionLocal() as session:
        summary = await crud.get_ai_metrics_summary(session, period)
        recent = await crud.get_recent_ai_metrics(session, limit=50)
        recent_list = [
            {
                "id": m.id,
                "full_name": full_name,
                "model_used": m.model_used,
                "tool_choice": m.tool_choice,
                "tool_names": m.tool_names or "—",
                "latency_ms": m.latency_ms,
                "status": m.status,
                "created_at": m.created_at.strftime("%d.%m %H:%M:%S"),
            }
            for m, full_name in recent
        ]
        return {"summary": summary, "recent": recent_list}
