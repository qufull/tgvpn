import html
from typing import Optional
from uuid import uuid4
from aiogram import Router, types, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.filters.logic import or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import InputMediaPhoto, Message
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Tariff
from app.tg_bot_router.common.link_worker import process_server_url
from app.tg_bot_router.filters.user_filter import AdminFilter
from app.tg_bot_router.kbds.reply import admin_menu_kbrd, choose_kbrd, cancel_kbrd, CANCEL_TEXT, CONTINUE_TEXT
from app.tg_bot_router.kbds.inline import get_inlineMix_btns
from app.utils.days_to_month import days_to_str
from app.setup_logger import logger
from app.database.queries import (
    orm_add_faq,
    orm_add_user_server,
    orm_delete_faq,
    orm_add_server,
    orm_add_tariff,
    orm_delete_server,
    orm_get_faq,
    orm_get_server,
    orm_get_server_by_ui,
    orm_get_servers,
    orm_get_tariff,
    orm_get_tariffs,
    orm_get_user_server_by_ti,
    orm_update_server,
    orm_update_tariff,
    orm_delete_tariff,
    orm_get_users,
    orm_get_subscribers,
    orm_delete_user_servers_by_si,
    orm_get_user_servers_by_si,
    orm_swap_server_order,
)
from app.utils.three_x_ui_api import ThreeXUIServer

admin_private_router = Router()
admin_private_router.message.filter(AdminFilter())


def detect_parse_mode(text: str) -> str | None:
    t = text or ""
    # грубый, но рабочий детектор
    if any(tag in t for tag in ("<b>", "<i>", "<u>", "<s>", "<a ", "<code>", "<pre>", "<span")):
        return "HTML"

    # MarkdownV2 маркеры (с телефона обычно именно это)
    if any(ch in t for ch in ("*", "_", "`", "~", "[", "]", "(", ")", "|")):
        return "MarkdownV2"

    return None


async def validate_html(bot: Bot, chat_id: int, text: str) -> tuple[bool, str | None]:
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            disable_notification=True,
        )
        await msg.delete()
        return True, None

    except TelegramBadRequest as e:
        # e.message обычно содержит "Bad Request: can't parse entities: ..."
        return False, str(e)


@admin_private_router.message(Command('admin'))
async def admin(message: types.Message):
    await message.answer(
        f"Здравствуйте {message.from_user.first_name}!\nВыберите действие:",
        reply_markup=admin_menu_kbrd()
    )


@admin_private_router.message(StateFilter("*"), Command("cancel"))
async def fsm_cancel(message: types.Message, state: FSMContext):
    currant_state = await state.get_state()
    if currant_state == None:
        return

    await state.clear()
    await message.answer("✅ Действия отменены", reply_markup=admin_menu_kbrd())


class FSMAddTariff(StatesGroup):
    days = State()
    price = State()
    ips = State()
    servers = State()
    trafic = State()

    tariff_to_change: Optional[Tariff] = None


@admin_private_router.message(StateFilter(None), F.text == '💰 Тарифы')
async def get_tariffs(message: types.Message, session: AsyncSession):
    tariffs = await orm_get_tariffs(session)

    if tariffs:
        for tariff in tariffs:
            await message.answer(
                f"<b>Цена: {tariff.price}</b>\n<b>Срок: {days_to_str(tariff.days)}</b>\n<b>Количества устройств: {tariff.ips}</b>",
                reply_markup=get_inlineMix_btns(
                    btns={
                        '🗑 Удалить': f'delete_tariff_{tariff.id}',
                        '✏️ Изменить': f'edit_tariff_{tariff.id}',
                    },
                    sizes=(2,),
                )
            )
        await message.answer(f"Всего тарифов: {len(tariffs)}",
                             reply_markup=get_inlineMix_btns(btns={"➕ Добавить тариф": "add_tariff"}))
    else:
        await message.answer("Тарифов пока нет.",
                             reply_markup=get_inlineMix_btns(btns={"➕ Добавить тариф": "add_tariff"}))


@admin_private_router.callback_query(StateFilter(None), F.data.startswith("delete_tariff"))
async def delete_tariff(callback_query: types.CallbackQuery, session: AsyncSession):
    try:
        tariff_id = int(callback_query.data.split("_")[-1])
        await orm_delete_tariff(session, tariff_id)
        await callback_query.message.delete()
        await callback_query.message.answer(f"✅ Тариф удален", reply_markup=admin_menu_kbrd())
    except:
        logger.error(f"Ошибка, не удалось удалить тариф", exc_info=True)
        await callback_query.message.answer("❌ Ошибка: тариф не найден", reply_markup=admin_menu_kbrd())
    await callback_query.answer()


@admin_private_router.callback_query(StateFilter(None), F.data.startswith("add_tariff"))
@admin_private_router.callback_query(StateFilter(None), F.data.startswith("edit_tariff"))
async def add_tariff(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    if callback_query.data.startswith("edit_tariff"):
        # Преобразуем tariff_id в integer
        tariff_id = int(callback_query.data.split('_')[-1])
        FSMAddTariff.tariff_to_change = await orm_get_tariff(session, tariff_id)

    await state.set_state(FSMAddTariff.days)
    await callback_query.message.answer(
        f"<b>Вы начали {'изменение' if FSMAddTariff.tariff_to_change else 'добавление'} тарифа</b>\nДля отмены напишите /cancel\n\n<b>Введите количество дней, на которое будет выдаваться подписка:</b>",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await callback_query.answer()


@admin_private_router.message(FSMAddTariff.days, F.text)
async def add_tariff_days(message: types.Message, state: FSMContext):
    if FSMAddTariff.tariff_to_change and message.text == ".":
        await state.update_data(days=FSMAddTariff.tariff_to_change.days)
    else:
        try:
            await state.update_data(days=int(message.text))
        except:
            await message.answer("Неверный формат. Введите количество дней в числах")
            return

    await state.set_state(FSMAddTariff.price)
    await message.answer("<b>Введите цену тарифа:</b>")


@admin_private_router.message(FSMAddTariff.price, F.text)
async def add_tariff_price(message: types.Message, state: FSMContext):
    if FSMAddTariff.tariff_to_change and message.text == ".":
        await state.update_data(price=FSMAddTariff.tariff_to_change.price)
    else:
        try:
            await state.update_data(price=int(message.text))
        except:
            await message.answer("Неверный формат. Введите цену тарифа:")
            return

    await state.set_state(FSMAddTariff.trafic)
    await message.answer("<b>Введите количество трафика в гигабайтах для обхода белых списков:</b>")


@admin_private_router.message(FSMAddTariff.trafic, F.text)
async def add_tariff_trafic(message: types.Message, state: FSMContext):
    if FSMAddTariff.tariff_to_change and message.text == ".":
        await state.update_data(tarif=FSMAddTariff.tariff_to_change.tarif)
    else:
        try:
            await state.update_data(trafic=int(message.text))
        except:
            await message.answer("Неверный формат. ведите количество трафика в гигабайтах:")
            return

    await state.set_state(FSMAddTariff.ips)
    await message.answer("<b>Введите количество устройств для этого тарифа:</b>")


@admin_private_router.message(FSMAddTariff.ips, F.text)
async def add_tariff_ips(message: types.Message, state: FSMContext, session: AsyncSession):
    if FSMAddTariff.tariff_to_change and message.text == ".":
        await state.update_data(ips=FSMAddTariff.tariff_to_change.days)
    else:
        try:
            await state.update_data(ips=int(message.text))
        except:
            await message.answer("Неверный формат. Введите количество устройств в числах")
            return

    data = await state.get_data()

    if FSMAddTariff.tariff_to_change:
        await orm_update_tariff(
            session,
            tariff_id=FSMAddTariff.tariff_to_change.id,
            data=data
        )
        FSMAddTariff.tariff_to_change = None
        await message.answer("✅ Тариф изменен", reply_markup=admin_menu_kbrd())
    else:
        await orm_add_tariff(
            session,
            days=data['days'],
            price=data['price'],
            ips=data['ips'],
            trafic=data['trafic']
        )
        await message.answer("✅ Тариф добавлен", reply_markup=admin_menu_kbrd())

    await state.clear()


# Servers
class FSMAddServer(StatesGroup):
    name = State()
    url = State()
    indoub_id = State()
    login = State()
    password = State()
    need_gb = State()

    server_to_change = None


@admin_private_router.message(F.text == '🌐 Сервера')
async def get_servers(message: types.Message, session: AsyncSession):
    servers = await orm_get_servers(session)

    if servers:
        for idx, server in enumerate(servers):
            btns = {}
            # Кнопка вверх (если не первый)
            if idx > 0:
                btns['⬆️'] = f'sort_server_{server.id}_up'
            # Кнопка вниз (если не последний)
            if idx < len(servers) - 1:
                btns['⬇️'] = f'sort_server_{server.id}_down'
            btns['🗑 Удалить'] = f'delete_server_{server.id}'
            btns['✏️ Изменить'] = f'edit_server_{server.id}'

            await message.answer(
                f"<b>{idx+1}. {server.name}</b>\n<b>URL: {server.url}</b>\n<b>Индауб: {server.indoub_id}</b>\nЛогин: <span class='tg-spoiler'>{server.login}</span>\nПароль: <span class='tg-spoiler'>{server.password}</span>",
                reply_markup=get_inlineMix_btns(btns=btns, sizes=(2, 2))
            )
        await message.answer(
            f"Всего серверов: {len(servers)}",
            reply_markup=get_inlineMix_btns(btns={"➕ Добавить сервер": "add_server"})
        )
    else:
        await message.answer(
            "Серверов пока нет.",
            reply_markup=get_inlineMix_btns(btns={"➕ Добавить сервер": "add_server"})
        )


@admin_private_router.callback_query(StateFilter(None), F.data.startswith("sort_server_"))
async def sort_server(callback_query: types.CallbackQuery, session: AsyncSession):
    """Обработчик кнопок ⬆️/⬇️ для сортировки серверов"""
    parts = callback_query.data.split("_")  # sort_server_ID_up/down
    server_id = int(parts[2])
    direction = parts[3]  # "up" или "down"

    servers = await orm_get_servers(session)
    server_ids = [s.id for s in servers]

    try:
        idx = server_ids.index(server_id)
    except ValueError:
        await callback_query.answer("❌ Сервер не найден")
        return

    if direction == "up" and idx > 0:
        swap_with = server_ids[idx - 1]
    elif direction == "down" and idx < len(server_ids) - 1:
        swap_with = server_ids[idx + 1]
    else:
        await callback_query.answer("Некуда двигать")
        return

    await orm_swap_server_order(session, server_id, swap_with)
    await callback_query.answer("✅ Порядок изменён")

    # Удаляем старое сообщение и показываем обновлённый список
    try:
        await callback_query.message.delete()
    except Exception:
        pass

    # Пересоздаём список — шлём как будто нажали "Сервера"
    await get_servers(callback_query.message, session)


@admin_private_router.callback_query(StateFilter(None), F.data.startswith("edit_server"))
@admin_private_router.callback_query(StateFilter(None), F.data.startswith("add_server"))
async def add_server(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    if callback_query.data.startswith("edit_server"):
        server_id = int(callback_query.data.split('_')[-1])
        FSMAddServer.server_to_change = await orm_get_server(session, server_id)

    await state.set_state(FSMAddServer.name)
    await callback_query.message.answer(
        f"<b>Вы начали {'изменение' if FSMAddServer.server_to_change else 'добавление'} сервера</b>\nДля отмены напишите /cancel\n\n<b>Введите название сервера:</b>",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await callback_query.answer()


@admin_private_router.message(FSMAddServer.name, F.text)
async def add_server_url(message: types.Message, state: FSMContext):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(name=FSMAddServer.server_to_change.name)
    else:
        await state.update_data(name=message.text)

    await state.set_state(FSMAddServer.url)
    await message.answer("Введите url на 3x-ui панель сервера:")


@admin_private_router.message(FSMAddServer.url, F.text)
async def add_server_name(message: types.Message, state: FSMContext):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(url=FSMAddServer.server_to_change.url)
    else:
        if not 'http' in message.text:
            await message.answer("❌ Не удалось обработать ссылку, отправте ссылку на панель сервера еще раз:")
            return

        await state.update_data(url=process_server_url(message.text))

    await state.set_state(FSMAddServer.indoub_id)
    await message.answer("Введите id индауба сервера:")


@admin_private_router.message(FSMAddServer.indoub_id, F.text)
async def add_server_indoub(message: types.Message, state: FSMContext):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(indoub_id=FSMAddServer.server_to_change.indoub_id)
    else:
        try:
            await state.update_data(indoub_id=int(message.text))
        except:
            await message.answer("❌ Некоректный формат! Ввелите id индауба в виде числа:")

    await state.set_state(FSMAddServer.login)
    await message.answer("Введите логин админ панели сервера:")


@admin_private_router.message(FSMAddServer.login, F.text)
async def add_server_url(message: types.Message, state: FSMContext):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(login=FSMAddServer.server_to_change.login)
    else:
        await state.update_data(login=message.text)

    await state.set_state(FSMAddServer.password)
    await message.answer("Введите пароль админ панели сервера:")


@admin_private_router.message(FSMAddServer.password, F.text)
async def add_server_need_gb(message: types.Message, state: FSMContext):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(password=FSMAddServer.server_to_change.password)
    else:
        await state.update_data(password=message.text)

    await state.set_state(FSMAddServer.need_gb)
    await message.answer(
        "Нужно ли ограничение по гигабайтам для данного сервера?",
        reply_markup=choose_kbrd()
    )


@admin_private_router.message(FSMAddServer.need_gb, F.text)
async def add_server_password(message: types.Message, state: FSMContext, session: AsyncSession):
    if FSMAddServer.server_to_change and message.text == '.':
        await state.update_data(need_gb=FSMAddServer.server_to_change.need_gb)
    else:
        if message.text.lower() == 'нет':
            await state.update_data(need_gb=False)
        elif message.text.lower() == 'да':
            await state.update_data(need_gb=True)
        else:
            return

    data = await state.get_data()

    if FSMAddServer.server_to_change:
        await orm_update_server(session, data, FSMAddServer.server_to_change.id)
        FSMAddServer.server_to_change = None
        await message.answer("✅ Сервер изменен", reply_markup=admin_menu_kbrd())
    else:
        await orm_add_server(
            session,
            name=data['name'],
            url=data['url'],
            indoub_id=data['indoub_id'],
            login=data['login'],
            password=data['password'],
            need_gb=data['need_gb']
        )
        users = await orm_get_users(session)
        servers = await orm_get_servers(session)
        threex_panel = ThreeXUIServer(
            0,
            data['url'],
            data['indoub_id'],
            data['login'],
            data['password'],
            data['need_gb']
        )
        server = await orm_get_server_by_ui(session, data['url'], data['indoub_id'])

        for user in users:
            if user.sub_end:
                tariff = None
                if data['need_gb']:
                    tariff = await orm_get_tariff(session, user.tariff_id)
                uuid = uuid4()
                await orm_add_user_server(
                    session,
                    user_id=user.id,
                    server_id=server.id,
                    tun_id=str(uuid)
                )
                user_server = await orm_get_user_server_by_ti(session, str(uuid))
                await threex_panel.add_client(
                    uuid=str(uuid),
                    email=data['name'] + '_' + str(user_server.id),
                    limit_ip=user.ips,
                    name=user.name,
                    tg_id=str(user.telegram_id),
                    expiry_time=int(user.sub_end.timestamp() * 1000),
                    total_gb=tariff.trafic if tariff and data['need_gb'] else 0
                )
        await message.answer("✅ Сервер добавлен", reply_markup=admin_menu_kbrd())

    await state.clear()


@admin_private_router.callback_query(StateFilter(None), F.data.startswith("delete_server"))
async def delete_server(callback_query: types.CallbackQuery, session: AsyncSession):
    try:
        server_id = int(callback_query.data.split("_")[-1])
        server = await orm_get_server(session, server_id)
        users_servers = await orm_get_user_servers_by_si(session, server_id)

        threex_panel = ThreeXUIServer(
            id=0,
            url=server.url,
            indoub_id=server.indoub_id,
            login=server.login,
            password=server.password
        )

        if users_servers:
            for i in users_servers:
                await threex_panel.delete_client(i.tun_id)

        await orm_delete_user_servers_by_si(session, server_id)
        await orm_delete_server(session, server_id)
        await callback_query.message.delete()
        await callback_query.message.answer(f"✅ сервер удален", reply_markup=admin_menu_kbrd())
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Ошибка, не удалось удалить сервер", exc_info=True)
        await callback_query.message.answer("❌ Ошибка: сервер не найден", reply_markup=admin_menu_kbrd())
        await callback_query.answer()


# FAQ
@admin_private_router.message(StateFilter(None), F.text.lower().contains('частые вопросы'))
async def get_faq(message: types.Message, session: AsyncSession):
    faqs = await orm_get_faq(session)

    if faqs:
        for faq in faqs:
            await message.answer(
                f"<b>Вопрос</b>: {faq.ask}\n<b>Ответ: </b> {faq.answer}",
                reply_markup=get_inlineMix_btns(btns={'🗑 Удалить': f'delete_faq_{faq.id}'})
            )
        await message.answer(
            f"Всего вопросов {len(faqs)}",
            reply_markup=get_inlineMix_btns(btns={'➕ Добавить вопрос': 'add_faq'})
        )
    else:
        await message.answer(
            f"Вопросов пока нет",
            reply_markup=get_inlineMix_btns(btns={'➕ Добавить вопрос': 'add_faq'})
        )


class FSMAddFaq(StatesGroup):
    ask = State()
    answer = State()


@admin_private_router.callback_query(StateFilter(None), F.data.startswith('add_faq'))
async def add_faq(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(FSMAddFaq.ask)
    await callback.message.answer(
        f"<b>Вы начали добавление вопроса</b>\nДля отмены напишите /cancel\n\n<b>Напишите вопрос:</b>",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await callback.answer()


@admin_private_router.message(FSMAddFaq.ask, F.text)
async def add_faq_ask(message: types.Message, state: FSMContext):
    await state.update_data(ask=message.text.strip())
    await state.set_state(FSMAddFaq.answer)
    await message.answer("Введите ответ на вопрос:")


@admin_private_router.message(FSMAddFaq.answer, F.text)
async def add_faq_answer(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.update_data(answer=message.text)
    data = await state.get_data()

    try:
        await orm_add_faq(session, data)
        await message.answer("✅ Вопрос и ответ добавлен", reply_markup=admin_menu_kbrd())
    except Exception as e:
        logger.error("Не удалось добавить вопрос", exc_info=True)
        await message.answer(f"Ошибка: {e}", reply_markup=admin_menu_kbrd())
    finally:
        await state.clear()


@admin_private_router.callback_query(StateFilter(None), F.data.startswith('delete_faq'))
async def delete_faq(callback_query: types.CallbackQuery, session: AsyncSession):
    try:
        # Преобразуем id в integer
        faq_id = int(callback_query.data.split("_")[-1])
        await orm_delete_faq(session, faq_id)
        await callback_query.message.delete()
        await callback_query.message.answer(f"✅ Вопрос удален", reply_markup=admin_menu_kbrd())
    except:
        logger.error(f"Ошибка, не удалось удалить вопрос", exc_info=True)
        await callback_query.message.answer("❌ Ошибка: вопрос не найден", reply_markup=admin_menu_kbrd())
    await callback_query.answer()


# Рассылка
class FSMSendLetter(StatesGroup):
    text = State()
    img = State()
    recipients = State()


@admin_private_router.message(StateFilter(None), F.text.lower().contains('рассылка'))
async def send_newsletter(message: types.Message, state: FSMContext):
    await state.set_state(FSMSendLetter.text)
    await message.answer(
        f"<b>Вы начали создание расслки</b>\nДля отмены напишите /cancel\n\n<b>Отправте текст сообщения. Для разметки используйте html теги:</b>",
        reply_markup=cancel_kbrd()
    )


@admin_private_router.message(FSMSendLetter.text, F.text, F.text != CANCEL_TEXT)
async def send_text(message: types.Message, state: FSMContext, bot: Bot):
    # ✅ забираем форматирование (жирный/курсив/ссылка) из entities
    text_html = message.html_text  # <-- ключевая строка

    ok, err = await validate_html(bot, message.chat.id, text_html)
    if not ok:
        await message.answer(
            "❌ <b>Неправильный HTML</b>\n\n"
            "✏️ Исправь текст и отправь заново.",
            parse_mode="HTML",
            reply_markup=cancel_kbrd()
        )
        return

    await state.update_data(text=text_html, parse_mode="HTML")
    await state.set_state(FSMSendLetter.img)
    await message.answer(
        f"<b>Отправте изображеня. Можно отправить до 10 штук. Отправте все изображения отдельными сообщениями. Когда закончите нажмите продолжить:</b>",
        parse_mode="HTML",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text=CONTINUE_TEXT)],
                [types.KeyboardButton(text=CANCEL_TEXT)],
            ],
            resize_keyboard=True
        )
    )



@admin_private_router.message(FSMSendLetter.img, F.photo)
async def collect_photos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    pictures = data.get("pictures", [])

    if len(pictures) >= 10:
        await message.answer("❗ Можно отправить не более 10 изображений")
        return

    pictures.append(message.photo[-1].file_id)
    await state.update_data(pictures=pictures)


@admin_private_router.message(FSMSendLetter.img, F.text.lower().contains('продолжить'))
async def skip_photos(message: types.Message, state: FSMContext):
    data = await state.get_data()
    pictures = data.get("pictures", [])

    await message.answer(
        f"📨 Получено изображений: {len(pictures)}\n"
        "Выберите кому отправить сообщение:",
        reply_markup=get_inlineMix_btns(
            btns={
                'Активные подписчики': 'active_subscribers',
                'Все': 'all',
            }, sizes=(1,)
        )
    )
    await state.set_state(FSMSendLetter.recipients)


@admin_private_router.callback_query(FSMSendLetter.recipients)
async def send_letter(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        bot: Bot
):
    data = await state.get_data()

    text: str = data.get("text")
    pictures: list[str] = data.get("pictures", [])

    if callback.data == "active_subscribers":
        users = await orm_get_subscribers(session)
    else:
        users = await orm_get_users(session)

    sent = 0

    for user in users:
        # Не отправляем рассылку самому себе (админу)
        if user.telegram_id == callback.from_user.id:
            continue

        try:
            if pictures:
                media = [
                    InputMediaPhoto(
                        media=pic,
                        caption=text if i == 0 else None,
                        parse_mode="HTML"
                    )
                    for i, pic in enumerate(pictures)
                ]
                await bot.send_media_group(chat_id=user.telegram_id, media=media)
            else:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,  # опционально, чтобы не раздувало предпросмотром
                )
            sent += 1

        except TelegramBadRequest:
            continue
        except Exception as e:
            print(f"Ошибка при отправке {user.telegram_id}: {e}")

    await callback.message.answer(
        f"✅ Рассылка завершена\n"
        f"Отправлено: {sent}",
        reply_markup=admin_menu_kbrd()
    )

    await state.clear()
    await callback.answer()


@admin_private_router.message(StateFilter("*"), F.text == CANCEL_TEXT)
async def cancel_by_button(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Действия отменены", reply_markup=admin_menu_kbrd())