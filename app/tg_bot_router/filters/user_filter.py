import os

from aiogram.filters import Filter
from aiogram import types
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import get_async_session
from app.database.queries import orm_get_user_by_tgid



class BlockedUsersFilter(Filter):
    def __init__(self, message: types.Message, session: AsyncSession):
        self.message = message
        self.session = session


    async def __call__(self):
        user = await orm_get_user_by_tgid(session=self.session, telegram_id=self.message.from_user.id)

        if user and user.blocked:
            return False

        return True


class AdminFilter(Filter):
    async def __call__(self, message: types.Message, session: AsyncSession) -> bool:
        user = await orm_get_user_by_tgid(session=session, telegram_id=message.from_user.id)

        if user and user.super_user == True:
            return True

        return False


