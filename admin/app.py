import os
import logging
from logging.handlers import RotatingFileHandler
import aiohttp
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqladmin import ModelView, action, BaseView, expose
from sqladmin.application import Admin as BaseAdmin
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from starlette.requests import Request as StarletteRequest

from config import load_config
from database.models import User, Medicine, MedicineRecord, ChatHistory, MedicineSchedule, Prescription
from database import crud

from wtforms.validators import NumberRange, DataRequired, Length, Regexp, AnyOf

# ─── Логування адмінки у файл ──────
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

# Ініціалізуємо FastAPI застосунок
app = FastAPI(
    title="MedBot Admin Panel",
    description="Панель керування медичним ботом",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    favicon_path = "static/favicon.ico"
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return FileResponse("static/favicon.ico")


templates = Jinja2Templates(directory="templates")


# ─── Кастомний Admin ─────
class DashboardAdmin(BaseAdmin):
    async def index(self, request: StarletteRequest):
        async with SessionLocal() as session:
            stats = await crud.get_global_intake_stats(session)

        return await self.templates.TemplateResponse(
            request, "sqladmin/index.html", context={"stats": stats},
        )


# Ініціалізуємо SQLAdmin (наш підклас замість базового Admin)
admin = DashboardAdmin(app, engine, title="MedBot Dashboard", templates_dir="templates")


# ─── Функція повідомлення бота про зміни ──────────────────────────────────
async def notify_bot(action_name: str, medicine_id: int):
    """Надсилає POST-запит з точковими даними (JSON) до внутрішнього API бота."""
    base_url = os.getenv("WEBHOOK_BASE_URL", "http://bot:8080")
    webhook_url = f"{base_url}/api/sync"

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json={"action": action_name, "medicine_id": medicine_id}, timeout=2)
            logger.info(f"Бот повідомлений: {action_name} для препарату ID {medicine_id}")
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося зв'язатися з ботом для синхронізації: {e}")


# ─── Налаштування відображення моделей в Адмін-Панелі ──────────────────────
class UserAdmin(ModelView, model=User):
    name = "Користувач"
    name_plural = "Користувачі"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.full_name, User.username, User.language, User.timezone, User.created_at]

    column_searchable_list = ["full_name", "username"]
    column_filters = [StaticValuesFilter(User.language, values=[("ua", "Українська"), ("en", "English"), ("ru", "Русский")])]
    column_default_sort = ("created_at", True)

    form_args = dict(
        full_name=dict(validators=[
            DataRequired(message="ПІБ є обов'язковим"),
            Length(min=2, max=100, message="Ім'я має бути від 2 до 100 символів")
        ]),
        timezone=dict(validators=[DataRequired(message="Часовий пояс не може бути порожнім")]),
    )


class MedicineAdmin(ModelView, model=Medicine):
    name = "Препарат"
    name_plural = "Препарати"
    icon = "fa-solid fa-pills"
    column_list = [Medicine.id, Medicine.user, Medicine.name, Medicine.dosage,
                   Medicine.course_duration, Medicine.stock_amount, Medicine.is_active]

    column_searchable_list = ["form", "user.full_name", "user.username"]
    column_filters = [BooleanFilter(Medicine.is_active)]
    column_details_exclude_list = [Medicine.records, Medicine.schedules]

    form_args = dict(
        name=dict(validators=[
            DataRequired(message="Назва препарату не може бути порожньою"),
            Length(max=150)
        ]),
        form=dict(validators=[DataRequired(message="Вкажіть форму випуску (наприклад: таблетки)")]),
        dosage=dict(validators=[DataRequired(message="Вкажіть дозування (наприклад: 500 мг)")]),
        course_duration=dict(validators=[NumberRange(min=1, message="Тривалість (дози) має бути числом більше 0")]),
        stock_amount=dict(validators=[NumberRange(min=0, message="Залишок має бути >= 0 (або залиште пустим)")]),
        low_stock_threshold=dict(validators=[NumberRange(min=0, message="Поріг має бути >= 0 (або залиште пустим)")])
    )

    async def after_model_change(self, data, model, is_created, request):
        await notify_bot("update", model.id)

    async def after_model_delete(self, model, request):
        await notify_bot("delete", model.id)

    @action(
        name="send_reminder_now",
        label="Надіслати нагадування",
        confirmation_message="Надіслати нагадування про цей препарат користувачу прямо зараз?",
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
                logger.error(f"Не вдалося надіслати нагадування для медикаменту {pk}: {e}")
                failed += 1

        from starlette.responses import RedirectResponse
        referer = request.headers.get("referer", "/admin/medicine/list")
        return RedirectResponse(url=referer, status_code=303)


class MedicineScheduleAdmin(ModelView, model=MedicineSchedule):
    name = "Розклад"
    name_plural = "Розклади прийомів"
    icon = "fa-solid fa-clock"
    column_list = [MedicineSchedule.id, MedicineSchedule.medicine, MedicineSchedule.scheduled_time]

    column_searchable_list = ["scheduled_time"]

    form_args = dict(
        scheduled_time=dict(validators=[
            DataRequired(message="Час є обов'язковим"),
            Regexp(r"^(?:[01]\d|2[0-3]):[0-5]\d$", message="Час має бути у форматі ГГ:ХХ (наприклад: 08:30 або 20:00)")
        ])
    )

    async def after_model_change(self, data, model, is_created, request):
        await notify_bot("update", model.medicine_id)

    async def after_model_delete(self, model, request):
        await notify_bot("delete", model.medicine_id)


class MedicineRecordAdmin(ModelView, model=MedicineRecord):
    name = "Запис прийому"
    name_plural = "Історія прийомів"
    icon = "fa-solid fa-clipboard-check"
    column_list = [MedicineRecord.id, MedicineRecord.medicine, MedicineRecord.status, MedicineRecord.taken_at,
                   MedicineRecord.remaining_days]
    column_default_sort = ("taken_at", True)
    column_filters = [StaticValuesFilter(MedicineRecord.status, values=[("taken", "Прийнято"), ("skipped", "Пропущено")])]

    form_args = dict(
        status=dict(validators=[
            DataRequired(message="Статус є обов'язковим"),
            AnyOf(["taken", "skipped"], message="Статус може бути тільки 'taken' або 'skipped'")
        ]),
        remaining_days=dict(validators=[NumberRange(min=0, message="Залишок не може бути від'ємним")])
    )


class PrescriptionAdmin(ModelView, model=Prescription):
    name = "Рецепт"
    name_plural = "Рецепти"
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
            DataRequired(message="Назва препарату обов'язкова"),
            Length(max=150)
        ]),
        max_quantity=dict(validators=[NumberRange(min=0, message="Має бути >= 0 (або пусто)")]),
        purchased_quantity=dict(validators=[NumberRange(min=0, message="Не може бути від'ємним")]),
        reminder_days_before=dict(validators=[NumberRange(min=0, max=90, message="Від 0 до 90 днів")]),
    )


class ChatHistoryAdmin(ModelView, model=ChatHistory):
    """
    Read-only перегляд переписки з ІІ-агентом.
    """
    name = "Повідомлення ШІ"
    name_plural = "Історія діалогів ШІ"
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


# ─── Перегляд логів ─────────────────────────────────────────────────────
LOG_FILES = {
    "bot": os.path.join(LOG_DIR, "bot.log"),
    "admin": os.path.join(LOG_DIR, "admin.log"),
}

_MAX_LINES = 1000


def _tail_lines(path: str, max_lines: int, chunk_size: int = 65536) -> list[str]:
    """
    Ефективно читає останні max_lines рядків файлу БЕЗ завантаження всього
    файлу в пам'ять — читає чанками з кінця файлу, поки не набереться
    достатньо рядків.
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


@app.get("/api/admin/logs")
async def get_admin_logs(
        source: str = "bot", lines: int = 200, level: str = "", search: str = ""
) -> dict:
    """
    JSON з останніми рядками логів.
    source: "bot" | "admin"
    lines: скільки рядків повернути (жорстко обмежено _MAX_LINES)
    level: "" | "INFO" | "WARNING" | "ERROR" — фільтр по рівню логування
    search: довільний текст для пошуку (case-insensitive підрядок)
    """
    path = LOG_FILES.get(source)
    if not path:
        return {"error": "invalid source", "lines": []}

    lines = max(1, min(lines, _MAX_LINES))

    raw_lines = _tail_lines(path, max_lines=lines * 3 if (level or search) else lines)

    if level:
        marker = f"| {level.upper()} |"
        raw_lines = [ln for ln in raw_lines if marker in ln]

    if search:
        needle = search.lower()
        raw_lines = [ln for ln in raw_lines if needle in ln.lower()]

    return {"source": source, "lines": raw_lines[-lines:]}


class LogsView(BaseView):
    """
    Кастомна сторінка в бічному меню Адмін-Панелі. Дані підвантажуються
    JS-ом через /api/admin/logs — сама сторінка лише рендерить шаблон.
    """
    name = "Логи"
    icon = "fa-solid fa-file-lines"

    @expose("/admin/logs-view", methods=["GET"])
    async def logs_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request, "sqladmin/logs.html", context={"request": request},
        )


admin.add_view(LogsView)


# ─── Роути для Дашборду та Статистики ─────────────────────────────────────
@app.get("/admin/dashboard")
async def admin_dashboard(request: Request):
    """Рендерить головну сторінку дашборду з графіками."""
    async with SessionLocal() as session:
        stats = await crud.get_global_intake_stats(session)

    return await admin.templates.TemplateResponse(
        request, "sqladmin/index.html", context={"stats": stats},
    )


@app.get("/api/admin/stats")
async def get_admin_stats(period: str = "all"):
    """API-ендпоінт, який повертає динамічний JSON для графіків залежно від обраного періоду."""
    async with SessionLocal() as session:
        stats = await crud.get_dashboard_stats(session, period)
        return stats
