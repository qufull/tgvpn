import asyncio
import base64
import os
from urllib.parse import quote
from datetime import datetime, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response,Header,BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import get_async_session
from app.utils.days_to_month import days_to_str
from app.tg_bot_router.bot import bot
from app.skynet_api_router.schemas import UpdateClientGS
from app.setup_logger import logger
from app.database.queries import (
    orm_get_server,
    orm_get_servers,
    orm_get_subscribers,
    orm_get_user_by_tgid,
    orm_get_user_servers,
    orm_get_users,
    orm_get_tariffs,
    orm_get_user,
    orm_get_admins,
    orm_update_user
)
from app.utils.three_x_ui_api import ThreeXUIServer

api_router = APIRouter(prefix='/api')

API_KEY_SECRET = os.getenv("API_KEY_SECRET")

async def verify_api_key(x_api_key: str = Header(None)):
    """Простая защита доступа через заголовок X-API-Key"""
    if x_api_key != API_KEY_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")
    return x_api_key


@api_router.get('/clients', dependencies=[Depends(verify_api_key)])
async def get_clients(session: AsyncSession = Depends(get_async_session)):
    users = await orm_get_users(session)
    tariffs = await orm_get_tariffs(session)
    tariff_map = {t.id: t for t in tariffs}

    # Сортировка
    users_sorted = sorted(users, key=lambda o: o.created or datetime.min)

    # Получаем базовый URL из окружения для формирования ключа
    base_url = os.getenv('URL', 'https://bot-skynetai.ru')

    result = []
    for user in users_sorted:
        if user.tariff_id > 0 or user.sub_end:
            tariff = tariff_map.get(user.tariff_id)
            sub_end_str = user.sub_end.strftime('%d.%m.%Y') if user.sub_end else "Нет даты"

            # ФОРМИРУЕМ КЛЮЧ
            # Используем ту же логику, что и в check_subscribe
            subscription_key = f"{base_url}/api/subscribtion?user_token={user.id}"

            if tariff:
                duration = days_to_str(tariff.days)
            else:
                duration = "Тариф удален" if user.tariff_id else "Подписка отменена"

            # Добавляем ключ в массив (теперь будет 7 колонок)
            result.append([
                user.telegram_id,
                user.name or "Без имени",
                user.email or "Нет почты",
                user.ips,
                sub_end_str,
                duration,
                subscription_key  # <--- НОВЫЙ СТОЛБЕЦ (7-й)
            ])

    return result


@api_router.post("/update_client", dependencies=[Depends(verify_api_key)])
async def update_clients(
        data: UpdateClientGS,
        background_tasks: BackgroundTasks,  # Для тихой отправки уведомлений админам
        session: AsyncSession = Depends(get_async_session)
):
    now = datetime.now()
    user = await orm_get_user_by_tgid(session, data.user_id)

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # 1. Валидация даты
    try:
        y, m, d = map(int, data.sub_time.split('-'))
        new_date = datetime(y, m, d, now.hour, now.minute, now.second)
        # Переводим в миллисекунды для 3x-ui (Unix Timestamp * 1000)
        # Добавляем 1 день запаса, как в оригинале
        expiry_ms = int((new_date + timedelta(days=1)).timestamp() * 1000)
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Неверный формат даты. Ожидается YYYY-MM-DD")

    # 2. Получаем данные серверов
    user_servers = await orm_get_user_servers(session, user.id)
    all_servers = await orm_get_servers(session)

    # Оптимизация: создаем карту серверов для мгновенного поиска O(1)
    server_info_map = {s.id: s for s in all_servers}

    # 3. Функция для параллельного обновления одной панели
    async def update_single_panel(user_server_obj):
        s_info = server_info_map.get(user_server_obj.server_id)
        if not s_info:
            return False

        panel = ThreeXUIServer(
            s_info.id, s_info.url, s_info.indoub_id,
            s_info.login, s_info.password, s_info.need_gb, s_info.name
        )

        try:
            if await panel.auth():
                total_gb = 0
                if panel.need_gb:
                    try:
                        cur = await panel.get_total_gb(user_server_obj.tun_id)
                        total_gb = max(cur, 30)
                    except Exception:
                        total_gb = 30

                return await panel.edit_client(
                    uuid=user_server_obj.tun_id,
                    name=user.name,
                    email=f"{panel.name}_{user_server_obj.id}",
                    limit_ip=data.devices,
                    expiry_time=expiry_ms,
                    tg_id=user.telegram_id,
                    total_gb=total_gb,
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении панели {s_info.name}: {e}")
            return False
        return False

    # 4. 🚀 ЗАПУСКАЕМ ОБНОВЛЕНИЕ ВСЕХ ПАНЕЛЕЙ ОДНОВРЕМЕННО
    if user_servers:
        update_tasks = [update_single_panel(us) for us in user_servers]
        await asyncio.gather(*update_tasks, return_exceptions=True)

    # 5. Обновляем локальную базу данных
    await orm_update_user(
        session,
        user_id=user.id,
        data={
            'ips': data.devices,
            'sub_end': new_date,
            'email': data.email
        }
    )

    # 6. Уведомление админов в фоновом режиме (чтобы не задерживать ответ API)
    admins = await orm_get_admins(session)

    async def notify_admins_task(admins_list, user_name, date_str, devices_count):
        for admin in admins_list:
            try:
                await bot.send_message(
                    admin.telegram_id,
                    f"✅ Данные изменены для пользователя {user_name}\n"
                    f"Дата: {date_str}\n"
                    f"Количество устройств: {devices_count}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу {admin.telegram_id}: {e}")

    # Добавляем задачу в фон, передавая все аргументы
    background_tasks.add_task(
        notify_admins_task,
        admins,
        user.name,
        new_date.strftime('%d.%m.%Y'),
        data.devices
    )

    return {"status": "success", "message": "User updated across all active servers"}


PANELS_CACHE = {}


async def get_cached_panel(server) -> ThreeXUIServer | None:
    """Возвращает панель из кэша. Авторизация и восстановление происходят 'под капотом'."""
    if server.id not in PANELS_CACHE:
        PANELS_CACHE[server.id] = ThreeXUIServer(
            server.id, server.url, server.indoub_id,
            server.login, server.password, server.need_gb, server.name
        )

    panel = PANELS_CACHE[server.id]

    if panel.is_offline:
        return None

    return panel


@api_router.get("/subscribtion")
async def generate_subscription_config(user_token: str, session: AsyncSession = Depends(get_async_session)):
    try:
        user_uuid = UUID(user_token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid token format")

    user = await orm_get_user(session, user_uuid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = datetime.now()

    # Проверка подписки
    if not user.sub_end or user.sub_end < today:
        response = Response(
            content=(
                f"vless://{user.id}@1.23.123.4:8452?"
                f"type=tcp&spx=%2F&flow=#{quote('❌ Ваша подписка закончилась')}"
            ),
            media_type="text/plain; charset=utf-8"
        )
        response.headers['profile-title'] = "base64:" + base64.b64encode('⚡️ SkynetVPN'.encode('utf-8')).decode(
            'latin-1')
        response.headers["announce"] = "base64:" + base64.b64encode(
            "🚀 Нажмите сюда, тут можно продлить подписку".encode('utf-8')).decode('latin-1')
        response.headers["announce-url"] = "https://t.me/skynetaivpn_bot"
        return response

    user_servers = await orm_get_user_servers(session, user.id)
    if not user_servers:
        raise HTTPException(status_code=404, detail="No servers for user")

    servers = await orm_get_servers(session)

    # 1. Внутренняя функция для работы с панелью
    async def fetch_panel_data(panel: ThreeXUIServer, us_server, current_user):
        vless = await panel.get_client_vless(us_server.tun_id)

        # === АВТО-ВОССТАНОВЛЕНИЕ КЛИЕНТА ===
        if not vless:
            logger.info(f"Клиент {us_server.tun_id} не найден на {panel.name}, пробуем создать...")
            expiry_ms = int(current_user.sub_end.timestamp() * 1000) if current_user.sub_end else 0
            email = f"{panel.name}_{us_server.id}"
            name = current_user.name or f"User_{current_user.id}"

            is_added = await panel.add_client(
                uuid=us_server.tun_id, email=email, limit_ip=current_user.ips or 1,
                expiry_time=expiry_ms, tg_id=str(current_user.telegram_id),
                name=name, total_gb=30 if panel.need_gb else 0, flow="xtls-rprx-vision"
            )

            if is_added:
                logger.info(f"✅ Успешно воссоздали клиента {us_server.tun_id} на {panel.name}")
                vless = await panel.get_client_vless(us_server.tun_id)
            else:
                logger.error(f"❌ Не удалось воссоздать клиента {us_server.tun_id} на {panel.name}")

        traffic = (0, 0, 0)
        if vless and panel.need_gb:
            res = await panel.client_remain_trafic(us_server.tun_id)
            if isinstance(res, tuple) and len(res) >= 3:
                traffic = res

        return vless, traffic

    # 2. НОВАЯ ЛОГИКА: Обработка ОДНОГО сервера с жестким лимитом времени (5 секунд)
    async def get_server_config_with_timeout(server, us_server, current_user):
        try:
            async def _process():
                # Получаем панель (с авторизацией) и запрашиваем данные
                panel = await get_cached_panel(server)
                if not panel:
                    return None
                return await fetch_panel_data(panel, us_server, current_user)

            # ⏱ Даем ровно 5.0 секунд на всё про всё. Не успел - пропускаем сервер.
            return await asyncio.wait_for(_process(), timeout=5.0)

        except asyncio.TimeoutError:
            logger.warning(f"⏱ Таймаут API: Сервер {server.name} отвечал слишком долго и был пропущен.")
            return None
        except Exception as e:
            logger.error(f"⚠️ Ошибка обработки сервера {server.name}: {e}")
            return None

    # 3. Собираем задачи по всем серверам
    tasks = []
    for server in servers:
        user_server = next((us for us in user_servers if us.server_id == server.id), None)
        if not user_server:
            continue
        # Передаем каждый сервер в обертку с таймаутом
        tasks.append(get_server_config_with_timeout(server, user_server, user))

    # 4. Выполняем запросы ко всем панелям ПАРАЛЛЕЛЬНО
    results = await asyncio.gather(*tasks, return_exceptions=True)

    config_lines = []
    total_traffic = [0, 0, 0]

    for res in results:
        # Если пришел None (сервер пропущен по таймауту или ошибке) - идем дальше
        if isinstance(res, Exception) or not res:
            continue

        vless, traffic = res
        if vless:
            config_lines.append(vless)
            total_traffic[0] += traffic[0]
            total_traffic[1] += traffic[1]
            total_traffic[2] += traffic[2]

    if not config_lines:
        raise HTTPException(status_code=404, detail="No configs found")

    response = Response(
        content="\n".join(config_lines),
        media_type="text/plain; charset=utf-8"
    )

    response.headers['profile-title'] = "base64:" + base64.b64encode('⚡️ SkynetVPN'.encode('utf-8')).decode('latin-1')
    response.headers["announce"] = "base64:" + base64.b64encode(
        ('Лимит на "Когда глушат интернет" 30 ГБ/мес. Остальной трафик не лимитирован."\n\n'
         "👑 - без рекламы на YouTube\n"
         "🎧 - YouTube можно сворачивать \n"
         "⚡️ - быстрая скорость\n\n"
         "↗️ Нажмите сюда, чтобы перейти в нашего бота\n").encode('utf-8')
    ).decode('latin-1')
    response.headers["announce-url"] = "https://t.me/skynetaivpn_bot"

    response.headers["subscription-userinfo"] = (
        f"expire={int(user.sub_end.timestamp())}; "
        f"upload={total_traffic[0]}; download={total_traffic[1]}; total={total_traffic[2]}"
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

    return response