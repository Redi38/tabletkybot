"""
sqladmin ModelView definitions — how each database model is displayed,
filtered, searched, validated, and edited in the admin panel.
"""

import logging

from sqladmin import ModelView, action
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from starlette.responses import RedirectResponse
from wtforms.validators import AnyOf, DataRequired, Length, NumberRange, Regexp

from admin.sync import notify_bot
from database.models import ChatHistory, Medicine, MedicineRecord, MedicineSchedule, Prescription, User

logger = logging.getLogger(__name__)


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-users"
    column_list = [User.id, User.full_name, User.username, User.language, User.timezone, User.created_at]

    column_searchable_list = ["full_name", "username"]
    column_filters = [
        StaticValuesFilter(User.language, values=[("ua", "Ukrainian"), ("en", "English"), ("ru", "Russian")])
    ]
    column_default_sort = ("created_at", True)

    form_args = dict(
        full_name=dict(
            validators=[
                DataRequired(message="Full name is required"),
                Length(min=2, max=100, message="Name must be between 2 and 100 characters"),
            ]
        ),
        timezone=dict(validators=[DataRequired(message="Timezone cannot be empty")]),
    )


class MedicineAdmin(ModelView, model=Medicine):
    name = "Medicine"
    name_plural = "Medicines"
    icon = "fa-solid fa-pills"
    column_list = [
        Medicine.id,
        Medicine.user,
        Medicine.name,
        Medicine.dosage,
        Medicine.course_duration,
        Medicine.stock_amount,
        Medicine.is_active,
    ]

    column_searchable_list = ["form", "user.full_name", "user.username"]
    column_filters = [BooleanFilter(Medicine.is_active)]
    column_details_exclude_list = [Medicine.records, Medicine.schedules]

    form_args = dict(
        name=dict(validators=[DataRequired(message="Medicine name cannot be empty"), Length(max=150)]),
        form=dict(validators=[DataRequired(message="Specify the form (e.g. tablets)")]),
        dosage=dict(validators=[DataRequired(message="Specify the dosage (e.g. 500 mg)")]),
        course_duration=dict(
            validators=[NumberRange(min=1, message="Course duration (doses) must be a number greater than 0")]
        ),
        stock_amount=dict(validators=[NumberRange(min=0, message="Stock must be >= 0 (or left empty)")]),
        low_stock_threshold=dict(validators=[NumberRange(min=0, message="Threshold must be >= 0 (or left empty)")]),
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

        referer = request.headers.get("referer", "/admin/medicine/list")
        return RedirectResponse(url=referer, status_code=303)


class MedicineScheduleAdmin(ModelView, model=MedicineSchedule):
    name = "Schedule"
    name_plural = "Intake Schedules"
    icon = "fa-solid fa-clock"
    column_list = [MedicineSchedule.id, MedicineSchedule.medicine, MedicineSchedule.scheduled_time]

    column_searchable_list = ["scheduled_time"]

    form_args = dict(
        scheduled_time=dict(
            validators=[
                DataRequired(message="Time is required"),
                Regexp(r"^(?:[01]\d|2[0-3]):[0-5]\d$", message="Time must be in HH:MM format (e.g. 08:30 or 20:00)"),
            ]
        )
    )

    async def after_model_change(self, data, model, is_created, request):
        await notify_bot("update", model.medicine_id)

    async def after_model_delete(self, model, request):
        await notify_bot("delete", model.medicine_id)


class MedicineRecordAdmin(ModelView, model=MedicineRecord):
    name = "Intake Record"
    name_plural = "Intake History"
    icon = "fa-solid fa-clipboard-check"
    column_list = [
        MedicineRecord.id,
        MedicineRecord.medicine,
        MedicineRecord.status,
        MedicineRecord.taken_at,
        MedicineRecord.remaining_days,
    ]
    column_default_sort = ("taken_at", True)
    column_filters = [StaticValuesFilter(MedicineRecord.status, values=[("taken", "Taken"), ("skipped", "Skipped")])]

    form_args = dict(
        status=dict(
            validators=[
                DataRequired(message="Status is required"),
                AnyOf(["taken", "skipped"], message="Status can only be 'taken' or 'skipped'"),
            ]
        ),
        remaining_days=dict(validators=[NumberRange(min=0, message="Remaining days cannot be negative")]),
    )


class PrescriptionAdmin(ModelView, model=Prescription):
    name = "Prescription"
    name_plural = "Prescriptions"
    icon = "fa-solid fa-file-prescription"
    column_list = [
        Prescription.id,
        Prescription.user,
        Prescription.medicine_name,
        Prescription.valid_from,
        Prescription.expires_at,
        Prescription.max_quantity,
        Prescription.purchased_quantity,
        Prescription.is_fully_purchased,
        Prescription.reminder_days_before,
        Prescription.reminder_sent,
        Prescription.is_active,
    ]
    column_searchable_list = ["medicine_name", "user.full_name"]
    column_default_sort = ("expires_at", False)
    column_filters = [BooleanFilter(Prescription.is_active), BooleanFilter(Prescription.is_fully_purchased)]

    form_args = dict(
        medicine_name=dict(validators=[DataRequired(message="Medicine name is required"), Length(max=150)]),
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
    column_details_list = [
        ChatHistory.id,
        ChatHistory.user,
        ChatHistory.role,
        ChatHistory.content,
        ChatHistory.created_at,
    ]
    column_searchable_list = ["user.full_name", "user.username"]
    column_filters = [StaticValuesFilter(ChatHistory.role, values=[("user", "user"), ("assistant", "assistant")])]
    column_default_sort = ("created_at", True)

    column_formatters = {
        ChatHistory.content: lambda m, a: (m.content[:80] + "…") if m.content and len(m.content) > 80 else m.content,
    }

    can_create = False
    can_edit = False
    can_export = True
