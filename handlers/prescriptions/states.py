"""FSM state groups for the prescriptions handlers."""

from aiogram.fsm.state import State, StatesGroup


class AddPrescription(StatesGroup):
    name = State()
    valid_from = State()
    duration = State()
    quantity = State()
    reminder = State()


class BuyPrescription(StatesGroup):
    waiting_amount = State()


class EditPrescription(StatesGroup):
    valid_from = State()
    quantity = State()


class RestorePrescription(StatesGroup):
    valid_from = State()
    duration = State()
    quantity = State()


class AddPurchaseToStock(StatesGroup):
    """
    Flow after marking a prescription purchase: we ask for the pack size and
    which (active) medicine to add the purchased quantity to in stock.
    """

    waiting_pack_size = State()
    waiting_medicine_choice = State()
