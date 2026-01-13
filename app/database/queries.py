from datetime import datetime
from typing import Optional
from uuid import UUID
from sqlalchemy import DateTime, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import query, selectinload

from app.database.models import User, UserServer, Server, Payment, Tariff, FAQ


# User
async def orm_add_user(
    session: AsyncSession,
    name: str,
    telegram_id: int,
    invited_by: Optional[int] = None,
):
    "Add new user to database if not exist"
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    
    if result.first() is None:
        session.add(User(
            name=name,
            telegram_id=telegram_id,
            invited_by=invited_by
        ))
        await session.commit()
    elif invited_by:
        query = update(User).where(User.telegram_id==telegram_id).values(invited_by=invited_by)


async def orm_update_user(
    session: AsyncSession,
    user_id: int,
    data: dict    
):
    query = update(User).where(User.id == user_id).values(
        **data
    )
    await session.execute(query)
    await session.commit()


async def orm_change_user_tariff(
    session: AsyncSession,
    user_id: UUID,
    tariff_id: int,
    sub_end: datetime,
    ips: int = 2,
    tun_ids: Optional[dict[int, str]] = None
):
    query = update(User).where(User.id == user_id).values(
        tariff_id=tariff_id,
        sub_end=sub_end,
        ips=ips
    )
    await session.execute(query)
    
    if tun_ids:
        for server_id, key in tun_ids.items():
            session.add(UserServer(
                user_id=user_id,
                server_id=server_id,
                tun_id=key
            ))

    await session.commit()


async def orm_get_users(session: AsyncSession):
    query = select(User)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_subscribers(session: AsyncSession):
    query = select(User).where(User.tariff_id > 0)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_admins(session: AsyncSession):
    query = select(User).where(User.super_user)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_user(session: AsyncSession, user_id: UUID):
    query = select(User).where(User.id == user_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_user_by_tgid(session: AsyncSession, telegram_id: int):
    query = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(query)
    return result.scalar()


# Server
async def orm_add_server(
    session: AsyncSession,
    name: str,
    url: str,
    indoub_id: int,
    login: str,
    password: str,
    need_gb: bool = False
):
    session.add(Server(
        name=name,
        url=url,
        indoub_id=indoub_id,
        login=login,
        password=password,
        need_gb=need_gb
    ))
    await session.commit()


async def orm_delete_server(session: AsyncSession, server_id: int):
    query = delete(Server).where(Server.id == server_id)
    await session.execute(query)
    await session.commit()


async def orm_update_server(
    session: AsyncSession,
    data: dict,
    server_id: int, 
):
    query = update(Server).where(Server.id == server_id).values(**data)
    await session.execute(query)
    await session.commit()


async def orm_get_servers(session: AsyncSession):
    query = select(Server).order_by(Server.name.asc())
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_server(session: AsyncSession, server_id):
    query = select(Server).where(Server.id == server_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_server_by_ui(session: AsyncSession, url: str, indoub_id: int):
    query = select(Server).where(Server.url == url).where(Server.indoub_id == indoub_id)
    result = await session.execute(query)
    return result.scalar()


# UserServer
async def orm_add_user_server(
    session: AsyncSession,
    tun_id: str,
    user_id: UUID,
    server_id: int
):
    session.add(UserServer(
        tun_id=tun_id,
        user_id=user_id,
        server_id=server_id
    ))
    await session.commit()


async def orm_get_user_servers(session: AsyncSession, user_id: UUID):
    query = select(UserServer).where(UserServer.user_id == user_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_user_server(session: AsyncSession, user_id: UUID, server_id: int):
    query = select(UserServer).where(UserServer.user_id == user_id).where(UserServer.server_id == server_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_user_server_by_ti(session: AsyncSession, tun_id: str):
    query = select(UserServer).where(UserServer.tun_id == tun_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_delete_user_servers(session: AsyncSession, tun_id: str):
    query = delete(UserServer).where(UserServer.tun_id == tun_id)
    await session.execute(query)
    await session.commit()


async def orm_get_user_servers_by_si(session: AsyncSession, server_id: int):
    query = select(UserServer).where(UserServer.server_id == server_id)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_user_servers_by_si(session: AsyncSession, server_id: str):
    query = delete(UserServer).where(UserServer.server_id == server_id)
    await session.execute(query)
    await session.commit()


# Tariff
async def orm_add_tariff(
    session: AsyncSession,
    days: int,
    ips: int,
    price: float,
    trafic: int
):
    session.add(Tariff(
        days=days,
        ips=ips,
        price=price,
        trafic=trafic
    ))
    await session.commit()


async def orm_update_tariff(session: AsyncSession, tariff_id, data):
    query = update(Tariff).where(Tariff.id == tariff_id).values(**data)
    await session.execute(query)
    await session.commit()


async def orm_delete_tariff(session: AsyncSession, tariff_id):
    # Сначала удаляем связанные платежи
    delete_payments = delete(Payment).where(Payment.tariff_id == tariff_id)
    await session.execute(delete_payments)

    # Обнуляем tariff_id у пользователей с этим тарифом
    update_users = update(User).where(User.tariff_id == tariff_id).values(tariff_id=0)
    await session.execute(update_users)

    # Теперь удаляем сам тариф
    query = delete(Tariff).where(Tariff.id == tariff_id)
    await session.execute(query)
    await session.commit()


async def orm_get_tariffs(session: AsyncSession):
    query = select(Tariff).order_by(Tariff.days)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_tariff(session: AsyncSession, tariff_id):
    query = select(Tariff).where(Tariff.id == tariff_id)
    result = await session.execute(query)
    return result.scalar()


# FAQ
async def orm_get_faq(session: AsyncSession):
    '''Возвращает список вопросов и ответов'''
    query = select(FAQ)
    result = await session.execute(query)
    return result.scalars().all()


async def orm_add_faq(session: AsyncSession, data: dict):
    '''Добавляет вопрос и ответ в таблицу'''
    obj = FAQ(
        ask=data["ask"],
        answer=data["answer"],
    )
    session.add(obj)
    await session.commit()


async def orm_get_faq_by_id(session: AsyncSession, id: int):
    '''Возвращает вопрос и ответ по id'''
    query = select(FAQ).where(FAQ.id == id)
    result = await session.execute(query)
    return result.scalar()


async def orm_delete_faq(session: AsyncSession, id: int):
    '''Удалить вопрос из таблицы'''
    query = delete(FAQ).where(FAQ.id == id)
    await session.execute(query)
    await session.commit()


async def orm_edit_faq(session: AsyncSession, id: int, fields: dict):
    '''Обновляет только переданные поля вопроса и ответа по id.
    fields: dict - только те поля, которые нужно обновить (например: {'ask': '...', 'answer': '...'})
    '''
    if not fields:
        return
    query = update(FAQ).where(FAQ.id == id).values(**fields)
    await session.execute(query)
    await session.commit()


# Payment
async def orm_end_payment(session: AsyncSession, id: int):
    query = update(Payment).where(Payment.id == id).values(paid = True)
    await session.execute(query)
    await session.commit()


async def orm_new_payment(session: AsyncSession, user_id: UUID, tariff_id: int, recurent: bool = False):
    '''Создает новую запись о платеже в таблицу'''
    obj = Payment(
        user_id=user_id,
        tariff_id=tariff_id,
        recurent=recurent,
    )
    session.add(obj)
    await session.commit()


async def orm_get_payment(session: AsyncSession, payment_id):
    '''Возвращает запись о платеже по id'''
    query = select(Payment).options(selectinload(Payment.user)).where(Payment.id == payment_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_last_payment_id(session: AsyncSession):
    '''Возвращает последнюю запись о платеже'''
    query = select(Payment).order_by(Payment.id.desc()).limit(1)
    result = await session.execute(query)
    payment = result.scalar_one_or_none()
    
    return payment.id if payment else 0


async def orm_get_last_payment(session: AsyncSession, user_id: UUID):
    '''Возвращает последнюю запись о платеже'''
    query = select(Payment).where(Payment.user_id == user_id).where(Payment.recurent == False).order_by(Payment.id.desc()).limit(1)
    result = await session.execute(query)
    payment = result.scalar_one_or_none()
    
    return payment.id if payment else 0


