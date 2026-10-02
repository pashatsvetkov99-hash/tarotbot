from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔮 Начать расклад", callback_data="start_reading")]
        ]
    )


def sphere_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💼 Карьера", callback_data="sphere_career"),
                InlineKeyboardButton(text="💰 Деньги", callback_data="sphere_money"),
            ],
            [
                InlineKeyboardButton(text="❤️ Нынешний партнёр", callback_data="sphere_current"),
                InlineKeyboardButton(text="💔 Бывший партнёр", callback_data="sphere_ex"),
            ],
            [
                InlineKeyboardButton(text="🌟 Общий", callback_data="sphere_general"),
            ],
        ]
    )


def pay_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Купить расклады (50 Stars)", callback_data="buy_credits")]
        ]
    )


ADMIN_USERNAME = "CCTPECCOCTb"


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{ADMIN_USERNAME}")]
        ]
    )