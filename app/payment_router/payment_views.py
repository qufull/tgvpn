import asyncio
from datetime import date, datetime, time
import os
import json
import hashlib
import urllib.parse
from typing import Union

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient
from starlette.responses import HTMLResponse

from app.utils.days_to_month import days_to_str
from app.database.engine import get_async_session, async_session_maker
from app.setup_logger import logger
from app.tg_bot_router.bot import bot

# Импортируем наш новый сервис активации
from app.payment_router.subscription_service import fulfill_subscription

from app.database.queries import (
    orm_change_user_tariff,
    orm_get_last_payment,
    orm_get_payment,
    orm_get_servers,
    orm_get_tariff,
    orm_get_last_payment_id,
    orm_get_user_by_tgid,
    orm_get_user_servers,
    orm_new_payment,
    orm_get_subscribers,
    orm_get_users,
    orm_get_tariffs,
)
from app.utils.three_x_ui_api import ThreeXUIServer

payment_router = APIRouter(prefix="/payment")


# ==========================================
# 1. ГЕНЕРАЦИЯ ССЫЛКИ ДЛЯ БОТА
# ==========================================
@payment_router.get('/payment_page')
async def payment_page(
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
                "name": item_name[:128],  # Защита от длинных названий для кассы
                "quantity": 1,
                "sum": float(tariff.price),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": "none"
            },
        ]
    }
    receipt_json = json.dumps(receipt, ensure_ascii=False)

    shop_id = os.getenv("SHOP_ID")
    password_1 = os.getenv("PASSWORD_1")

    # Подпись для инициализации (Password #1)
    base_string = f"{shop_id}:{tariff.price}:{invoice_id}:{receipt_json}:{password_1}"
    signature_value = hashlib.md5(base_string.encode("utf-8")).hexdigest()

    await orm_new_payment(session, tariff_id=tariff.id, user_id=user.id)

    # Формируем параметры и сразу редиректим юзера на кассу

    html_content = f"""
        <!DOCTYPE html>
        <html>
        <body onload="document.getElementById('payForm').submit();" style="display: flex; justify-content: center; align-items: center; height: 100vh; font-family: sans-serif; background: #f9f9f9;">
            <h2>Перенаправляем в кассу...</h2>
            <form id="payForm" method="POST" action="https://auth.robokassa.ru/Merchant/Index.aspx" style="display: none;">
                <input type="hidden" name="MerchantLogin" value="{shop_id}">
                <input type="hidden" name="OutSum" value="{tariff.price}">
                <input type="hidden" name="InvId" value="{invoice_id}">
                <input type="hidden" name="Description" value="{item_name}">
                <input type="hidden" name="SignatureValue" value="{signature_value}">
                <input type="hidden" name="Receipt" value='{receipt_json}'>
                <input type="hidden" name="Recurring" value="true">
            </form>
        </body>
        </html>
        """

    return HTMLResponse(content=html_content)


# ==========================================
# 2. WEBHOOK ОТ ROBOKASSA (ЕДИНЫЙ)
# ==========================================
@payment_router.post("/get_payment")
async def choose_server(
        OutSum: str = Form(...),
        InvId: str = Form(...),
        SignatureValue: str = Form(...),
        EMail: Union[str, None] = Form(None),
        Shp_origin: Union[str, None] = Form(None),
        session: AsyncSession = Depends(get_async_session)
):
    # --- КРИТИЧЕСКАЯ ЗАЩИТА: Проверка подписи Robokassa ---
    # password_2 = os.getenv('PASSWORD_2')
    # if not password_2:
    #     logger.error("КРИТИЧЕСКИ: Не задан PASSWORD_2 в .env файле!")
    #     raise HTTPException(status_code=500, detail="Server misconfiguration")
    #
    # # Формируем строку: OutSum:InvId:Пароль#2
    # my_sign_base = f"{OutSum}:{InvId}:{password_2}"
    # my_signature = hashlib.md5(my_sign_base.encode("utf-8")).hexdigest()
    #
    # if SignatureValue.lower() != my_signature.lower():
    #     logger.error(f"Взлом/Ошибка подписи! Пришло: {SignatureValue}, Ожидалось: {my_signature}")
    #     raise HTTPException(status_code=400, detail="Bad signature")

    # --- ВСЯ МАГИЯ ТЕПЕРЬ ПРОИСХОДИТ ЗДЕСЬ ---
    await fulfill_subscription(session, bot, int(InvId), email=EMail)

    return f'OK{InvId}'


# ==========================================
# 3. ФОНОВЫЕ ЗАДАЧИ (БЕЗОПАСНЫЕ ДЛЯ САЙТА)
# ==========================================
async def check_subscription_expiry(bot: Bot):
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if not user.sub_end: continue
            days_left = (user.sub_end - today).days

            # ВАЖНО: Проверяем, есть ли у юзера Телеграм
            if user.telegram_id:
                try:
                    if days_left == 3:
                        await bot.send_message(user.telegram_id, f'⚠️ <b>Подписка истекает через 3 дня</b>')
                    elif days_left == 1:
                        await bot.send_message(user.telegram_id, f'🔔 <b>Подписка истекает завтра!</b>')
                    elif days_left == 0:
                        await bot.send_message(user.telegram_id, f'❌ <b>Срок действия подписки завершён</b>')
                except TelegramAPIError:
                    pass

            await asyncio.sleep(0.05)


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

            # Проверяем на блокировку, только если юзер из ТГ
            if user.telegram_id:
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
                        if user.telegram_id:
                            try:
                                await bot.send_message(user.telegram_id, "⚠️ Не удалось автоматически продлить подписку.")
                            except TelegramAPIError:
                                pass
            except Exception as e:
                logger.error(f"Критическая ошибка автопродления: {e}")

            await asyncio.sleep(0.5)


semaphore = asyncio.Semaphore(15)


async def reset_monthly_traffic(bot: Bot):
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        servers = await orm_get_servers(session)
        tariffs = await orm_get_tariffs(session)

        today = datetime.now()
        tariff_map = {t.id: t.trafic for t in tariffs}

        panels_info = [s for s in servers if s.need_gb]
        if not panels_info:
            return

        GB = 1073741824
        success_count = 0
        error_count = 0

        logger.info("🔄 Начинаю плавный сброс месячного трафика...")

        for user in users:
            if not user.sub_end or user.sub_end < today:
                continue

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

                result = await safe_process_single_traffic(
                    panel, us, user, base_limit_gb, expiry_ms, GB
                )

                if result:
                    success_count += 1
                else:
                    error_count += 1

                await asyncio.sleep(0.1)

        logger.info(f"✅ Сброс завершен. Успешно: {success_count}, Ошибок: {error_count}")


async def safe_process_single_traffic(panel, us, user, base_limit_gb, expiry_ms, GB):
    email = f"{panel.name}_{us.id}"
    try:
        if not await panel.auth():
            return False

        traf = await panel.client_remain_trafic(us.tun_id)
        if traf is None or traf is False:
            logger.warning(f"⚠️ Клиент {email} не найден в панели {panel.name}. Пропускаем.")
            return False

        used_bytes = (traf[0] or 0) + (traf[1] or 0)
        total_bytes = (traf[2] or 0)
        remaining_gb = int(max(total_bytes - used_bytes, 0) // GB)
        new_total_gb = max(base_limit_gb, remaining_gb)

        # ВАЖНО: Если юзер с сайта, передаем пустую строку вместо "None" в панель
        safe_tg_id = str(user.telegram_id) if user.telegram_id else ""
        safe_name = user.name if user.name else f"Web User {user.id}"

        await panel.edit_client(
            uuid=us.tun_id,
            name=safe_name,
            email=email,
            limit_ip=user.ips or 1,
            expiry_time=expiry_ms,
            tg_id=safe_tg_id,
            total_gb=new_total_gb
        )
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

            # ВАЖНО: Проверяем, есть ли у юзера Телеграм
            if user.telegram_id:
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