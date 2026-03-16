import asyncio
from datetime import date, datetime, time
import os
import json
import hashlib
from typing import Union
from uuid import uuid4

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.tg_bot_router.kbds.inline import succes_pay_btns, succes_pay_btns_for_gb
from app.utils.days_to_month import days_to_str
from app.database.engine import get_async_session, async_session_maker
from app.setup_logger import logger
from app.tg_bot_router.bot import bot
from app.database.queries import (
    orm_add_user_server,
    orm_change_user_tariff,
    orm_get_last_payment,
    orm_get_payment,
    orm_get_servers,
    orm_get_tariff,
    orm_get_last_payment_id,
    orm_get_user_by_tgid,
    orm_get_user_server_by_ti,
    orm_get_user_servers,
    orm_new_payment,
    orm_update_user,
    orm_get_subscribers,
    orm_get_users,
    orm_add_referral_bonus, orm_get_tariffs, orm_get_admins,
)
from app.utils.three_x_ui_api import ThreeXUIServer

payment_router = APIRouter(prefix="/payment")
templates = Jinja2Templates(directory='app/payment_router/templates')


async def preserve_total_gb(panel: ThreeXUIServer, *, uuid: str, tariff_gb: int) -> int:
    if not panel.need_gb:
        return 0

    base_gb = int(tariff_gb) if tariff_gb else 30
    current_gb = await panel.get_total_gb(uuid)
    return max(current_gb or 0, base_gb)


async def _apply_referral_bonus(session, user, tariff, bot):
    """Начислить реферальный бонус если ещё не начислялся"""
    if user.referral_rewarded or not user.invited_by:
        return

    if tariff.days >= 365:
        bonus_days = 30
    elif tariff.days >= 180:
        bonus_days = 15
    elif tariff.days >= 30:
        bonus_days = 7
    else:
        return

    try:
        referrer = await orm_get_user_by_tgid(session, user.invited_by)
        referrer_name = referrer.name if referrer else "Неизвестно"

        new_referrer_end = await orm_add_referral_bonus(
            session,
            referrer_tg_id=user.invited_by,
            bonus_days=bonus_days,
            referred_user_id=user.id
        )
        await bot.send_message(
            user.invited_by,
            f"🎁 Вы получили бонус для продления подписки на {bonus_days} дней!\n"
            f"📅 Ваша подписка продлена до {new_referrer_end.strftime('%d.%m.%Y')}",
            parse_mode='HTML'
        )

        admins = await orm_get_admins(session)
        for admin in admins:
            try:
                await bot.send_message(
                    admin.telegram_id,
                    f"<b>Реферальный бонус начислен</b>\n\n"
                    f"Пригласил: <code>{user.invited_by}</code> ({referrer_name}\n"
                    f"Новый пользователь: <code>{user.telegram_id}</code> ({user.name})\n"
                    f"Бонус: <b>{bonus_days} дней</b>\n"
                    f"Подписка реферера до: <b>{new_referrer_end.strftime('%d.%m.%Y')}</b>",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить админа {admin.telegram_id}: {e}")

        logger.info(f"Реферальный бонус {bonus_days} дней → {user.invited_by} за {user.telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка реферального бонуса: {e}")


@payment_router.get('/payment_page', response_class=HTMLResponse)
async def payment_page(
        request: Request,
        telegram_id: int,
        tariff_id: int,
        session: AsyncSession = Depends(get_async_session)
):
    tariff = await orm_get_tariff(session, tariff_id=int(tariff_id))
    user = await orm_get_user_by_tgid(session, telegram_id=telegram_id)
    if not tariff or not user:
        raise HTTPException(status_code=404, detail="Tariff or User not found")

    invoice_id = await orm_get_last_payment_id(session) + 1

    is_addon = (tariff.days == 0 and tariff.ips == 0)
    item_name = (
        f"доп. трафик {tariff.trafic} ГБ (обход белых списков)"
        if is_addon
        else f"подписка skynetvpn на {days_to_str(tariff.days)}"
    )

    receipt = {
        "sno": "patent",
        "items": [
            {
                "name": item_name,
                "quantity": 1,
                "sum": float(tariff.price),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": "none"  # Убедитесь, что vat10 или none настроен верно по вашей кассе
            },
        ]
    }

    base_string = f"{os.getenv('SHOP_ID')}:{tariff.price}:{invoice_id}:{json.dumps(receipt, ensure_ascii=False)}:{os.getenv('PASSWORD_1')}"
    signature_value = hashlib.md5(base_string.encode("utf-8")).hexdigest()

    await orm_new_payment(session, tariff_id=tariff.id, user_id=user.id)

    return templates.TemplateResponse(
        "/payment_page.html",
        {
            "request": request,
            "price": tariff.price,
            "time": ("+GB" if is_addon else days_to_str(tariff.days).split(' ')[0]),
            "show_time": (f"{tariff.trafic} ГБ" if is_addon else days_to_str(tariff.days)),
            "pay_data": json.dumps(receipt, ensure_ascii=False),
            "shop_id": os.getenv("SHOP_ID"),
            "signature_value": signature_value,
            "invoice_id": invoice_id
        }
    )


@payment_router.post("/get_payment")
async def choose_server(
        OutSum: str = Form(...),  # Обязательно строка для MD5
        InvId: str = Form(...),  # Обязательно строка для MD5
        SignatureValue: str = Form(...),
        Fee: Union[str, float, int, None] = Form(None),
        EMail: Union[str, None] = Form(None),
        PaymentMethod: Union[str, None] = Form(None),
        IncCurrLabel: Union[str, None] = Form(None),
        Shp_Receipt: Union[str, None] = Form(None),
        session: AsyncSession = Depends(get_async_session)
):
    # --- 1. КРИТИЧЕСКАЯ ЗАЩИТА: Проверка подписи Robokassa ---
    password_2 = os.getenv('PASSWORD_2')
    if not password_2:
        logger.error("КРИТИЧЕСКИ: Не задан PASSWORD_2 в .env файле!")
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    # Формируем строку: OutSum:InvId:Пароль#2
    my_sign_base = f"{OutSum}:{InvId}:{password_2}"
    my_signature = hashlib.md5(my_sign_base.encode("utf-8")).hexdigest()

    if SignatureValue.lower() != my_signature.lower():
        logger.error(f"Взлом/Ошибка подписи! Пришло: {SignatureValue}, Ожидалось: {my_signature}")
        raise HTTPException(status_code=400, detail="Bad signature")

    payment = await orm_get_payment(session, int(InvId))
    if not payment:
        raise HTTPException(status_code=404, detail="Оплата не найдена")

    user = payment.user

    if EMail:
        try:
            await orm_update_user(session, user.id, {'email': EMail})
        except Exception:
            logger.error("Не удалось сменить почту пользователя")

    tariff = await orm_get_tariff(session, payment.tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    user_servers = await orm_get_user_servers(session, user.id)
    servers = await orm_get_servers(session)

    threex_panels = [
        ThreeXUIServer(s.id, s.url, s.indoub_id, s.login, s.password, s.need_gb, s.name)
        for s in servers
    ]

    is_addon = (tariff.days == 0 and tariff.ips == 0)

    # --- ДОП ПРОДУКТ: докупка трафика ---
    if (not payment.recurent) and is_addon and (tariff.trafic or 0) > 0:
        now = datetime.now()
        if not user.sub_end or user.sub_end < now:
            await bot.send_message(
                user.telegram_id,
                "❌ Докупить трафик можно только при активной подписке.\n\nОткрой /start → 🛍 Купить подписку.",
                parse_mode='HTML'
            )
            return f'OK{InvId}'

        add_gb = int(tariff.trafic)
        GB = 1073741824

        # Параллельный сбор текущего лимита
        async def fetch_limit(p, tun_id):
            if not p.need_gb: return 0
            return await p.get_total_gb(tun_id)

        limit_tasks = []
        for panel in threex_panels:
            us = next((us for us in user_servers if us.server_id == panel.id), None)
            if us: limit_tasks.append(fetch_limit(panel, us.tun_id))

        limits = await asyncio.gather(*limit_tasks, return_exceptions=True)
        valid_limits = [l for l in limits if isinstance(l, int) and l > 0]

        current_limit_gb = max(valid_limits) if valid_limits else 30
        new_limit_gb = current_limit_gb + add_gb

        # Параллельное обновление панелей
        async def update_gb(p, us):
            email = f"{p.name}_{us.id}"
            traf = await p.client_remain_trafic(us.tun_id)
            current_total_bytes = int((traf[2] if traf else 0) or 30 * GB)
            if current_total_bytes < 30 * GB: current_total_bytes = 30 * GB
            new_total_gb = int((current_total_bytes + add_gb * GB) // GB)

            await p.edit_client(
                uuid=us.tun_id, email=email, limit_ip=user.ips,
                expiry_time=int(user.sub_end.timestamp() * 1000),
                tg_id=user.telegram_id, name=user.name, total_gb=new_total_gb,
            )

        update_tasks = []
        for panel in threex_panels:
            if panel.need_gb:
                us = next((us for us in user_servers if us.server_id == panel.id), None)
                if us: update_tasks.append(update_gb(panel, us))

        await asyncio.gather(*update_tasks, return_exceptions=True)

        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
        try:
            await bot.send_message(
                user.telegram_id,
                f"✅ <b>Трафик добавлен!</b>\n\n📦 Было: <b>{current_limit_gb} ГБ</b>\n➕ Добавлено: <b>{add_gb} ГБ</b>\n🏳️ Стало: <b>{new_limit_gb} ГБ</b>\n\n<b>Ваша ссылка на ключ. 🔑</b>\nНажмите 1 раз чтобы скопировать:\n\n<pre><code>{url}</code></pre>",
                parse_mode="HTML", reply_markup=succes_pay_btns_for_gb(user)
            )
        except TelegramAPIError:
            # Если юзер удалил чат, просто игнорируем ошибку
            pass
        return f'OK{InvId}'

    # --- Общая логика дат ---
    today_datetime = datetime.combine(date.today(), time.min)
    if user_servers and user.sub_end and user.sub_end > today_datetime and not payment.recurent:
        end_datetime = user.sub_end + relativedelta(days=tariff.days)
    else:
        end_datetime = today_datetime + relativedelta(days=tariff.days)

    end_timestamp = int(end_datetime.timestamp() * 1000)

    # --- Подготовка БД (строго последовательно) ---
    panels_to_setup = []
    for panel in threex_panels:
        user_server = next((us for us in user_servers if us.server_id == panel.id), None)
        is_new = False
        if not user_server:
            new_uuid = str(uuid4())
            await orm_add_user_server(session, server_id=panel.id, tun_id=new_uuid, user_id=user.id)
            user_server = await orm_get_user_server_by_ti(session, new_uuid)
            is_new = True
        panels_to_setup.append({"panel": panel, "us": user_server, "is_new": is_new})

    # --- Параллельные сетевые запросы к 3x-ui ---
    async def process_panel(item):
        panel = item["panel"]
        us = item["us"]
        email = f"{panel.name}_{us.id}"

        if item["is_new"]:
            await panel.add_client(
                uuid=us.tun_id, email=email, limit_ip=tariff.ips,
                expiry_time=end_timestamp, tg_id=user.telegram_id,
                name=user.name, total_gb=30 if panel.need_gb else 0
            )
        else:
            total_gb = await preserve_total_gb(panel, uuid=us.tun_id, tariff_gb=int(tariff.trafic or 0))
            await panel.edit_client(
                uuid=us.tun_id, email=email, limit_ip=tariff.ips, name=user.name,
                expiry_time=end_timestamp, tg_id=user.telegram_id, total_gb=total_gb,
            )

    api_tasks = [process_panel(item) for item in panels_to_setup]
    await asyncio.gather(*api_tasks, return_exceptions=True)

    # --- Обновление юзера и бонусы ---
    await orm_change_user_tariff(session, ips=tariff.ips, tariff_id=tariff.id, user_id=user.id, sub_end=end_datetime)
    if not payment.recurent:
        await _apply_referral_bonus(session, user, tariff, bot)

    # --- Уведомление в ТГ ---
    url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
    try:
        if not payment.recurent:
            await bot.send_message(
                user.telegram_id,
                f"<b>✅ Спасибо! Вы оформили подписку!</b>\n\n🗓 Ваша подписка активна до {end_datetime.strftime('%d.%m.%Y')}\n\n<b>Для автоматического подключения нажмите кнопку \"Подключиться\"\n\nДля ручного ввода скопируйте ключ. Для копирования ключа нажмите на него 1 раз. ⬇️</b>\n\n<pre><code>{url}</code></pre>",
                reply_markup=succes_pay_btns(user), parse_mode='HTML'
            )
        else:
            await bot.send_message(
                user.telegram_id,
                f"<b>🔄 Ваша подписка успешно продлена!</b>\n\n🗓 Подписка активна до {end_datetime.strftime('%d.%m.%Y')}\n💰 Сумма списания: {tariff.price}₽\n\n<b>Для автоматического подключения нажмите кнопку \"Подключиться\"\n\nДля ручного ввода скопируйте ключ. Для копирования ключа нажмите на него 1 раз. ⬇️</b>\n<code>{url}</code>",
                reply_markup=succes_pay_btns(user), parse_mode='HTML'
            )
    except TelegramAPIError as e:
    # Логируем, что юзер заблокировал бота, но не роняем сервер
        logger.warning(f"Юзер {user.telegram_id} недоступен для отправки ключа: {e}")

    return f'OK{InvId}'


async def check_subscription_expiry(bot: Bot):
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if not user.sub_end: continue
            days_left = (user.sub_end - today).days

            try:
                if days_left == 3:
                    await bot.send_message(user.telegram_id, f'⚠️ <b>Подписка истекает через 3 дня</b>')
                elif days_left == 1:
                    await bot.send_message(user.telegram_id, f'🔔 <b>Подписка истекает завтра!</b>')
                elif days_left == 0:
                    await bot.send_message(user.telegram_id, f'❌ <b>Срок действия подписки завершён</b>')
            except TelegramAPIError:
                pass  # Если юзер заблокировал бота, просто игнорируем

            await asyncio.sleep(0.05)  # Плавная отправка


DAY10_ID = int(os.getenv("TARIFF_DAY10_ID", "0") or 0)
MONTH300_ID = int(os.getenv("TARIFF_MONTH300_ID", "0") or 0)


async def recurent_payment(bot: Bot):
    async with async_session_maker() as session:
        users = await orm_get_subscribers(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if user.tariff_id == 0 or not user.sub_end or user.sub_end > today: continue
            last_payment = await orm_get_last_payment(session, user.id)
            if not last_payment: continue

            renew_tariff_id = MONTH300_ID if DAY10_ID and MONTH300_ID and user.tariff_id == DAY10_ID else user.tariff_id
            tariff = await orm_get_tariff(session, tariff_id=renew_tariff_id)
            if not tariff: continue

            # Защита: не списываем у тех, кто заблокировал бота
            try:
                await bot.send_chat_action(chat_id=user.telegram_id, action=ChatAction.TYPING)
            except TelegramForbiddenError:
                logger.info(f"🚫 Юзер {user.telegram_id} заблокировал бота. Отменяем автопродление.")
                try:
                    await orm_change_user_tariff(session, ips=0, tariff_id=0, user_id=user.id, sub_end=user.sub_end)
                except Exception:
                    pass
                continue
            except Exception:
                pass

            await orm_new_payment(session, tariff_id=renew_tariff_id, user_id=user.id, recurent=True)
            invoice_id = await orm_get_last_payment_id(session)
            price_str = "{:.2f}".format(float(tariff.price))
            item_name = f"Подписка SkynetVPN на {days_to_str(tariff.days)}"

            receipt = {
                "sno": "patent",
                "items": [{"name": item_name[:128], "quantity": 1, "sum": price_str, "payment_method": "full_payment",
                           "payment_object": "service", "tax": "none"}]
            }
            receipt_json = json.dumps(receipt, ensure_ascii=False, separators=(',', ':'))

            # ИСПРАВЛЕНИЕ ПОДПИСИ (Receipt добавлен в строку)
            sign_str = f"{os.getenv('SHOP_ID')}:{price_str}:{invoice_id}:{receipt_json}:{os.getenv('PASSWORD_1')}"
            signature_value = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

            try:
                async with AsyncClient() as client:
                    data_payload = {
                        "MerchantLogin": os.getenv("SHOP_ID"), "InvoiceID": str(invoice_id),
                        "PreviousInvoiceID": str(last_payment), "Description": "SkynetVPN Автопродление",
                        "SignatureValue": signature_value, "OutSum": price_str,
                        "Receipt": receipt_json
                    }
                    response = await client.post("https://auth.robokassa.ru/Merchant/Recurring", data=data_payload,
                                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})

                    if response.status_code != 200:
                        logger.error(f"Ошибка Robokassa: {response.text}")
                        try:
                            await bot.send_message(user.telegram_id, "⚠️ Не удалось автоматически продлить подписку.")
                        except TelegramAPIError:
                            pass
            except Exception as e:
                logger.error(f"Критическая ошибка автопродления: {e}")

            # Защита от спам-блокировок API
            await asyncio.sleep(0.5)


import asyncio
from datetime import datetime

# Ограничиваем количество ОДНОВРЕМЕННЫХ запросов к API серверов (например, 15)
# Это защитит бот от блокировок и перегрузки системы
semaphore = asyncio.Semaphore(15)


async def reset_monthly_traffic(bot: Bot):
    async with async_session_maker() as session:
        # 1. Загружаем всё необходимое
        users = await orm_get_users(session)
        servers = await orm_get_servers(session)
        tariffs = await orm_get_tariffs(session)

        today = datetime.now()
        tariff_map = {t.id: t.trafic for t in tariffs}

        # Только панели, требующие лимита ГБ
        panels_info = [s for s in servers if s.need_gb]
        if not panels_info:
            return

        GB = 1073741824
        success_count = 0
        error_count = 0

        logger.info("🔄 Начинаю плавный сброс месячного трафика...")

        for user in users:
            # Пропускаем неактивных, чтобы не дергать API зря
            if not user.sub_end or user.sub_end < today:
                continue

            # Определяем базовый лимит из тарифа
            base_limit_gb = tariff_map.get(user.tariff_id, 30) or 30
            expiry_ms = int(user.sub_end.timestamp() * 1000)

            user_servers = await orm_get_user_servers(session, user.id)

            for us in user_servers:
                s_info = next((p for p in panels_info if p.id == us.server_id), None)
                if not s_info:
                    continue

                panel = ThreeXUIServer(
                    s_info.id, s_info.url, s_info.indoub_id,
                    s_info.login, s_info.password, s_info.need_gb, s_info.name
                )

                # Обновляем каждого клиента изолированно
                result = await safe_process_single_traffic(
                    panel, us, user, base_limit_gb, expiry_ms, GB
                )

                if result:
                    success_count += 1
                else:
                    error_count += 1

                # 🛡 Пауза 0.1 сек — защита от блокировки API
                await asyncio.sleep(0.1)

        logger.info(f"✅ Сброс завершен. Успешно: {success_count}, Ошибок: {error_count}")


async def safe_process_single_traffic(panel, us, user, base_limit_gb, expiry_ms, GB):
    """Безопасная обработка одного клиента на панели"""
    email = f"{panel.name}_{us.id}"

    try:
        if not await panel.auth():
            return False

        # ПРОВЕРКА: существует ли клиент на панели?
        traf = await panel.client_remain_trafic(us.tun_id)

        # Если трафик не вернулся (None или False), значит юзера нет в панели
        if traf is None or traf is False:
            logger.warning(f"⚠️ Клиент {email} не найден в панели {panel.name}. Пропускаем.")
            return False

        # Вычисляем остаток (Total - (Up + Down))
        # traf[0] - up, traf[1] - down, traf[2] - total
        used_bytes = (traf[0] or 0) + (traf[1] or 0)
        total_bytes = (traf[2] or 0)

        # Оставшиеся ГБ, которые юзер не дорасходовал
        remaining_gb = int(max(total_bytes - used_bytes, 0) // GB)

        # Новое значение total_gb = базовый тариф + то, что осталось
        new_total_gb = max(base_limit_gb, remaining_gb)

        # 1. Обновляем лимиты и дату
        await panel.edit_client(
            uuid=us.tun_id,
            name=user.name,
            email=email,
            limit_ip=user.ips or 1,
            expiry_time=expiry_ms,
            tg_id=str(user.telegram_id),
            total_gb=new_total_gb
        )

        # 2. Сбрасываем счетчик использованного трафика (статистику) в ноль
        await panel.reset_client_traffic(email)

        return True

    except Exception as e:
        logger.error(f"❌ Ошибка API для {email} на {panel.name}: {e}")
        return False


async def notify_expired_users(bot: Bot):
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if not user.sub_end or user.sub_end > today: continue
            days_expired = (today - user.sub_end).days

            try:
                if days_expired == 5:
                    await bot.send_message(user.telegram_id,
                                           '⚠️ <b>Ваша подписка истекла 5 дней назад</b>\n\nМы скучаем по вам! Продлите подписку.\n👉 Нажмите /start',
                                           parse_mode='HTML')
                elif days_expired == 15:
                    await bot.send_message(user.telegram_id,
                                           '📢 <b>Прошло уже 15 дней без SkynetVPN</b>\n\nПродлите подписку и получите доступ ко всем серверам.\n👉 Нажмите /start',
                                           parse_mode='HTML')
                elif days_expired == 30:
                    await bot.send_message(user.telegram_id,
                                           '🔔 <b>Месяц без SkynetVPN!</b>\n\nВозвращайтесь — мы ждём вас.\n👉 Нажмите /start',
                                           parse_mode='HTML')
            except Exception:
                pass