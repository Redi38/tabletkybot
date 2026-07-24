"""
Tests for handlers/prescriptions/keyboards.py: the inline keyboard
builders used across the prescriptions handlers.
"""

from handlers.prescriptions.keyboards import (
    archived_prescription_row,
    back_to_list_kb,
    duration_kb,
    edit_duration_kb,
    edit_field_kb,
    prescription_back_only_kb,
    prescription_menu_kb,
    stock_ask_kb,
)


def _all_callback_data(keyboard):
    return {btn.callback_data for row in keyboard.inline_keyboard for btn in row}


class TestPrescriptionMenuKb:
    def test_has_add_list_and_back_buttons(self):
        keyboard = prescription_menu_kb("en")

        assert _all_callback_data(keyboard) == {"presc_add", "presc_list", "presc_back"}


class TestPrescriptionBackOnlyKb:
    def test_has_a_single_back_button(self):
        keyboard = prescription_back_only_kb("en")

        assert _all_callback_data(keyboard) == {"presc_menu"}


class TestBackToListKb:
    def test_has_a_single_back_to_list_button(self):
        keyboard = back_to_list_kb("en")

        assert _all_callback_data(keyboard) == {"presc_list"}


class TestDurationKb:
    def test_has_30_and_60_day_options(self):
        keyboard = duration_kb("en")

        assert _all_callback_data(keyboard) == {"presc_dur_30", "presc_dur_60"}


class TestEditDurationKb:
    def test_options_are_scoped_to_the_prescription_id(self):
        keyboard = edit_duration_kb(42, "en")

        assert _all_callback_data(keyboard) == {"presc_edur_30_42", "presc_edur_60_42"}


class TestEditFieldKb:
    def test_has_all_three_editable_fields_plus_back(self):
        keyboard = edit_field_kb(42, "en")

        assert _all_callback_data(keyboard) == {
            "presc_ef_valid_42",
            "presc_ef_duration_42",
            "presc_ef_quantity_42",
            "presc_list",
        }


class TestArchivedPrescriptionRow:
    def test_has_restore_and_delete_scoped_to_the_prescription_id(self):
        row = archived_prescription_row(42, "en")

        assert {btn.callback_data for btn in row} == {"presc_restore_42", "presc_delete_ask_42"}


class TestStockAskKb:
    def test_encodes_the_prescription_id_and_amount_in_the_yes_callback(self):
        keyboard = stock_ask_kb(prescription_id=42, amount=10, language="en")

        assert _all_callback_data(keyboard) == {"presc_stock_yes_42_10", "presc_stock_no"}
