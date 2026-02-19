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
    orm_get_subscribers,
    orm_get_users
)
from app.utils.three_x_ui_api import ThreeXUIServer


payment_router = APIRouter(prefix="/payment")
templates = Jinja2Templates(directory='app/payment_router/templates')


async def preserve_total_gb(panel: ThreeXUIServer, *, uuid: str, tariff_gb: int) -> int:
    """
    На need_gb панелях НЕ уменьшаем лимит:
    ставим max(текущий лимит в панели, тарифный лимит или 30).
    На остальных панелях возвращаем 0.
    """
    if not panel.need_gb:
        return 0

    base_gb = int(tariff_gb) if tariff_gb else 30
    try:
        current_gb = await panel.get_total_gb(uuid)
    except Exception:
        current_gb = 0

    return max(current_gb, base_gb)


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

    # Если days=0 и ips=0 — считаем это доп.продуктом (докупка трафика).
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
                "tax": "vat10"
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
    except Exception:
        logger.error("Не удалось сменить почту пользователя")

    tariff = await orm_get_tariff(session, payment.tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    user_servers = await orm_get_user_servers(session, user.id)
    servers = await orm_get_servers(session)

    threex_panels = []
    for s in servers:
        threex_panels.append(ThreeXUIServer(
            s.id,
            s.url,
            s.indoub_id,
            s.login,
            s.password,
            s.need_gb,
            s.name,  # важно для единого формата email
        ))

    is_addon = (tariff.days == 0 and tariff.ips == 0)

    # --- ДОП ПРОДУКТ: докупка трафика (накопительно: current + add) ---
    if (not payment.recurent) and is_addon and (tariff.trafic or 0) > 0:
        now = datetime.now()
        if not user.sub_end or user.sub_end < now:
            await bot.send_message(
                user.telegram_id,
                "❌ Докупить трафик можно только при активной подписке.\n\n"
                "Открой /start → 🛍 Купить подписку.",
                parse_mode='HTML'
            )
            return f'OK{InvId}'

        add_gb = int(tariff.trafic)

        # Берём текущий лимит (в ГБ) по панелям need_gb и прибавляем add_gb.
        # Чтобы не урезать нигде — берём максимальный текущий лимит среди need_gb панелей.
        limits = []
        for panel in threex_panels:
            if not panel.need_gb:
                continue
            us = await orm_get_user_server(session, user.id, panel.id)
            if not us:
                continue
            try:
                cur_gb = await panel.get_total_gb(us.tun_id)
                if cur_gb > 0:
                    limits.append(cur_gb)
            except Exception as e:
                logger.error(f"[EXTRA_GB] Не удалось прочитать текущий лимит user={user.telegram_id} panel={panel.id}: {e}")

        current_limit_gb = max(limits) if limits else 30
        new_limit_gb = current_limit_gb + add_gb

        GB = 1073741824
        add_gb = int(tariff.trafic)

        changed = 0
        for panel in threex_panels:
            if not panel.need_gb:
                continue

            try:
                us = await orm_get_user_server(session, user.id, panel.id)
                if not us:
                    continue

                email = f"{panel.name}_{us.id}"

                # читаем up/down/total (байты)
                traf = await panel.client_remain_trafic(us.tun_id)
                if not traf:
                    # если не смогли прочитать — хотя бы увеличим от 30
                    current_total_bytes = 30 * GB
                else:
                    up, down, total = traf
                    current_total_bytes = int(total or 0)
                    # страховка: минимум 30ГБ
                    if current_total_bytes < 30 * GB:
                        current_total_bytes = 30 * GB

                new_total_bytes = current_total_bytes + add_gb * GB
                new_total_gb = int(new_total_bytes // GB)

                await panel.edit_client(
                    uuid=us.tun_id,
                    email=email,
                    limit_ip=user.ips,
                    expiry_time=int(user.sub_end.timestamp() * 1000),
                    tg_id=user.telegram_id,
                    name=user.name,
                    total_gb=new_total_gb,
                )

                changed += 1
            except Exception as e:
                logger.error(f"[EXTRA_GB] Не удалось применить докупку user={user.telegram_id} panel={panel.id}: {e}")

        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"

        await bot.send_message(
            user.telegram_id,
            (
                "✅ <b>Трафик добавлен!</b>\n\n"
                f"📦 Было: <b>{current_limit_gb} ГБ</b>\n"
                f"➕ Добавлено: <b>{add_gb} ГБ</b>\n"
                f"🏳️ Стало: <b>{new_limit_gb} ГБ</b>\n\n"
                "<b>Ваша ссылка на ключ. 🔑</b>\n\n"
                "Нажмите 1 раз чтобы скопировать:\n\n"
                f"<pre><code>{url}</code></pre>"
            ),
            parse_mode="HTML",
            reply_markup=succes_pay_btns_for_gb(user),
        )

        logger.info(f"[EXTRA_GB] user={user.telegram_id} add={add_gb} from={current_limit_gb} to={new_limit_gb} panels_changed={changed}")
        return f'OK{InvId}'

    # --- Обычная покупка/продление тарифа ---
    if not payment.recurent:

        # первая покупка (серверов ещё нет)
        if not user_servers:
            today_datetime = datetime.combine(date.today(), time.min)
            end_datetime = today_datetime + relativedelta(days=tariff.days)
            end_timestamp = int(end_datetime.timestamp() * 1000)

            for panel in threex_panels:
                uuid = uuid4()
                await orm_add_user_server(
                    session,
                    server_id=panel.id,
                    tun_id=str(uuid),
                    user_id=user.id,
                )
                user_server = await orm_get_user_server_by_ti(session, str(uuid))
                server = await orm_get_server(session, user_server.server_id)

                email = server.name + '_' + str(user_server.id)

                await panel.add_client(
                    uuid=str(uuid),
                    email=email,
                    limit_ip=tariff.ips,
                    expiry_time=end_timestamp,
                    tg_id=user.telegram_id,
                    name=user.name,
                    total_gb=30 if panel.need_gb else 0
                )

            await orm_change_user_tariff(
                session,
                ips=tariff.ips,
                tariff_id=tariff.id,
                user_id=user.id,
                sub_end=end_datetime
            )

        # продление/смена тарифа (серверы уже есть) — НЕ уменьшаем totalGB
        else:
            today_datetime = datetime.combine(date.today(), time.min)
            if user.sub_end and user.sub_end > today_datetime:
                end_datetime = user.sub_end + relativedelta(days=tariff.days)
            else:
                end_datetime = today_datetime + relativedelta(days=tariff.days)
            end_timestamp = int(end_datetime.timestamp() * 1000)

            for panel in threex_panels:
                user_server = await orm_get_user_server(session, user.id, panel.id)
                server = await orm_get_server(session, user_server.server_id)

                total_gb = await preserve_total_gb(panel, uuid=user_server.tun_id, tariff_gb=int(tariff.trafic or 0))

                await panel.edit_client(
                    uuid=user_server.tun_id,
                    email=server.name + '_' + str(user_server.id),
                    limit_ip=tariff.ips,
                    name=user.name,
                    expiry_time=end_timestamp,
                    tg_id=user.telegram_id,
                    total_gb=total_gb,
                )

            await orm_change_user_tariff(
                session,
                ips=tariff.ips,
                tariff_id=tariff.id,
                user_id=user.id,
                sub_end=end_datetime
            )

        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"

        await bot.send_message(
            user.telegram_id,
            f"<b>✅ Спасибо! Вы оформили подписку!</b>\n\n"
            f"🗓 Ваша подписка активна до {user.sub_end.date().strftime('%d.%m.%Y')}\n\n"
            f"<b>Для автоматического подключения нажмите кнопку \"Подключиться\"\n\n"
            f"Для ручного ввода скопируйте ключ. Для копирования ключа нажмите на него 1 раз. ⬇️</b>\n\n"
            f"<pre><code>{url}</code></pre>",
            reply_markup=succes_pay_btns(user),
            parse_mode='HTML'
        )

    # --- Рекуррентное продление — НЕ уменьшаем totalGB ---
    else:
        today_datetime = datetime.combine(date.today(), time.min)
        end_datetime = today_datetime + relativedelta(days=tariff.days)
        end_timestamp = int(end_datetime.timestamp() * 1000)

        for panel in threex_panels:
            user_server = await orm_get_user_server(session, user.id, panel.id)

            total_gb = await preserve_total_gb(panel, uuid=user_server.tun_id, tariff_gb=int(tariff.trafic or 0))

            await panel.edit_client(
                uuid=user_server.tun_id,
                email=user.name,
                limit_ip=tariff.ips,
                expiry_time=end_timestamp,
                tg_id=user.telegram_id,
                name=user.name,
                total_gb=total_gb,
            )

        await orm_change_user_tariff(
            session,
            ips=tariff.ips,
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
            parse_mode='HTML'
        )

    return f'OK{InvId}'


# ниже — твои функции check_subscription_expiry / recurent_payment / reset_monthly_traffic / notify_expired_users
# их можно оставить как есть (reset_monthly_traffic уже не трогает totalGB, только reset счётчика)

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

DAY10_ID = int(os.getenv("TARIFF_DAY10_ID", "0") or 0)
MONTH300_ID = int(os.getenv("TARIFF_MONTH300_ID", "0") or 0)


async def recurent_payment(bot: Bot):
    """Автоматическое продление подписок через Robokassa"""
    async with async_session_maker() as session:
        users = await orm_get_subscribers(session)
        today = datetime.combine(date.today(), time.min)

        for user in users:
            # Проверяем: есть тариф и подписка истекла
            if user.tariff_id == 0 or not user.sub_end or user.sub_end > today:
                continue

            logger.info(f"Автопродление для {user.name} (tg:{user.telegram_id})")

            # Последний НЕрекуррентный платёж (первичный)
            last_payment = await orm_get_last_payment(session, user.id)
            if not last_payment:
                logger.warning(f"Нет предыдущего платежа для {user.telegram_id}")
                continue

            # Определяем, какой тариф продлеваем
            renew_tariff_id = user.tariff_id

            # 🔁 ВАЖНО: переход с дневного тарифа на месячный
            if DAY10_ID and MONTH300_ID and user.tariff_id == DAY10_ID:
                renew_tariff_id = MONTH300_ID

            tariff = await orm_get_tariff(session, tariff_id=renew_tariff_id)
            if not tariff:
                logger.warning(f"Тариф {renew_tariff_id} не найден")
                continue

            # Создаём новый рекуррентный платёж
            await orm_new_payment(
                session,
                tariff_id=renew_tariff_id,
                user_id=user.id,
                recurent=True
            )
            invoice_id = await orm_get_last_payment_id(session)

            # --- ИСПРАВЛЕНИЕ 1: Строгая типизация суммы ---
            # Приводим к строке с 2 знаками, чтобы MD5 и OutSum совпадали
            price_str = "{:.2f}".format(float(tariff.price))

            # 📌 Описание в чеке
            item_name = f"Подписка SkynetVPN на {days_to_str(tariff.days)}"
            if DAY10_ID and MONTH300_ID and user.tariff_id == DAY10_ID:
                item_name += " (после 1 дня автоматически подключится тариф 300 ₽/месяц)"

            # Обрезаем имя, если слишком длинное (требование касс - макс 128 символов)
            item_name = item_name[:128]

            # --- ИСПРАВЛЕНИЕ 2: Формирование JSON чека ---
            receipt = {
                "sno": "patent",
                # Проверьте, соответствует ли это настройкам в ЛК Робокассы (usn_income, patent и т.д.)
                "items": [
                    {
                        "name": item_name,
                        "quantity": 1,
                        "sum": price_str,  # Передаем строкой
                        "payment_method": "full_payment",
                        "payment_object": "service",
                        "tax": "none"  # Проверьте ставку НДС (vat10, vat20, none)
                    },
                ]
            }
            # Кодируем чек. separators убирает пробелы, чтобы URL не ломался
            receipt_json = json.dumps(receipt, ensure_ascii=False, separators=(',', ':'))

            # --- ИСПРАВЛЕНИЕ 3: Расчет подписи ---
            # Используем price_str вместо tariff.price
            base_string = f"{os.getenv('SHOP_ID')}:{price_str}:{invoice_id}:{os.getenv('PASSWORD_1')}"
            signature_value = hashlib.md5(base_string.encode("utf-8")).hexdigest()

            try:
                async with AsyncClient() as client:
                    # Формируем данные
                    data_payload = {
                        "MerchantLogin": os.getenv("SHOP_ID"),
                        "InvoiceID": str(invoice_id),
                        "PreviousInvoiceID": str(last_payment),
                        "Description": "SkynetVPN Auto-renew",  # Лучше латиницей
                        "SignatureValue": signature_value,
                        "OutSum": price_str,
                        "Receipt": receipt_json  # <--- ИСПРАВЛЕНИЕ 4: Раскомментировано!
                    }

                    # Логируем данные перед отправкой для отладки
                    logger.info(f"DEBUG PAYLOAD for {user.telegram_id}: {data_payload}")

                    response = await client.post(
                        "https://auth.robokassa.ru/Merchant/Recurring",
                        data=data_payload,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'}
                    )

                    logger.info(
                        f"Robokassa ответ для {user.telegram_id}: {response.status_code} - {response.text}"
                    )

                    if response.status_code == 200:
                        logger.info(f"Запрос на автопродление отправлен для {user.telegram_id}")
                    else:
                        # Если ошибка не 200, логируем тело ответа
                        logger.error(f"Ошибка Robokassa: {response.text}")

                        # --- ИСПРАВЛЕНИЕ 5: Защита от блокировки бота ---
                        try:
                            await bot.send_message(
                                user.telegram_id,
                                "⚠️ Не удалось автоматически продлить подписку. "
                                "Пожалуйста, продлите вручную: /start → Купить подписку"
                            )
                        except Exception as e_msg:
                            # Если юзер заблокировал бота, просто пишем в лог и идем дальше
                            logger.warning(
                                f"Не удалось отправить сообщение пользователю {user.telegram_id} (возможно бот заблокирован): {e_msg}")

            except Exception as e:
                logger.error(f"Критическая ошибка автопродления для {user.telegram_id}: {e}")


async def reset_monthly_traffic(bot: Bot):
    """Ежемесячный сброс трафика на сервере обхода белых списков.
    Если у пользователя осталось больше свободных ГБ, чем заложено в его тарифе,
    оставляем этот остаток. В противном случае устанавливаем стандартный лимит тарифа.
    Затем сбрасываем статистику использования (reset usage).
    """
    async with async_session_maker() as session:
        users = await orm_get_users(session)
        servers = await orm_get_servers(session)
        today = datetime.now()

        panels = []
        for s in servers:
            if s.need_gb:
                panels.append(ThreeXUIServer(
                    s.id, s.url, s.indoub_id, s.login, s.password, s.need_gb, s.name
                ))

        if not panels:
            logger.info("Нет серверов с need_gb для сброса трафика")
            return

        GB = 1073741824
        reset_count = 0
        bonus_count = 0

        for user in users:
            if not user.sub_end or user.sub_end < today:
                continue

            # 1. Получаем тариф пользователя, чтобы узнать его базовый лимит
            tariff = await orm_get_tariff(session, user.tariff_id)
            # Если тариф по какой-то причине не найден, берем 30 ГБ как страховку
            base_limit_gb = tariff.trafic if tariff else 30

            user_servers = await orm_get_user_servers(session, user.id)

            for us in user_servers:
                for panel in panels:
                    if panel.id != us.server_id:
                        continue

                    email = f"{panel.name}_{us.id}"

                    try:
                        traf = await panel.client_remain_trafic(us.tun_id)
                        if not traf:
                            break

                        up, down, total = traf
                        up = up or 0
                        down = down or 0
                        total = total or 0

                        # Считаем остаток
                        used = up + down
                        remaining = total - used
                        if remaining < 0:
                            remaining = 0

                        remaining_gb = int(remaining // GB)

                        # 2. Сравниваем остаток с лимитом ИМЕННО ЭТОГО тарифа
                        new_total_gb = max(base_limit_gb, remaining_gb)

                        # Обновляем лимит клиента в панели
                        client = await panel.get_client_by_uuid(us.tun_id)
                        if not client:
                            logger.warning(
                                f"Не найден клиент для обновления лимита: tg={user.telegram_id} panel={panel.id} uuid={us.tun_id}")
                        else:
                            await panel.edit_client(
                                uuid=us.tun_id,
                                name=client.get("comment") or user.name,
                                email=client.get("email") or email,
                                limit_ip=int(client.get("limitIp") or user.ips or 1),
                                expiry_time=int(client.get("expiryTime") or int(user.sub_end.timestamp() * 1000)),
                                tg_id=str(client.get("tgId") or user.telegram_id),
                                total_gb=new_total_gb,
                            )

                            if remaining_gb > base_limit_gb:
                                bonus_count += 1
                                logger.info(
                                    f"Сохранён остаток >{base_limit_gb}ГБ: tg={user.telegram_id} panel={panel.name} "
                                    f"remaining={remaining_gb}GB -> new_total={new_total_gb}GB"
                                )

                        # Сбрасываем использованный трафик до 0 (теперь лимит = new_total_gb)
                        result = await panel.reset_client_traffic(email)
                        if result:
                            reset_count += 1
                            if remaining_gb <= base_limit_gb:
                                logger.info(
                                    f"Сброшен трафик для {user.name} на {panel.name} до базовых {base_limit_gb}GB")

                    except Exception as e:
                        logger.error(f"Ошибка обновления трафика для {user.name} ({user.telegram_id}): {e}")

                    break  # Переходим к следующему серверу пользователя

        logger.info(
            f"Ежемесячный сброс завершён. Выполнен сброс: {reset_count}, сохранено увеличенных остатков: {bonus_count}")

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

