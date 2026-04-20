import hashlib
import json
import os
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Union

from app.database.engine import get_async_session
from app.database.queries import (
    orm_get_user_by_email,
    orm_create_user_from_site,
    orm_get_tariff,
    orm_get_last_payment_id,
    orm_new_payment,
    orm_get_payment
)
from app.payment_router.subscription_service import fulfill_subscription
from app.tg_bot_router.bot import bot
from app.utils.days_to_month import days_to_str

site_router = APIRouter(prefix="/site")


# ==========================================
# 1. СТАТИЧНЫЕ СТРАНИЦЫ (Без изменений)
# ==========================================
@site_router.get('/')
async def main_page():
    return FileResponse('app/site_router/templates/index.html')


@site_router.get('/privacy_policy')
async def private_policy_page():
    return FileResponse('app/site_router/templates/privacy-policy.html')


@site_router.get('/terms_of_service')
async def terms_of_service_page():
    return FileResponse('app/site_router/templates/terms-of-service.html')


# ==========================================
# 1. ГЕНЕРАЦИЯ ОПЛАТЫ ДЛЯ САЙТА
# ==========================================
@site_router.post('/pay_on_site')
async def pay_from_site(
    email: str = Form(...),
    tariff_id: int = Form(...),
    agreement: str = Form(None),
    session: AsyncSession = Depends(get_async_session)
):
    # Ищем пользователя по почте или создаем нового
    user = await orm_get_user_by_email(session, email)
    if not user:
        user = await orm_create_user_from_site(session, email)

    tariff = await orm_get_tariff(session, tariff_id=tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Тариф не найден")

    invoice_id = await orm_get_last_payment_id(session) + 1
    await orm_new_payment(session, tariff_id=tariff.id, user_id=user.id)

    is_addon = (tariff.days == 0 and tariff.ips == 0)
    item_name = f"доп. трафик {tariff.trafic} ГБ" if is_addon else f"подписка skynetvpn на {days_to_str(tariff.days)}"

    receipt = {
        "sno": "patent",
        "items": [{
            "name": item_name[:128],
            "quantity": 1,
            "sum": float(tariff.price),
            "payment_method": "full_payment",
            "payment_object": "service",
            "tax": "none"
        }]
    }
    receipt_json = json.dumps(receipt, ensure_ascii=False)

    # ИСПОЛЬЗУЕМ КЛЮЧИ ОТ МАГАЗИНА САЙТА
    shop_id = os.getenv('SITE_SHOP_ID')
    password_1 = os.getenv('SITE_PASSWORD_1')

    sign_str = f"{shop_id}:{tariff.price}:{invoice_id}:{receipt_json}:{password_1}"
    signature_value = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

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
            <input type="hidden" name="Email" value="{email}">
            <input type="hidden" name="Recurring" value="true">
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ==========================================
# 2. WEBHOOK ДЛЯ САЙТА (Прием ответа от кассы)
# ==========================================
@site_router.post("/get_payment")
async def site_robokassa_webhook(
        OutSum: str = Form(...),
        InvId: str = Form(...),
        SignatureValue: str = Form(...),
        EMail: Union[str, None] = Form(None),
        session: AsyncSession = Depends(get_async_session)
):
    # ПРОВЕРЯЕМ ПО ПАРОЛЮ #2 ОТ САЙТА
    password_2 = os.getenv('SITE_PASSWORD_2')
    if not password_2:
        raise HTTPException(status_code=500, detail="Site Robokassa misconfiguration")

    my_sign_base = f"{OutSum}:{InvId}:{password_2}"
    my_signature = hashlib.md5(my_sign_base.encode("utf-8")).hexdigest()

    if SignatureValue.lower() != my_signature.lower():
        raise HTTPException(status_code=400, detail="Bad signature")

    # Начисляем подписку через нашу единую функцию
    await fulfill_subscription(session, bot, int(InvId), email=EMail)

    return f'OK{InvId}'


# ==========================================
# 3. СТРАНИЦА УСПЕХА ДЛЯ САЙТА
# ==========================================
@site_router.get('/success', response_class=HTMLResponse)
async def payment_success(
        request: Request,
        InvId: int,
        session: AsyncSession = Depends(get_async_session)
):
    payment = await orm_get_payment(session, InvId)
    if not payment:
        return HTMLResponse("<h1>Ошибка: Платеж не найден</h1>")

    user = payment.user
    subscription_url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
    sub_end_str = user.sub_end.strftime('%d.%m.%Y') if user.sub_end else "активируется..."
    bot_username = os.getenv("BOT_USERNAME", "skynetaivpn_bot")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Успешная оплата</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 20px; background: #f9f9f9; color: #333; }}
            .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 0 15px rgba(0,0,0,0.1); max-width: 500px; margin: 40px auto; }}
            .key-box {{ background: #f4f4f4; padding: 15px; border-radius: 5px; font-family: monospace; word-break: break-all; margin: 20px 0; border: 1px dashed #ccc; }}
            .btn {{ background: #5C81F0; color: white; text-decoration: none; padding: 12px 20px; border-radius: 5px; display: inline-block; font-weight: bold; width: 100%; box-sizing: border-box; }}
            h1 {{ color: #4CAF50; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✅ Оплата успешна!</h1>
            <p>Ваша подписка SkynetVPN активна до: <b>{sub_end_str}</b></p>
            <p>Ваш ключ доступа (скопируйте и вставьте в приложение):</p>
            <div class="key-box">{subscription_url}</div>
            <a href="https://t.me/{bot_username}" class="btn">Открыть Telegram-бота</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)