import os
import logging
import aiohttp
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin, ModelView
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import load_config
from database.models import User, Medicine, MedicineRecord, ChatHistory, MedicineSchedule
from database import crud

from wtforms.validators import NumberRange, DataRequired, Length, Regexp, AnyOf

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

templates = Jinja2Templates(directory="templates")

# Ініціалізуємо SQLAdmin
admin = Admin(app, engine, title="MedBot Dashboard", templates_dir="templates")

# ─── Функція повідомлення бота про зміни ──────────────────────────────────
async def notify_bot(action: str, medicine_id: int):
    """Надсилає POST-запит з точковими даними (JSON) до внутрішнього API бота."""
    base_url = os.getenv("WEBHOOK_BASE_URL", "http://bot:8080")
    webhook_url = f"{base_url}/api/sync"

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json={"action": action, "medicine_id": medicine_id}, timeout=2)
            logger.info(f"Бот повідомлений: {action} для препарату ID {medicine_id}")
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося зв'язатися з ботом для синхронізації: {e}")

# ─── Налаштування відображення моделей в Адмін-Панелі ──────────────────────
class UserAdmin(ModelView, model=User):
    name = "Користувач"
    name_plural = "Користувачі"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.full_name, User.username, User.language, User.timezone, User.created_at]

    column_searchable_list = ["full_name", "username"]
    column_default_sort = ("created_at", True)

    # Валідація для користувача
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
    column_details_exclude_list = [Medicine.records, Medicine.schedules]

    # Валідація для ліків
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

class MedicineScheduleAdmin(ModelView, model=MedicineSchedule):
    name = "Розклад"
    name_plural = "Розклади прийомів"
    icon = "fa-solid fa-clock"
    column_list = [MedicineSchedule.id, MedicineSchedule.medicine, MedicineSchedule.scheduled_time]

    column_searchable_list = ["scheduled_time"]

    # Валідація правильного формату часу
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

    # Валідація статусу прийому
    form_args = dict(
        status=dict(validators=[
            DataRequired(message="Статус є обов'язковим"),
            AnyOf(["taken", "skipped"], message="Статус може бути тільки 'taken' або 'skipped'")
        ]),
        remaining_days=dict(validators=[NumberRange(min=0, message="Залишок не може бути від'ємним")])
    )

class ChatHistoryAdmin(ModelView, model=ChatHistory):
    name = "Повідомлення ШІ"
    name_plural = "Історія діалогів ШІ"
    icon = "fa-solid fa-robot"
    column_list = [ChatHistory.id, ChatHistory.user, ChatHistory.role, ChatHistory.created_at]
    column_default_sort = ("created_at", True)

admin.add_view(UserAdmin)
admin.add_view(MedicineAdmin)
admin.add_view(MedicineScheduleAdmin)
admin.add_view(MedicineRecordAdmin)
admin.add_view(ChatHistoryAdmin)

# ─── Роути для Дашборду та Статистики ─────────────────────────────────────
@app.get("/admin/dashboard")
async def admin_dashboard(request: Request):
    """Рендерить головну сторінку дашборду з графіками."""
    async with SessionLocal() as session:
        stats = await crud.get_global_intake_stats(session)

    return templates.TemplateResponse(
        request=request,
        name="layout.html",
        context={"request": request, "stats": stats}
    )

@app.get("/api/admin/stats")
async def get_admin_stats(period: str = "all"):
    """API-ендпоінт, який повертає динамічний JSON для графіків залежно від обраного періоду."""
    async with SessionLocal() as session:
        stats = await crud.get_dashboard_stats(session, period)
        return stats
