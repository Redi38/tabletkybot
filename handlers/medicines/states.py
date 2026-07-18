"""FSM state groups for the medicines handlers."""

from aiogram.fsm.state import State, StatesGroup


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
