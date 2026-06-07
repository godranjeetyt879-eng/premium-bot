#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║          TELEGRAM PREMIUM STORE BOT - COMPLETE SINGLE FILE          ║
║                    Aiogram 3.x + PostgreSQL + Redis                  ║
║                    Render.com Deployment Ready                       ║
║                                                                      ║
║  Bot Token : 8933696080:AAEgINT0PKhhSOnW4E-ujL682q-zdMvNXmY        ║
║  Admin ID  : 6106058051                                              ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ══════════════════════════════════════════════════════════════════════
# SECTION 1: IMPORTS
# ══════════════════════════════════════════════════════════════════════

import asyncio
import enum
import os
import random
import string
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardMarkup, KeyboardButton,
    Message, ReplyKeyboardMarkup, TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from loguru import logger
from pydantic import field_validator  # noqa
from pydantic_settings import BaseSettings
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, Float,
    ForeignKey, Integer, JSON, String, Text, and_, func, update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import relationship, selectinload
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


# ══════════════════════════════════════════════════════════════════════
# SECTION 2: CONFIGURATION & SETTINGS
# ══════════════════════════════════════════════════════════════════════

class Settings(BaseSettings):
    # Bot
    BOT_TOKEN: str = "8933696080:AAEgINT0PKhhSOnW4E-ujL682q-zdMvNXmY"
    BOT_USERNAME: str = "PremiumStoreBot"

    # Admin
    ADMIN_IDS: str = "6106058051"
    SUPER_ADMIN_ID: int = 6106058051

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/premium_bot"

    # Redis (optional, falls back to memory)
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_REDIS: bool = False

    # Webhook
    WEBHOOK_MODE: bool = False
    WEBHOOK_HOST: str = "https://your-app.onrender.com"
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_PORT: int = 8080

    # Payment / UPI
    UPI_ID: str = "yourstore@upi"
    UPI_NAME: str = "Premium Store"
    UPI_QR_CODE_URL: str = ""

    # Security
    SECRET_KEY: str = "change-me-in-production-secret"
    ENCRYPTION_KEY: str = "change-me-32-chars-exactly!!!!"

    # Rate Limiting
    RATE_LIMIT_MESSAGES: int = 30
    RATE_LIMIT_WINDOW: int = 60

    # App
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Kolkata"

    # Referral / Deposit
    REFERRAL_BONUS: float = 10.0
    MIN_DEPOSIT: float = 100.0
    MAX_DEPOSIT: float = 50000.0

    @property
    def admin_ids_list(self) -> List[int]:
        ids: List[int] = []
        for id_str in self.ADMIN_IDS.split(","):
            id_str = id_str.strip()
            if id_str.isdigit():
                ids.append(int(id_str))
        if self.SUPER_ADMIN_ID not in ids:
            ids.append(self.SUPER_ADMIN_ID)
        return ids

    @property
    def webhook_url(self) -> str:
        return f"{self.WEBHOOK_HOST}{self.WEBHOOK_PATH}"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()


# ══════════════════════════════════════════════════════════════════════
# SECTION 3: DATABASE MODELS
# ══════════════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    pass


class DepositStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OrderStatus(str, enum.Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    COMPLETED  = "completed"
    FAILED     = "failed"
    REFUNDED   = "refunded"


class TicketStatus(str, enum.Enum):
    OPEN        = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED    = "resolved"
    CLOSED      = "closed"


class TicketPriority(str, enum.Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    URGENT = "urgent"


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id   = Column(BigInteger, unique=True, nullable=False, index=True)
    username      = Column(String(255), nullable=True)
    first_name    = Column(String(255), nullable=False, default="User")
    last_name     = Column(String(255), nullable=True)
    is_banned     = Column(Boolean, default=False)
    is_admin      = Column(Boolean, default=False)
    referral_code = Column(String(20), unique=True, nullable=True, index=True)
    referred_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_count= Column(Integer, default=0)
    total_spent   = Column(Float, default=0.0)
    created_at    = Column(DateTime, default=func.now())
    updated_at    = Column(DateTime, default=func.now(), onupdate=func.now())
    last_active   = Column(DateTime, default=func.now())

    wallet    = relationship("Wallet",  back_populates="user", uselist=False)
    deposits  = relationship("Deposit", back_populates="user")
    orders    = relationship("Order",   back_populates="user")
    tickets   = relationship("Ticket",  back_populates="user")
    referrals = relationship("User", backref="referrer", foreign_keys=[referred_by])

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() if self.last_name else self.first_name

    @property
    def mention(self) -> str:
        if self.username:
            return f"@{self.username}"
        return f"<a href='tg://user?id={self.telegram_id}'>{self.full_name}</a>"


class Wallet(Base):
    __tablename__ = "wallets"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    user_id         = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance         = Column(Float, default=0.0)
    total_deposited = Column(Float, default=0.0)
    total_withdrawn = Column(Float, default=0.0)
    created_at      = Column(DateTime, default=func.now())
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="wallet")


class Deposit(Base):
    __tablename__ = "deposits"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    user_id            = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount             = Column(Float, nullable=False)
    status             = Column(Enum(DepositStatus), default=DepositStatus.PENDING)
    screenshot_file_id = Column(String(512), nullable=True)
    transaction_id     = Column(String(255), nullable=True)
    admin_remark       = Column(Text, nullable=True)
    approved_by        = Column(BigInteger, nullable=True)
    approved_at        = Column(DateTime, nullable=True)
    created_at         = Column(DateTime, default=func.now())
    updated_at         = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="deposits")


class Plan(Base):
    __tablename__ = "plans"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    name            = Column(String(255), nullable=False)
    description     = Column(Text, nullable=True)
    duration_months = Column(Integer, nullable=False)
    price           = Column(Float, nullable=False)
    original_price  = Column(Float, nullable=True)
    is_active       = Column(Boolean, default=True)
    sort_order      = Column(Integer, default=0)
    emoji           = Column(String(10), default="⭐")
    features        = Column(JSON, nullable=True)
    created_at      = Column(DateTime, default=func.now())
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())

    orders = relationship("Order", back_populates="plan")

    @property
    def discount_percent(self) -> int:
        if self.original_price and self.original_price > self.price:
            return int(((self.original_price - self.price) / self.original_price) * 100)
        return 0


class Order(Base):
    __tablename__ = "orders"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan_id          = Column(Integer, ForeignKey("plans.id"), nullable=False)
    telegram_account = Column(String(255), nullable=True)
    amount           = Column(Float, nullable=False)
    status           = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    coupon_id        = Column(Integer, ForeignKey("coupons.id"), nullable=True)
    discount_amount  = Column(Float, default=0.0)
    admin_note       = Column(Text, nullable=True)
    completed_at     = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=func.now())
    updated_at       = Column(DateTime, default=func.now(), onupdate=func.now())

    user   = relationship("User",   back_populates="orders")
    plan   = relationship("Plan",   back_populates="orders")
    coupon = relationship("Coupon", back_populates="orders")


class Coupon(Base):
    __tablename__ = "coupons"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    code           = Column(String(50), unique=True, nullable=False, index=True)
    discount_type  = Column(String(20), default="percentage")
    discount_value = Column(Float, nullable=False)
    min_order_amount = Column(Float, default=0.0)
    max_uses       = Column(Integer, nullable=True)
    used_count     = Column(Integer, default=0)
    is_active      = Column(Boolean, default=True)
    expires_at     = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=func.now())

    orders = relationship("Order", back_populates="coupon")

    @property
    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.max_uses and self.used_count >= self.max_uses:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True


class Ticket(Base):
    __tablename__ = "tickets"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject     = Column(String(255), nullable=False)
    message     = Column(Text, nullable=False)
    status      = Column(Enum(TicketStatus), default=TicketStatus.OPEN)
    priority    = Column(Enum(TicketPriority), default=TicketPriority.MEDIUM)
    admin_reply = Column(Text, nullable=True)
    replied_by  = Column(BigInteger, nullable=True)
    replied_at  = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=func.now())
    updated_at  = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="tickets")


class Admin(Base):
    __tablename__ = "admins"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id  = Column(BigInteger, unique=True, nullable=False)
    username     = Column(String(255), nullable=True)
    is_super_admin = Column(Boolean, default=False)
    permissions  = Column(JSON, default=dict)
    added_by     = Column(BigInteger, nullable=True)
    created_at   = Column(DateTime, default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    admin_id    = Column(BigInteger, nullable=False)
    action      = Column(String(255), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id   = Column(Integer, nullable=True)
    details     = Column(JSON, nullable=True)
    created_at  = Column(DateTime, default=func.now())


# ══════════════════════════════════════════════════════════════════════
# SECTION 4: DATABASE ENGINE & SESSION
# ══════════════════════════════════════════════════════════════════════

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=NullPool,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Database tables created")


@asynccontextmanager
async def get_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ══════════════════════════════════════════════════════════════════════
# SECTION 5: SERVICES
# ══════════════════════════════════════════════════════════════════════

def _generate_referral_code(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


# ── User Service ──────────────────────────────────────────────────────

class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(
        self, telegram_id: int, username: str = None,
        first_name: str = "User", last_name: str = None,
        referral_code: str = None,
    ) -> User:
        from sqlalchemy import select as sa_select
        result = await self.session.execute(
            sa_select(User)
            .where(User.telegram_id == telegram_id)
            .options(selectinload(User.wallet))
        )
        user = result.scalar_one_or_none()

        if not user:
            from sqlalchemy import select as sa_select
            ref_code = _generate_referral_code()
            for _ in range(10):
                ex = await self.session.execute(
                    sa_select(User).where(User.referral_code == ref_code)
                )
                if not ex.scalar_one_or_none():
                    break
                ref_code = _generate_referral_code()

            referrer = None
            if referral_code:
                ex = await self.session.execute(
                    sa_select(User).where(User.referral_code == referral_code)
                )
                referrer = ex.scalar_one_or_none()

            user = User(
                telegram_id=telegram_id, username=username,
                first_name=first_name, last_name=last_name,
                referral_code=ref_code,
                referred_by=referrer.id if referrer else None,
            )
            self.session.add(user)
            await self.session.flush()

            wallet = Wallet(user_id=user.id, balance=0.0)
            self.session.add(wallet)

            if referrer:
                await self._credit_wallet(referrer.id, settings.REFERRAL_BONUS)
                await self.session.execute(
                    update(User).where(User.id == referrer.id)
                    .values(referral_count=User.referral_count + 1)
                )
            await self.session.flush()
            logger.info(f"New user: {telegram_id} @{username}")
        else:
            user.username    = username
            user.first_name  = first_name or user.first_name
            user.last_name   = last_name
            user.last_active = func.now()
            await self.session.flush()
        return user

    async def get_user(self, telegram_id: int) -> Optional[User]:
        from sqlalchemy import select as sa_select
        result = await self.session.execute(
            sa_select(User).where(User.telegram_id == telegram_id)
            .options(selectinload(User.wallet))
        )
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        from sqlalchemy import select as sa_select
        result = await self.session.execute(
            sa_select(User).where(User.id == user_id)
            .options(selectinload(User.wallet))
        )
        return result.scalar_one_or_none()

    async def ban_user(self, telegram_id: int) -> bool:
        r = await self.session.execute(
            update(User).where(User.telegram_id == telegram_id)
            .values(is_banned=True).returning(User.id)
        )
        return r.scalar_one_or_none() is not None

    async def unban_user(self, telegram_id: int) -> bool:
        r = await self.session.execute(
            update(User).where(User.telegram_id == telegram_id)
            .values(is_banned=False).returning(User.id)
        )
        return r.scalar_one_or_none() is not None

    async def add_balance(self, user_id: int, amount: float) -> float:
        return await self._credit_wallet(user_id, amount)

    async def remove_balance(self, user_id: int, amount: float) -> Optional[float]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Wallet).where(Wallet.user_id == user_id))
        wallet = r.scalar_one_or_none()
        if not wallet or wallet.balance < amount:
            return None
        wallet.balance -= amount
        wallet.total_withdrawn += amount
        await self.session.flush()
        return wallet.balance

    async def deduct_balance(self, user_id: int, amount: float) -> bool:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Wallet).where(Wallet.user_id == user_id))
        wallet = r.scalar_one_or_none()
        if not wallet or wallet.balance < amount:
            return False
        wallet.balance -= amount
        await self.session.flush()
        await self.session.execute(
            update(User).where(User.id == user_id)
            .values(total_spent=User.total_spent + amount)
        )
        return True

    async def _credit_wallet(self, user_id: int, amount: float) -> float:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Wallet).where(Wallet.user_id == user_id))
        wallet = r.scalar_one_or_none()
        if wallet:
            wallet.balance         += amount
            wallet.total_deposited += amount
            await self.session.flush()
            return wallet.balance
        return 0.0

    async def get_all_users(self, limit: int = 10000, offset: int = 0) -> List[User]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(User).limit(limit).offset(offset).order_by(User.created_at.desc())
        )
        return list(r.scalars().all())

    async def search_user(self, query: str) -> List[User]:
        from sqlalchemy import select as sa_select
        if query.isdigit():
            r = await self.session.execute(
                sa_select(User).where(User.telegram_id == int(query))
                .options(selectinload(User.wallet))
            )
        else:
            r = await self.session.execute(
                sa_select(User).where(User.username.ilike(f"%{query}%"))
                .options(selectinload(User.wallet)).limit(10)
            )
        return list(r.scalars().all())

    async def get_stats(self) -> dict:
        from sqlalchemy import select as sa_select
        total     = await self.session.execute(sa_select(func.count(User.id)))
        banned    = await self.session.execute(sa_select(func.count(User.id)).where(User.is_banned == True))
        today     = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        new_today = await self.session.execute(
            sa_select(func.count(User.id)).where(User.created_at >= today)
        )
        return {
            "total":     total.scalar()     or 0,
            "banned":    banned.scalar()    or 0,
            "new_today": new_today.scalar() or 0,
        }


# ── Deposit Service ───────────────────────────────────────────────────

class DepositService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_deposit(self, user_id: int, amount: float,
                              screenshot_file_id: str) -> Deposit:
        dep = Deposit(user_id=user_id, amount=amount,
                      status=DepositStatus.PENDING,
                      screenshot_file_id=screenshot_file_id)
        self.session.add(dep)
        await self.session.flush()
        return dep

    async def get_deposit(self, deposit_id: int) -> Optional[Deposit]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Deposit).where(Deposit.id == deposit_id)
            .options(selectinload(Deposit.user).selectinload(User.wallet))
        )
        return r.scalar_one_or_none()

    async def approve_deposit(self, deposit_id: int, admin_id: int,
                               remark: str = "Approved") -> tuple:
        dep = await self.get_deposit(deposit_id)
        if not dep or dep.status != DepositStatus.PENDING:
            return False, None
        dep.status       = DepositStatus.APPROVED
        dep.approved_by  = admin_id
        dep.approved_at  = datetime.utcnow()
        dep.admin_remark = remark
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Wallet).where(Wallet.user_id == dep.user_id))
        wallet = r.scalar_one_or_none()
        if wallet:
            wallet.balance         += dep.amount
            wallet.total_deposited += dep.amount
        await self.session.flush()
        return True, dep

    async def reject_deposit(self, deposit_id: int, admin_id: int,
                              reason: str) -> tuple:
        dep = await self.get_deposit(deposit_id)
        if not dep or dep.status != DepositStatus.PENDING:
            return False, None
        dep.status       = DepositStatus.REJECTED
        dep.approved_by  = admin_id
        dep.approved_at  = datetime.utcnow()
        dep.admin_remark = reason
        await self.session.flush()
        return True, dep

    async def get_all_deposits(self, limit: int = 10, offset: int = 0,
                                status: Optional[DepositStatus] = None) -> List[Deposit]:
        from sqlalchemy import select as sa_select
        q = sa_select(Deposit).options(selectinload(Deposit.user))
        if status:
            q = q.where(Deposit.status == status)
        q = q.order_by(Deposit.created_at.desc()).limit(limit).offset(offset)
        r = await self.session.execute(q)
        return list(r.scalars().all())

    async def get_user_deposits(self, user_id: int, limit: int = 10) -> List[Deposit]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Deposit).where(Deposit.user_id == user_id)
            .order_by(Deposit.created_at.desc()).limit(limit)
        )
        return list(r.scalars().all())

    async def get_stats(self) -> dict:
        from sqlalchemy import select as sa_select
        total    = await self.session.execute(sa_select(func.count(Deposit.id)))
        pending  = await self.session.execute(
            sa_select(func.count(Deposit.id)).where(Deposit.status == DepositStatus.PENDING)
        )
        approved = await self.session.execute(
            sa_select(func.count(Deposit.id), func.sum(Deposit.amount))
            .where(Deposit.status == DepositStatus.APPROVED)
        )
        apr_row = approved.first()
        today   = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        daily   = await self.session.execute(
            sa_select(func.sum(Deposit.amount)).where(
                and_(Deposit.status == DepositStatus.APPROVED,
                     Deposit.approved_at >= today)
            )
        )
        return {
            "total":         total.scalar()   or 0,
            "pending":       pending.scalar() or 0,
            "approved_count":apr_row[0]       or 0,
            "total_revenue": apr_row[1]       or 0.0,
            "daily_revenue": daily.scalar()   or 0.0,
        }


# ── Plan Service ──────────────────────────────────────────────────────

class PlanService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_plans(self) -> List[Plan]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Plan).where(Plan.is_active == True)
            .order_by(Plan.sort_order.asc(), Plan.price.asc())
        )
        return list(r.scalars().all())

    async def get_all_plans(self) -> List[Plan]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Plan).order_by(Plan.sort_order.asc(), Plan.price.asc())
        )
        return list(r.scalars().all())

    async def get_plan(self, plan_id: int) -> Optional[Plan]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Plan).where(Plan.id == plan_id))
        return r.scalar_one_or_none()

    async def create_plan(self, name: str, description: Optional[str],
                           duration_months: int, price: float,
                           original_price: float = None, emoji: str = "⭐",
                           features: list = None) -> Plan:
        plan = Plan(name=name, description=description,
                    duration_months=duration_months, price=price,
                    original_price=original_price, emoji=emoji,
                    features=features or [])
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def update_plan(self, plan_id: int, **kwargs) -> bool:
        r = await self.session.execute(
            update(Plan).where(Plan.id == plan_id).values(**kwargs).returning(Plan.id)
        )
        return r.scalar_one_or_none() is not None

    async def toggle_plan(self, plan_id: int) -> Optional[bool]:
        plan = await self.get_plan(plan_id)
        if not plan:
            return None
        new_status = not plan.is_active
        await self.update_plan(plan_id, is_active=new_status)
        return new_status

    async def delete_plan(self, plan_id: int) -> bool:
        return await self.update_plan(plan_id, is_active=False)


# ── Order Service ─────────────────────────────────────────────────────

class OrderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_order(self, user_id: int, plan_id: int,
                            telegram_account: str, amount: float,
                            coupon_id: int = None, discount_amount: float = 0.0) -> Order:
        order = Order(user_id=user_id, plan_id=plan_id,
                      telegram_account=telegram_account, amount=amount,
                      coupon_id=coupon_id, discount_amount=discount_amount,
                      status=OrderStatus.PENDING)
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_order(self, order_id: int) -> Optional[Order]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Order).where(Order.id == order_id)
            .options(selectinload(Order.user), selectinload(Order.plan))
        )
        return r.scalar_one_or_none()

    async def get_user_orders(self, user_id: int, limit: int = 10) -> List[Order]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Order).where(Order.user_id == user_id)
            .options(selectinload(Order.plan))
            .order_by(Order.created_at.desc()).limit(limit)
        )
        return list(r.scalars().all())

    async def get_all_orders(self, limit: int = 20, offset: int = 0,
                              status: Optional[OrderStatus] = None) -> List[Order]:
        from sqlalchemy import select as sa_select
        q = sa_select(Order).options(selectinload(Order.user), selectinload(Order.plan))
        if status:
            q = q.where(Order.status == status)
        q = q.order_by(Order.created_at.desc()).limit(limit).offset(offset)
        r = await self.session.execute(q)
        return list(r.scalars().all())

    async def update_order_status(self, order_id: int, status: OrderStatus,
                                   admin_note: str = None) -> bool:
        values: dict = {"status": status}
        if admin_note:
            values["admin_note"] = admin_note
        if status == OrderStatus.COMPLETED:
            values["completed_at"] = datetime.utcnow()
        r = await self.session.execute(
            update(Order).where(Order.id == order_id).values(**values).returning(Order.id)
        )
        return r.scalar_one_or_none() is not None

    async def get_stats(self) -> dict:
        from sqlalchemy import select as sa_select
        total     = await self.session.execute(sa_select(func.count(Order.id)))
        pending   = await self.session.execute(
            sa_select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
        )
        completed = await self.session.execute(
            sa_select(func.count(Order.id), func.sum(Order.amount))
            .where(Order.status == OrderStatus.COMPLETED)
        )
        row = completed.first()
        return {
            "total":     total.scalar()   or 0,
            "pending":   pending.scalar() or 0,
            "completed": row[0]           or 0,
            "revenue":   row[1]           or 0.0,
        }


# ── Coupon Service ────────────────────────────────────────────────────

class CouponService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def validate_coupon(self, code: str, amount: float) -> tuple:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Coupon).where(Coupon.code == code.upper())
        )
        coupon = r.scalar_one_or_none()
        if not coupon:
            return None, "❌ Invalid coupon code."
        if not coupon.is_valid:
            return None, "❌ Coupon expired or usage limit reached."
        if amount < coupon.min_order_amount:
            return None, f"❌ Minimum order ₹{coupon.min_order_amount:.0f} required."
        return coupon, "✅ Coupon applied!"

    async def apply_coupon(self, coupon_id: int, order_amount: float) -> float:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(sa_select(Coupon).where(Coupon.id == coupon_id))
        coupon = r.scalar_one_or_none()
        if not coupon:
            return 0.0
        if coupon.discount_type == "percentage":
            discount = order_amount * (coupon.discount_value / 100)
        else:
            discount = coupon.discount_value
        coupon.used_count += 1
        await self.session.flush()
        return min(discount, order_amount)

    async def create_coupon(self, code: str, discount_type: str, discount_value: float,
                             min_order: float = 0, max_uses: int = None,
                             expires_at=None) -> Coupon:
        coupon = Coupon(code=code.upper(), discount_type=discount_type,
                        discount_value=discount_value, min_order_amount=min_order,
                        max_uses=max_uses, expires_at=expires_at)
        self.session.add(coupon)
        await self.session.flush()
        return coupon

    async def get_all_coupons(self) -> List[Coupon]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Coupon).order_by(Coupon.created_at.desc())
        )
        return list(r.scalars().all())


# ── Ticket Service ────────────────────────────────────────────────────

class TicketService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_ticket(self, user_id: int, subject: str, message: str,
                             priority: TicketPriority = TicketPriority.MEDIUM) -> Ticket:
        t = Ticket(user_id=user_id, subject=subject,
                   message=message, priority=priority)
        self.session.add(t)
        await self.session.flush()
        return t

    async def get_ticket(self, ticket_id: int) -> Optional[Ticket]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Ticket).where(Ticket.id == ticket_id)
            .options(selectinload(Ticket.user))
        )
        return r.scalar_one_or_none()

    async def get_user_tickets(self, user_id: int, limit: int = 5) -> List[Ticket]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Ticket).where(Ticket.user_id == user_id)
            .order_by(Ticket.created_at.desc()).limit(limit)
        )
        return list(r.scalars().all())

    async def get_open_tickets(self, limit: int = 20) -> List[Ticket]:
        from sqlalchemy import select as sa_select
        r = await self.session.execute(
            sa_select(Ticket).where(Ticket.status == TicketStatus.OPEN)
            .options(selectinload(Ticket.user))
            .order_by(Ticket.created_at.asc()).limit(limit)
        )
        return list(r.scalars().all())

    async def reply_ticket(self, ticket_id: int, admin_id: int, reply: str) -> bool:
        r = await self.session.execute(
            update(Ticket).where(Ticket.id == ticket_id)
            .values(admin_reply=reply, replied_by=admin_id,
                    replied_at=datetime.utcnow(), status=TicketStatus.RESOLVED)
            .returning(Ticket.id)
        )
        return r.scalar_one_or_none() is not None

    async def close_ticket(self, ticket_id: int) -> bool:
        r = await self.session.execute(
            update(Ticket).where(Ticket.id == ticket_id)
            .values(status=TicketStatus.CLOSED).returning(Ticket.id)
        )
        return r.scalar_one_or_none() is not None

    async def get_stats(self) -> dict:
        from sqlalchemy import select as sa_select
        open_c = await self.session.execute(
            sa_select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.OPEN)
        )
        total = await self.session.execute(sa_select(func.count(Ticket.id)))
        return {"open": open_c.scalar() or 0, "total": total.scalar() or 0}


# ══════════════════════════════════════════════════════════════════════
# SECTION 6: KEYBOARDS
# ══════════════════════════════════════════════════════════════════════

# ── User Keyboards ─────────────────────────────────────────────────────

def main_menu_kb() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="👤 My Profile"),  KeyboardButton(text="💰 My Wallet"))
    b.row(KeyboardButton(text="⭐ Buy Premium"), KeyboardButton(text="📦 My Orders"))
    b.row(KeyboardButton(text="🎫 Support"),     KeyboardButton(text="🎟 Coupon"))
    b.row(KeyboardButton(text="👥 Referrals"),   KeyboardButton(text="ℹ️ Help"))
    return b.as_markup(resize_keyboard=True)


def wallet_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Deposit Balance",  callback_data="deposit_start")
    b.button(text="📊 Deposit History", callback_data="deposit_history")
    b.adjust(1)
    return b.as_markup()


def deposit_amount_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for amt in [100, 200, 500, 1000, 2000, 5000]:
        b.button(text=f"₹{amt}", callback_data=f"deposit_amount:{amt}")
    b.button(text="✏️ Custom Amount", callback_data="deposit_custom")
    b.button(text="🔙 Back",          callback_data="wallet_back")
    b.adjust(3, 3, 1, 1)
    return b.as_markup()


def cancel_kb(cb: str = "cancel") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data=cb)
    return b.as_markup()


def plans_kb(plans: List[Plan]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in plans:
        disc = f" (-{p.discount_percent}%)" if p.discount_percent > 0 else ""
        b.button(text=f"{p.emoji} {p.name} — ₹{p.price:.0f}{disc}",
                 callback_data=f"plan_select:{p.id}")
    b.adjust(1)
    return b.as_markup()


def plan_detail_kb(plan_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Buy Now",       callback_data=f"plan_buy:{plan_id}")
    b.button(text="🎟 Apply Coupon",  callback_data=f"plan_coupon:{plan_id}")
    b.button(text="🔙 Back to Plans", callback_data="plans_back")
    b.adjust(2, 1)
    return b.as_markup()


def confirm_order_kb(plan_id: int, coupon_code: str = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    suffix = f":{coupon_code}" if coupon_code else ""
    b.button(text="✅ Confirm Purchase", callback_data=f"order_confirm:{plan_id}{suffix}")
    b.button(text="❌ Cancel",          callback_data="order_cancel")
    b.adjust(1)
    return b.as_markup()


def order_history_kb(orders: List[Order]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    emoji_map = {"pending": "⏳", "processing": "🔄",
                 "completed": "✅", "failed": "❌", "refunded": "↩️"}
    for o in orders:
        e = emoji_map.get(o.status.value, "❓")
        name = o.plan.name if o.plan else "N/A"
        b.button(text=f"{e} #{o.id} — {name}", callback_data=f"order_detail:{o.id}")
    b.adjust(1)
    return b.as_markup()


def deposit_history_kb(deposits: List[Deposit]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    e_map = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    for d in deposits:
        e = e_map.get(d.status.value, "❓")
        b.button(text=f"{e} ₹{d.amount:.0f} — {d.created_at.strftime('%d/%m/%y')}",
                 callback_data=f"deposit_detail:{d.id}")
    b.adjust(1)
    return b.as_markup()


def referral_kb(referral_code: str, bot_username: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    ref_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Get+Telegram+Premium+cheap!"
    b.button(text="📤 Share Referral Link", url=share_url)
    return b.as_markup()


def ticket_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 New Ticket",  callback_data="ticket_new")
    b.button(text="📋 My Tickets", callback_data="ticket_list")
    b.adjust(1)
    return b.as_markup()


def back_kb(cb: str = "main_menu") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Back", callback_data=cb)
    return b.as_markup()


# ── Admin Keyboards ────────────────────────────────────────────────────

def admin_menu_kb() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="📊 Dashboard"),  KeyboardButton(text="💰 Deposits"))
    b.row(KeyboardButton(text="👥 Users"),      KeyboardButton(text="📦 Orders"))
    b.row(KeyboardButton(text="⭐ Plans"),       KeyboardButton(text="🎟 Coupons"))
    b.row(KeyboardButton(text="🎫 Tickets"),    KeyboardButton(text="📢 Broadcast"))
    b.row(KeyboardButton(text="🔙 User Menu"))
    return b.as_markup(resize_keyboard=True)


def deposit_mgmt_kb(deposits: List[Deposit], offset: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    e_map = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    for d in deposits:
        e    = e_map.get(d.status.value, "❓")
        name = (d.user.username or d.user.full_name) if d.user else "Unknown"
        b.button(text=f"{e} #{d.id} @{name} ₹{d.amount:.0f}",
                 callback_data=f"admin_dep:{d.id}")
    if offset > 0:
        b.button(text="◀️ Prev", callback_data=f"admin_deps:{offset-10}")
    b.button(text="🔄 Refresh", callback_data="admin_deps:0")
    if len(deposits) >= 10:
        b.button(text="▶️ Next", callback_data=f"admin_deps:{offset+10}")
    b.adjust(1)
    return b.as_markup()


def deposit_action_kb(dep_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "pending":
        b.button(text="✅ Approve", callback_data=f"dep_approve:{dep_id}")
        b.button(text="❌ Reject",  callback_data=f"dep_reject:{dep_id}")
    b.button(text="🖼 Screenshot", callback_data=f"dep_screenshot:{dep_id}")
    b.button(text="🔙 Back",       callback_data="admin_deps:0")
    b.adjust(2, 1, 1)
    return b.as_markup()


def user_mgmt_kb(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_banned:
        b.button(text="✅ Unban",      callback_data=f"admin_unban:{user_id}")
    else:
        b.button(text="🚫 Ban",        callback_data=f"admin_ban:{user_id}")
    b.button(text="➕ Add Balance",    callback_data=f"admin_add_bal:{user_id}")
    b.button(text="➖ Remove Balance", callback_data=f"admin_rem_bal:{user_id}")
    b.button(text="📦 View Orders",   callback_data=f"admin_user_orders:{user_id}")
    b.adjust(1)
    return b.as_markup()


def plan_mgmt_kb(plans: List[Plan]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in plans:
        status = "✅" if p.is_active else "❌"
        b.button(text=f"{status} {p.emoji} {p.name} — ₹{p.price:.0f}",
                 callback_data=f"admin_plan:{p.id}")
    b.button(text="➕ Add Plan", callback_data="admin_plan_add")
    b.adjust(1)
    return b.as_markup()


def plan_action_kb(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Disable" if is_active else "✅ Enable",
             callback_data=f"plan_toggle:{plan_id}")
    b.button(text="✏️ Edit Price",   callback_data=f"plan_edit_price:{plan_id}")
    b.button(text="🗑 Delete Plan",  callback_data=f"plan_delete:{plan_id}")
    b.button(text="🔙 Back",         callback_data="admin_plans_list")
    b.adjust(1)
    return b.as_markup()


def broadcast_type_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Text",  callback_data="broadcast_text")
    b.button(text="🖼 Photo", callback_data="broadcast_photo")
    b.button(text="🎥 Video", callback_data="broadcast_video")
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    b.adjust(3, 1)
    return b.as_markup()


def broadcast_target_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 All Users",       callback_data="broadcast_target:all")
    b.button(text="🛍 Buyers Only",     callback_data="broadcast_target:buyers")
    b.button(text="❌ Cancel",          callback_data="admin_cancel")
    b.adjust(1)
    return b.as_markup()


def ticket_action_kb(ticket_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status in ("open", "in_progress"):
        b.button(text="💬 Reply", callback_data=f"ticket_reply:{ticket_id}")
        b.button(text="🔒 Close", callback_data=f"ticket_close:{ticket_id}")
    b.button(text="🔙 Back", callback_data="admin_tickets_list")
    b.adjust(2, 1)
    return b.as_markup()


def order_action_kb(order_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "pending":
        b.button(text="🔄 Processing",  callback_data=f"order_processing:{order_id}")
        b.button(text="✅ Complete",     callback_data=f"order_complete:{order_id}")
        b.button(text="❌ Failed",       callback_data=f"order_failed:{order_id}")
    elif status == "processing":
        b.button(text="✅ Complete",     callback_data=f"order_complete:{order_id}")
        b.button(text="❌ Failed",       callback_data=f"order_failed:{order_id}")
    b.button(text="🔙 Back", callback_data="admin_orders_list")
    b.adjust(2, 1)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════
# SECTION 7: MIDDLEWARES
# ══════════════════════════════════════════════════════════════════════

class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with get_session() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        telegram_user = None
        if isinstance(event, Message):
            telegram_user = event.from_user
        elif isinstance(event, CallbackQuery):
            telegram_user = event.from_user

        if telegram_user and "session" in data:
            session = data["session"]
            svc     = UserService(session)

            referral_code = None
            if isinstance(event, Message) and event.text:
                parts = event.text.split(" ", 1)
                if len(parts) > 1 and parts[1].startswith("ref_"):
                    referral_code = parts[1][4:]

            user = await svc.get_or_create_user(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name or "User",
                last_name=telegram_user.last_name,
                referral_code=referral_code,
            )
            data["user"]     = user
            data["is_admin"] = telegram_user.id in settings.admin_ids_list

            if user and user.is_banned:
                if isinstance(event, Message):
                    await event.answer("🚫 You are banned from this bot.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 You are banned.", show_alert=True)
                return

        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, limit: int = 30, window: int = 60):
        self.limit   = limit
        self.window  = window
        self._store: Dict[int, list] = defaultdict(list)

    async def __call__(self, handler, event, data):
        uid = None
        if isinstance(event, (Message, CallbackQuery)):
            uid = event.from_user.id if event.from_user else None

        if uid and uid not in settings.admin_ids_list:
            now = time.time()
            self._store[uid] = [t for t in self._store[uid] if now - t < self.window]
            if len(self._store[uid]) >= self.limit:
                if isinstance(event, Message):
                    await event.answer("⚠️ Too many requests. Slow down.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Too many requests!", show_alert=True)
                return
            self._store[uid].append(now)

        return await handler(event, data)


# ══════════════════════════════════════════════════════════════════════
# SECTION 8: FSM STATES
# ══════════════════════════════════════════════════════════════════════

class DepositStates(StatesGroup):
    waiting_custom_amount = State()
    waiting_screenshot    = State()

class OrderStates(StatesGroup):
    waiting_telegram_account = State()
    waiting_coupon           = State()

class TicketStates(StatesGroup):
    waiting_subject = State()
    waiting_message = State()

class AdminStates(StatesGroup):
    reject_reason        = State()
    search_user          = State()
    add_balance_amount   = State()
    remove_balance_amount= State()
    plan_name            = State()
    plan_duration        = State()
    plan_price           = State()
    plan_description     = State()
    plan_edit_price      = State()
    broadcast_message    = State()
    broadcast_photo      = State()
    ticket_reply_msg     = State()
    coupon_code          = State()
    coupon_discount      = State()


# ══════════════════════════════════════════════════════════════════════
# SECTION 9: HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

def fmt_profile(user: User) -> str:
    bal   = user.wallet.balance         if user.wallet else 0.0
    dep   = user.wallet.total_deposited if user.wallet else 0.0
    status= "🚫 BANNED" if user.is_banned else "✅ Active"
    return (
        f"👤 <b>My Profile</b>\n\n"
        f"🆔 <b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"👤 <b>Name:</b> {user.full_name}\n"
        f"📱 <b>Username:</b> @{user.username or 'Not set'}\n"
        f"📊 <b>Status:</b> {status}\n\n"
        f"💰 <b>Wallet Balance:</b> ₹{bal:.2f}\n"
        f"📈 <b>Total Deposited:</b> ₹{dep:.2f}\n"
        f"💸 <b>Total Spent:</b> ₹{user.total_spent:.2f}\n\n"
        f"👥 <b>Referrals:</b> {user.referral_count}\n"
        f"🎫 <b>Referral Code:</b> <code>{user.referral_code}</code>\n\n"
        f"📅 <b>Member Since:</b> {user.created_at.strftime('%d %b %Y')}"
    )


async def notify_admins(bot: Bot, text: str, reply_markup=None):
    for aid in settings.admin_ids_list:
        try:
            await bot.send_message(aid, text, parse_mode="HTML",
                                   reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Admin notify error {aid}: {e}")


def admin_quick_deposit_kb(dep_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Approve", callback_data=f"dep_approve:{dep_id}")
    b.button(text="❌ Reject",  callback_data=f"dep_reject:{dep_id}")
    b.button(text="👁 View",   callback_data=f"admin_dep:{dep_id}")
    b.adjust(2, 1)
    return b.as_markup()


def admin_quick_order_kb(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Complete",    callback_data=f"order_complete:{order_id}")
    b.button(text="🔄 Processing", callback_data=f"order_processing:{order_id}")
    b.button(text="👁 View",       callback_data=f"admin_order:{order_id}")
    b.adjust(2, 1)
    return b.as_markup()


# ══════════════════════════════════════════════════════════════════════
# SECTION 10: USER HANDLERS
# ══════════════════════════════════════════════════════════════════════

user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, user: User, state: FSMContext):
    await state.clear()
    text = (
        f"👋 Welcome to <b>Premium Store</b>, {user.first_name}!\n\n"
        f"🌟 Get <b>Telegram Premium</b> at the best prices!\n\n"
        f"Choose an option from the menu below:"
    )
    is_admin_user = message.from_user.id in settings.admin_ids_list
    kb = admin_menu_kb() if is_admin_user else main_menu_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@user_router.message(F.text == "👤 My Profile")
async def show_profile(message: Message, user: User, session: AsyncSession):
    svc  = UserService(session)
    user = await svc.get_user(message.from_user.id)
    await message.answer(fmt_profile(user), parse_mode="HTML")


@user_router.message(F.text == "💰 My Wallet")
async def show_wallet(message: Message, user: User):
    bal = user.wallet.balance         if user.wallet else 0.0
    dep = user.wallet.total_deposited if user.wallet else 0.0
    await message.answer(
        f"💰 <b>My Wallet</b>\n\n"
        f"💵 <b>Balance:</b> ₹{bal:.2f}\n"
        f"📈 <b>Total Deposited:</b> ₹{dep:.2f}\n\n"
        f"Use the buttons below to manage your wallet.",
        parse_mode="HTML", reply_markup=wallet_kb()
    )


# ── Deposit flow ───────────────────────────────────────────────────────

@user_router.callback_query(F.data == "deposit_start")
async def deposit_start(callback: CallbackQuery, state: FSMContext):
    text = (
        f"💳 <b>Deposit Instructions</b>\n\n"
        f"📱 <b>UPI ID:</b> <code>{settings.UPI_ID}</code>\n"
        f"👤 <b>Pay To:</b> {settings.UPI_NAME}\n\n"
        f"Select amount or enter custom:\n"
        f"⚠️ <i>Min: ₹{settings.MIN_DEPOSIT:.0f} | Max: ₹{settings.MAX_DEPOSIT:.0f}</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=deposit_amount_kb())


@user_router.callback_query(F.data.startswith("deposit_amount:"))
async def deposit_amount_selected(callback: CallbackQuery, state: FSMContext):
    amount = float(callback.data.split(":")[1])
    await state.update_data(deposit_amount=amount)
    await state.set_state(DepositStates.waiting_screenshot)
    await callback.message.edit_text(
        f"💳 <b>Complete Payment</b>\n\n"
        f"📱 <b>UPI ID:</b> <code>{settings.UPI_ID}</code>\n"
        f"💰 <b>Amount:</b> ₹{amount:.2f}\n\n"
        f"1️⃣ Pay ₹{amount:.2f} to UPI ID above\n"
        f"2️⃣ Screenshot the payment confirmation\n"
        f"3️⃣ Send screenshot here 📸",
        parse_mode="HTML", reply_markup=cancel_kb("deposit_cancel")
    )


@user_router.callback_query(F.data == "deposit_custom")
async def deposit_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositStates.waiting_custom_amount)
    await callback.message.edit_text(
        f"✏️ <b>Enter Amount</b>\n\n"
        f"Enter deposit amount (₹{settings.MIN_DEPOSIT:.0f}–₹{settings.MAX_DEPOSIT:.0f}):",
        parse_mode="HTML", reply_markup=cancel_kb("deposit_cancel")
    )


@user_router.message(DepositStates.waiting_custom_amount)
async def process_custom_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", ""))
        if amount < settings.MIN_DEPOSIT:
            await message.answer(f"❌ Minimum is ₹{settings.MIN_DEPOSIT:.0f}")
            return
        if amount > settings.MAX_DEPOSIT:
            await message.answer(f"❌ Maximum is ₹{settings.MAX_DEPOSIT:.0f}")
            return
    except ValueError:
        await message.answer("❌ Enter a valid number.")
        return
    await state.update_data(deposit_amount=amount)
    await state.set_state(DepositStates.waiting_screenshot)
    await message.answer(
        f"💳 <b>Complete Payment</b>\n\n"
        f"📱 <b>UPI ID:</b> <code>{settings.UPI_ID}</code>\n"
        f"💰 <b>Amount:</b> ₹{amount:.2f}\n\n"
        f"Pay and send screenshot here 📸",
        parse_mode="HTML", reply_markup=cancel_kb("deposit_cancel")
    )


@user_router.message(DepositStates.waiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext,
                              session: AsyncSession, user: User):
    data   = await state.get_data()
    amount = data.get("deposit_amount")
    if not amount:
        await state.clear()
        await message.answer("❌ Session expired. Please start again.")
        return

    file_id = message.photo[-1].file_id
    dep_svc = DepositService(session)
    deposit = await dep_svc.create_deposit(user.id, amount, file_id)
    await state.clear()

    await message.answer(
        f"✅ <b>Deposit Request Submitted!</b>\n\n"
        f"🆔 Request ID: <b>#{deposit.id}</b>\n"
        f"💰 Amount: ₹{amount:.2f}\n"
        f"⏳ Status: Pending\n\n"
        f"Admin will review within 30 minutes.",
        parse_mode="HTML"
    )

    admin_text = (
        f"🔔 <b>New Deposit!</b>\n\n"
        f"🆔 #{deposit.id}\n"
        f"👤 {user.mention} (<code>{user.telegram_id}</code>)\n"
        f"💰 ₹{amount:.2f}\n"
        f"📅 {deposit.created_at.strftime('%d %b %Y %H:%M')}"
    )
    for aid in settings.admin_ids_list:
        try:
            await message.bot.send_photo(
                aid, photo=file_id,
                caption=admin_text, parse_mode="HTML",
                reply_markup=admin_quick_deposit_kb(deposit.id)
            )
        except Exception as e:
            logger.error(f"Admin notify fail {aid}: {e}")


@user_router.message(DepositStates.waiting_screenshot)
async def wrong_screenshot(message: Message):
    await message.answer("❌ Please send a photo/screenshot.")


@user_router.callback_query(F.data == "deposit_cancel")
async def deposit_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Deposit cancelled.")


@user_router.callback_query(F.data == "deposit_history")
async def deposit_history(callback: CallbackQuery, user: User, session: AsyncSession):
    dep_svc  = DepositService(session)
    deposits = await dep_svc.get_user_deposits(user.id)
    if not deposits:
        await callback.message.edit_text(
            "📊 <b>Deposit History</b>\n\nNo deposits found.",
            parse_mode="HTML", reply_markup=back_kb("wallet_back")
        )
        return
    await callback.message.edit_text(
        f"📊 <b>Deposit History</b> (last {len(deposits)}):",
        parse_mode="HTML", reply_markup=deposit_history_kb(deposits)
    )


@user_router.callback_query(F.data.startswith("deposit_detail:"))
async def deposit_detail(callback: CallbackQuery, user: User, session: AsyncSession):
    dep_id  = int(callback.data.split(":")[1])
    dep_svc = DepositService(session)
    deposit = await dep_svc.get_deposit(dep_id)
    if not deposit or deposit.user_id != user.id:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    e_map = {"pending": "⏳ Pending", "approved": "✅ Approved", "rejected": "❌ Rejected"}
    text  = (
        f"💰 <b>Deposit #{deposit.id}</b>\n\n"
        f"💵 Amount: ₹{deposit.amount:.2f}\n"
        f"📊 Status: {e_map.get(deposit.status.value, '?')}\n"
        f"📅 Date: {deposit.created_at.strftime('%d %b %Y %H:%M')}"
    )
    if deposit.admin_remark:
        text += f"\n💬 Remark: {deposit.admin_remark}"
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=back_kb("deposit_history"))


@user_router.callback_query(F.data == "wallet_back")
async def wallet_back(callback: CallbackQuery, user: User, session: AsyncSession):
    svc  = UserService(session)
    user = await svc.get_user(callback.from_user.id)
    bal  = user.wallet.balance if user.wallet else 0.0
    await callback.message.edit_text(
        f"💰 <b>My Wallet</b>\n\n💵 Balance: ₹{bal:.2f}",
        parse_mode="HTML", reply_markup=wallet_kb()
    )


# ── Referral ───────────────────────────────────────────────────────────

@user_router.message(F.text == "👥 Referrals")
async def show_referrals(message: Message, user: User):
    link = f"https://t.me/{settings.BOT_USERNAME}?start=ref_{user.referral_code}"
    await message.answer(
        f"👥 <b>Referral Program</b>\n\n"
        f"🎁 Earn <b>₹{settings.REFERRAL_BONUS:.0f}</b> per referral!\n\n"
        f"🔗 <b>Your Link:</b>\n<code>{link}</code>\n\n"
        f"🎫 <b>Code:</b> <code>{user.referral_code}</code>\n"
        f"👥 <b>Referrals:</b> {user.referral_count}",
        parse_mode="HTML",
        reply_markup=referral_kb(user.referral_code, settings.BOT_USERNAME)
    )


@user_router.message(F.text == "ℹ️ Help")
async def show_help(message: Message):
    await message.answer(
        "ℹ️ <b>Help & FAQ</b>\n\n"
        "<b>How to buy Telegram Premium?</b>\n"
        "1. Deposit funds (💰 My Wallet)\n"
        "2. Click ⭐ Buy Premium\n"
        "3. Choose a plan\n"
        "4. Enter your Telegram username\n"
        "5. Confirm ✅\n\n"
        "<b>How long is delivery?</b>\n"
        "Usually 1–6 hours after order.\n\n"
        "<b>Problem?</b> Use 🎫 Support.",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════════
# SECTION 11: SHOP HANDLERS
# ══════════════════════════════════════════════════════════════════════

shop_router = Router()


@shop_router.message(F.text == "⭐ Buy Premium")
async def show_plans(message: Message, session: AsyncSession):
    svc   = PlanService(session)
    plans = await svc.get_active_plans()
    if not plans:
        await message.answer("⭐ No plans available right now. Check back soon!")
        return
    await message.answer(
        "⭐ <b>Telegram Premium Plans</b>\n\nChoose a plan:",
        parse_mode="HTML", reply_markup=plans_kb(plans)
    )


@shop_router.callback_query(F.data.startswith("plan_select:"))
async def plan_selected(callback: CallbackQuery, session: AsyncSession, user: User):
    plan_id = int(callback.data.split(":")[1])
    svc     = PlanService(session)
    plan    = await svc.get_plan(plan_id)
    if not plan or not plan.is_active:
        await callback.answer("❌ Plan unavailable.", show_alert=True)
        return
    bal      = user.wallet.balance if user.wallet else 0.0
    disc_txt = f"\n🏷 <s>₹{plan.original_price:.0f}</s> (-{plan.discount_percent}%)" if plan.discount_percent > 0 else ""
    feat_txt = ("\n\n✨ <b>Features:</b>\n" + "\n".join(f"• {f}" for f in plan.features)) if plan.features else ""
    affordable = "✅ You can afford this!" if bal >= plan.price else f"⚠️ Need ₹{plan.price - bal:.2f} more"
    await callback.message.edit_text(
        f"{plan.emoji} <b>{plan.name}</b>\n\n"
        f"📅 Duration: {plan.duration_months} month(s)\n"
        f"💰 Price: ₹{plan.price:.2f}{disc_txt}"
        f"{feat_txt}\n\n"
        f"💵 Your Balance: ₹{bal:.2f}\n"
        f"{affordable}"
        + (f"\n\n📝 {plan.description}" if plan.description else ""),
        parse_mode="HTML", reply_markup=plan_detail_kb(plan_id)
    )


@shop_router.callback_query(F.data.startswith("plan_buy:"))
async def plan_buy(callback: CallbackQuery, state: FSMContext,
                   session: AsyncSession, user: User):
    plan_id = int(callback.data.split(":")[1])
    svc     = PlanService(session)
    plan    = await svc.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    bal = user.wallet.balance if user.wallet else 0.0
    if bal < plan.price:
        await callback.answer(
            f"❌ Insufficient balance! Need ₹{plan.price - bal:.2f} more.",
            show_alert=True
        )
        return
    await state.update_data(plan_id=plan_id, plan_price=plan.price, plan_name=plan.name)
    await state.set_state(OrderStates.waiting_telegram_account)
    await callback.message.edit_text(
        f"📱 <b>Enter Telegram Account</b>\n\n"
        f"Enter the username or phone number for premium activation:\n\n"
        f"Example: @username or +91XXXXXXXXXX",
        parse_mode="HTML", reply_markup=cancel_kb("order_cancel")
    )


@shop_router.message(OrderStates.waiting_telegram_account)
async def process_tg_account(message: Message, state: FSMContext,
                              session: AsyncSession, user: User):
    account = message.text.strip()
    if not account:
        await message.answer("❌ Enter a valid username or phone number.")
        return
    data           = await state.get_data()
    plan_id        = data.get("plan_id")
    plan_price     = data.get("plan_price")
    plan_name      = data.get("plan_name")
    coupon_code    = data.get("coupon_code")
    discount_amount= data.get("discount_amount", 0.0)
    final_price    = plan_price - discount_amount
    bal            = user.wallet.balance if user.wallet else 0.0
    await state.update_data(telegram_account=account)
    c_txt = f"🎟 Coupon: {coupon_code} (-₹{discount_amount:.2f})\n" if coupon_code else ""
    await message.answer(
        f"🛒 <b>Order Confirmation</b>\n\n"
        f"⭐ Plan: {plan_name}\n"
        f"📱 Account: <code>{account}</code>\n"
        f"💰 Price: ₹{plan_price:.2f}\n"
        f"{c_txt}"
        f"💳 Total: ₹{final_price:.2f}\n"
        f"💵 Balance: ₹{bal:.2f}\n\n"
        f"Confirm order?",
        parse_mode="HTML",
        reply_markup=confirm_order_kb(plan_id, coupon_code)
    )


@shop_router.callback_query(F.data.startswith("order_confirm:"))
async def confirm_order(callback: CallbackQuery, state: FSMContext,
                        session: AsyncSession, user: User):
    parts       = callback.data.split(":")
    plan_id     = int(parts[1])
    coupon_code = parts[2] if len(parts) > 2 else None
    data        = await state.get_data()
    account     = data.get("telegram_account")
    disc_amt    = data.get("discount_amount", 0.0)

    plan_svc = PlanService(session)
    plan     = await plan_svc.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ Plan not found.", show_alert=True)
        return

    coupon_id = None
    if coupon_code:
        c_svc  = CouponService(session)
        coupon, _ = await c_svc.validate_coupon(coupon_code, plan.price)
        if coupon:
            disc_amt  = await c_svc.apply_coupon(coupon.id, plan.price)
            coupon_id = coupon.id

    final = plan.price - disc_amt
    u_svc = UserService(session)
    if not await u_svc.deduct_balance(user.id, final):
        await callback.answer("❌ Insufficient balance!", show_alert=True)
        return

    o_svc = OrderService(session)
    order = await o_svc.create_order(
        user_id=user.id, plan_id=plan_id,
        telegram_account=account, amount=final,
        coupon_id=coupon_id, discount_amount=disc_amt
    )
    await state.clear()
    new_bal = (user.wallet.balance if user.wallet else 0) - final

    await callback.message.edit_text(
        f"🎉 <b>Order Placed!</b>\n\n"
        f"🆔 Order ID: <b>#{order.id}</b>\n"
        f"⭐ Plan: {plan.name}\n"
        f"📱 Account: <code>{account}</code>\n"
        f"💰 Paid: ₹{final:.2f}\n"
        f"💵 Balance left: ₹{new_bal:.2f}\n\n"
        f"⏳ Delivery within <b>1–6 hours</b>. You'll be notified! 🔔",
        parse_mode="HTML"
    )

    admin_text = (
        f"🛍 <b>New Order!</b>\n\n"
        f"🆔 #{order.id}\n"
        f"👤 {user.mention} (<code>{user.telegram_id}</code>)\n"
        f"⭐ {plan.name}\n"
        f"📱 {account}\n"
        f"💰 ₹{final:.2f}"
    )
    await notify_admins(callback.message.bot, admin_text,
                        reply_markup=admin_quick_order_kb(order.id))


@shop_router.callback_query(F.data.startswith("plan_coupon:"))
async def plan_coupon(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split(":")[1])
    await state.update_data(plan_id=plan_id)
    await state.set_state(OrderStates.waiting_coupon)
    await callback.message.edit_text(
        "🎟 <b>Enter Coupon Code</b>",
        parse_mode="HTML", reply_markup=cancel_kb("order_cancel")
    )


@shop_router.message(OrderStates.waiting_coupon)
async def process_coupon(message: Message, state: FSMContext,
                         session: AsyncSession, user: User):
    code = message.text.strip().upper()
    data = await state.get_data()
    plan_svc = PlanService(session)
    plan     = await plan_svc.get_plan(data.get("plan_id"))
    if not plan:
        await state.clear()
        await message.answer("❌ Plan not found.")
        return
    c_svc  = CouponService(session)
    coupon, msg = await c_svc.validate_coupon(code, plan.price)
    await message.answer(msg)
    if coupon:
        disc = plan.price * (coupon.discount_value / 100) if coupon.discount_type == "percentage" else coupon.discount_value
        final = plan.price - disc
        await state.update_data(coupon_code=code, discount_amount=disc)
        await state.set_state(OrderStates.waiting_telegram_account)
        await message.answer(
            f"✅ Coupon applied! Final price: ₹{final:.2f}\n\n"
            f"📱 Now enter Telegram account for activation:",
            reply_markup=cancel_kb("order_cancel")
        )
    else:
        await state.set_state(None)


@shop_router.callback_query(F.data == "order_cancel")
async def order_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Order cancelled.")


@shop_router.callback_query(F.data == "plans_back")
async def plans_back(callback: CallbackQuery, session: AsyncSession):
    svc   = PlanService(session)
    plans = await svc.get_active_plans()
    await callback.message.edit_text(
        "⭐ <b>Telegram Premium Plans</b>\n\nChoose a plan:",
        parse_mode="HTML", reply_markup=plans_kb(plans)
    )


@shop_router.message(F.text == "📦 My Orders")
async def my_orders(message: Message, session: AsyncSession, user: User):
    svc    = OrderService(session)
    orders = await svc.get_user_orders(user.id)
    if not orders:
        await message.answer("📦 <b>My Orders</b>\n\nNo orders yet.", parse_mode="HTML")
        return
    await message.answer(
        f"📦 <b>My Orders</b> ({len(orders)}):",
        parse_mode="HTML", reply_markup=order_history_kb(orders)
    )


@shop_router.callback_query(F.data.startswith("order_detail:"))
async def order_detail(callback: CallbackQuery, session: AsyncSession, user: User):
    oid   = int(callback.data.split(":")[1])
    svc   = OrderService(session)
    order = await svc.get_order(oid)
    if not order or order.user_id != user.id:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    e_map = {"pending": "⏳ Pending", "processing": "🔄 Processing",
             "completed": "✅ Completed", "failed": "❌ Failed", "refunded": "↩️ Refunded"}
    text  = (
        f"📦 <b>Order #{order.id}</b>\n\n"
        f"⭐ Plan: {order.plan.name if order.plan else 'N/A'}\n"
        f"📱 Account: <code>{order.telegram_account}</code>\n"
        f"💰 Amount: ₹{order.amount:.2f}\n"
        f"📊 Status: {e_map.get(order.status.value, '?')}\n"
        f"📅 Date: {order.created_at.strftime('%d %b %Y %H:%M')}"
    )
    if order.admin_note:
        text += f"\n💬 Note: {order.admin_note}"
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=back_kb("orders_back"))


@shop_router.message(F.text == "🎟 Coupon")
async def coupon_info(message: Message):
    await message.answer(
        "🎟 <b>Coupons</b>\n\n"
        "Coupons are applied during checkout when buying a plan.\n"
        "Click ⭐ Buy Premium → select plan → 🎟 Apply Coupon.",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════════════════════
# SECTION 12: TICKET HANDLERS
# ══════════════════════════════════════════════════════════════════════

ticket_router = Router()


@ticket_router.message(F.text == "🎫 Support")
async def support_menu(message: Message):
    await message.answer(
        "🎫 <b>Support Center</b>\n\n"
        "Our team is here to help! Create a ticket or view existing ones:",
        parse_mode="HTML", reply_markup=ticket_kb()
    )


@ticket_router.callback_query(F.data == "ticket_new")
async def new_ticket(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TicketStates.waiting_subject)
    await callback.message.edit_text(
        "📝 <b>New Ticket</b>\n\nEnter the subject of your issue:",
        parse_mode="HTML", reply_markup=cancel_kb("ticket_cancel")
    )


@ticket_router.message(TicketStates.waiting_subject)
async def ticket_subject(message: Message, state: FSMContext):
    subject = message.text.strip()
    if len(subject) < 5:
        await message.answer("❌ Subject too short (min 5 chars).")
        return
    await state.update_data(subject=subject)
    await state.set_state(TicketStates.waiting_message)
    await message.answer(
        f"📋 Subject: {subject}\n\nNow describe your issue in detail:",
        reply_markup=cancel_kb("ticket_cancel")
    )


@ticket_router.message(TicketStates.waiting_message)
async def ticket_message(message: Message, state: FSMContext,
                          session: AsyncSession, user: User):
    msg_text = message.text.strip()
    if len(msg_text) < 10:
        await message.answer("❌ Message too short (min 10 chars).")
        return
    data    = await state.get_data()
    subject = data.get("subject")
    svc     = TicketService(session)
    ticket  = await svc.create_ticket(user.id, subject, msg_text)
    await state.clear()
    await message.answer(
        f"✅ <b>Ticket Submitted!</b>\n\n"
        f"🆔 #{ticket.id}\n"
        f"📋 Subject: {subject}\n"
        f"⏳ Status: Open\n\n"
        f"We'll respond within 24 hours.",
        parse_mode="HTML"
    )
    admin_text = (
        f"🎫 <b>New Support Ticket!</b>\n\n"
        f"🆔 #{ticket.id}\n"
        f"👤 {user.mention} (<code>{user.telegram_id}</code>)\n"
        f"📋 {subject}\n"
        f"💬 {msg_text[:200]}{'...' if len(msg_text) > 200 else ''}"
    )
    b = InlineKeyboardBuilder()
    b.button(text="💬 Reply", callback_data=f"ticket_reply:{ticket.id}")
    b.button(text="🔒 Close", callback_data=f"ticket_close:{ticket.id}")
    b.adjust(2)
    await notify_admins(message.bot, admin_text, reply_markup=b.as_markup())


@ticket_router.callback_query(F.data == "ticket_list")
async def ticket_list(callback: CallbackQuery, session: AsyncSession, user: User):
    svc     = TicketService(session)
    tickets = await svc.get_user_tickets(user.id)
    if not tickets:
        await callback.message.edit_text(
            "🎫 <b>My Tickets</b>\n\nNo tickets found.",
            parse_mode="HTML", reply_markup=back_kb("ticket_back")
        )
        return
    b = InlineKeyboardBuilder()
    e_map = {"open": "🟢", "in_progress": "🔵", "resolved": "✅", "closed": "🔒"}
    for t in tickets:
        b.button(text=f"{e_map.get(t.status.value,'?')} #{t.id} — {t.subject[:25]}",
                 callback_data=f"ticket_view:{t.id}")
    b.adjust(1)
    await callback.message.edit_text(
        f"🎫 <b>My Tickets</b> ({len(tickets)}):",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@ticket_router.callback_query(F.data.startswith("ticket_view:"))
async def ticket_view(callback: CallbackQuery, session: AsyncSession, user: User):
    tid    = int(callback.data.split(":")[1])
    svc    = TicketService(session)
    ticket = await svc.get_ticket(tid)
    if not ticket or ticket.user_id != user.id:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    s_map  = {"open": "🟢 Open", "in_progress": "🔵 In Progress",
               "resolved": "✅ Resolved", "closed": "🔒 Closed"}
    text   = (
        f"🎫 <b>Ticket #{ticket.id}</b>\n\n"
        f"📋 Subject: {ticket.subject}\n"
        f"📊 Status: {s_map.get(ticket.status.value,'?')}\n"
        f"📅 Date: {ticket.created_at.strftime('%d %b %Y %H:%M')}\n\n"
        f"💬 Your Message:\n{ticket.message}"
    )
    if ticket.admin_reply:
        text += f"\n\n👨‍💼 Admin Reply:\n{ticket.admin_reply}"
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=back_kb("ticket_list"))


@ticket_router.callback_query(F.data == "ticket_cancel")
async def ticket_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Cancelled.")


# ══════════════════════════════════════════════════════════════════════
# SECTION 13: ADMIN HANDLERS
# ══════════════════════════════════════════════════════════════════════

admin_router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids_list


# ── Admin Menu ─────────────────────────────────────────────────────────

@admin_router.message(Command("admin"))
async def admin_panel(message: Message, is_admin: bool):
    if not is_admin:
        await message.answer("❌ Access denied.")
        return
    await message.answer(
        "👨‍💼 <b>Admin Panel</b>\n\nWelcome, Admin!",
        parse_mode="HTML", reply_markup=admin_menu_kb()
    )


@admin_router.message(F.text == "🔙 User Menu")
async def back_user_menu(message: Message, is_admin: bool):
    if not is_admin:
        return
    await message.answer("👤 User Menu:", reply_markup=main_menu_kb())


# ── Dashboard ──────────────────────────────────────────────────────────

@admin_router.message(F.text == "📊 Dashboard")
async def dashboard(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    u_stats  = await UserService(session).get_stats()
    d_stats  = await DepositService(session).get_stats()
    o_stats  = await OrderService(session).get_stats()
    t_stats  = await TicketService(session).get_stats()
    await message.answer(
        f"📊 <b>Dashboard</b>\n"
        f"{'─'*28}\n\n"
        f"👥 <b>Users</b>\n"
        f"  Total: {u_stats['total']}  |  Today: {u_stats['new_today']}  |  Banned: {u_stats['banned']}\n\n"
        f"💰 <b>Deposits</b>\n"
        f"  Pending: {d_stats['pending']}  |  Approved: {d_stats['approved_count']}\n"
        f"  Revenue: ₹{d_stats['total_revenue']:.2f}  |  Today: ₹{d_stats['daily_revenue']:.2f}\n\n"
        f"📦 <b>Orders</b>\n"
        f"  Total: {o_stats['total']}  |  Pending: {o_stats['pending']}  |  Done: {o_stats['completed']}\n\n"
        f"🎫 <b>Tickets</b>\n"
        f"  Open: {t_stats['open']}  |  Total: {t_stats['total']}",
        parse_mode="HTML"
    )


# ── Deposits Admin ─────────────────────────────────────────────────────

@admin_router.message(F.text == "💰 Deposits")
async def admin_deposits(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    deps = await DepositService(session).get_all_deposits(limit=10)
    if not deps:
        await message.answer("💰 No deposits yet.")
        return
    await message.answer(
        f"💰 <b>Deposits</b> ({len(deps)} shown):",
        parse_mode="HTML", reply_markup=deposit_mgmt_kb(deps)
    )


@admin_router.callback_query(F.data.startswith("admin_deps:"))
async def admin_deps_page(callback: CallbackQuery, session: AsyncSession):
    offset = int(callback.data.split(":")[1])
    deps   = await DepositService(session).get_all_deposits(limit=10, offset=offset)
    await callback.message.edit_text(
        "💰 <b>Deposits</b>:", parse_mode="HTML",
        reply_markup=deposit_mgmt_kb(deps, offset)
    )


@admin_router.callback_query(F.data.startswith("admin_dep:"))
async def admin_dep_view(callback: CallbackQuery, session: AsyncSession):
    dep_id  = int(callback.data.split(":")[1])
    deposit = await DepositService(session).get_deposit(dep_id)
    if not deposit:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    u     = deposit.user
    e_map = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    await callback.message.edit_text(
        f"💰 <b>Deposit #{deposit.id}</b>\n\n"
        f"👤 {u.mention if u else 'N/A'} (<code>{u.telegram_id if u else 'N/A'}</code>)\n"
        f"💵 ₹{deposit.amount:.2f}\n"
        f"📊 {e_map.get(deposit.status.value,'?')} {deposit.status.value.title()}\n"
        f"📅 {deposit.created_at.strftime('%d %b %Y %H:%M')}"
        + (f"\n💬 {deposit.admin_remark}" if deposit.admin_remark else ""),
        parse_mode="HTML",
        reply_markup=deposit_action_kb(deposit.id, deposit.status.value)
    )


@admin_router.callback_query(F.data.startswith("dep_screenshot:"))
async def dep_screenshot(callback: CallbackQuery, session: AsyncSession):
    dep_id  = int(callback.data.split(":")[1])
    deposit = await DepositService(session).get_deposit(dep_id)
    if not deposit or not deposit.screenshot_file_id:
        await callback.answer("❌ No screenshot.", show_alert=True)
        return
    await callback.message.answer_photo(
        photo=deposit.screenshot_file_id,
        caption=f"📸 Deposit #{deposit.id} — ₹{deposit.amount:.2f}"
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("dep_approve:"))
async def dep_approve(callback: CallbackQuery, session: AsyncSession):
    dep_id  = int(callback.data.split(":")[1])
    ok, dep = await DepositService(session).approve_deposit(dep_id, callback.from_user.id)
    if not ok:
        await callback.answer("❌ Already processed.", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ Deposit #{dep_id} approved! ₹{dep.amount:.2f} credited.",
        parse_mode="HTML"
    )
    if dep and dep.user:
        bal = dep.user.wallet.balance if dep.user.wallet else 0
        try:
            await callback.message.bot.send_message(
                dep.user.telegram_id,
                f"✅ <b>Deposit Approved!</b>\n\n"
                f"₹{dep.amount:.2f} added to your wallet.\n"
                f"💵 Balance: ₹{bal:.2f}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Notify fail: {e}")


@admin_router.callback_query(F.data.startswith("dep_reject:"))
async def dep_reject_start(callback: CallbackQuery, state: FSMContext):
    dep_id = int(callback.data.split(":")[1])
    await state.update_data(deposit_id=dep_id)
    await state.set_state(AdminStates.reject_reason)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        f"❌ <b>Reject Deposit #{dep_id}</b>\n\nEnter rejection reason:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.reject_reason)
async def dep_reject_reason(message: Message, state: FSMContext, session: AsyncSession):
    reason = message.text.strip()
    data   = await state.get_data()
    dep_id = data.get("deposit_id")
    ok, dep = await DepositService(session).reject_deposit(dep_id, message.from_user.id, reason)
    await state.clear()
    if not ok:
        await message.answer("❌ Could not reject (already processed?).")
        return
    await message.answer(f"✅ Deposit #{dep_id} rejected.\nReason: {reason}")
    if dep and dep.user:
        try:
            await message.bot.send_message(
                dep.user.telegram_id,
                f"❌ <b>Deposit Rejected</b>\n\n"
                f"Amount: ₹{dep.amount:.2f}\n"
                f"Reason: {reason}\n\n"
                f"Open a support ticket if needed.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Notify fail: {e}")


# ── Users Admin ────────────────────────────────────────────────────────

@admin_router.message(F.text == "👥 Users")
async def admin_users(message: Message, state: FSMContext, is_admin: bool):
    if not is_admin:
        return
    await state.set_state(AdminStates.search_user)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await message.answer(
        "👥 <b>User Search</b>\n\nEnter username or Telegram ID:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.search_user)
async def do_search_user(message: Message, state: FSMContext, session: AsyncSession):
    query = message.text.strip().lstrip("@")
    svc   = UserService(session)
    users = await svc.search_user(query)
    await state.clear()
    if not users:
        await message.answer(f"❌ No user found: {query}")
        return
    user = users[0]
    bal  = user.wallet.balance if user.wallet else 0
    st   = "🚫 BANNED" if user.is_banned else "✅ Active"
    await message.answer(
        f"👤 <b>User</b>\n\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"👤 {user.full_name} | @{user.username or 'N/A'}\n"
        f"📊 {st}\n"
        f"💰 ₹{bal:.2f} | 💸 Spent ₹{user.total_spent:.2f}\n"
        f"👥 Referrals: {user.referral_count}\n"
        f"📅 Joined: {user.created_at.strftime('%d %b %Y')}",
        parse_mode="HTML",
        reply_markup=user_mgmt_kb(user.telegram_id, user.is_banned)
    )


@admin_router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban(callback: CallbackQuery, session: AsyncSession):
    uid = int(callback.data.split(":")[1])
    ok  = await UserService(session).ban_user(uid)
    await callback.answer("✅ Banned." if ok else "❌ Failed.", show_alert=True)
    if ok:
        await callback.message.edit_reply_markup(reply_markup=user_mgmt_kb(uid, True))


@admin_router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban(callback: CallbackQuery, session: AsyncSession):
    uid = int(callback.data.split(":")[1])
    ok  = await UserService(session).unban_user(uid)
    await callback.answer("✅ Unbanned." if ok else "❌ Failed.", show_alert=True)
    if ok:
        await callback.message.edit_reply_markup(reply_markup=user_mgmt_kb(uid, False))


@admin_router.callback_query(F.data.startswith("admin_add_bal:"))
async def admin_add_bal_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":")[1])
    await state.update_data(target_uid=uid)
    await state.set_state(AdminStates.add_balance_amount)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "➕ <b>Add Balance</b>\n\nEnter amount:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.add_balance_amount)
async def admin_add_bal(message: Message, state: FSMContext, session: AsyncSession):
    try:
        amount = float(message.text.strip())
        assert amount > 0
    except (ValueError, AssertionError):
        await message.answer("❌ Invalid amount.")
        return
    data = await state.get_data()
    uid  = data.get("target_uid")
    svc  = UserService(session)
    user = await svc.get_user(uid)
    if not user:
        await state.clear()
        await message.answer("❌ User not found.")
        return
    new_bal = await svc.add_balance(user.id, amount)
    await state.clear()
    await message.answer(f"✅ Added ₹{amount:.2f} | New balance: ₹{new_bal:.2f}")
    try:
        await message.bot.send_message(uid,
            f"💰 ₹{amount:.2f} added by admin. Balance: ₹{new_bal:.2f}")
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin_rem_bal:"))
async def admin_rem_bal_start(callback: CallbackQuery, state: FSMContext):
    uid = int(callback.data.split(":")[1])
    await state.update_data(target_uid=uid)
    await state.set_state(AdminStates.remove_balance_amount)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "➖ <b>Remove Balance</b>\n\nEnter amount:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.remove_balance_amount)
async def admin_rem_bal(message: Message, state: FSMContext, session: AsyncSession):
    try:
        amount = float(message.text.strip())
        assert amount > 0
    except (ValueError, AssertionError):
        await message.answer("❌ Invalid amount.")
        return
    data = await state.get_data()
    uid  = data.get("target_uid")
    svc  = UserService(session)
    user = await svc.get_user(uid)
    if not user:
        await state.clear()
        await message.answer("❌ User not found.")
        return
    result = await svc.remove_balance(user.id, amount)
    await state.clear()
    if result is None:
        await message.answer("❌ Insufficient balance.")
    else:
        await message.answer(f"✅ Removed ₹{amount:.2f} | New balance: ₹{result:.2f}")


@admin_router.callback_query(F.data.startswith("admin_user_orders:"))
async def admin_user_orders(callback: CallbackQuery, session: AsyncSession):
    uid    = int(callback.data.split(":")[1])
    svc    = UserService(session)
    user   = await svc.get_user(uid)
    if not user:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    o_svc  = OrderService(session)
    orders = await o_svc.get_user_orders(user.id)
    if not orders:
        await callback.answer("No orders for this user.", show_alert=True)
        return
    text = f"📦 <b>Orders for {user.mention}</b>\n\n"
    for o in orders:
        text += f"#{o.id} {o.plan.name if o.plan else 'N/A'} — ₹{o.amount:.2f} [{o.status.value}]\n"
    await callback.message.edit_text(text, parse_mode="HTML")


# ── Plans Admin ────────────────────────────────────────────────────────

@admin_router.message(F.text == "⭐ Plans")
async def admin_plans(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    plans = await PlanService(session).get_all_plans()
    await message.answer(
        f"⭐ <b>Plans</b> ({len(plans)} total):",
        parse_mode="HTML", reply_markup=plan_mgmt_kb(plans)
    )


@admin_router.callback_query(F.data.startswith("admin_plan:"))
async def admin_plan_view(callback: CallbackQuery, session: AsyncSession):
    pid  = int(callback.data.split(":")[1])
    plan = await PlanService(session).get_plan(pid)
    if not plan:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    await callback.message.edit_text(
        f"{plan.emoji} <b>{plan.name}</b>\n\n"
        f"📅 {plan.duration_months} month(s)\n"
        f"💰 ₹{plan.price:.2f}"
        + (f" (orig ₹{plan.original_price:.2f})" if plan.original_price else "")
        + f"\n{'✅ Active' if plan.is_active else '❌ Inactive'}"
        + (f"\n📝 {plan.description}" if plan.description else ""),
        parse_mode="HTML",
        reply_markup=plan_action_kb(plan.id, plan.is_active)
    )


@admin_router.callback_query(F.data.startswith("plan_toggle:"))
async def plan_toggle(callback: CallbackQuery, session: AsyncSession):
    pid    = int(callback.data.split(":")[1])
    status = await PlanService(session).toggle_plan(pid)
    txt    = "enabled ✅" if status else "disabled ❌"
    await callback.answer(f"Plan {txt}", show_alert=True)
    plan = await PlanService(session).get_plan(pid)
    await callback.message.edit_reply_markup(
        reply_markup=plan_action_kb(pid, plan.is_active)
    )


@admin_router.callback_query(F.data.startswith("plan_edit_price:"))
async def plan_edit_price_start(callback: CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    await state.update_data(edit_plan_id=pid)
    await state.set_state(AdminStates.plan_edit_price)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "✏️ <b>Edit Price</b>\n\nEnter new price (₹):",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.plan_edit_price)
async def plan_edit_price(message: Message, state: FSMContext, session: AsyncSession):
    try:
        price = float(message.text.strip())
        assert price > 0
    except (ValueError, AssertionError):
        await message.answer("❌ Invalid price.")
        return
    data = await state.get_data()
    pid  = data.get("edit_plan_id")
    ok   = await PlanService(session).update_plan(pid, price=price)
    await state.clear()
    await message.answer(f"✅ Price updated to ₹{price:.2f}" if ok else "❌ Update failed.")


@admin_router.callback_query(F.data == "admin_plan_add")
async def admin_plan_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.plan_name)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "➕ <b>Add Plan (1/4)</b>\n\nEnter plan name:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.plan_name)
async def plan_add_name(message: Message, state: FSMContext):
    await state.update_data(new_plan_name=message.text.strip())
    await state.set_state(AdminStates.plan_duration)
    await message.answer("2/4: Duration in months (e.g. 1, 3, 12):")


@admin_router.message(AdminStates.plan_duration)
async def plan_add_duration(message: Message, state: FSMContext):
    try:
        months = int(message.text.strip())
        assert months > 0
    except (ValueError, AssertionError):
        await message.answer("❌ Enter a valid number.")
        return
    await state.update_data(new_plan_duration=months)
    await state.set_state(AdminStates.plan_price)
    await message.answer("3/4: Enter price (₹):")


@admin_router.message(AdminStates.plan_price)
async def plan_add_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip())
        assert price > 0
    except (ValueError, AssertionError):
        await message.answer("❌ Enter a valid price.")
        return
    await state.update_data(new_plan_price=price)
    await state.set_state(AdminStates.plan_description)
    await message.answer("4/4: Enter description (or 'skip'):")


@admin_router.message(AdminStates.plan_description)
async def plan_add_desc(message: Message, state: FSMContext, session: AsyncSession):
    desc  = None if message.text.strip().lower() == "skip" else message.text.strip()
    data  = await state.get_data()
    await state.clear()
    svc   = PlanService(session)
    plan  = await svc.create_plan(
        name=data["new_plan_name"],
        description=desc,
        duration_months=data["new_plan_duration"],
        price=data["new_plan_price"],
        features=["No Ads", "Faster Downloads", "Exclusive Stickers", "Voice-to-Text"]
    )
    await message.answer(
        f"✅ <b>Plan Created!</b>\n\n"
        f"{plan.emoji} {plan.name} — ₹{plan.price:.2f} / {plan.duration_months}mo",
        parse_mode="HTML"
    )


@admin_router.callback_query(F.data.startswith("plan_delete:"))
async def plan_delete(callback: CallbackQuery, session: AsyncSession):
    pid = int(callback.data.split(":")[1])
    await PlanService(session).delete_plan(pid)
    await callback.answer("✅ Plan deactivated.", show_alert=True)
    plans = await PlanService(session).get_all_plans()
    await callback.message.edit_text(
        "⭐ <b>Plans</b>:", parse_mode="HTML",
        reply_markup=plan_mgmt_kb(plans)
    )


@admin_router.callback_query(F.data == "admin_plans_list")
async def admin_plans_list(callback: CallbackQuery, session: AsyncSession):
    plans = await PlanService(session).get_all_plans()
    await callback.message.edit_text(
        "⭐ <b>Plans</b>:", parse_mode="HTML",
        reply_markup=plan_mgmt_kb(plans)
    )


# ── Orders Admin ────────────────────────────────────────────────────────

@admin_router.message(F.text == "📦 Orders")
async def admin_orders(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    orders = await OrderService(session).get_all_orders(limit=10)
    if not orders:
        await message.answer("📦 No orders yet.")
        return
    b    = InlineKeyboardBuilder()
    e_map= {"pending": "⏳", "processing": "🔄", "completed": "✅", "failed": "❌"}
    for o in orders:
        e    = e_map.get(o.status.value, "❓")
        uname= (o.user.username or str(o.user.telegram_id)) if o.user else "?"
        pname= o.plan.name if o.plan else "N/A"
        b.button(text=f"{e} #{o.id} @{uname} — {pname}",
                 callback_data=f"admin_order:{o.id}")
    b.adjust(1)
    await message.answer("📦 <b>Orders</b>:", parse_mode="HTML",
                         reply_markup=b.as_markup())


@admin_router.callback_query(F.data.startswith("admin_order:"))
async def admin_order_view(callback: CallbackQuery, session: AsyncSession):
    oid   = int(callback.data.split(":")[1])
    order = await OrderService(session).get_order(oid)
    if not order:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    u    = order.user
    p    = order.plan
    e_map= {"pending": "⏳", "processing": "🔄",
             "completed": "✅", "failed": "❌", "refunded": "↩️"}
    await callback.message.edit_text(
        f"📦 <b>Order #{order.id}</b>\n\n"
        f"👤 {u.mention if u else 'N/A'} (<code>{u.telegram_id if u else 'N/A'}</code>)\n"
        f"⭐ {p.name if p else 'N/A'}\n"
        f"📱 <code>{order.telegram_account}</code>\n"
        f"💰 ₹{order.amount:.2f}\n"
        f"📊 {e_map.get(order.status.value,'?')} {order.status.value.title()}\n"
        f"📅 {order.created_at.strftime('%d %b %Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=order_action_kb(order.id, order.status.value)
    )


@admin_router.callback_query(F.data.startswith("order_complete:"))
async def order_complete(callback: CallbackQuery, session: AsyncSession):
    oid   = int(callback.data.split(":")[1])
    svc   = OrderService(session)
    await svc.update_order_status(oid, OrderStatus.COMPLETED)
    await callback.answer("✅ Completed!", show_alert=True)
    order = await svc.get_order(oid)
    if order and order.user:
        try:
            await callback.message.bot.send_message(
                order.user.telegram_id,
                f"🎉 <b>Premium Activated!</b>\n\n"
                f"Order #{order.id} completed!\n"
                f"📱 Account: <code>{order.telegram_account}</code>\n"
                f"Enjoy Telegram Premium! 🌟",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Notify fail: {e}")


@admin_router.callback_query(F.data.startswith("order_processing:"))
async def order_processing(callback: CallbackQuery, session: AsyncSession):
    oid = int(callback.data.split(":")[1])
    await OrderService(session).update_order_status(oid, OrderStatus.PROCESSING)
    await callback.answer("🔄 Marked as processing.", show_alert=True)


@admin_router.callback_query(F.data.startswith("order_failed:"))
async def order_failed(callback: CallbackQuery, session: AsyncSession):
    oid = int(callback.data.split(":")[1])
    await OrderService(session).update_order_status(oid, OrderStatus.FAILED)
    await callback.answer("❌ Marked as failed.", show_alert=True)


@admin_router.callback_query(F.data == "admin_orders_list")
async def admin_orders_list(callback: CallbackQuery, session: AsyncSession):
    orders = await OrderService(session).get_all_orders(limit=10)
    b    = InlineKeyboardBuilder()
    e_map= {"pending":"⏳","processing":"🔄","completed":"✅","failed":"❌"}
    for o in orders:
        e = e_map.get(o.status.value,"❓")
        b.button(text=f"{e} #{o.id}", callback_data=f"admin_order:{o.id}")
    b.adjust(1)
    await callback.message.edit_text("📦 <b>Orders</b>:", parse_mode="HTML",
                                     reply_markup=b.as_markup())


# ── Tickets Admin ───────────────────────────────────────────────────────

@admin_router.message(F.text == "🎫 Tickets")
async def admin_tickets(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    tickets = await TicketService(session).get_open_tickets()
    if not tickets:
        await message.answer("🎫 No open tickets.")
        return
    b = InlineKeyboardBuilder()
    for t in tickets:
        uname = (t.user.username or str(t.user.telegram_id)) if t.user else "?"
        b.button(text=f"🟢 #{t.id} @{uname} — {t.subject[:20]}",
                 callback_data=f"admin_ticket:{t.id}")
    b.adjust(1)
    await message.answer(f"🎫 <b>Open Tickets</b> ({len(tickets)}):",
                         parse_mode="HTML", reply_markup=b.as_markup())


@admin_router.callback_query(F.data.startswith("admin_ticket:"))
async def admin_ticket_view(callback: CallbackQuery, session: AsyncSession):
    tid    = int(callback.data.split(":")[1])
    ticket = await TicketService(session).get_ticket(tid)
    if not ticket:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    u = ticket.user
    await callback.message.edit_text(
        f"🎫 <b>Ticket #{ticket.id}</b>\n\n"
        f"👤 {u.mention if u else 'N/A'}\n"
        f"📋 {ticket.subject}\n"
        f"💬 {ticket.message}\n"
        f"📅 {ticket.created_at.strftime('%d %b %Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=ticket_action_kb(ticket.id, ticket.status.value)
    )


@admin_router.callback_query(F.data.startswith("ticket_reply:"))
async def ticket_reply_start(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split(":")[1])
    await state.update_data(reply_ticket_id=tid)
    await state.set_state(AdminStates.ticket_reply_msg)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        f"💬 <b>Reply to Ticket #{tid}</b>\n\nEnter reply:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.ticket_reply_msg)
async def ticket_reply(message: Message, state: FSMContext, session: AsyncSession):
    reply_txt = message.text.strip()
    data      = await state.get_data()
    tid       = data.get("reply_ticket_id")
    svc       = TicketService(session)
    ticket    = await svc.get_ticket(tid)
    ok        = await svc.reply_ticket(tid, message.from_user.id, reply_txt)
    await state.clear()
    if not ok:
        await message.answer("❌ Failed to send reply.")
        return
    await message.answer(f"✅ Reply sent to ticket #{tid}")
    if ticket and ticket.user:
        try:
            await message.bot.send_message(
                ticket.user.telegram_id,
                f"💬 <b>Ticket Reply</b>\n\n"
                f"🆔 Ticket #{tid}: {ticket.subject}\n\n"
                f"👨‍💼 Admin:\n{reply_txt}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Notify fail: {e}")


@admin_router.callback_query(F.data.startswith("ticket_close:"))
async def ticket_close(callback: CallbackQuery, session: AsyncSession):
    tid = int(callback.data.split(":")[1])
    await TicketService(session).close_ticket(tid)
    await callback.answer("🔒 Ticket closed.", show_alert=True)


@admin_router.callback_query(F.data == "admin_tickets_list")
async def admin_tickets_list(callback: CallbackQuery, session: AsyncSession):
    tickets = await TicketService(session).get_open_tickets()
    b = InlineKeyboardBuilder()
    for t in tickets:
        b.button(text=f"🟢 #{t.id} — {t.subject[:25]}",
                 callback_data=f"admin_ticket:{t.id}")
    b.adjust(1)
    await callback.message.edit_text(
        "🎫 <b>Open Tickets</b>:", parse_mode="HTML",
        reply_markup=b.as_markup()
    )


# ── Broadcast ──────────────────────────────────────────────────────────

@admin_router.message(F.text == "📢 Broadcast")
async def broadcast_menu(message: Message, is_admin: bool):
    if not is_admin:
        return
    await message.answer(
        "📢 <b>Broadcast</b>\n\nChoose type:",
        parse_mode="HTML", reply_markup=broadcast_type_kb()
    )


@admin_router.callback_query(F.data == "broadcast_text")
async def bc_text_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(bc_type="text")
    await state.set_state(AdminStates.broadcast_message)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "📝 <b>Text Broadcast</b>\n\nEnter your message (HTML supported):",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.callback_query(F.data == "broadcast_photo")
async def bc_photo_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(bc_type="photo")
    await state.set_state(AdminStates.broadcast_photo)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "🖼 <b>Photo Broadcast</b>\n\nSend a photo with caption:",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.callback_query(F.data == "broadcast_video")
async def bc_video_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(bc_type="text")
    await state.set_state(AdminStates.broadcast_message)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "🎥 <b>Video Broadcast</b>\n\nEnter message text (video not supported in this mode, use text):",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.broadcast_message)
async def bc_text_content(message: Message, state: FSMContext):
    await state.update_data(bc_content=message.text)
    await state.set_state(None)
    await message.answer("📢 Select target:", reply_markup=broadcast_target_kb())


@admin_router.message(AdminStates.broadcast_photo, F.photo)
async def bc_photo_content(message: Message, state: FSMContext):
    await state.update_data(bc_content=message.caption or "",
                             bc_file_id=message.photo[-1].file_id)
    await state.set_state(None)
    await message.answer("📢 Select target:", reply_markup=broadcast_target_kb())


@admin_router.callback_query(F.data.startswith("broadcast_target:"))
async def bc_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    target  = callback.data.split(":")[1]
    data    = await state.get_data()
    bc_type = data.get("bc_type", "text")
    content = data.get("bc_content", "")
    file_id = data.get("bc_file_id")
    await state.clear()

    users = await UserService(session).get_all_users()
    if target == "buyers":
        from sqlalchemy import select as sa_select
        r    = await session.execute(sa_select(Order.user_id).distinct())
        bids = {row[0] for row in r.fetchall()}
        users= [u for u in users if u.id in bids]

    await callback.message.edit_text(
        f"📢 Broadcasting to {len(users)} users...",
        parse_mode="HTML"
    )

    sent = failed = 0
    for u in users:
        if u.is_banned:
            continue
        try:
            if bc_type == "photo" and file_id:
                await callback.message.bot.send_photo(
                    u.telegram_id, photo=file_id,
                    caption=content, parse_mode="HTML"
                )
            else:
                await callback.message.bot.send_message(
                    u.telegram_id, content, parse_mode="HTML"
                )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"✅ <b>Broadcast Done!</b>\n\n"
        f"📤 Sent: {sent}\n❌ Failed: {failed}\n👥 Total: {len(users)}",
        parse_mode="HTML"
    )


# ── Coupons Admin ───────────────────────────────────────────────────────

@admin_router.message(F.text == "🎟 Coupons")
async def admin_coupons(message: Message, session: AsyncSession, is_admin: bool):
    if not is_admin:
        return
    coupons = await CouponService(session).get_all_coupons()
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Coupon", callback_data="admin_coupon_add")
    for c in coupons[:10]:
        st  = "✅" if c.is_active else "❌"
        val = f"{c.discount_value}%" if c.discount_type == "percentage" else f"₹{c.discount_value:.0f}"
        b.button(text=f"{st} {c.code} — {val} ({c.used_count} uses)",
                 callback_data=f"admin_coupon:{c.id}")
    b.adjust(1)
    await message.answer(f"🎟 <b>Coupons</b> ({len(coupons)}):",
                         parse_mode="HTML", reply_markup=b.as_markup())


@admin_router.callback_query(F.data == "admin_coupon_add")
async def coupon_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.coupon_code)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Cancel", callback_data="admin_cancel")
    await callback.message.edit_text(
        "🎟 <b>Add Coupon</b>\n\nEnter coupon code (e.g. SAVE20):",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.message(AdminStates.coupon_code)
async def coupon_code_input(message: Message, state: FSMContext):
    await state.update_data(new_coupon_code=message.text.strip().upper())
    await state.set_state(AdminStates.coupon_discount)
    await message.answer(
        "Enter discount:\n"
        "• Percentage: <code>20</code> = 20% off\n"
        "• Fixed: <code>fixed:50</code> = ₹50 off",
        parse_mode="HTML"
    )


@admin_router.message(AdminStates.coupon_discount)
async def coupon_discount_input(message: Message, state: FSMContext, session: AsyncSession):
    txt = message.text.strip()
    if txt.lower().startswith("fixed:"):
        d_type = "fixed"
        try:
            d_val = float(txt[6:])
        except ValueError:
            await message.answer("❌ Invalid format.")
            return
    else:
        d_type = "percentage"
        try:
            d_val = float(txt)
        except ValueError:
            await message.answer("❌ Invalid number.")
            return
    data   = await state.get_data()
    code   = data.get("new_coupon_code")
    await state.clear()
    svc    = CouponService(session)
    coupon = await svc.create_coupon(code, d_type, d_val)
    val    = f"{d_val}%" if d_type == "percentage" else f"₹{d_val:.0f}"
    await message.answer(f"✅ Coupon <b>{coupon.code}</b> ({val}) created!",
                         parse_mode="HTML")


@admin_router.callback_query(F.data.startswith("admin_coupon:"))
async def admin_coupon_view(callback: CallbackQuery, session: AsyncSession):
    cid = int(callback.data.split(":")[1])
    from sqlalchemy import select as sa_select
    r = await session.execute(sa_select(Coupon).where(Coupon.id == cid))
    c = r.scalar_one_or_none()
    if not c:
        await callback.answer("❌ Not found.", show_alert=True)
        return
    val = f"{c.discount_value}%" if c.discount_type == "percentage" else f"₹{c.discount_value:.0f}"
    b = InlineKeyboardBuilder()
    b.button(text=f"{'❌ Disable' if c.is_active else '✅ Enable'}",
             callback_data=f"coupon_toggle:{cid}")
    b.adjust(1)
    await callback.message.edit_text(
        f"🎟 <b>{c.code}</b>\n\n"
        f"Discount: {val}\nMin order: ₹{c.min_order_amount:.0f}\n"
        f"Uses: {c.used_count}/{c.max_uses or '∞'}\n"
        f"Status: {'✅ Active' if c.is_active else '❌ Inactive'}",
        parse_mode="HTML", reply_markup=b.as_markup()
    )


@admin_router.callback_query(F.data.startswith("coupon_toggle:"))
async def coupon_toggle(callback: CallbackQuery, session: AsyncSession):
    cid = int(callback.data.split(":")[1])
    from sqlalchemy import select as sa_select
    r = await session.execute(sa_select(Coupon).where(Coupon.id == cid))
    c = r.scalar_one_or_none()
    if c:
        c.is_active = not c.is_active
        await session.flush()
        await callback.answer(f"{'✅ Enabled' if c.is_active else '❌ Disabled'}", show_alert=True)


# ── General Admin Callbacks ─────────────────────────────────────────────

@admin_router.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Cancelled.")


@admin_router.callback_query(F.data == "admin_back")
async def admin_back_cb(callback: CallbackQuery):
    await callback.message.edit_text("👨‍💼 Admin Panel")
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════
# SECTION 14: DEFAULT PLANS (seeded on first run)
# ══════════════════════════════════════════════════════════════════════

async def seed_default_plans():
    """Seed the database with default Telegram Premium plans."""
    from sqlalchemy import select as sa_select
    async with get_session() as session:
        r = await session.execute(sa_select(func.count(Plan.id)))
        if (r.scalar() or 0) > 0:
            return  # Already seeded

        default_plans = [
            {
                "name": "1 Month Premium",
                "description": "Perfect for trying out Telegram Premium!",
                "duration_months": 1,
                "price": 179.0,
                "original_price": 249.0,
                "emoji": "⭐",
                "features": ["No Ads", "Faster Downloads (4x)", "Exclusive Stickers",
                             "Voice-to-Text", "Animated Profile Pictures"],
                "sort_order": 1,
            },
            {
                "name": "3 Month Premium",
                "description": "Great value for 3 months of Premium!",
                "duration_months": 3,
                "price": 479.0,
                "original_price": 749.0,
                "emoji": "🌟",
                "features": ["No Ads", "Faster Downloads (4x)", "Exclusive Stickers",
                             "Voice-to-Text", "4GB File Upload", "Stories"],
                "sort_order": 2,
            },
            {
                "name": "6 Month Premium",
                "description": "Best deal! 6 months at a great price.",
                "duration_months": 6,
                "price": 899.0,
                "original_price": 1499.0,
                "emoji": "💫",
                "features": ["All Premium Features", "Priority Support",
                             "No Ads", "Faster Downloads", "Extra Storage"],
                "sort_order": 3,
            },
            {
                "name": "1 Year Premium",
                "description": "Maximum savings! Full year of Telegram Premium.",
                "duration_months": 12,
                "price": 1499.0,
                "original_price": 2999.0,
                "emoji": "👑",
                "features": ["All Premium Features", "Priority Support",
                             "No Ads", "Maximum Storage", "All Exclusive Content"],
                "sort_order": 4,
            },
        ]

        for p_data in default_plans:
            sort = p_data.pop("sort_order")
            plan = Plan(**p_data, sort_order=sort)
            session.add(plan)

        logger.info("✅ Default plans seeded!")


# ══════════════════════════════════════════════════════════════════════
# SECTION 15: BOT STARTUP & MAIN
# ══════════════════════════════════════════════════════════════════════

async def on_startup(bot: Bot):
    """Tasks to run on bot startup."""
    await create_tables()
    await seed_default_plans()
    logger.info(f"✅ Bot @{settings.BOT_USERNAME} started!")
    logger.info(f"👨‍💼 Admin IDs: {settings.admin_ids_list}")
    logger.info(f"💳 UPI ID: {settings.UPI_ID}")
    logger.info(f"🔗 Webhook mode: {settings.WEBHOOK_MODE}")

    if settings.WEBHOOK_MODE:
        await bot.set_webhook(
            url=settings.webhook_url,
            allowed_updates=["message", "callback_query", "inline_query"],
            drop_pending_updates=True,
        )
        logger.info(f"🌐 Webhook set: {settings.webhook_url}")
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Polling mode activated")

    # Notify super admin
    try:
        await bot.send_message(
            settings.SUPER_ADMIN_ID,
            f"✅ <b>Bot Started!</b>\n\n"
            f"🤖 @{settings.BOT_USERNAME}\n"
            f"🔧 Mode: {'Webhook' if settings.WEBHOOK_MODE else 'Polling'}\n"
            f"💾 DB: Connected\n"
            f"⏰ {datetime.now().strftime('%d %b %Y %H:%M')}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Could not notify admin: {e}")


async def on_shutdown(bot: Bot):
    """Tasks to run on bot shutdown."""
    logger.info("🛑 Bot shutting down...")
    if settings.WEBHOOK_MODE:
        await bot.delete_webhook()


def create_bot() -> Bot:
    return Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    # Storage
    if settings.USE_REDIS:
        try:
            from redis.asyncio import Redis as AioRedis
            redis_client = AioRedis.from_url(settings.REDIS_URL)
            storage = RedisStorage(redis_client)
            logger.info("📦 Using Redis storage")
        except Exception:
            storage = MemoryStorage()
            logger.warning("⚠️ Redis unavailable, using memory storage")
    else:
        storage = MemoryStorage()
        logger.info("📦 Using memory storage")

    dp = Dispatcher(storage=storage)

    # Register middlewares (order matters)
    dp.update.outer_middleware(DatabaseMiddleware())
    dp.update.outer_middleware(UserMiddleware())
    dp.message.outer_middleware(RateLimitMiddleware(
        limit=settings.RATE_LIMIT_MESSAGES,
        window=settings.RATE_LIMIT_WINDOW
    ))

    # Register routers (admin first so /admin command takes priority)
    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(shop_router)
    dp.include_router(ticket_router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


async def health_handler(request):
    """Health check for Render.com"""
    return web.Response(text="OK", status=200)


async def run_polling():
    """Polling mode — also binds port for Render health check."""
    import os
    bot  = create_bot()
    dp   = create_dispatcher()
    port = int(os.environ.get("PORT", 8080))

    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"✅ Health server on port {port}")

    logger.info("🚀 Starting POLLING mode...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


async def run_webhook():
    """Webhook mode for Render.com production."""
    import os
    bot  = create_bot()
    dp   = create_dispatcher()
    port = int(os.environ.get("PORT", 8080))

    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)

    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=settings.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    logger.info(f"✅ Server on 0.0.0.0:{port}")
    logger.info(f"🌐 Webhook: {settings.webhook_url}")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main():
    """Entry point."""
    import sys, os
    os.makedirs("logs", exist_ok=True)

    logger.remove()
    logger.add(sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level=settings.LOG_LEVEL, colorize=True)
    try:
        logger.add("logs/bot.log", rotation="10 MB",
                   retention="7 days", level="INFO", encoding="utf-8")
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("  TELEGRAM PREMIUM STORE BOT")
    logger.info("  Aiogram 3.x | PostgreSQL | Render.com Ready")
    logger.info("=" * 60)

    if settings.WEBHOOK_MODE:
        asyncio.run(run_webhook())
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
