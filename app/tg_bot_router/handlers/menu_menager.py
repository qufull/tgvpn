from typing import Optional
from datetime import datetime
import os

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import types

from app.database.queries import orm_get_faq, orm_change_user_tariff, orm_get_servers, orm_get_tariff, orm_get_tariffs, orm_get_user_by_tgid, orm_get_user_servers
from app.utils.days_to_month import days_to_str
from app.tg_bot_router.kbds.inline import (
    MenuCallback,
    choose_device_btns,
    get_inlineMix_btns,
    get_main_btns,
    get_pay_btns,
    get_start_btns,
    get_tariffs_btns,
    install_btns,
    menu_btn,
    other_products_btns
)


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
        caption="<b>SkyNetVPN — сервис шифрованных подключений.</b>\n\n"\
                "Мы не анализируем содержимое трафика и не ведём его содержательные логи. \n\n"\
                "Устанавливается на:  <b>Windows / macOS / iOS / Android / Linux / Android TV. </b>\n\n"\
                "Трафик со стороны сервиса не лимитируется. \n"\
                "Фактическая скорость соединения зависит от вашей сети и устройства.\n\n"\
                "<b>Оплатите тариф и начинайте пользоваться.</b>"
    elif menu_name == 'invite':
        caption=f"<b>Приглашайте друзей и получайте бонусы!</b> \n\nЗа каждую покупку приглашенных пользователей Вы получите к вашей подписке:\n\nЗа 1 мес. – 15 дней\nЗа 6 мес. – 30 дней\nЗа 12 мес. – 45 дней\n\nВаша реферальная ссылка:\nhttps://t.me/skynetaivpn_bot?start={user_id}"
    elif menu_name == 'policy':
        caption=f"О нас: \nМы предоставляем техническую услугу по организации шифрованного соединения (VPN). Не являемся СМИ, не размещаем и не контролируем контент. Сервис не предназначен для обхода ограничений и доступа к запрещённой информации. \n\nПолный текст —  <a href=\"{os.getenv('URL')}/site/privacy_policy\">Политика конфидециальности</a>.\n\nХарактеристики, сроки и стоимость — в интерфейсе бота и в <a href=\"{os.getenv('URL')}/site/terms_of_service\">публичной оферте</a>."
    elif menu_name == 'faq':
        caption="<b>Часто задаваемые вопросы:</b>"

        faq = await orm_get_faq(session)

        for i in faq:
            caption += f"\n\n<b>❓ {i.ask}</b>\n✅ {i.answer}"


    if include_image:
        baner = types.FSInputFile("media/img/main_logo_bg.jpg")
        media = types.InputMediaPhoto(media=baner, caption=caption)
        return media, kbd
    else:
        return caption, kbd


async def buy_subscribe(
    session: AsyncSession,
    level: int,
    menu_name: str,
    user_id: int | None = None,
) -> tuple:
    tariffs = await orm_get_tariffs(session)
    servers = await orm_get_servers(session)

    caption = "<b>⚡️ Вы покупаете премиум подписку на Skynet VPN</b>\n\n● Возможность подключить любые устройства\n● До 8 устройств одновременно\n● Без лимитов и ограничений по скорости\n\n<b>Список поддерживаемых устройств:</b>\n\nAndroid (Android 7.0 или новее.) | Windows (Windows 8.1 или новее.) | iOS, iPadOS (iOS 16.0 или новее.) | macOS процессоры M  (macOS 13.0 или новее) | macOS  c процессором Intel (macOS 11.0 или новее.) | Android TV ( Android 7.0 или новее.) | Linux\n\n<b>🌍 Доступные страны:</b>\n👑 - без рекламы на YouTube\n🎧 - YouTube можно сворачивать\n 🏳️ - Лимит по обходу белых списков 30 ГБ каждый месяц\n"

    for server in servers:
        if server.id == servers[-1].id:
            caption += f"\n└ {server.name}"
            continue
        caption += f"\n├ {server.name}"

    # --- Доп. кнопка «+100 ГБ» ---
    # Показываем только если:
    # 1) есть хотя бы один сервер с need_gb=True
    # 2) у пользователя есть активная подписка (sub_end в будущем)
    # 3) в окружении задан id тарифа-доппродукта (EXTRA_GB_TARIFF_ID)
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
    is_expired = bool(has_end and user.sub_end <= now)         # закончилась/истекла
    has_tariff = bool(user.tariff_id and user.tariff_id > 0)   # есть тариф (не отменён)
    has_servers = bool(user_servers)                           # есть привязанные сервера

    # тариф может быть удалён из БД — страхуемся
    tariff = await orm_get_tariff(session, user.tariff_id) if has_tariff else None

    # 1) Подписка активна: есть тариф и дата в будущем
    if has_tariff and has_end and user.sub_end > now:
        price = tariff.price if tariff else "—"
        days = days_to_str(tariff.days) if tariff else "—"

        caption = (
            "⚙️ Ваша подписка SkynetVPN:\n"
            f"├ Цена: {price}\n"
            f"├ Срок: {days}\n"
            f"├ Количество устройств: {user.ips}\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
            "Ваша ссылка на ключ. 🔑\n\n"
            "Нажмите 1 раз чтобы скопировать:\n"
            f"<code>{os.getenv('URL')}/api/subscribtion?user_token={user.id}</code>"
        )
        keyboard = get_inlineMix_btns(
            btns={
                "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
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
            "⛔️ Ваша подписка SkynetVPN закончилась.\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
            "Нажмите «Продлить подписку», оплатите тариф и после оплаты обновите информацию.\n\n"
        )
        keyboard = get_inlineMix_btns(
            btns={
                "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
                "🛍 Продлить подписку": MenuCallback(level=2, menu_name='subscribes').pack(),
                "🔄 Обновить ключ": MenuCallback(level=4, menu_name='check').pack(),
                "⬅️ Назад": MenuCallback(level=1, menu_name='main').pack(),
            },
            sizes=(1,)
        )
        return caption, keyboard

    # 3) Подписка отменена (tariff_id <= 0), но ещё есть sub_end и сервера (действует до даты)
    if (not has_tariff) and has_end and (user.sub_end > now) and has_servers:
        caption = (
            "⚙️ Ваша подписка SkynetVPN:\n"
            f"└ оплачено до {user.sub_end.strftime('%d-%m-%Y')}\n\n"
            "⚠️ Подписка отменена и больше не будет автоматически продлеваться.\n\n"
            "Ваша ссылка на ключ. 🔑\n\n"
            "Нажмите 1 раз чтобы скопировать:\n"
            f"<code>{os.getenv('URL')}/api/subscribtion?user_token={user.id}</code>"
        )
        keyboard = get_inlineMix_btns(
            btns={
                "↗️ Подключиться v2rayTun": f"{os.getenv('URL')}/bot/v2ray?telegram_id={user.telegram_id}",
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

    caption = f"Вы выбрали подписку: <b>{days_to_str(tariff.days)}</b>\nСтоимость: {tariff.price} руб.\nСпособ оплаты: Банковская карта\nВремя на оплату: 10 минут\n\nВсе подписки продлеваются автоматически. Отмена подписки возможна в любой момент.\n\nПосле оплаты SkynetVPN будет отправлена в течение минуты."
    keyboard = get_pay_btns(tariff, user_id)

    return caption, keyboard


async def help_menu(level: int, menu_name: str) -> tuple:
    text = {
        'android': '<b>📖 Для подключения VPN на Android:</b>\n\n1. Установите приложение «v2RayTun» из Google Play по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://play.google.com/store/apps/details?id=com.v2raytun.android&pcampaignid=web_share',
        'iphone': '<b>📖 Для подключения VPN на Iphone:</b>\n\n1. Установите приложение «v2RayTun» из App Store по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://apps.apple.com/ru/app/v2raytun/id6476628951',
        'windows': '<b>Инструкция для Windows:</b>\n\n1. Скопировать ключ, который вы получили\n\n2. Запустить приложение v2raytun от имени администратора (1 СКРИН )\n\n3. Вверху справа нажать "+" и выбрать первое предложенное "Импортировать из буфера обмена" или на английском: "Import from clickboard"  (2 СКРИН)\n\n4. Зайти в Настройки – Настройки трафика – Режим – Туннель (3 СКРИН)\n\n5. Вернуться в главное меню, проверить появился ли ключ и запустить ВПН (4 СКРИН)\n\n6. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://storage.v2raytun.com/v2RayTun_Setup.exe',
        'macos': '<b>📖 Для подключения VPN на MacOS:</b>\n\n1. Установите приложение «v2RayTun» из App Store по кнопке ниже.\n\n2. Нажмите кнопку «🔗 Добавить профиль», чтобы добавить подключение в приложение.\n\n3. Всё готово! Теперь вы под защитой и можете без преград пользоваться интернетом!|||https://apps.apple.com/ru/app/v2raytun/id6476628951',
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
            "AgACAgIAAxkBAAISCWliOcxbehrgKAQ-mhbK0xpmV8TTAAL7DWsbKbsQS0uTR4ioiA0YAQADAgADeQADOAQ",  # ID 1
            "AgACAgIAAxkBAAISC2liOdBt2nXBLj1txG3rL_0xQ2w7AAL8DWsbKbsQS-nom7PEVaBiAQADAgADeQADOAQ",  # ID 2
            "AgACAgIAAxkBAAISDWliOdX-hJilRCDdOLJmMtgljCG4AAL9DWsbKbsQS1LF3ji8ESjWAQADAgADeQADOAQ",  # ID 3
            "AgACAgIAAxkBAAISD2liOdfYbPwejLUQUuqAq05-wDpSAAL-DWsbKbsQS74EYSqDCO8oAQADAgADeQADOAQ"  # ID 4
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
    else:
        return await start_message(session, level, menu_name, user_id)





