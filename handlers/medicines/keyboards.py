"""Inline keyboards used by the medicines handlers."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from locales.texts import DEFAULT_LANG, get_text


def medicine_menu_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=get_text(language, "btn_add"), callback_data="med_add", style="success"),
                InlineKeyboardButton(text=get_text(language, "btn_list"), callback_data="med_list", style="primary"),
            ],
            [InlineKeyboardButton(text=get_text(language, "btn_stats"), callback_data="med_stats", style="primary")],
            [InlineKeyboardButton(text=get_text(language, "btn_report"), callback_data="med_reports", style="primary")],
            [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_back")],
        ]
    )


def medicine_back_only_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_menu")]]
    )


def med_reports_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_gen_excel"), callback_data="report_excel", style="primary"
                ),
                InlineKeyboardButton(
                    text=get_text(language, "btn_gen_csv"), callback_data="report_csv", style="primary"
                ),
            ],
            [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="med_menu")],
        ]
    )


def track_stock_kb(language: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=get_text(language, "btn_yes"), callback_data="track_stock_yes"),
                InlineKeyboardButton(text=get_text(language, "btn_no"), callback_data="track_stock_no"),
            ]
        ]
    )
