import hashlib
from html import escape
from typing import Optional
from datetime import datetime
from aiogram.exceptions import TelegramAPIError,TelegramForbiddenError
from aiogram.enums import ChatAction
import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import types

from app.database.queries import orm_get_faq, orm_get_faq_by_id, orm_change_user_tariff, orm_get_servers, \
    orm_get_tariff, orm_get_tariffs, orm_get_user_by_tgid, orm_get_user_servers, orm_get_referral_count
from app.utils.days_to_month import days_to_str
from app.tg_bot_router.kbds.inline import (
    MenuCallback,
    choose_device_btns,
    get_inlineMix_btns,
    get_main_btns,
    get_pay_btns,
    get_start_btns,
    get_tariffs_btns,
    get_faq_list_btns,
    get_faq_back_btn,
    install_btns,
    menu_btn,
    other_products_btns
)

async def proxy_menu(level: int, menu_name: str) -> tuple:
    caption = (
        "<b>🛡 Выберите прокси для Telegram:</b>\n\n"
        "Нажмите на любую из кнопок ниже, чтобы применить настройки прокси "
        "и обеспечить стабильное соединение прямо в приложении Telegram."
    )

    # Замените эти ссылки на ваши реальные прокси
    proxy_1 = "tg://socks?server=skynetai.ru&port=1080&user=tg&pass=nYmR5laSgfngWJVn"
    proxy_2 = "tg://proxy?server=skynetai.ru&port=443&secret=7pSZCxViSqmZqUDxHBGMd215YW5kZXgucnU"
    proxy_3 = "https://t.me/proxy?server=n2.skynetai.ru&port=444&secret=7pIfrhJtjEQD8kvHfPECCMp3d3cud2lraXBlZGlhLm9yZw"

    keyboard = get_inlineMix_btns(
        btns={
            "🛡 1 Моб. связь": proxy_1,
            "🛡 2 Моб. связь + комп": proxy_2,
            "🛡 3 Моб. связь + комп": proxy_3,
            "⬅️ Назад": MenuCallback(level=4, menu_name='check').pack(), # Возврат в меню подписки
        },
        sizes=(1, 1, 1, 1) # Каждая кнопка на новой строке
    )

    return caption, keyboard

async def start_message(session, level, menu_name, user_id):
    baner = types.FSInputFile("media/img/main_logo_bg.jpg")
    caption = '<b>SkynetVPN — сервис защищённых подключений.</b>\n\n<b>Используя бота, вы подтверждаете, что ознакомились и принимаете условия <a href="https://https://bot-skynetai.ru/terms-of-service.html">Публичной оферты</a> и <a href="https://https://bot-skynetai.ru/terms-of-service.html">Политики обработки персональных данных</a>.</b>\n\nСервис не предназначен для обхода ограничений доступа к информации. Получение/распространение запрещённой информации в РФ запрещено.\n\nМы предоставляем техническую услугу по организации шифрованного соединения и не формируем/не контролируем содержимое трафика.\n\nПользователь обязуется соблюдать законодательство РФ (в т.ч. 149-ФЗ, 114-ФЗ, 436-ФЗ, 187-ФЗ).'
    kbrd = get_start_btns(user_id=user_id)

    media = types.InputMediaPhoto(media=baner, caption=caption)

    return media, kbrd


async def main_menu(session: AsyncSession, level, menu_name, user_id: Optional[int] = None, include_image: bool = False) -> tuple:
    caption = ""
    kbd = get_main_btns()
    if menu_name == 'main':
        caption="<b>SKYNET VPN — сервис шифрованных подключений.</b>\n\n"\
                "Мы не анализируем содержимое трафика и не ведём его содержательные логи. \n\n"\
                "Устанавливается на:  <b>Windows / macOS / iOS / Android / Linux / Android TV. </b>\n\n"\
                "Безлимитный трафик (кроме трафика для обхода блокировок) \n"\
                "Фактическая скорость соединения зависит от вашей сети и устройства.\n\n"\
                "<b>Оплатите тариф и начинайте пользоваться.</b>\n\n"
    elif menu_name == 'invite':
        referrals_count = await orm_get_referral_count(session, user_id)
        caption=f"<b>Приглашайте друзей и получайте бонусы!</b> \n\nЗа каждую покупку приглашенных пользователей Вы получите к вашей подписке:\n\nЗа 1 мес. – 7 дней\nЗа 6 мес. – 15 дней\nЗа 12 мес. – 30 дней\n\nПриглашено пользователей: <b>{referrals_count}</b>\n\nВаша реферальная ссылка:\nhttps://t.me/skynetaivpn_bot?start={user_id}"
    elif menu_name == 'policy':
        caption=f"О нас: \nМы предоставляем техническую услугу по организации шифрованного соединения (VPN). Не являемся СМИ, не размещаем и не контролируем контент. Сервис не предназначен для обхода ограничений и доступа к запрещённой информации. \n\nПолный текст —  <a href=\"{os.getenv('URL')}/site/privacy_policy\">Политика конфидециальности</a>.\n\nХарактеристики, сроки и стоимость — в интерфейсе бота и в <a href=\"{os.getenv('URL')}/site/terms_of_service\">публичной оферте</a>."
    elif menu_name == 'faq':
        caption="<b>Часто задаваемые вопросы:</b>"


    if include_image:
        baner = types.FSInputFile("media/img/main_logo_bg.jpg")
        media = types.InputMediaPhoto(media=baner, caption=caption)
        return media, kbd
    else:
        return caption, kbd

DAY10_ID = int(os.getenv("TARIFF_DAY10_ID", "0") or 0)

async def buy_subscribe(
    session: AsyncSession,
    level: int,
    menu_name: str,
    user_id: int | None = None,
) -> tuple:
    tariffs = await orm_get_tariffs(session)
    servers = await orm_get_servers(session)

    caption = "<b>⚡️ Вы покупаете премиум подписку на SKYNET VPN</b>\n\n● Подключайте любые устройства: Smart TV, мобильное устройство, компьютер, планшет.\n● До 8 устройств одновременно\n● Без ограничений по скорости\n● 30 ГБ/мес – только для обхода блокировок (белые списки). Остальной трафик не лимитируется.\n\n<b>Список поддерживаемых устройств:</b>\n\nAndroid (Android 7.0 или новее.) | Windows (Windows 8.1 или новее.) | iOS, iPadOS (iOS 16.0 или новее.) | macOS процессоры M  (macOS 13.0 или новее) | macOS  c процессором Intel (macOS 11.0 или новее.) | Android TV ( Android 7.0 или новее.) | Linux\n\n<b>🌍 Доступные страны:</b>\n👑 - без рекламы на YouTube\n🎧 - YouTube можно сворачивать\n 🏳️ - Лимит по обходу белых списков 30 ГБ каждый месяц\n 🎭 - обходят блокировки VLESS\n ⚡️ - быстрая скорость\n"

    for server in servers:
        if server.id == servers[-1].id:
            caption += f"\n└ {server.name}"
            continue
        caption += f"\n├ {server.name}"

    if user_id and DAY10_ID:
        user = await orm_get_user_by_tgid(session, user_id)

        is_new_user = False
        if user:
            user_servers = await orm_get_user_servers(session, user.id)

            is_new_user = (
                    user.tariff_id == 0
                    and user.sub_end is None
                    and not user_servers
            )

        if not is_new_user:
            tariffs = [t for t in tariffs if t.id != DAY10_ID]

    extra_gb_url = None
    try:
        extra_tariff_id = int(os.getenv("EXTRA_GB_TARIFF_ID", "0"))
    except Exception:
        extra_tariff_id = 0

    if user_id and extra_tariff_id > 0 and any(s.need_gb for s in servers):
        user = await orm_get_user_by_tgid(session, user_id)
        # активная подписка: sub_end существует и ещё не истекла
        if user and user.sub_end and user.sub_end > datetime.now():
            extra_gb_url = f"{os.getenv('URL')}/payment/payment_page?tariff_id={extra_tariff_id}&telegram_id={user_id}"

    kbrd = get_tariffs_btns(tariffs, extra_gb_url=extra_gb_url)

    return caption, kbrd


async def check_subscribe(
        session: AsyncSession,
        level: int,
        menu_name: str,
        user_id: int
) -> tuple:
    user = await orm_get_user_by_tgid(session, user_id)
    if not user:
        return "❌ Пользователь не найден в базе. Отправьте /start для начала работы.", menu_btn()

    # Отмена подписки
    if menu_name == "cancel":
        await orm_change_user_tariff(
            session,
            user.id,
            tariff_id=0,
            sub_end=user.sub_end,
            ips=user.ips,
        )
        # важно: перечитать пользователя после изменения, иначе покажешь старые данные
        user = await orm_get_user_by_tgid(session, user_id)

    user_servers = await orm_get_user_servers(session, user.id)

    now = datetime.now()
    has_end = bool(user.sub_end)
    is_expired = bool(has_end and user.sub_end <= now)  # закончилась/истекла
    has_tariff = bool(user.tariff_id and user.tariff_id > 0)  # есть тариф (не отменён)
    has_servers = bool(user_servers)  # есть привязанные сервера

    # тариф может быть удалён из БД — страхуемся
    tariff = await orm_get_tariff(session, user.tariff_id) if has_tariff else None

    # --- ГЕНЕРАЦИЯ DEEP-LINK ДЛЯ ВСЕХ АКТИВНЫХ ПОДПИСОК ---
    # Генерируем ссылку один раз, если подписка действует прямо сейчас
    deep_link = None
    if has_end and user.sub_end > now:
        days_left = max(1, (user.sub_end - now).days)
        shared_secret = os.getenv("SHARED_BOT_SECRET",
                                  "rAdi8YYvr54ghTjv97TTZxQ1BSwpELkjfgj9Ft07TDC0BJIY4l73L8n0oanRIHzMX7p5aP4NHVlzkQOoabOmduek3c2NMQT10zpAPgINSAI9zf5UaNHrHSZ5Iuxqgqhr")

        raw_string = f"{user.telegram_id}:{days_left}:{shared_secret}"
        signature = hashlib.sha256(raw_string.encode()).hexdigest()[:16]

        media_bot_username = "Skynet_download_bot"
        deep_link = f"https://t.me/{media_bot_username}?start=vpn_{user.telegram_id}_{days_left}_{signature}"
    # --------------------------------------------------------

    # 1) Подписка активна: есть тариф и дата в будущем
    if has_tariff and has_end and user.sub_end > now:
        price = tariff.price if tariff else "—"
        days = days_to_str(tariff.days) if tariff else "—"
        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
        caption = (
            "<b>⚙️ Ваша подписка SKYNET VPN:</b>\n"
            f"├ Цена: {price}\n"
            f"├ Срок: {days}\n"
            f"├ Количество устройств: {user.ips}\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
        )

        # Предупреждение для пробного 1-дневного тарифа
        if DAY10_ID and user.tariff_id == DAY10_ID:
            caption += (
                "⚠️ <b>Внимание:</b> после окончания пробного периода "
                "автоматически подключится месячный тариф за 299 ₽.\n"
                "Нажмите «Отменить подписку», если не хотите автопродления.\n\n"
            )

        caption += (
            "<b>Ваша ссылка на ключ. 🔑</b>\n\n"
            "Нажмите 1 раз чтобы скопировать:\n\n"
            f"<pre><code>{escape(url)}</code></pre>"
        )
        keyboard = get_inlineMix_btns(
            btns={
                "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
                "🎥 Premium в Медиа-боте": deep_link,  # Ссылка берется из переменной
                "🛡 Telegram Прокси": MenuCallback(level=9, menu_name='proxies').pack(),
                "🛍 Продлить подписку": MenuCallback(level=2, menu_name='subscribes').pack(),
                "❌ Отменить подписку": MenuCallback(level=4, menu_name='cancel').pack(),
                "🔄 Обновить ключ": MenuCallback(level=4, menu_name='check').pack(),
                "⬅️ Назад": MenuCallback(level=1, menu_name='main').pack(),
            },
            sizes=(1,)
        )
        return caption, keyboard

    # 2) Подписка закончилась (дата <= now): показываем "закончилась" + продлить + обновить + подключиться
    if is_expired and has_servers:
        caption = (
            "<b>⛔️ Ваша подписка SKYNET VPN закончилась.</b>\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
            "Нажмите «Продлить подписку», оплатите тариф и после оплаты обновите информацию.\n\n"
        )

        btns = {
            "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
            "🛍 Продлить подписку": MenuCallback(level=2, menu_name='subscribes').pack(),
        }

        # Если автопродление ещё не отменено — показываем кнопку отмены
        if has_tariff:
            btns["❌ Отменить автопродление"] = MenuCallback(level=4, menu_name='cancel').pack()

        btns["🔄 Обновить ключ"] = MenuCallback(level=4, menu_name='check').pack()
        btns["⬅️ Назад"] = MenuCallback(level=1, menu_name='main').pack()

        keyboard = get_inlineMix_btns(
            btns=btns,
            sizes=(1,)
        )
        return caption, keyboard

    # 3) Подписка отменена (tariff_id <= 0), но ещё есть sub_end и сервера (действует до даты)
    if (not has_tariff) and has_end and (user.sub_end > now) and has_servers:
        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
        caption = (
            "<b>⚙️ Ваша подписка SKYNET VPN:</b>\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
            "⚠️ Подписка отменена и больше не будет автоматически продлеваться.\n\n"
            "<b>Ваша ссылка на ключ. 🔑</b>\n\n"
            "Нажмите 1 раз чтобы скопировать:\n\n"
            f"<pre><code>{escape(url)}</code></pre>"
        )
        keyboard = get_inlineMix_btns(
            btns={
                "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
                "🎥 Premium в Медиа-боте": deep_link,  # <--- ТЕПЕРЬ КНОПКА ЕСТЬ И ЗДЕСЬ
                "🛡 Telegram Прокси": MenuCallback(level=9, menu_name='proxies').pack(),
                "🛍 Продлить подписку": MenuCallback(level=2, menu_name='subscribes').pack(),
                "🔄 Обновить ключ": MenuCallback(level=4, menu_name='check').pack(),
                "⬅️ Назад": MenuCallback(level=1, menu_name='main').pack(),
            },
            sizes=(1,)
        )
        return caption, keyboard

    # 4) Вообще нет подписки/серверов
    caption = "❌ У вас нет активной подписки."
    keyboard = get_inlineMix_btns(
        btns={
            "🛍 Приобрести подписку": MenuCallback(level=2, menu_name='subscribes').pack(),
            "⬅️ Назад": MenuCallback(level=1, menu_name='main').pack(),
        },
        sizes=(1,)
    )
    return caption, keyboard


async def pay_menu(
    session: AsyncSession,
    level: int,
    menu_name: str,
    user_id: int
):
    tariff = await orm_get_tariff(session, int(menu_name))
    if not tariff:
        return "❌ Тариф не найден", menu_btn()

    caption = (
        f"Вы выбрали подписку: <b>{days_to_str(tariff.days)}</b>\n"
        f"Стоимость: {tariff.price} руб.\n"
        "Способ оплаты: Банковская карта\n"
        "Время на оплату: 10 минут\n\n"
    )

    if tariff.id == DAY10_ID:
        caption += (
            "<b>⚠️ Важно:</b>\n"
            "Тариф действует <b>24 часа</b>.\n"
            "После окончания пробного тарифа, автоматически\n"
            "подключается <b>месячный тариф за 299 ₽.</b>\n\n"
            '<b>Подписку можно будет отменить в любое время в разделе "Моя подписка"</b>\n\n'
        )
    else:
        caption += '<b>Все подписки продлеваются автоматически. Отмена подписки возможна в любой момент в разделе "Моя подписка"!</b>\n\n'

    caption += "После оплаты ключ доступа будет отправлен в течение минуты."
    keyboard = get_pay_btns(tariff, user_id)

    return caption, keyboard


async def help_menu(level: int, menu_name: str) -> tuple:
    text = {
        'android': '<b>📖 Для подключения VPN на Android:</b>\n\n1. Установите приложение «v2RayTun» из Google Play по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://play.google.com/store/apps/details?id=com.v2raytun.android&pcampaignid=web_share',
        'iphone': '<b>📖 Для подключения VPN на Iphone:</b>\n\n1. Установите приложение «v2RayTun» из App Store по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB',
        'windows': '<b>Инструкция для Windows:</b>\n\n1. Скопировать ключ, который вы получили\n\n2. Запустить приложение v2raytun от имени администратора (1 СКРИН )\n\n3. Вверху справа нажать "+" и выбрать первое предложенное "Импортировать из буфера обмена" или на английском: "Import from clickboard"  (2 СКРИН)\n\n4. Зайти в Настройки – Настройки трафика – Режим – Туннель (3 СКРИН)\n\n5. Вернуться в главное меню, проверить появился ли ключ и запустить ВПН (4 СКРИН)\n\n6. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://drive.google.com/file/d/1Bbmxgz30fRv4jcn4rJj4M6Q0ZkZnu7Ao/view?usp=drive_link',
        'macos': '<b>📖 Для подключения VPN на MacOS:</b>\n\n1. Установите приложение «v2RayTun» из App Store по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB',
        'linux': '<b>📖 Для подключения VPN на Linux:</b>\n\n1. Скачайте приложение Hiddify по кнопке ниже и установите его на ваш компьютер.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://github.com/hiddify/hiddify-app/releases/latest/download/Hiddify-Linux-x64.AppImage',
        'androidtv': '<b>📖 Для подключения VPN на Android:</b>\n\n1. Установите приложение «v2RayTun» из Google Play по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://play.google.com/store/apps/details?id=com.v2raytun.android&pcampaignid=web_share',
    }

    if menu_name == 'help':
        caption = "<b>Выберите своё устройство:</b> \n\nСделали пошаговые инструкции для подключения VPN! Нажмите на нужную кнопку и подключайтесь за несколько минут."
        keyboard = choose_device_btns()
        return caption, keyboard

    # --- ЛОГИКА ДЛЯ WINDOWS (4 СКРИНА) ---
    elif menu_name == 'windows':
        # Сюда вставьте реальные file_id ваших 4 скриншотов
        windows_file_ids = [
            "AgACAgIAAxkBAAI8Z2liQG8WHMNi86qAywjp-4E74eXbAAJYD2sbdzkQS-e206zBEjc6AQADAgADeQADOAQ",  # ID 1
            "AgACAgIAAxkBAAI8aWliQIFSah1I-HnRqLEAAesaL4WWKgACWw9rG3c5EEvExokAAdUtTEIBAAMCAAN5AAM4BA",  # ID 2
            "AgACAgIAAxkBAAI8a2liQJEQUL9EQ2YgYIEnjvt3G69_AAJdD2sbdzkQS-MRv_dOBQ1oAQADAgADeQADOAQ",  # ID 3
            "AgACAgIAAxkBAAI8bWliQKViSn1g_gtJd_sBLXzC5gWCAAJeD2sbdzkQS6DLPJ_zcIgbAQADAgADeQADOAQ"  # ID 4
        ]

        album = []
        for file_id in windows_file_ids:
            # InputMediaPhoto принимает file_id или FSInputFile
            album.append(types.InputMediaPhoto(media=file_id))

        caption_text = text['windows'].split('|||')[0]
        keyboard = install_btns(text['windows'].split('|||')[-1], level)

        # Возвращаем 3 элемента: альбом, текст, клавиатуру
        return album, caption_text, keyboard
    # -------------------------------------

    else:
        caption = text[menu_name].split('|||')[0]
        keyboard = install_btns(text[menu_name].split('|||')[-1], level)
        return caption, keyboard



async def other_products(level: int, menu_name: str):
    caption = "<b>Наши другие продукты:</b>"
    keyboard = other_products_btns(level)

    return caption, keyboard


async def faq_menu(session: AsyncSession, level: int, menu_name: str) -> tuple:
    """FAQ: список вопросов или конкретный ответ"""

    # Конкретный вопрос: menu_name = "faq_123"
    if menu_name.startswith("faq_"):
        try:
            faq_id = int(menu_name.split("_")[1])
        except (IndexError, ValueError):
            return "❌ Вопрос не найден.", menu_btn()

        faq = await orm_get_faq_by_id(session, faq_id)
        if not faq:
            return "❌ Вопрос не найден.", menu_btn()

        caption = f"<b>❓ {faq.ask}</b>\n\n✅ {faq.answer}"
        return caption, get_faq_back_btn()

    # Список всех вопросов
    faq_list = await orm_get_faq(session)
    if not faq_list:
        return "Вопросов пока нет.", menu_btn()

    caption = "<b>❓ Часто задаваемые вопросы</b>\n\nВыберите интересующий вопрос:"
    return caption, get_faq_list_btns(faq_list)


async def support_menu(level: int, menu_name: str) -> tuple:
    caption = (
        "<b>Поддержка SKYNET VPN</b>\n\n"
        "Здесь вы можете найти ответы на частые вопросы или напрямую обратиться к нашему специалисту."
    )

    # Обязательно замени 'https://t.me/твой_аккаунт_поддержки' на реальную ссылку
    keyboard = get_inlineMix_btns(
        btns={
            "❓ Частые вопросы": MenuCallback(level=7, menu_name='faq').pack(),
            "💬 Написать в поддержку": "https://t.me/skynetaivpn_support",
            "⬅️ Назад": MenuCallback(level=1, menu_name='main').pack(),
        },
        sizes=(1, 1, 1)
    )

    return caption, keyboard


async def get_menu_content(
    session: AsyncSession,
    level: int,
    menu_name: str,
    user_id: Optional[int] = None,
    include_image: bool = False
) -> tuple:
    if level == 0:
        return await start_message(session, level, menu_name, user_id)
    elif level == 1:
        return await main_menu(session, level, menu_name, user_id, include_image)
    elif level == 2:
        return await buy_subscribe(session, level, menu_name, user_id=user_id)
    elif level == 3:
        return await pay_menu(session, level, menu_name, user_id)
    elif level == 4:
        return await check_subscribe(session, level, menu_name, user_id)
    elif level == 5:
        return await help_menu(level, menu_name)
    elif level == 6:
        return await other_products(level, menu_name)
    elif level == 7:
        return await faq_menu(session, level, menu_name)
    elif level == 8:
        return await support_menu(level, menu_name)
    elif level == 9:
        return await proxy_menu(level, menu_name)
    else:
        return await start_message(session, level, menu_name, user_id)