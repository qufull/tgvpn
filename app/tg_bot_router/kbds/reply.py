from aiogram import types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def admin_menu_kbrd():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text='📫 Рассылка'),
                KeyboardButton(text='📦 Заказы'),
            ],
            [
                KeyboardButton(text='💰 Тарифы'),
                KeyboardButton(text='🌐 Сервера'),
            ],
            [
                KeyboardButton(text='⚙️ Редактировать FAQ'),
                KeyboardButton(text='➕ Добавить дни'),
            ]
        ],
        resize_keyboard=True
    )
    return keyboard


def choose_kbrd():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text='Да'),
                KeyboardButton(text='Нет'),
            ],
        ],
        resize_keyboard=True
    )
    return keyboard


CANCEL_TEXT = "❌ Отмена"

def cancel_kbrd():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=CANCEL_TEXT)]],
        resize_keyboard=True
    )