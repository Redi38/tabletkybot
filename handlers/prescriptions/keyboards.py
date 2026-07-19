"""Inline keyboards used by the prescriptions handlers."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from locales.texts import DEFAULT_LANG, get_text


def prescription_menu_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=get_text(language, "btn_add"), callback_data="presc_add", style="success"),
                InlineKeyboardButton(text=get_text(language, "btn_list"), callback_data="presc_list", style="primary"),
            ],
            [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_back")],
        ]
    )


def prescription_back_only_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_menu")]]
    )


def back_to_list_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_list")]]
    )


def duration_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=get_text(language, "btn_duration_30"), callback_data="presc_dur_30"),
                InlineKeyboardButton(text=get_text(language, "btn_duration_60"), callback_data="presc_dur_60"),
            ]
        ]
    )


def edit_duration_kb(prescription_id: int, language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_duration_30"), callback_data=f"presc_edur_30_{prescription_id}"
                ),
                InlineKeyboardButton(
                    text=get_text(language, "btn_duration_60"), callback_data=f"presc_edur_60_{prescription_id}"
                ),
            ]
        ]
    )


def edit_field_kb(prescription_id: int, language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_edit_valid_from"),
                    callback_data=f"presc_ef_valid_{prescription_id}",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_edit_presc_duration"),
                    callback_data=f"presc_ef_duration_{prescription_id}",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_edit_quantity"),
                    callback_data=f"presc_ef_quantity_{prescription_id}",
                    style="primary",
                )
            ],
            [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="presc_list")],
        ]
    )


def archived_prescription_row(prescription_id: int, language: str = DEFAULT_LANG) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=get_text(language, "btn_restore_presc"),
            callback_data=f"presc_restore_{prescription_id}",
            style="success",
        ),
        InlineKeyboardButton(
            text=get_text(language, "btn_delete_presc"),
            callback_data=f"presc_delete_ask_{prescription_id}",
            style="danger",
        ),
    ]


def stock_ask_kb(prescription_id: int, amount: int, language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_yes"),
                    callback_data=f"presc_stock_yes_{prescription_id}_{amount}",
                    style="success",
                ),
                InlineKeyboardButton(text=get_text(language, "btn_no"), callback_data="presc_stock_no"),
            ]
        ]
    )
