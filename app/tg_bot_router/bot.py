import os

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.queries import orm_get_user_by_tgid
from app.setup_logger import logger
from app.tg_bot_router.handlers.admin_private import admin_private_router
from app.tg_bot_router.handlers.user_private import user_private_router
from app.tg_bot_router.middlewares.session_middleware import DataBaseSession
from app.database.engine import async_session_maker, get_async_session


token = os.getenv("BOT_TOKEN")
if not token:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher()
dp.update.middleware.register(DataBaseSession(session_pool=async_session_maker))
dp.include_router(user_private_router)
dp.include_router(admin_private_router)

bot_router = APIRouter(prefix='/bot')


@bot_router.post("")
async def webhook(request: Request):
    payload = await request.json()
    logger.warning(f"WEBHOOK HIT chat={payload.get('message', {}).get('chat', {}).get('id')} keys={list(payload.keys())}")

    update = types.Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


@bot_router.get("/v2ray")
async def redirect_to_v2ray(telegram_id: int, session: AsyncSession = Depends(get_async_session)):
    user = await orm_get_user_by_tgid(session, telegram_id=telegram_id)

    if user:
        url = f'v2raytun://import/{os.getenv("URL")}/api/subscribtion?user_token={user.id}'
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    else:
        return HTTPException(status_code=404, detail="Item not found")


async def start_bot():
    await bot.set_my_commands(
        commands=[
            types.BotCommand(command='menu', description="Главное меню")
        ], 
        scope=types.BotCommandScopeAllPrivateChats()
    )
    await bot.set_webhook(url=os.getenv("URL")+'/bot',
                          allowed_updates=dp.resolve_used_update_types(),
                          drop_pending_updates=True)
    logger.info("Бот запущен")
    

async def stop_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()
    logger.info("Бот остановлен")






