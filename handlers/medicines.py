import pytz
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Medicine
from locales.texts import get_text, TEXTS, btn_variants
from services.scheduler import add_reminders_for_medicine, remove_reminders, cancel_repeat_reminder

router = Router()


# ── Допоміжні функції ───────────────────────────────────────────────────────
def is_valid_time(time_str: str) -> bool:
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False


def parse_times(times_str: str) -> list[str] | None:
    raw = [t.strip() for t in times_str.replace(";", ",").split(",")]
    valid = [t for t in raw if is_valid_time(t)]
    return valid if valid and len(valid) == len(raw) else None


def parse_int(val_str: str) -> int | None:
    try:
        val = int(val_str)
        return val if val >= 0 else None
    except ValueError:
        return None


async def _base_ctx(call: CallbackQuery, session: AsyncSession) -> tuple[Message, str] | None:
    if not isinstance(call.message, Message) or not call.from_user:
        return None
    lang = await crud.get_user_language(session, call.from_user.id)
    return call.message, lang


async def _valid_medicine_ctx(
        call: CallbackQuery, session: AsyncSession
) -> tuple[Message, str, int, Medicine] | None:
    base = await _base_ctx(call, session)
    if not base or not call.data:
        return None
    msg, lang = base
    try:
        medicine_id = int(str(call.data).split("_")[-1])
    except ValueError:
        return None
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if not medicine:
        await call.answer(get_text(lang, "med_not_found"), show_alert=True)
        return None
    return msg, lang, medicine_id, medicine


# ── FSM ────────────────────────────────────────────────────────────────────
class AddMedicine(StatesGroup):
    name = State()
    form = State()
    dosage = State()
    time = State()
    duration = State()
    timezone = State()
    track_stock = State()
    stock_amount = State()
    stock_threshold = State()
    lang = State()


class EditMedicine(StatesGroup):
    waiting_value = State()


class ExtendMedicine(StatesGroup):
    waiting_for_days = State()


class RestockMedicine(StatesGroup):
    waiting_for_amount = State()


# ── Клавіатури ─────────────────────────────────────────────────────────────
def medicine_menu_kb(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(language, "btn_add"), callback_data="med_add", style="success"),
            InlineKeyboardButton(text=get_text(language, "btn_list"), callback_data="med_list", style="primary"),
        ],
        [InlineKeyboardButton(text=get_text(language, "btn_stats"), callback_data="med_stats", style="primary")],
	[InlineKeyboardButton(text=get_text(language, "btn_report"), callback_data="med_reports", style="primary")],
	[InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_back")],
    ])


def medicine_back_only_kb(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_menu")]
    ])


def med_reports_kb(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(language, "btn_gen_excel"), callback_data="report_excel", style="primary"),
            InlineKeyboardButton(text=get_text(language, "btn_gen_csv"), callback_data="report_csv", style="primary"),
        ],
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_menu")],
    ])


def track_stock_kb(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(language, "btn_yes"), callback_data="track_stock_yes"),
        InlineKeyboardButton(text=get_text(language, "btn_no"), callback_data="track_stock_no"),
    ]])


# ── Навігація ─────────────────────────────────────────────────────────────
@router.message(F.text.in_(btn_variants("btn_medicines")))
async def medicines_menu(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(get_text(language, "med_menu_title"), reply_markup=medicine_menu_kb(language), parse_mode="HTML")


@router.callback_query(F.data == "med_menu")
async def back_to_med_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.clear()
    await msg.edit_text(get_text(lang, "med_menu_title"), reply_markup=medicine_menu_kb(lang), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "med_back")
async def back_to_main_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, _ = ctx
    await state.clear()
    user = await crud.get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.full_name)
    lang = str(user.language) if user.language else "ua"
    await msg.edit_text(get_text(lang, "start_text", name=str(user.full_name)), parse_mode="HTML")
    await call.answer()


# ── Звіти (перенесено сюди з головної клавіатури) ──────────────────────────
@router.callback_query(F.data == "med_reports")
async def medicine_reports_menu(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "report_menu_title"), reply_markup=med_reports_kb(lang), parse_mode="HTML")
    await call.answer()


# ── Додавання ліків ──────────────────────────────────────────────────────
@router.callback_query(F.data == "med_add")
async def add_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.update_data(lang=lang)
    await msg.edit_text(get_text(lang, "add_name"), parse_mode="HTML")
    await state.set_state(AddMedicine.name)


@router.message(AddMedicine.name)
async def add_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(name=message.text.strip())
    await message.answer(get_text(lang, "add_form"), parse_mode="HTML")
    await state.set_state(AddMedicine.form)


@router.message(AddMedicine.form)
async def add_form(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(form=message.text.strip())
    await message.answer(get_text(lang, "add_dosage"), parse_mode="HTML")
    await state.set_state(AddMedicine.dosage)


@router.message(AddMedicine.dosage)
async def add_dosage(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(dosage=message.text.strip())
    await message.answer(get_text(lang, "add_time"), parse_mode="HTML")
    await state.set_state(AddMedicine.time)


@router.message(AddMedicine.time)
async def add_time(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    parsed_times = parse_times(message.text)
    if not parsed_times:
        await message.answer(get_text(lang, "err_time"), parse_mode="HTML")
        return
    await state.update_data(time=parsed_times)
    await message.answer(get_text(lang, "add_duration"), parse_mode="HTML")
    await state.set_state(AddMedicine.duration)


@router.message(AddMedicine.duration)
async def add_duration(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    lang = (await state.get_data()).get("lang", "ua")
    days = parse_int(message.text.strip())
    if days is None or days == 0:
        await message.answer(get_text(lang, "err_duration"))
        return
    await state.update_data(duration=days)
    user = await crud.get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    if user.timezone:
        await state.update_data(timezone=user.timezone)
        await message.answer(get_text(lang, "ask_track_stock"), reply_markup=track_stock_kb(lang), parse_mode="HTML")
        await state.set_state(AddMedicine.track_stock)
    else:
        await message.answer(get_text(lang, "add_timezone"), parse_mode="HTML")
        await state.set_state(AddMedicine.timezone)


@router.message(AddMedicine.timezone)
async def add_timezone(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    lang = (await state.get_data()).get("lang", "ua")
    tz_text = message.text.strip()
    try:
        pytz.timezone(tz_text)
    except pytz.UnknownTimeZoneError:
        await message.answer(get_text(lang, "err_timezone"), parse_mode="HTML")
        return
    await state.update_data(timezone=tz_text)
    await crud.update_user_timezone(session, message.from_user.id, tz_text)
    await message.answer(get_text(lang, "ask_track_stock"), reply_markup=track_stock_kb(lang), parse_mode="HTML")
    await state.set_state(AddMedicine.track_stock)


@router.callback_query(F.data.startswith("track_stock_"))
async def add_track_stock(call: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    if str(call.data).split("_")[2] == "yes":
        await msg.edit_text(get_text(lang, "add_stock_amount"), parse_mode="HTML")
        await state.set_state(AddMedicine.stock_amount)
    else:
        await _save_new_medicine(msg, state, session, bot, lang, None, None)


@router.message(AddMedicine.stock_amount)
async def add_stock_amount(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    amount = parse_int(message.text.strip())
    if amount is None:
        await message.answer(get_text(lang, "err_stock"))
        return
    await state.update_data(stock_amount=amount)
    await message.answer(get_text(lang, "ask_stock_threshold"), parse_mode="HTML")
    await state.set_state(AddMedicine.stock_threshold)


@router.message(AddMedicine.stock_threshold)
async def add_stock_threshold(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    threshold = parse_int(message.text.strip())
    if threshold is None:
        await message.answer(get_text(lang, "err_invalid_number"))
        return
    await _save_new_medicine(message, state, session, bot, lang, data["stock_amount"], threshold)


async def _save_new_medicine(
        message: Message, state: FSMContext, session: AsyncSession, bot: Bot,
        lang: str, stock_amount: int | None, threshold: int | None,
) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    total_doses = data["duration"] * len(data["time"])

    medicine = await crud.add_medicine(
        session=session, user_id=user_id,
        name=data["name"], form=data["form"], dosage=data["dosage"],
        schedules_list=data["time"], course_duration=total_doses,
        stock_amount=stock_amount, low_stock_threshold=threshold,
    )
    if not medicine:
        return

    add_reminders_for_medicine(bot, medicine, data["timezone"], user_id, lang)
    await state.clear()
    times_str = ", ".join(data["time"])
    await message.answer(
        get_text(lang, "med_added", name=str(medicine.name), time=times_str, duration=str(data["duration"])),
        parse_mode="HTML", reply_markup=medicine_menu_kb(lang),
    )


# ── Список та статистика ──────────────────────────────────────────────────
@router.callback_query(F.data == "med_list")
async def list_medicines(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    medicines = [m for m in await crud.get_user_medicines(session, call.from_user.id) if m.is_active]

    if not medicines:
        buttons = [
            [InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="med_archive_list", style="primary")],
            [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_menu")],
        ]
        await msg.edit_text(get_text(lang, "med_empty"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    text = get_text(lang, "med_list_title")
    buttons = []
    for med in medicines:
        schedules_str = ", ".join(s.scheduled_time for s in med.schedules)
        stock_info = get_text(lang, "med_stock_info", amount=med.stock_amount) if med.stock_amount is not None else ""
        remaining_info = get_text(lang, "med_remaining_doses", duration=med.course_duration)
        text += (
            f"💊 <b>{med.name}</b>\n   {get_text(lang, 'med_form')}: {med.form} | "
            f"{get_text(lang, 'med_dose')}: {med.dosage}\n   ⏰ {schedules_str} | "
            f"{remaining_info}{stock_info}\n\n"
        )
        buttons.append([
            InlineKeyboardButton(text=f"✏️ {med.name}", callback_data=f"edit_med_{med.id}", style="primary"),
            InlineKeyboardButton(text=get_text(lang, "btn_archive"), callback_data=f"med_archive_ask_{med.id}", style="danger"),
        ])

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="med_archive_list")])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_menu")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data == "med_stats")
async def medicine_stats(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    stats = await crud.get_medicine_intake_stats(session, call.from_user.id)
    if stats["total"] == 0:
        await msg.edit_text(get_text(lang, "med_stats_empty"), reply_markup=medicine_back_only_kb(lang))
        return
    taken_p = stats["taken"] / stats["total"] * 100
    skipped_p = stats["skipped"] / stats["total"] * 100
    text = (
        f"{get_text(lang, 'med_stats_title')}\n\n"
        f"{get_text(lang, 'med_stats_total')}: <b>{stats['total']}</b>\n"
        f"✅ {get_text(lang, 'med_stats_taken')}: <b>{taken_p:.1f}%</b> ({stats['taken']})\n"
        f"⏭️ {get_text(lang, 'med_stats_skipped')}: <b>{skipped_p:.1f}%</b> ({stats['skipped']})"
    )
    await msg.edit_text(text, reply_markup=medicine_back_only_kb(lang), parse_mode="HTML")


# ── Архів та Видалення ─────────────────────────────────────────────────────
@router.callback_query(F.data == "med_archive_list")
async def list_archived_medicines(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    archived = await crud.get_archived_medicines(session, call.from_user.id)
    if not archived:
        await msg.edit_text(get_text(lang, "med_archived_empty"), reply_markup=medicine_back_only_kb(lang))
        return

    text = f"🗂 <b>{get_text(lang, 'med_archived_title')}</b>\n\n"
    buttons = []
    for med in archived:
        time_str = ", ".join(s.scheduled_time for s in med.schedules) if med.schedules else "-"
        stock_info = get_text(lang, "med_stock_info", amount=med.stock_amount) if med.stock_amount is not None else ""
        text += (
            f"💊 <b>{med.name}</b>\n   {get_text(lang, 'med_form')}: {med.form} | "
            f"{get_text(lang, 'med_dose')}: {med.dosage}\n   ⏰ {time_str}{stock_info}\n\n"
        )
        buttons.append([
            InlineKeyboardButton(text=f"🔄 {med.name}", callback_data=f"med_restore_ask_{med.id}", style="primary"),
            InlineKeyboardButton(text=get_text(lang, "btn_archive_del"), callback_data=f"med_del_{med.id}", style="danger"),
        ])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("med_archive_ask_"))
async def archive_medicine_ask(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_archive"), callback_data=f"med_archive_confirm_{medicine_id}")],
        [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")],
    ])
    await msg.edit_text(get_text(lang, "med_archive_confirm", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("med_archive_confirm_"))
async def archive_medicine_exec(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    _, lang, medicine_id, _ = ctx
    await crud.update_medicine_field(session, medicine_id, "is_active", False)
    remove_reminders(medicine_id)
    await call.answer(get_text(lang, "edit_success"))
    await list_medicines(call, session)


@router.callback_query(F.data.startswith("med_del_"))
async def delete_medicine(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_confirm_del"), callback_data=f"med_confirm_del_{medicine_id}", style="danger")],
        [InlineKeyboardButton(text=get_text(lang, "btn_cancel_del"), callback_data="med_list", style="primary")],
    ])
    await msg.edit_text(get_text(lang, "med_del_prompt", name=str(medicine.name)), reply_markup=buttons, parse_mode="HTML")


@router.callback_query(F.data.startswith("med_confirm_del_"))
async def confirm_delete_medicine(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    _, lang, medicine_id, medicine = ctx
    await crud.delete_medicine(session, medicine_id)
    remove_reminders(medicine_id)
    await call.answer(get_text(lang, "med_deleted", name=str(medicine.name)))
    await list_medicines(call, session)


# ── Продовження курсу ─────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("med_restore_ask_") | F.data.startswith("med_extend_ask_"))
async def extend_course_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, _ = ctx
    await state.update_data(medicine_id=medicine_id, lang=lang)
    await msg.edit_text(get_text(lang, "ask_extend_days"), parse_mode="HTML")
    await state.set_state(ExtendMedicine.waiting_for_days)


@router.message(ExtendMedicine.waiting_for_days)
async def extend_course_save(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ua")
    days = parse_int(message.text.strip()) if message.text else None
    if days is None:
        await message.answer(get_text(lang, "err_duration"))
        return

    medicine_id = data["medicine_id"]
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if not medicine or not message.from_user:
        return

    schedules_count = len(medicine.schedules) if medicine.schedules else 1
    await crud.update_medicine_field(session, medicine_id, "course_duration", days * schedules_count)
    await crud.update_medicine_field(session, medicine_id, "is_active", True)

    tz = await crud.get_user_timezone(session, message.from_user.id)
    add_reminders_for_medicine(bot, medicine, str(tz), message.from_user.id, lang)
    await state.clear()
    await message.answer(get_text(lang, "med_restored", name=str(medicine.name), days=days), parse_mode="HTML")


# ── Редагування ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("edit_med_"))
async def edit_medicine_menu(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx

    buttons = [
        [InlineKeyboardButton(text=get_text(lang, "btn_edit_name"), callback_data=f"edit_field_name_{medicine_id}")],
        [InlineKeyboardButton(text=get_text(lang, "btn_edit_dosage"), callback_data=f"edit_field_dosage_{medicine_id}")],
        [InlineKeyboardButton(text=get_text(lang, "btn_edit_time"), callback_data=f"edit_field_schedules_{medicine_id}")],
        [InlineKeyboardButton(text=get_text(lang, "btn_edit_duration"), callback_data=f"edit_field_course_duration_{medicine_id}")],
    ]
    if medicine.stock_amount is not None:
        buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_edit_stock"), callback_data=f"edit_field_stock_amount_{medicine_id}")])
        buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_edit_threshold"), callback_data=f"edit_field_low_stock_threshold_{medicine_id}")])
    else:
        buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_enable_stock"), callback_data=f"edit_field_stock_amount_{medicine_id}")])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="med_list")])
    await msg.edit_text(get_text(lang, "edit_what"), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("edit_field_"))
async def edit_field_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, _ = ctx
    field = "_".join(str(call.data).split("_")[2:-1])
    await state.update_data(medicine_id=medicine_id, field=field, lang=lang)
    await msg.edit_text(get_text(lang, "edit_enter_new", field=field), parse_mode="HTML")
    await state.set_state(EditMedicine.waiting_value)


@router.message(EditMedicine.waiting_value)
async def edit_field_save(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    medicine_id, field, lang = data["medicine_id"], data["field"], data.get("lang", "ua")
    new_value = message.text.strip()

    if field == "schedules":
        parsed_times = parse_times(new_value)
        if not parsed_times:
            await message.answer(get_text(lang, "err_time"), parse_mode="HTML")
            return
        medicine = await crud.get_medicine_by_id(session, medicine_id)
        if medicine:
            old_count = len(medicine.schedules) if medicine.schedules else 1
            new_count = len(parsed_times)
            new_total = int((medicine.course_duration / old_count) * new_count)
            await crud.update_medicine_field(session, medicine_id, "course_duration", new_total)
        await crud.update_medicine_schedules(session, medicine_id, parsed_times)
    else:
        final_value: str | int = new_value
        if field in ("course_duration", "stock_amount", "low_stock_threshold"):
            val = parse_int(new_value)
            if val is None:
                err_key = {"course_duration": "err_duration", "stock_amount": "err_stock"}.get(field, "err_threshold")
                await message.answer(get_text(lang, err_key))
                return
            if field == "course_duration":
                medicine = await crud.get_medicine_by_id(session, medicine_id)
                count = len(medicine.schedules) if medicine and medicine.schedules else 1
                final_value = val * count
            else:
                final_value = val
        await crud.update_medicine_field(session, medicine_id, field, final_value)

    await session.commit()
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    if medicine:
        tz = await crud.get_user_timezone(session, message.from_user.id)
        add_reminders_for_medicine(bot, medicine, str(tz), message.from_user.id, lang)

    await state.clear()
    await message.answer(get_text(lang, "edit_success"), reply_markup=medicine_menu_kb(lang))


# ── Прийом / Пропуск ──────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("take_") | F.data.startswith("skip_"))
async def process_medicine_status(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await call.answer()

    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, medicine = ctx
    action = str(call.data).split("_")[0]

    cancel_repeat_reminder(call.from_user.id, medicine_id)

    if action == "take" and medicine.stock_amount is not None and medicine.stock_amount <= 0:
        await state.update_data(medicine_id=medicine_id, lang=lang)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(lang, "btn_restock_yes"), callback_data=f"restock_yes_{medicine_id}")],
            [InlineKeyboardButton(text=get_text(lang, "btn_restock_no"), callback_data=f"restock_no_{medicine_id}")],
        ])
        await msg.edit_text(get_text(lang, "alert_empty_but_time", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML")
        return

    db_status = "taken" if action == "take" else "skipped"
    result = await crud.record_medicine_taken(session, medicine_id, status=db_status)
    remaining = result.get("remaining_days", 0)
    stock = result.get("stock_amount")
    threshold = result.get("low_stock_threshold")

    if remaining <= 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(lang, "btn_course_continue"), callback_data=f"med_extend_ask_{medicine_id}", style="success")],
            [InlineKeyboardButton(text=get_text(lang, "btn_course_finish"), callback_data=f"med_archive_confirm_{medicine_id}", style="primary")],
        ])
        await msg.edit_text(get_text(lang, "course_finished_ask", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML")
    else:
        success_key = "med_taken" if action == "take" else "med_skipped"
        await msg.edit_text(get_text(lang, success_key, days=remaining), parse_mode="HTML")

        if action == "take" and stock is not None:
            if stock == 0:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=get_text(lang, "btn_add_stock"), callback_data=f"restock_ask_{medicine_id}")
                ]])
                await msg.answer(get_text(lang, "alert_empty_stock", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML")
            elif threshold is not None and stock <= threshold:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=get_text(lang, "btn_add_stock"), callback_data=f"restock_ask_{medicine_id}")],
                    [InlineKeyboardButton(text=get_text(lang, "btn_remind_later"), callback_data="delete_message")],
                ])
                await msg.answer(get_text(lang, "alert_low_stock", name=str(medicine.name), amount=stock), reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "delete_message")
async def delete_alert_message(call: CallbackQuery) -> None:
    if isinstance(call.message, Message):
        await call.message.delete()


# ── Поповнення Аптечки ────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("restock_yes_") | F.data.startswith("restock_ask_"))
async def restock_ask_amount(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, _ = ctx
    needs_take = "restock_yes" in str(call.data)
    await state.update_data(medicine_id=medicine_id, lang=lang, needs_take=needs_take)
    await msg.edit_text(get_text(lang, "ask_restock_amount"), parse_mode="HTML")
    await state.set_state(RestockMedicine.waiting_for_amount)


@router.callback_query(F.data.startswith("restock_no_"))
async def restock_skip(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, _ = ctx
    result = await crud.record_medicine_taken(session, medicine_id, status="skipped")
    await msg.edit_text(get_text(lang, "med_skipped", days=result.get("remaining_days", 0)), parse_mode="HTML")


@router.message(RestockMedicine.waiting_for_amount)
async def restock_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    medicine_id = data["medicine_id"]
    lang = data.get("lang", "ua")
    needs_take = data.get("needs_take", False)

    amount = parse_int(message.text.strip())
    if amount is None:
        await message.answer(get_text(lang, "err_stock"))
        return

    new_stock = await crud.add_stock(session, medicine_id, amount)

    if needs_take:
        result = await crud.record_medicine_taken(session, medicine_id, status="taken")
        new_stock = result.get("stock_amount", new_stock)

    await state.clear()
    await message.answer(get_text(lang, "restock_success", amount=new_stock), parse_mode="HTML")
