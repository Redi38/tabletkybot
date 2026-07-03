from datetime import datetime, date, timedelta
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Prescription
from locales.texts import get_text, btn_variants

router = Router()


# ── Допоміжні функції ───────────────────────────────────────────────────────
def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_optional_int(text: str) -> int | None:
    """Повертає None якщо юзер надіслав '-' (пропустити), або число, або -1 при помилці."""
    text = text.strip()
    if text == "-":
        return None
    try:
        val = int(text)
        return val if val >= 0 else -1
    except ValueError:
        return -1


def parse_optional_text(text: str) -> str | None:
    text = text.strip()
    return None if text == "-" else text


async def _base_ctx(call: CallbackQuery, session: AsyncSession) -> tuple[Message, str] | None:
    if not isinstance(call.message, Message) or not call.from_user:
        return None
    lang = await crud.get_user_language(session, call.from_user.id)
    return call.message, lang


async def _valid_prescription_ctx(
        call: CallbackQuery, session: AsyncSession
) -> tuple[Message, str, int, Prescription] | None:
    base = await _base_ctx(call, session)
    if not base or not call.data:
        return None
    msg, lang = base
    try:
        prescription_id = int(str(call.data).split("_")[-1])
    except ValueError:
        return None
    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        await call.answer(get_text(lang, "med_not_found"), show_alert=True)
        return None
    return msg, lang, prescription_id, prescription


# ── FSM ──────────────────────────────────────────────────────────────────
class AddPrescription(StatesGroup):
    name = State()
    issued = State()
    valid_match = State()
    valid_from = State()
    duration = State()
    quantity = State()
    reminder = State()


class BuyPrescription(StatesGroup):
    waiting_amount = State()


class EditPrescription(StatesGroup):
    issued = State()
    valid_from = State()
    quantity = State()


class RestorePrescription(StatesGroup):
    issued = State()
    valid_from = State()
    duration = State()
    quantity = State()


# ── Клавіатури ───────────────────────────────────────────────────────────
def prescription_menu_kb(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(language, "btn_add"), callback_data="presc_add", style="success"),
            InlineKeyboardButton(text=get_text(language, "btn_list"), callback_data="presc_list", style="primary"),
        ],
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_back")],
    ])


def prescription_back_only_kb(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_menu")]
    ])


def back_to_list_kb(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_list")]
    ])


def valid_match_kb(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(language, "btn_yes"), callback_data="presc_valid_yes"),
        InlineKeyboardButton(text=get_text(language, "btn_no"), callback_data="presc_valid_no"),
    ]])


def duration_kb(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(language, "btn_duration_30"), callback_data="presc_dur_30"),
        InlineKeyboardButton(text=get_text(language, "btn_duration_60"), callback_data="presc_dur_60"),
    ]])


def edit_duration_kb(prescription_id: int, language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(language, "btn_duration_30"), callback_data=f"presc_edur_30_{prescription_id}"),
        InlineKeyboardButton(text=get_text(language, "btn_duration_60"), callback_data=f"presc_edur_60_{prescription_id}"),
    ]])


def edit_field_kb(prescription_id: int, language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_edit_issued"), callback_data=f"presc_ef_issued_{prescription_id}")],
        [InlineKeyboardButton(text=get_text(language, "btn_edit_valid_from"), callback_data=f"presc_ef_valid_{prescription_id}")],
        [InlineKeyboardButton(text=get_text(language, "btn_edit_presc_duration"), callback_data=f"presc_ef_duration_{prescription_id}")],
        [InlineKeyboardButton(text=get_text(language, "btn_edit_quantity"), callback_data=f"presc_ef_quantity_{prescription_id}")],
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_list")],
    ])


def archived_prescription_row(prescription_id: int, language: str = "uk") -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=get_text(language, "btn_restore_presc"), callback_data=f"presc_restore_{prescription_id}"),
        InlineKeyboardButton(text=get_text(language, "btn_delete_presc"), callback_data=f"presc_delete_ask_{prescription_id}"),
    ]


# ── Навігація ────────────────────────────────────────────────────────────
@router.message(F.text.in_(btn_variants("btn_prescriptions")))
async def prescriptions_menu(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(
        get_text(language, "presc_menu_title"),
        reply_markup=prescription_menu_kb(language),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "presc_menu")
async def back_to_presc_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.clear()
    await msg.edit_text(get_text(lang, "presc_menu_title"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "presc_back")
async def back_to_main_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, _ = ctx
    await state.clear()
    user = await crud.get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.full_name)
    lang = str(user.language) if user.language else "uk"
    await msg.edit_text(get_text(lang, "start_text", name=str(user.full_name)), parse_mode="HTML")
    await call.answer()


# ── Додавання рецепту ────────────────────────────────────────────────────
@router.callback_query(F.data == "presc_add")
async def add_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.update_data(lang=lang)
    await msg.edit_text(get_text(lang, "add_presc_name"), parse_mode="HTML")
    await state.set_state(AddPrescription.name)


@router.message(AddPrescription.name)
async def add_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    await state.update_data(name=message.text.strip())
    await message.answer(get_text(lang, "add_presc_issued"), parse_mode="HTML")
    await state.set_state(AddPrescription.issued)


@router.message(AddPrescription.issued)
async def add_issued(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    issued = parse_date(message.text)
    if not issued:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await state.update_data(issued=issued.isoformat())
    await message.answer(
        get_text(lang, "presc_valid_match_question"),
        reply_markup=valid_match_kb(lang), parse_mode="HTML",
    )
    await state.set_state(AddPrescription.valid_match)


@router.callback_query(AddPrescription.valid_match, F.data == "presc_valid_yes")
async def valid_match_yes(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message):
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    await state.update_data(valid_from=data["issued"])
    await call.message.edit_text(
        get_text(lang, "presc_choose_duration"),
        reply_markup=duration_kb(lang), parse_mode="HTML",
    )
    await state.set_state(AddPrescription.duration)
    await call.answer()


@router.callback_query(AddPrescription.valid_match, F.data == "presc_valid_no")
async def valid_match_no(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message):
        return
    lang = (await state.get_data()).get("lang", "uk")
    await call.message.edit_text(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(AddPrescription.valid_from)
    await call.answer()


@router.message(AddPrescription.valid_from)
async def add_valid_from(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    valid_from = parse_date(message.text)
    if not valid_from:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await state.update_data(valid_from=valid_from.isoformat())
    await message.answer(
        get_text(lang, "presc_choose_duration"),
        reply_markup=duration_kb(lang), parse_mode="HTML",
    )
    await state.set_state(AddPrescription.duration)


@router.callback_query(AddPrescription.duration, F.data.in_({"presc_dur_30", "presc_dur_60"}))
async def duration_chosen(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message) or not call.data:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    days = int(str(call.data).split("_")[-1])

    valid_from = date.fromisoformat(data["valid_from"])
    expires_at = valid_from + timedelta(days=days)
    await state.update_data(expires=expires_at.isoformat())

    await call.message.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(AddPrescription.quantity)
    await call.answer()


@router.message(AddPrescription.quantity)
async def add_quantity(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return
    await state.update_data(quantity=qty)
    await message.answer(get_text(lang, "add_presc_reminder"), parse_mode="HTML")
    await state.set_state(AddPrescription.reminder)


@router.message(AddPrescription.reminder)
async def add_reminder(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    text = message.text.strip()

    reminder_days = 3
    if text != "-":
        val = parse_optional_int(text)
        if val == -1:
            await message.answer(get_text(lang, "err_invalid_number"), parse_mode="HTML")
            return
        if val is not None:
            reminder_days = val

    prescription = await crud.add_prescription(
        session=session,
        user_id=message.from_user.id,
        medicine_name=data["name"],
        issued_at=date.fromisoformat(data["issued"]),
        valid_from=date.fromisoformat(data["valid_from"]),
        expires_at=date.fromisoformat(data["expires"]),
        max_quantity=data.get("quantity"),
        reminder_days_before=reminder_days,
    )
    await state.clear()
    await message.answer(
        get_text(
            lang, "presc_added",
            name=prescription.medicine_name,
            valid_from=prescription.valid_from.strftime("%d.%m.%Y"),
            expires=prescription.expires_at.strftime("%d.%m.%Y"),
        ),
        parse_mode="HTML",
        reply_markup=prescription_menu_kb(lang),
    )


# ── Список рецептів ──────────────────────────────────────────────────────
@router.callback_query(F.data == "presc_list")
async def list_prescriptions(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    prescriptions = await crud.get_user_prescriptions(session, call.from_user.id)

    if not prescriptions:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="presc_archive_list")],
            [InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_menu")],
        ])
        await msg.edit_text(get_text(lang, "presc_empty"), reply_markup=kb, parse_mode="HTML")
        return

    text = get_text(lang, "presc_list_title")
    buttons = []
    for p in prescriptions:
        max_str = str(p.max_quantity) if p.max_quantity is not None else "∞"
        text += (
            f"💊 <b>{p.medicine_name}</b>\n"
            f"   📅 {get_text(lang, 'presc_valid_from_label')}: {p.valid_from.strftime('%d.%m.%Y')}\n"
            f"   📅 {get_text(lang, 'presc_valid_until')}: <b>{p.expires_at.strftime('%d.%m.%Y')}</b>\n"
            f"   🛒 {get_text(lang, 'presc_purchased_label')}: {p.purchased_quantity}/{max_str}\n\n"
        )
        row = []
        if not p.is_fully_purchased:
            row.append(InlineKeyboardButton(text=get_text(lang, "btn_mark_bought"), callback_data=f"presc_buy_ask_{p.id}"))
        row.append(InlineKeyboardButton(text=get_text(lang, "btn_edit_presc"), callback_data=f"presc_edit_{p.id}"))
        row.append(InlineKeyboardButton(text=get_text(lang, "btn_archive_presc"), callback_data=f"presc_archive_{p.id}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_archive_list"), callback_data="presc_archive_list")])
    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_menu")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


# ── Редагування рецепту ──────────────────────────────────────────────────
@router.callback_query(F.data.startswith("presc_edit_"))
async def edit_menu(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await msg.edit_text(
        get_text(lang, "presc_edit_title", name=prescription.medicine_name),
        reply_markup=edit_field_kb(prescription_id, lang), parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_ef_issued_"))
async def edit_issued_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_issued"), parse_mode="HTML")
    await state.set_state(EditPrescription.issued)
    await call.answer()


@router.message(EditPrescription.issued)
async def edit_issued_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    new_date = parse_date(message.text)
    if not new_date:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await crud.update_prescription_field(session, data["prescription_id"], "issued_at", new_date)
    await state.clear()
    await message.answer(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")


@router.callback_query(F.data.startswith("presc_ef_valid_"))
async def edit_valid_from_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(EditPrescription.valid_from)
    await call.answer()


@router.message(EditPrescription.valid_from)
async def edit_valid_from_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    new_date = parse_date(message.text)
    if not new_date:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await crud.update_prescription_field(session, data["prescription_id"], "valid_from", new_date)
    await state.clear()
    await message.answer(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")


@router.callback_query(F.data.startswith("presc_ef_duration_"))
async def edit_duration_start(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await msg.edit_text(
        get_text(lang, "presc_choose_duration"),
        reply_markup=edit_duration_kb(prescription_id, lang), parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_edur_"))
async def edit_duration_save(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    parts = str(call.data).split("_")
    days, prescription_id = int(parts[2]), int(parts[3])

    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        return
    new_expires = prescription.valid_from + timedelta(days=days)
    await crud.update_prescription_field(session, prescription_id, "expires_at", new_expires)

    await msg.edit_text(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("presc_ef_quantity_"))
async def edit_quantity_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(EditPrescription.quantity)
    await call.answer()


@router.message(EditPrescription.quantity)
async def edit_quantity_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return
    await crud.update_prescription_field(session, data["prescription_id"], "max_quantity", qty)
    await state.clear()
    await message.answer(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")


# ── Відмітка купівлі ─────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("presc_buy_ask_"))
async def buy_ask_amount(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await state.update_data(prescription_id=prescription_id, lang=lang)

    if prescription.max_quantity is not None:
        remaining = prescription.max_quantity - prescription.purchased_quantity
        text = get_text(lang, "ask_bought_amount_limit", remaining=remaining)
    else:
        text = get_text(lang, "ask_bought_amount")

    await msg.answer(text, parse_mode="HTML")
    await state.set_state(BuyPrescription.waiting_amount)
    await call.answer()


@router.message(BuyPrescription.waiting_amount)
async def buy_amount_entered(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    prescription_id = data["prescription_id"]

    amount = parse_optional_int(message.text)
    if amount is None or amount == -1 or amount <= 0:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return

    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        await state.clear()
        return

    # ── Валідація проти ліміту рецепту ──────────────────────────────────
    if prescription.max_quantity is not None:
        remaining = prescription.max_quantity - prescription.purchased_quantity
        if amount > remaining:
            await message.answer(
                get_text(lang, "err_exceeds_prescription_limit", remaining=remaining),
                parse_mode="HTML",
            )
            return  # лишаємось в тому ж стані, чекаємо коректне число

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=get_text(lang, "btn_confirm_bought"),
            callback_data=f"presc_buy_confirm_{prescription_id}_{amount}",
        ),
        InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_list"),
    ]])
    await message.answer(
        get_text(lang, "presc_bought_confirm", amount=amount, name=prescription.medicine_name),
        reply_markup=kb, parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data.startswith("presc_buy_confirm_"))
async def buy_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    parts = str(call.data).split("_")
    prescription_id, amount = int(parts[-2]), int(parts[-1])

    result = await crud.mark_prescription_purchased(session, prescription_id, amount)
    if not result.get("success"):
        return

    await msg.edit_text(
        get_text(lang, "presc_bought_success", purchased=result["purchased_quantity"],
                  max=result["max_quantity"] if result["max_quantity"] is not None else "∞"),
        parse_mode="HTML",
    )

    if result.get("is_fully_purchased"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=get_text(lang, "btn_presc_archive_now"), callback_data=f"presc_finish_archive_{prescription_id}"),
            InlineKeyboardButton(text=get_text(lang, "btn_presc_keep_active"), callback_data=f"presc_finish_keep_{prescription_id}"),
        ]])
        await msg.answer(
            get_text(lang, "presc_fully_purchased_ask", name=result["medicine_name"]),
            reply_markup=kb, parse_mode="HTML",
        )
    await call.answer()


@router.callback_query(F.data.startswith("presc_finish_archive_"))
async def finish_archive(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await crud.archive_prescription(session, prescription_id)
    await msg.edit_text(get_text(lang, "presc_archived"), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("presc_finish_keep_"))
async def finish_keep(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "presc_kept_active"), parse_mode="HTML")
    await call.answer()


# ── Архівування (вручну, зі списку) ──────────────────────────────────────
@router.callback_query(F.data.startswith("presc_archive_") & ~F.data.startswith("presc_archive_list"))
async def archive_prescription_manual(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    _, lang, prescription_id, _ = ctx
    await crud.archive_prescription(session, prescription_id)
    await call.answer(get_text(lang, "presc_archived"))
    await list_prescriptions(call, session)


# ── Архів рецептів ────────────────────────────────────────────────────────
@router.callback_query(F.data == "presc_archive_list")
async def archive_list(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.from_user:
        return
    msg, lang = ctx
    archived = await crud.get_user_archived_prescriptions(session, call.from_user.id)

    if not archived:
        await msg.edit_text(get_text(lang, "presc_archive_empty"), reply_markup=back_to_list_kb(lang), parse_mode="HTML")
        return

    text = get_text(lang, "presc_archive_title")
    buttons = []
    for p in archived:
        text += f"💊 <b>{p.medicine_name}</b> — {p.expires_at.strftime('%d.%m.%Y')}\n"
        buttons.append(archived_prescription_row(p.id, lang))

    buttons.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_list")])
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


# ── Видалення (з підтвердженням) ─────────────────────────────────────────
@router.callback_query(F.data.startswith("presc_delete_ask_"))
async def delete_ask(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(lang, "btn_confirm_delete"), callback_data=f"presc_delete_confirm_{prescription_id}"),
        InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_archive_list"),
    ]])
    await msg.edit_text(
        get_text(lang, "presc_delete_confirm_q", name=prescription.medicine_name),
        reply_markup=kb, parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_delete_confirm_"))
async def delete_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await crud.delete_prescription(session, prescription_id)
    await msg.edit_text(get_text(lang, "presc_deleted"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
    await call.answer()


# ── Відновлення (з новими датами/кількістю) ──────────────────────────────
@router.callback_query(F.data.startswith("presc_restore_"))
async def restore_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_issued"), parse_mode="HTML")
    await state.set_state(RestorePrescription.issued)
    await call.answer()


@router.message(RestorePrescription.issued)
async def restore_issued(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    issued = parse_date(message.text)
    if not issued:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await state.update_data(issued=issued.isoformat())
    await message.answer(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(RestorePrescription.valid_from)


@router.message(RestorePrescription.valid_from)
async def restore_valid_from(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "uk")
    valid_from = parse_date(message.text)
    if not valid_from:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await state.update_data(valid_from=valid_from.isoformat())
    await message.answer(
        get_text(lang, "presc_choose_duration"),
        reply_markup=duration_kb(lang), parse_mode="HTML",
    )
    await state.set_state(RestorePrescription.duration)


@router.callback_query(RestorePrescription.duration, F.data.in_({"presc_dur_30", "presc_dur_60"}))
async def restore_duration(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message) or not call.data:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    days = int(str(call.data).split("_")[-1])
    valid_from = date.fromisoformat(data["valid_from"])
    expires_at = valid_from + timedelta(days=days)
    await state.update_data(expires=expires_at.isoformat())
    await call.message.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(RestorePrescription.quantity)
    await call.answer()


@router.message(RestorePrescription.quantity)
async def restore_quantity(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "uk")
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return

    await crud.restore_prescription(
        session, data["prescription_id"],
        issued_at=date.fromisoformat(data["issued"]),
        valid_from=date.fromisoformat(data["valid_from"]),
        expires_at=date.fromisoformat(data["expires"]),
        max_quantity=qty,
    )
    await state.clear()
    await message.answer(get_text(lang, "presc_restored"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
