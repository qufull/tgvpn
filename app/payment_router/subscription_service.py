import asyncio
import os
from datetime import date, datetime, time
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.queries import (
    orm_get_payment,
    orm_get_tariff,
    orm_update_user,
    orm_get_user_servers,
    orm_get_servers,
    orm_add_user_server,
    orm_get_user_server_by_ti,
    orm_change_user_tariff,
    orm_get_user_by_tgid,
    orm_add_referral_bonus,
    orm_get_admins
)
from app.utils.three_x_ui_api import ThreeXUIServer
from app.tg_bot_router.kbds.inline import succes_pay_btns, succes_pay_btns_for_gb
from app.setup_logger import logger


async def preserve_total_gb(panel: ThreeXUIServer, *, uuid: str, tariff_gb: int) -> int:
    """Вспомогательная функция сохранения/расчета трафика"""
    if not panel.need_gb:
        return 0
    base_gb = int(tariff_gb) if tariff_gb else 30
    current_gb = await panel.get_total_gb(uuid)
    return max(current_gb or 0, base_gb)


async def _apply_referral_bonus(session: AsyncSession, user, tariff, bot: Bot):
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
        # У реферера точно есть telegram_id, так что тут безопасно
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
                    f"Пригласил: <code>{user.invited_by}</code> ({referrer_name})\n"
                    f"Новый пользователь: <code>{user.telegram_id or 'Сайт'}</code> ({user.name or 'Без имени'})\n"
                    f"Бонус: <b>{bonus_days} дней</b>",
                    parse_mode='HTML'
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Ошибка реферального бонуса: {e}")


async def fulfill_subscription(session: AsyncSession, bot: Bot, invoice_id: int, email: str = None) -> bool:
    """
    Главная функция начисления подписки или трафика.
    """
    payment = await orm_get_payment(session, int(invoice_id))
    if not payment:
        logger.error(f"Оплата {invoice_id} не найдена")
        return False

    user = payment.user

    # Если оплата с сайта и передана почта - привязываем к аккаунту
    if email:
        try:
            await orm_update_user(session, user.id, {'email': email})
        except Exception:
            logger.error("Не удалось сменить почту пользователя")

    tariff = await orm_get_tariff(session, payment.tariff_id)
    if not tariff:
        logger.error("Тариф не найден")
        return False

    user_servers = await orm_get_user_servers(session, user.id)
    servers = await orm_get_servers(session)
    threex_panels = [
        ThreeXUIServer(s.id, s.url, s.indoub_id, s.login, s.password, s.need_gb, s.name)
        for s in servers
    ]

    is_addon = (tariff.days == 0 and tariff.ips == 0)

    # Безопасные данные для панели 3x-ui (для юзеров с сайта)
    safe_tg_id = str(user.telegram_id) if user.telegram_id else ""
    safe_name = user.name or user.email or f"Web_{user.id}"

    # ==========================================
    # ЛОГИКА 1: ДОКУПКА ТРАФИКА
    # ==========================================
    if (not payment.recurent) and is_addon and (tariff.trafic or 0) > 0:
        now = datetime.now()
        if not user.sub_end or user.sub_end < now:
            if user.telegram_id:
                try:
                    await bot.send_message(
                        user.telegram_id,
                        "❌ Докупить трафик можно только при активной подписке.\n\nОткрой /start → 🛍 Купить подписку.",
                        parse_mode='HTML'
                    )
                except TelegramAPIError:
                    pass
            return True

        add_gb = int(tariff.trafic)
        GB = 1073741824

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

        async def update_gb(p, us):
            panel_email = f"{p.name}_{us.id}"
            traf = await p.client_remain_trafic(us.tun_id)
            current_total_bytes = int((traf[2] if traf else 0) or 30 * GB)
            if current_total_bytes < 30 * GB: current_total_bytes = 30 * GB
            new_total_gb = int((current_total_bytes + add_gb * GB) // GB)

            await p.edit_client(
                uuid=us.tun_id, email=panel_email, limit_ip=user.ips,
                expiry_time=int(user.sub_end.timestamp() * 1000),
                tg_id=safe_tg_id, name=safe_name, total_gb=new_total_gb,
            )

        update_tasks = []
        for panel in threex_panels:
            if panel.need_gb:
                us = next((us for us in user_servers if us.server_id == panel.id), None)
                if us: update_tasks.append(update_gb(panel, us))

        await asyncio.gather(*update_tasks, return_exceptions=True)

        url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"
        if user.telegram_id:
            try:
                await bot.send_message(
                    user.telegram_id,
                    f"✅ <b>Трафик добавлен!</b>\n\n📦 Было: <b>{current_limit_gb} ГБ</b>\n➕ Добавлено: <b>{add_gb} ГБ</b>\n🏳️ Стало: <b>{new_limit_gb} ГБ</b>\n\n<b>Ваша ссылка на ключ. 🔑</b>\nНажмите 1 раз чтобы скопировать:\n\n<pre><code>{url}</code></pre>",
                    parse_mode="HTML", reply_markup=succes_pay_btns_for_gb(user)
                )
            except TelegramAPIError:
                pass
        return True

    # ==========================================
    # ЛОГИКА 2: ПОКУПКА ИЛИ ПРОДЛЕНИЕ ПОДПИСКИ
    # ==========================================
    today_datetime = datetime.combine(date.today(), time.min)
    if user_servers and user.sub_end and user.sub_end > today_datetime and not payment.recurent:
        end_datetime = user.sub_end + relativedelta(days=tariff.days)
    else:
        end_datetime = today_datetime + relativedelta(days=tariff.days)

    end_timestamp = int(end_datetime.timestamp() * 1000)

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

    async def process_panel(item):
        panel = item["panel"]
        us = item["us"]
        panel_email = f"{panel.name}_{us.id}"

        if item["is_new"]:
            await panel.add_client(
                uuid=us.tun_id, email=panel_email, limit_ip=tariff.ips,
                expiry_time=end_timestamp, tg_id=safe_tg_id,
                name=safe_name, total_gb=30 if panel.need_gb else 0
            )
        else:
            total_gb = await preserve_total_gb(panel, uuid=us.tun_id, tariff_gb=int(tariff.trafic or 0))
            await panel.edit_client(
                uuid=us.tun_id, email=panel_email, limit_ip=tariff.ips, name=safe_name,
                expiry_time=end_timestamp, tg_id=safe_tg_id, total_gb=total_gb,
            )
            if panel.need_gb:
                try:
                    await panel.reset_client_traffic(panel_email)
                except Exception as e:
                    logger.error(f"Не удалось сбросить трафик: {e}")

    api_tasks = [process_panel(item) for item in panels_to_setup]
    await asyncio.gather(*api_tasks, return_exceptions=True)

    # Обновляем юзера и начисляем бонусы
    await orm_change_user_tariff(session, ips=tariff.ips, tariff_id=tariff.id, user_id=user.id, sub_end=end_datetime)

    if not payment.recurent:
        await _apply_referral_bonus(session, user, tariff, bot)

    # Отправляем уведомление только если есть телеграм
    url = f"{os.getenv('URL')}/api/subscribtion?user_token={user.id}"

    if user.telegram_id:
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
            logger.warning(f"Юзер {user.telegram_id} недоступен: {e}")

    return True