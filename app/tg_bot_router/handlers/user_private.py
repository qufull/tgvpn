from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.tg_bot_router.handlers.menu_menager import get_menu_content
from app.tg_bot_router.kbds.inline import MenuCallback

from app.database.queries import orm_add_user
from app.setup_logger import logger


user_private_router = Router()


@user_private_router.message(Command('start'))
async def start(message: types.Message, command: CommandObject, session: AsyncSession):
    try:
        refer_id = int(command.args)
    except:
        refer_id = None
        logger.warning("Wrong refer id")
    user_name = message.from_user.username or message.from_user.full_name
    await orm_add_user(
        session, 
        name=user_name, 
        telegram_id=message.from_user.id,
        invited_by=refer_id
    )

    media, reply_markup = await get_menu_content(session, level=0, menu_name="start", user_id=message.from_user.id)

    await message.answer_video(media.media, caption=media.caption, reply_markup=reply_markup)


@user_private_router.message(Command('menu'))
async def user_menu_by_command(message: types.Message, session: AsyncSession):
    try:
        content, reply_markup = await get_menu_content(
            session,
            level=1,
            menu_name='main',
            user_id=message.from_user.id,
            include_image=True
        )

        # покажем, что там внутри
        logger.info(f"menu content type={type(content)} media={getattr(content,'media',None)} caption_len={len(getattr(content,'caption','') or '')}")

        await message.answer_photo(
            content.media,
            caption=content.caption,
            reply_markup=reply_markup
        )
    except Exception:
        logger.exception("MENU FAILED")
        await message.answer("Меню упало ❌. Причина в логах.")


@user_private_router.callback_query(MenuCallback.filter())
async def user_menu(callback_query: types.CallbackQuery, callback_data: MenuCallback, session: AsyncSession):
    content = await get_menu_content(
        session,
        level=callback_data.level,
        menu_name=callback_data.menu_name,
        user_id=callback_query.from_user.id,
    )

    # --- ПРОВЕРКА НА АЛЬБОМ (WINDOWS) ---
    # Если вернулось 3 элемента, значит это случай с MediaGroup
    if isinstance(content, tuple) and len(content) == 3:
        album, caption, reply_markup = content

        try:
            # Удаляем старое сообщение меню, так как мы не можем превратить его в альбом
            await callback_query.message.delete()
        except:
            pass  # Если сообщение уже удалено или слишком старое

        # Отправляем альбом (4 фото)
        # Внимание: send_media_group не поддерживает клавиатуры (reply_markup)
        await callback_query.message.answer_media_group(media=album)

        # Отправляем отдельным сообщением текст инструкции и кнопки
        await callback_query.message.answer(text=caption, reply_markup=reply_markup)

        await callback_query.answer()
        return

    # Стандартная обработка (как было раньше)
    caption, reply_markup = content

    try:
        # Проверяем, пришло ли медиа или просто текст
        if isinstance(caption, types.InputMediaPhoto):
            await callback_query.message.edit_media(media=caption, reply_markup=reply_markup)
        else:
            await callback_query.message.edit_caption(caption=caption, reply_markup=reply_markup)
    except Exception as e:
        if "there is no caption in the message to edit" in str(e) or "message is not modified" in str(e):
            pass
            # Логика обработки ошибок (из вашего кода)
            baner = types.FSInputFile("media/img/main_logo_bg.jpg")
            # Если пришел текст, а было фото, edit_caption может упасть если фото нет, но тут скорее всего все ок.
            # Если caption это строка:
            if isinstance(caption, str):
                await callback_query.message.answer_photo(photo=baner, caption=caption, reply_markup=reply_markup)
        elif isinstance(e, TelegramBadRequest):
            pass
            logger.error(msg='Ошибка при смене инлайн меню: ', exc_info=True)
        else:
            logger.error(msg='Ошибка при смене инлайн меню: ', exc_info=True)

    await callback_query.answer()
