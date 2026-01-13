import uuid

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func, nullslast
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    created: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = 'users'

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=True)
    tariff_id: Mapped[int] = mapped_column(Integer, default=0)
    sub_end: Mapped[DateTime] = mapped_column(DateTime, nullable=True)
    ips: Mapped[int] = mapped_column(Integer, default=0)
    invited_by: Mapped[int] = mapped_column(BigInteger, nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    super_user: Mapped[bool] = mapped_column(Boolean, default=False)


class Tariff(Base):
    __tablename__ = 'tariffs'
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    days: Mapped[int] = mapped_column(Integer())
    ips: Mapped[int] = mapped_column(Integer())
    trafic: Mapped[int] = mapped_column(Integer())
    price: Mapped[int] = mapped_column(Numeric(10, 2))


class FAQ(Base):
    __tablename__ = 'faqs'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ask: Mapped[str] = mapped_column(Text())
    answer: Mapped[str] = mapped_column(Text())


class Payment(Base):
    __tablename__ = 'payments'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'))
    tariff_id: Mapped[int] = mapped_column(Integer(), ForeignKey('tariffs.id'))
    recurent: Mapped[bool] = mapped_column(Boolean(), default=False)
    user = relationship(argument="User")
    tariff = relationship(argument="Tariff")


class Server(Base):
    __tablename__ = 'servers'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    indoub_id: Mapped[int] = mapped_column(Integer(), nullable=False)
    login: Mapped[str] = mapped_column(String(50), nullable=False)
    password: Mapped[str] = mapped_column(String(100), nullable=False)
    need_gb: Mapped[bool] = mapped_column(Boolean(), default=False)

 
class UserServer(Base):
    __tablename__ = 'users_servers'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tun_id: Mapped[str] = mapped_column(Text())
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey('users.id'))
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"))

    server = relationship(argument=Server)

   



