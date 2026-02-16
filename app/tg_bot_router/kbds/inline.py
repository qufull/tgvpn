import os
from typing import Optional

from aiogram.types import InlineKeyboardButton
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.models import Tariff, User
from app.utils.days_to_month import days_to_str


class MenuCallback(CallbackData, prefix="main"):
    level: int
    menu_name: str
    user_id: int = 0


def get_start_btns(
        *,
        user_id: int,
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(text='Политика ПДН', url=f"{os.getenv('URL')}/site/privacy_policy"))
    keyboard.add(InlineKeyboardButton(text='Оферта', url=f"{os.getenv('URL')}/site/terms_of_service"))
    keyboard.add(InlineKeyboardButton(text='Продолжить', callback_data=MenuCallback(level=1, menu_name='main',
                                                                                    user_id=int(user_id)).pack()))

    return keyboard.adjust(*sizes).as_markup()


def get_main_btns(
        sizes: tuple = (1, 1, 2, 2, 2),
):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(text='🛍 Купить подписку',
                                      callback_data=MenuCallback(level=2, menu_name='subscribes').pack()))
    keyboard.add(InlineKeyboardButton(text='🔎 Проверить подписку',
                                      callback_data=MenuCallback(level=4, menu_name='check').pack()))
    keyboard.add(
        InlineKeyboardButton(text='📲 Установить VPN', callback_data=MenuCallback(level=5, menu_name='help').pack()))
    keyboard.add(
        InlineKeyboardButton(text='👫 Пригласить', callback_data=MenuCallback(level=1, menu_name='invite').pack()))
    keyboard.add(
        InlineKeyboardButton(text='❓ Частые вопросы', callback_data=MenuCallback(level=7, menu_name='faq').pack()))
    keyboard.add(InlineKeyboardButton(text='☎️ Поддержка', url="https://t.me/skynetaivpn_support"))
    keyboard.add(InlineKeyboardButton(text='🛒 Другие продукты',
                                      callback_data=MenuCallback(level=6, menu_name='other_products').pack()))
    keyboard.add(InlineKeyboardButton(text='📄 Оферта | Политика',
                                      callback_data=MenuCallback(level=1, menu_name='policy').pack()))

    return keyboard.adjust(*sizes).as_markup()


def menu_btn(sizes: tuple[int] = (1,)):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(
        text=f"⬅️ Назад",
        callback_data=MenuCallback(level=1, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def choose_device_btns(sizes: tuple = (2, 2, 2, 1)):
    keyboard = get_inlineMix_btns(
        btns={
            '📱 Android': MenuCallback(level=5, menu_name='android').pack(),
            '🍏 Iphone': MenuCallback(level=5, menu_name='iphone').pack(),
            '🖥 Windows': MenuCallback(level=5, menu_name='windows').pack(),
            '💻 MacOS': MenuCallback(level=5, menu_name='macos').pack(),
            '🐧 Linux': MenuCallback(level=5, menu_name='linux').pack(),
            '📺 AndroidTV': MenuCallback(level=5, menu_name='androidtv').pack(),
            "⬅ Назад": MenuCallback(level=1, menu_name='main').pack()
        },
        sizes=sizes
    )

    return keyboard


def install_btns(url, level):
    keyboard = get_inlineMix_btns(
        btns={
            '🔗 Установить': url,
            "📡 Подключиться": MenuCallback(level=4, menu_name='main').pack(),
            "⬅ Назад": MenuCallback(level=level, menu_name='help').pack()
        }
    )
    return keyboard


def succes_pay_btns(user: User, sizes: tuple = (1,)):
    keyboard = get_inlineMix_btns(
        btns={
            "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
            "📔 Инструкция по установке": MenuCallback(level=5, menu_name='help').pack()
        },
        sizes=sizes
    )
    return keyboard


def succes_pay_btns_for_gb(user: User, sizes: tuple = (1,)):
    keyboard = get_inlineMix_btns(
        btns={
            "↗️ Перейти в v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
            "⬅ В меню": MenuCallback(level=1, menu_name='main').pack()
        },
        sizes=sizes
    )
    return keyboard


def other_products_btns(level: int, sizes: tuple[int] = (1,)):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(
        text=f"📲 Скачивание видео из Соцсетей",
        url="https://t.me/Skynet_download_bot"
    ))
    keyboard.add(InlineKeyboardButton(
        text=f"📫 Наш телеграм канал",
        url="https://t.me/Sky_Net_AI"
    ))
    keyboard.add(InlineKeyboardButton(
        text=f"⬅️ Назад",
        callback_data=MenuCallback(level=1, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def get_faq_list_btns(faq_list, sizes: tuple[int] = (1,)):
    """Клавиатура со списком вопросов FAQ"""
    keyboard = InlineKeyboardBuilder()

    for faq in faq_list:
        keyboard.add(InlineKeyboardButton(
            text=f"❓ {faq.ask}",
            callback_data=MenuCallback(level=7, menu_name=f'faq_{faq.id}').pack()
        ))

    keyboard.add(InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=MenuCallback(level=1, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def get_faq_back_btn(sizes: tuple[int] = (1,)):
    """Кнопка назад к списку FAQ"""
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(
        text="⬅️ К списку вопросов",
        callback_data=MenuCallback(level=7, menu_name='faq').pack()
    ))
    keyboard.add(InlineKeyboardButton(
        text="🏠 В меню",
        callback_data=MenuCallback(level=1, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def get_tariffs_btns(
        tariffs,
        sizes: tuple[int] = (1,),
        *,
        extra_gb_url: str | None = None,
):
    keyboard = InlineKeyboardBuilder()

    for tariff in tariffs:
        if tariff.days <= 0:
            continue
        keyboard.add(InlineKeyboardButton(
            text=f"{days_to_str(tariff.days)}, {int(tariff.price)} ₽, кол. устройств {tariff.ips}",
            callback_data=MenuCallback(level=3, menu_name=f'{tariff.id}').pack()
        ))

    # Доп. продукт: докупить трафик для серверов с обходом белых списков.
    # Кнопка показывается только если передан url (формируется в menu_menager по условиям подписки).
    if extra_gb_url:
        keyboard.add(InlineKeyboardButton(
            text="+100 ГБ (обход белых списков)",
            url=extra_gb_url,
        ))

    keyboard.add(InlineKeyboardButton(
        text=f"⬅️ Назад",
        callback_data=MenuCallback(level=1, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def get_pay_btns(tariff: Tariff, user_id: int, sizes: tuple = (1,)):
    keyboard = InlineKeyboardBuilder()

    keyboard.add(InlineKeyboardButton(
        text=f"💳 Оплатить",
        url=f"{os.getenv('URL')}/payment/payment_page?tariff_id={tariff.id}&telegram_id={user_id}"
    ))
    keyboard.add(InlineKeyboardButton(
        text=f"⬅️ Назад",
        callback_data=MenuCallback(level=2, menu_name='main').pack()
    ))

    return keyboard.adjust(*sizes).as_markup()


def get_callback_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, data in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=data))

    return keyboard.adjust(*sizes).as_markup()


def get_url_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, url in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, url=url))

    return keyboard.adjust(*sizes).as_markup()


# Создать микс из CallBack и URL кнопок
def get_inlineMix_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, value in btns.items():
        if '://' in value:
            keyboard.add(InlineKeyboardButton(text=text, url=value))
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    return keyboard.adjust(*sizes).as_markup()