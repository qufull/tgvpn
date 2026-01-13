from datetime import date, datetime, time
import os
import json
import hashlib
from typing import Union
from uuid import UUID, uuid4

from aiogram import Bot
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request 
from fastapi.templating import Jinja2Templates
from starlette.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.tg_bot_router.kbds.inline import succes_pay_btns
from app.utils.days_to_month import days_to_str
from app.database.engine import get_async_session, async_session_maker
from app.setup_logger import logger
from app.tg_bot_router.bot import bot
from app.database.queries import (
    orm_add_user_server,
    orm_change_user_tariff,
    orm_get_last_payment,
    orm_get_payment,
    orm_get_server,
    orm_get_servers,
    orm_get_tariff,
    orm_get_last_payment_id,
    orm_get_user,
    orm_get_user_by_tgid,
    orm_get_user_server,
    orm_get_user_server_by_ti,
    orm_get_user_servers,
    orm_new_payment,
    orm_update_user,
    orm_get_subscribers, orm_get_users
)
from app.utils.three_x_ui_api import ThreeXUIServer


payment_router = APIRouter(prefix="/payment")
templates = Jinja2Templates(directory='app/payment_router/templates')


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

    receipt =  {
          "sno":"patent",
          "items": [
            {
              "name": f"подписка skynetvpn на {days_to_str(tariff.days)}",
              "quantity": 1,
              "sum": float(tariff.price),
              "payment_method": "full_payment",
              "payment_object": "service",
              "tax": "vat10"
            },
          ]
        }

    print(json.dumps(receipt, ensure_ascii=False))
    base_string = f"{os.getenv('SHOP_ID')}:{tariff.price}:{invoice_id}:{json.dumps(receipt, ensure_ascii=False)}:{os.getenv('PASSWORD_1')}"
    signature_value = hashlib.md5(base_string.encode("utf-8")).hexdigest()
    await orm_new_payment(session, tariff_id=tariff.id, user_id=user.id)

    return templates.TemplateResponse(
    "/payment_page.html", 
        {
            "request": request, 
            "price": tariff.price, 
            "time": days_to_str(tariff.days).split(' ')[0], 
            "show_time": days_to_str(tariff.days), 
            "pay_data": json.dumps(receipt, ensure_ascii=False), 
            "shop_id": os.getenv("SHOP_ID"), 
            "signature_value": signature_value, 
            "invoice_id": invoice_id
        }
    )


@payment_router.post("/get_payment")
async def choose_server(
        OutSum: Union[str, float, int] = Form(...),
        InvId: Union[str, float, int] = Form(...),
        Fee: Union[str, float, int, None] = Form(None),
        SignatureValue: str = Form(...),
        EMail: Union[str, None] = Form(None),
        PaymentMethod: Union[str, None] = Form(None),
        IncCurrLabel: Union[str, None] = Form(None),
        Shp_Receipt: Union[str, None] = Form(None),
        session: AsyncSession = Depends(get_async_session)
    ):
    payment = await orm_get_payment(session, int(InvId))
    if not payment:
        raise HTTPException(status_code=404, detail="Оплата не найдена")

    user = payment.user

    try:
        await orm_update_user(session, user.id, {'email': EMail})
    except:
        logger.error("Не удалось сменить почту пользователя")

    tariff = await orm_get_tariff(session, payment.tariff_id)
    user_servers = await orm_get_user_servers(session, user.id)
    servers = await orm_get_servers(session)
    threex_panels = []
    for i in servers:
        threex_panels.append(ThreeXUIServer(
            i.id,
            i.url,
            i.indoub_id,
            i.login,
            i.password,
            i.need_gb
        ))

    if not payment.recurent:

        if not user_servers:
            today_datetime = datetime.combine(date.today(), time.min)
            end_datetime = today_datetime + relativedelta(days=tariff.days)
            end_timestamp = int(end_datetime.timestamp() * 1000)

            for i in threex_panels:
                uuid = uuid4()
                await orm_add_user_server(
                    session, 
                    server_id=i.id,
                    tun_id = str(uuid),
                    user_id = user.id,
                )
                user_server = await orm_get_user_server_by_ti(session, str(uuid))
                server = await orm_get_server(session, user_server.server_id)
                await i.add_client(
                    uuid=str(uuid),
                    email=server.name + '_' + str(user_server.id),
                    limit_ip=tariff.ips,
                    expiry_time=end_timestamp,
                    tg_id=user.telegram_id,
                    name=user.name,
                    total_gb=30 if i.need_gb else 0
                )
            
            await orm_change_user_tariff(
                session, 
                tariff_id=tariff.id,
                user_id=user.id,
                sub_end=end_datetime
            )

        else:
            today_datetime = datetime.combine(date.today(), time.min)
            if user.sub_end > today_datetime:
                end_datetime = user.sub_end + relativedelta(days=tariff.days)
            else:
                end_datetime = today_datetime + relativedelta(days=tariff.days)
            end_timestamp = int(end_datetime.timestamp() * 1000)

            for i in threex_panels:
                user_server = await orm_get_user_server(session, user.id, i.id)
                server = await orm_get_server(session, user_server.server_id)
                await i.edit_client(
                    uuid=user_server.tun_id,
                    email=server.name + '_' + str(user_server.id),
                    limit_ip=tariff.ips,
                    name=user.name,
                    expiry_time=end_timestamp,
                    tg_id=user.telegram_id,
                    total_gb=tariff.trafic if i.need_gb else 0
                )
            
            await orm_change_user_tariff(
                session, 
                tariff_id=tariff.id,
                user_id=user.id,
                sub_end=end_datetime
            )

        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
            
        await bot.send_message(
            user.telegram_id, 
            f"<b>✅ Спасибо! Вы оформили подписку!</b>\n\n🗓 Ваша подписка активна до {user.sub_end.date().strftime('%d.%m.%Y')}\n\n<b>Для автоматического подключения нажмите кнопку \"Подключиться\"\n\nДля ручного ввода скопируйте ключ. Для копирования ключа нажмите на него 1 раз. ⬇️</b>\n<code>{url}</code>",
            reply_markup=succes_pay_btns(user)
        )
        
    else:
        today_datetime = datetime.combine(date.today(), time.min)
        end_datetime = today_datetime + relativedelta(days=tariff.days)
        end_timestamp = int(end_datetime.timestamp() * 1000)

        for i in threex_panels:
            user_server = await orm_get_user_server(session, user.id, i.id)
            await i.edit_client(
                uuid=user_server.tun_id,
                email=user.name,
                limit_ip=tariff.ips,
                expiry_time=end_timestamp,
                tg_id=user.telegram_id,
                name=user.name,
                total_gb=tariff.trafic if i.need_gb else 0
            )
        
        await orm_change_user_tariff(
            session, 
            tariff_id=tariff.id,
            user_id=user.id,
            sub_end=end_datetime
        )

        url = f"{os.getenv('URL')}/api/get_sub?token={user.id}"
        await bot.send_message(
            user.telegram_id,
            f"<b>🔄 Ваша подписка успешно продлена!</b>\n\n"
            f"🗓 Подписка активна до {end_datetime.strftime('%d.%m.%Y')}\n"
            f"💰 Сумма списания: {tariff.price}₽\n\n"
            f"<b>Для автоматического подключения нажмите кнопку \"Подключиться\"\n\n"
            f"Для ручного ввода скопируйте ключ. Для копирования ключа нажмите на него 1 раз. ⬇️</b>\n"
            f"<code>{url}</code>",
            reply_markup=succes_pay_btns(user),
        )
    return f'OK{InvId}'


async def check_subscription_expiry(bot: Bot):
    """
    Проверяет подписки и отправляет уведомления:
    - За 3 дня до окончания
    - За 1 день до окончания
    - После окончания подписки
    """
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if not user.sub_end:
                continue

            # Пропускаем активных подписчиков (с автопродлением)
            if user.tariff_id and user.tariff_id > 0:
                continue

            days_left = (user.sub_end - today).days

            try:
                if days_left == 3:
                    await bot.send_message(
                        user.telegram_id,
                        f'⚠️ <b>Ваша подписка истекает через 3 дня</b>\n\n'
                        f'📅 Дата окончания: {user.sub_end.strftime("%d.%m.%Y")}\n\n'
                        f'Пожалуйста, заранее позаботьтесь о продлении, чтобы всегда оставаться на связи.\n\n'
                        f'👉 Для продления нажмите /start и выберите "Купить подписку"'
                    )
                    logger.info(f"Отправлено уведомление за 3 дня: {user.telegram_id}")

                elif days_left == 1:
                    await bot.send_message(
                        user.telegram_id,
                        f'🔔 <b>Ваша подписка истекает завтра!</b>\n\n'
                        f'📅 Дата окончания: {user.sub_end.strftime("%d.%m.%Y")}\n\n'
                        f'Не забудьте продлить подписку, чтобы не потерять доступ к VPN.\n\n'
                        f'👉 Для продления нажмите /start и выберите "Купить подписку"'
                    )
                    logger.info(f"Отправлено уведомление за 1 день: {user.telegram_id}")

                elif days_left == 0:
                    await bot.send_message(
                        user.telegram_id,
                        f'❌ <b>Срок действия вашей подписки завершён</b>\n\n'
                        f'Чтобы продолжить пользоваться SkynetVPN, оформите новую подписку.\n\n'
                        f'👉 Для оформления нажмите /start и выберите "Купить подписку"'
                    )
                    logger.info(f"Отправлено уведомление об окончании: {user.telegram_id}")

            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление {user.telegram_id}: {e}")


async def recurent_payment(bot: Bot):
    """Автоматическое продление подписок через Robokassa"""
    async with async_session_maker() as session:
        users = await orm_get_subscribers(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            # Проверяем: tariff_id > 0 и подписка истекла
            if user.tariff_id != 0 and user.sub_end and user.sub_end <= today:
                logger.info(f"Автопродление для {user.name} (tg:{user.telegram_id})")

                # Получаем последний НЕрекуррентный платёж (первичный)
                last_payment = await orm_get_last_payment(session, user.id)
                if not last_payment:
                    logger.warning(f"Нет предыдущего платежа для {user.telegram_id}")
                    continue

                tariff = await orm_get_tariff(session, tariff_id=user.tariff_id)
                if not tariff:
                    logger.warning(f"Тариф {user.tariff_id} не найден")
                    continue

                # Создаём новый рекуррентный платёж
                await orm_new_payment(
                    session,
                    tariff_id=user.tariff_id,
                    user_id=user.id,
                    recurent=True
                )
                invoice_id = await orm_get_last_payment_id(session)

                receipt = {
                    "sno": "patent",
                    "items": [
                        {
                            "name": f"Подписка SkynetVPN на {days_to_str(tariff.days)}",
                            "quantity": 1,
                            "sum": float(tariff.price),
                            "payment_method": "full_payment",
                            "payment_object": "service",
                            "tax": "vat10"
                        },
                    ]
                }

                # Подпись для рекуррентного платежа
                base_string = f"{os.getenv('SHOP_ID')}:{tariff.price}:{invoice_id}:{os.getenv('PASSWORD_1')}"
                signature_value = hashlib.md5(base_string.encode("utf-8")).hexdigest()

                try:
                    async with AsyncClient() as client:
                        response = await client.post(
                            'https://auth.robokassa.ru/Merchant/Recurring',
                            data={
                                "MerchantLogin": os.getenv('SHOP_ID'),
                                "InvoiceID": int(invoice_id),
                                "PreviousInvoiceID": int(last_payment),
                                "Description": "Автопродление подписки SkynetVPN",
                                "SignatureValue": signature_value,
                                "OutSum": float(tariff.price),
                            }
                        )

                        logger.info(f"Robokassa ответ для {user.telegram_id}: {response.status_code} - {response.text}")

                        if response.status_code == 200:
                            # Robokassa приняла запрос, ждём callback на /payment/get_payment
                            logger.info(f"Запрос на автопродление отправлен для {user.telegram_id}")
                        else:
                            logger.error(f"Ошибка Robokassa: {response.text}")
                            await bot.send_message(
                                user.telegram_id,
                                "⚠️ Не удалось автоматически продлить подписку. "
                                "Пожалуйста, продлите вручную: /start → Купить подписку"
                            )

                except Exception as e:
                    logger.error(f"Ошибка автопродления для {user.telegram_id}: {e}")


async def reset_monthly_traffic(bot: Bot):
    """Ежемесячный сброс трафика на сервере обхода белых списков"""
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        servers = await orm_get_servers(session)
        today = datetime.now()

        # Создаём панели только для need_gb серверов
        panels = []
        for s in servers:
            if s.need_gb:
                panels.append(ThreeXUIServer(
                    s.id, s.url, s.indoub_id, s.login, s.password, s.need_gb, s.name
                ))

        if not panels:
            logger.info("Нет серверов с need_gb для сброса трафика")
            return

        reset_count = 0

        for user in users:
            # Только активные подписчики
            if not user.sub_end or user.sub_end < today:
                continue

            user_servers = await orm_get_user_servers(session, user.id)

            for us in user_servers:
                for panel in panels:
                    if panel.id != us.server_id:
                        continue

                    try:
                        # Формируем email как в других местах
                        email = panel.name + '_' + str(us.id)
                        result = await panel.reset_client_traffic(email)
                        if result:
                            reset_count += 1
                            logger.info(f"Сброшен трафик для {user.name} на {panel.name}")
                    except Exception as e:
                        logger.error(f"Ошибка сброса трафика для {user.name}: {e}")
                    break

        logger.info(f"Ежемесячный сброс трафика завершён. Сброшено: {reset_count}")


async def notify_expired_users(bot: Bot):
    """Уведомления пользователям с истёкшей подпиской (5, 15, 30 дней)"""
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            if not user.sub_end:
                continue

            # Только истёкшие подписки
            if user.sub_end > today:
                continue

            days_expired = (today - user.sub_end).days

            try:
                if days_expired == 5:
                    await bot.send_message(
                        user.telegram_id,
                        '⚠️ <b>Ваша подписка истекла 5 дней назад</b>\n\n'
                        'Мы скучаем по вам! Продлите подписку, чтобы снова пользоваться SkynetVPN.\n\n'
                        '👉 Для продления нажмите /start',
                        parse_mode='HTML'
                    )
                    logger.info(f"Уведомление 5 дней: {user.name}")

                elif days_expired == 15:
                    await bot.send_message(
                        user.telegram_id,
                        '📢 <b>Прошло уже 15 дней без SkynetVPN</b>\n\n'
                        'Не забывайте о безопасности в интернете! '
                        'Продлите подписку и получите доступ ко всем серверам.\n\n'
                        '👉 Для продления нажмите /start',
                        parse_mode='HTML'
                    )
                    logger.info(f"Уведомление 15 дней: {user.name}")

                elif days_expired == 30:
                    await bot.send_message(
                        user.telegram_id,
                        '🔔 <b>Месяц без SkynetVPN!</b>\n\n'
                        'Мы всё ещё ждём вас. Возвращайтесь — '
                        'быстрый и безопасный VPN всегда к вашим услугам.\n\n'
                        '👉 Для продления нажмите /start',
                        parse_mode='HTML'
                    )
                    logger.info(f"Уведомление 30 дней: {user.name}")

            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление {user.telegram_id}: {e}")

        logger.info("Проверка истёкших подписок завершена")

