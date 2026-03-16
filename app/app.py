import base64
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.site_router.site_views import site_router
from app.database.engine import create_db
from app.tg_bot_router.bot import start_bot, stop_bot, bot_router
from app.payment_router.payment_views import payment_router
from app.skynet_api_router.skynet_api_views import api_router
from app.database.engine import get_async_session
from app.tg_bot_router.bot import bot
from app.setup_logger import logger
from app.database.queries import (
    orm_get_servers,
    orm_get_user_by_tgid,
    orm_get_user_servers,
)
from app.utils.three_x_ui_api import ThreeXUIServer
from app.payment_router.payment_views import recurent_payment, check_subscription_expiry, notify_expired_users,reset_monthly_traffic


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db()
    await start_bot()

    expired_trigger = CronTrigger(
        year="*", month="*", day="*", hour="21", minute="09", second="0"
    )
    scheduler.add_job(
        notify_expired_users,
        trigger=expired_trigger,
        id='notify_expired_users',
        replace_existing=True,
        args=[bot]
    )

    # Проверка подписок и уведомления - каждый день в 10:00
    subscription_trigger = CronTrigger(
        year="*", month="*", day="*", hour="21", minute="09", second="0"
    )
    scheduler.add_job(
        check_subscription_expiry,
        trigger=subscription_trigger,
        id='check_subscription_expiry',
        replace_existing=True,
        args=[bot]
    )

    # Автопродление подписок - каждый день в 14:00
    recurent_trigger = CronTrigger(
        year="*", month="*", day="*", hour="21", minute="09", second="0"
    )
    scheduler.add_job(
        recurent_payment,
        trigger=recurent_trigger,
        id='recurent_payment',
        replace_existing=True,
        args=[bot]
    )

    traffic_reset_trigger = CronTrigger(
        day="1", hour="3", minute="0", second="0"
    )
    scheduler.add_job(
        reset_monthly_traffic,
        trigger=traffic_reset_trigger,
        id='reset_monthly_traffic',
        replace_existing=True,
        args=[bot]
    )

    scheduler.start()
    yield
    await stop_bot()


scheduler = AsyncIOScheduler()
app = FastAPI(lifespan=lifespan)
app.include_router(site_router, tags=['Site'])
app.include_router(bot_router, tags=['TG_BOT'])
app.include_router(payment_router, tags=['Payment'])
app.include_router(api_router, tags=['Rest API'])


@app.get("/subscription")
async def generate_subscription_config(user_token: str, session: AsyncSession = Depends(get_async_session)):
    user = await orm_get_user_by_tgid(session, int(user_token))
    user_servers = await orm_get_user_servers(session, user.id)
    if not user or not user_servers:
        raise HTTPException(status_code=404, detail="User not found or no servers available")

    # 3. Генерируем vless:// ссылки для каждого сервера
    config_lines = []
    trafic = (0, 0, 0)
    servers = await orm_get_servers(session)
    threex_panels = []
    for server in servers:
        threex_panels.append(ThreeXUIServer(
            server.id,
            server.url,
            server.indoub_id,
            server.login,
            server.password,
            server.need_gb
        ))
    for user_server in user_servers:
        vless_url = None
        for panel in threex_panels:
            if panel.id == user_server.server_id:
                try:
                    vless_url = await panel.get_client_vless(user_server.tun_id)
                    if panel.need_gb == True:
                        trafic = await panel.client_remain_trafic(user_server.tun_id) or (0, 0, 0)
                except Exception as e:
                    logger.warning(f"Сервер {user_server.server_id} недоступен: {e}")

        if not vless_url:
            logger.warning(f"Пользователь не найден на сервере {user_server.server_id}")
            continue
        config_lines.append(vless_url)

    if not config_lines:
        raise HTTPException(status_code=404, detail="Не найдены сервера")
    subscription_content = "\n".join(config_lines)

    response = Response(
        content=subscription_content,
        media_type="text/plain; charset=utf-8"
    )

    response.headers['profile-title'] = "base64:" + base64.b64encode('⚡️ SkynetVPN'.encode('utf-8')).decode('latin-1')
    response.headers["announce"] = "base64:" + base64.b64encode(
        f"🚀 Нажмите сюда, чтобы перейти в нашего бота\n\n👑 - без рекламы на YouTube\n🎧 - YouTube можно сворачивать \n\nОтображаемое количество трафика относиться только к обходу белых списков.".encode(
            'utf-8')).decode('latin-1')
    response.headers["announce-url"] = "https://t.me/skynetaivpn_bot"
    response.headers[
        "subscription-userinfo"] = f"expire={int(user.sub_end.timestamp())}; upload={trafic[0]}; download={trafic[1]}; total={trafic[2]}"
    response.headers["X-Frame-Options"] = 'SAMEORIGIN'
    response.headers["Referrer-Policy"] = 'no-referrer-when-downgrade'
    response.headers["X-Content-Type-Options"] = 'nosniff'
    response.headers["Permissions-Policy"] = 'geolocation=(), microphone=()'
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

    return response