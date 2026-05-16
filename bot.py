"""
Vest Multi - Telegram Bot | Aiogram 3.x + PostgreSQL + Telethon + RollyPay SBP
Подписки, админ-панель по /admin, промокоды
"""

import asyncio
import hashlib
import hmac
import logging
import random
import re
import string
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Numeric,
    Boolean,
    DateTime,
    ForeignKey,
    create_engine,
    text,
    func
)
from sqlalchemy.orm import declarative_base, sessionmaker

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError
)
from telethon.sessions import StringSession

from yoomoney import Quickpay
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/vest_multi"
).replace("+asyncpg", "").replace("+psycopg", "")

MAIN_BOT_USERNAME = os.getenv("MAIN_BOT_USERNAME", "VestMultiBot")

CRYPTO_BOT_API = "https://pay.crypt.bot/api"
ROLLYPAY_API = "https://api.rollypay.io/api/v1"
ADMIN_ID = 7973988177
API_ID = 32480523
API_HASH = "147839735c9fa4e83451209e9b55cfc5"

TARIFFS = {
    "free": {
        "name": "Бесплатный",
        "max_bots": 1,
        "price": 0
    },
    "pro": {
        "name": "PRO",
        "max_bots": 5,
        "price": 50
    },
    "pro_max": {
        "name": "PRO MAX",
        "max_bots": 20,
        "price": 150
    }
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

Base = declarative_base()
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True
)
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255))
    balance = Column(Numeric(10, 2), default=0)
    tariff = Column(String(50), default="free")
    tariff_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True)
    owner_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id"),
        nullable=False
    )
    bot_token = Column(String(255), unique=True, nullable=False)
    bot_name = Column(String(255), nullable=False)
    bot_username = Column(String(255))
    support_username = Column(String(255))
    welcome_message = Column(Text, default="👋 Добро пожаловать!")
    crypto_bot_token = Column(String(255))
    yoomoney_wallet = Column(String(255))
    rollypay_terminal_id = Column(String(255))
    rollypay_api_key = Column(String(255))
    rollypay_signing_secret = Column(String(255))
    status = Column(String(50), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    shop_id = Column(
        Integer,
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False
    )
    name = Column(String(255), nullable=False)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    category_id = Column(
        Integer,
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False
    )
    shop_id = Column(
        Integer,
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False
    )
    name = Column(String(255), nullable=False)
    description = Column(Text)
    price = Column(Numeric(10, 2), nullable=False)
    phone_number = Column(String(20))
    session_string = Column(Text)
    has_2fa = Column(Boolean, default=False)
    password_2fa = Column(String(255))
    status = Column(String(50), default="available")


class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    user_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id"),
        nullable=False
    )
    product_id = Column(
        Integer,
        ForeignKey("products.id"),
        nullable=False
    )
    shop_id = Column(
        Integer,
        ForeignKey("shops.id"),
        nullable=False
    )
    price = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    payment_id = Column(String(255))
    status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class AdminSettings(Base):
    __tablename__ = "admin_settings"
    id = Column(Integer, primary_key=True)
    crypto_bot_token = Column(String(255))
    yoomoney_wallet = Column(String(255))
    rollypay_terminal_id = Column(String(255))
    rollypay_api_key = Column(String(255))
    rollypay_signing_secret = Column(String(255))


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id"),
        nullable=False
    )
    tariff = Column(String(50), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50))
    payment_id = Column(String(255))
    status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class PromoCode(Base):
    __tablename__ = "promocodes"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    shop_id = Column(
        Integer,
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False
    )
    amount = Column(Numeric(10, 2), nullable=False)
    max_activations = Column(Integer, default=1)
    activations = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PromoCodeUsage(Base):
    __tablename__ = "promocode_usages"
    id = Column(Integer, primary_key=True)
    promocode_id = Column(
        Integer,
        ForeignKey("promocodes.id", ondelete="CASCADE"),
        nullable=False
    )
    user_id = Column(
        BigInteger,
        ForeignKey("users.telegram_id"),
        nullable=False
    )
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        alterations = [
            ("payment_id", "VARCHAR(255)", "purchases"),
            ("status", "VARCHAR(50) DEFAULT 'completed'", "purchases"),
            ("rollypay_terminal_id", "VARCHAR(255)", "shops"),
            ("rollypay_api_key", "VARCHAR(255)", "shops"),
            ("rollypay_signing_secret", "VARCHAR(255)", "shops"),
            ("tariff", "VARCHAR(50) DEFAULT 'free'", "users"),
            ("tariff_expires", "TIMESTAMP", "users")
        ]
        for col, col_type, table in alterations:
            try:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )
                )
                conn.commit()
            except Exception:
                pass

    session = SessionLocal()
    try:
        if not session.query(AdminSettings).first():
            session.add(AdminSettings())
            session.commit()
    finally:
        session.close()


init_db()


class CreateBotStates(StatesGroup):
    waiting_token = State()
    waiting_name = State()
    waiting_support = State()


class AddCategoryStates(StatesGroup):
    waiting_name = State()


class AddProductStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()


class EditWelcomeStates(StatesGroup):
    waiting_text = State()


class EditSupportStates(StatesGroup):
    waiting_username = State()


class GiveBalanceStates(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()


class PaymentSettingsStates(StatesGroup):
    waiting_crypto_token = State()
    waiting_yoomoney_wallet = State()
    waiting_rollypay_terminal = State()
    waiting_rollypay_api_key = State()
    waiting_rollypay_secret = State()


class BroadcastStates(StatesGroup):
    waiting_message = State()


class AdminGiveSubStates(StatesGroup):
    waiting_user_id = State()
    waiting_tariff = State()


class AdminPaymentStates(StatesGroup):
    waiting_crypto_token = State()
    waiting_yoomoney_wallet = State()
    waiting_rollypay_terminal = State()
    waiting_rollypay_api_key = State()
    waiting_rollypay_secret = State()


class PromoCodeStates(StatesGroup):
    waiting_amount = State()
    waiting_max_activations = State()


class PromoCodeActivateStates(StatesGroup):
    waiting_code = State()


def kb_main():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🤖 Создать бота"))
    builder.row(
        KeyboardButton(text="📋 Мои боты"),
        KeyboardButton(text="👤 Профиль")
    )
    builder.row(
        KeyboardButton(text="🆘 Поддержка"),
        KeyboardButton(text="📖 Инструкции")
    )
    return builder.as_markup(resize_keyboard=True)


def kb_profile():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐ Купить подписку",
            callback_data="buy_sub"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад в главное меню",
            callback_data="main_menu"
        )
    )
    return builder.as_markup()


def kb_admin():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin_stats"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Рассылка",
            callback_data="admin_brd_sel"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👤 Выдать подписку",
            callback_data="admin_give_sub"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 Платёжные реквизиты",
            callback_data="admin_pay_settings"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="main_menu"
        )
    )
    return builder.as_markup()


def kb_shop(shop_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📂 Категории",
            callback_data=f"cats_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📦 Товары",
            callback_data=f"prods_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 Реквизиты",
            callback_data=f"pay_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📝 Приветствие",
            callback_data=f"wel_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎧 Поддержка",
            callback_data=f"sup_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👤 Выдать баланс",
            callback_data=f"bal_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎁 Промокоды",
            callback_data=f"promo_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data=f"st_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Рассылка",
            callback_data=f"brd_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"del_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="my_bots"
        )
    )
    return builder.as_markup()


def kb_child(owner_tariff="free"):
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🛍 Купить аккаунт"))
    builder.row(
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="📦 Мои покупки")
    )
    builder.row(
        KeyboardButton(text="🎁 Промокод"),
        KeyboardButton(text="🆘 Поддержка")
    )
    if owner_tariff == "free":
        builder.row(
            KeyboardButton(text="🤖 Хочу такого бота")
        )
    return builder.as_markup(resize_keyboard=True)


def kb_subs():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐ PRO - 50₽",
            callback_data="sub_pro"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👑 PRO MAX - 150₽",
            callback_data="sub_pro_max"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="profile"
        )
    )
    return builder.as_markup()


def kb_sub_pay(tariff_key):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🪙 Crypto Bot",
            callback_data=f"psub_crypto_{tariff_key}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 ЮMoney",
            callback_data=f"psub_ym_{tariff_key}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 СБП",
            callback_data=f"psub_rp_{tariff_key}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="buy_sub"
        )
    )
    return builder.as_markup()


def kb_payment_check(payment_url, check_callback):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💳 Оплатить",
            url=payment_url
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=check_callback
        )
    )
    return builder.as_markup()


def kb_promo_menu(shop_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Создать промокод",
            callback_data=f"promo_create_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📋 Список промокодов",
            callback_data=f"promo_list_{shop_id}"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"sm_{shop_id}"
        )
    )
    return builder.as_markup()


async def get_usdt_rate():
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": "tether", "vs_currencies": "rub"}
            async with session.get(url, params=params) as response:
                data = await response.json()
                return Decimal(str(data["tether"]["rub"]))
    except Exception:
        return Decimal("90")


class CryptoBotAPI:
    @staticmethod
    async def create_invoice(token, amount_rub, description):
        try:
            rate = await get_usdt_rate()
            usdt = round(amount_rub / rate, 2)
            headers = {"Crypto-Pay-API-Token": token}
            data = {
                "asset": "USDT",
                "amount": str(usdt),
                "description": description,
                "allow_comments": False
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{CRYPTO_BOT_API}/createInvoice",
                    json=data,
                    headers=headers
                ) as response:
                    result = await response.json()
                    if result.get("ok"):
                        return {
                            **result["result"],
                            "amount_usdt": usdt,
                            "amount_rub": amount_rub,
                            "rate": rate
                        }
                    logger.error(f"Crypto Bot error: {result}")
                    return None
        except Exception as e:
            logger.error(f"Crypto Bot API error: {e}")
            return None

    @staticmethod
    async def check_payment(token, invoice_id):
        try:
            headers = {"Crypto-Pay-API-Token": token}
            data = {"invoice_id": invoice_id}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{CRYPTO_BOT_API}/getInvoice",
                    json=data,
                    headers=headers
                ) as response:
                    result = await response.json()
                    if result.get("ok"):
                        return result["result"]
                    return None
        except Exception:
            return None


class YooMoneyAPI:
    @staticmethod
    def create_payment(wallet, amount, description, label):
        try:
            quickpay = Quickpay(
                receiver=wallet,
                quickpay_form="shop",
                targets=description,
                paymentType="SB",
                sum=float(amount),
                label=label
            )
            return quickpay.base_url
        except Exception as e:
            logger.error(f"YooMoney error: {e}")
            return None


class RollyPayAPI:
    @staticmethod
    async def create_payment(
        api_key,
        terminal_id,
        amount,
        order_id,
        description
    ):
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "amount": str(float(amount)),
                "payment_currency": "RUB",
                "payment_method": "sbp",
                "order_id": order_id,
                "terminal_id": terminal_id,
                "description": description
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{ROLLYPAY_API}/payments",
                    json=data,
                    headers=headers
                ) as response:
                    response_text = await response.text()
                    logger.info(
                        f"RollyPay response: {response.status} - {response_text}"
                    )
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(
                            f"RollyPay error: {response.status} - {response_text}"
                        )
                        return None
        except Exception as e:
            logger.error(f"RollyPay API error: {e}")
            return None

    @staticmethod
    async def check_payment(api_key, payment_id):
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{ROLLYPAY_API}/payments/{payment_id}",
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            f"RollyPay check: {data.get('status')} "
                            f"for payment {payment_id}"
                        )
                        return data
                    else:
                        logger.error(
                            f"RollyPay check error: {response.status}"
                        )
                        return None
        except Exception as e:
            logger.error(f"RollyPay check error: {e}")
            return None


class TelethonManager:
    def __init__(self):
        self.api_id = API_ID
        self.api_hash = API_HASH

    async def send_code(self, phone_number):
        try:
            client = TelegramClient(
                StringSession(),
                self.api_id,
                self.api_hash
            )
            await client.connect()
            sent = await client.send_code_request(phone_number)
            return client, sent.phone_code_hash
        except Exception as e:
            logger.error(f"Ошибка отправки кода: {e}")
            return None, None

    async def sign_in(
        self,
        client,
        phone_number,
        code,
        phone_code_hash,
        password_2fa=None
    ):
        try:
            await client.sign_in(
                phone_number,
                code,
                phone_code_hash=phone_code_hash
            )
            session_string = client.session.save()
            return session_string, None, False
        except SessionPasswordNeededError:
            if password_2fa:
                try:
                    await client.sign_in(password=password_2fa)
                    session_string = client.session.save()
                    return session_string, password_2fa, True
                except PasswordHashInvalidError:
                    await client.disconnect()
                    return None, None, False
            return None, None, True
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
            logger.error(f"Ошибка кода: {e}")
            await client.disconnect()
            return None, None, False
        except Exception as e:
            logger.error(f"Ошибка входа: {e}")
            await client.disconnect()
            return None, None, False

    async def get_latest_code(self, session_string):
        client = None
        try:
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash
            )
            await client.connect()

            if not await client.is_user_authorized():
                return None

            dialogs = await client.get_dialogs(limit=30)
            dialogs_sorted = sorted(
                dialogs,
                key=lambda d: d.date if d.date else datetime.min,
                reverse=True
            )

            for dialog in dialogs_sorted:
                if dialog.is_user:
                    username = getattr(dialog.entity, 'username', None)
                    if username in ['Telegram', 'telegram']:
                        continue

                messages = await client.get_messages(dialog, limit=20)

                for message in messages:
                    if message.text:
                        codes = re.findall(
                            r'(?<!\d)\d{5}(?!\d)',
                            message.text
                        )
                        if codes:
                            return codes[0]

            return None
        except Exception as e:
            logger.error(f"Ошибка получения кода: {e}")
            return None
        finally:
            if client:
                await client.disconnect()


tm = TelethonManager()
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)
child_bots: Dict[int, Bot] = {}


def get_admin_settings():
    session = SessionLocal()
    try:
        return session.query(AdminSettings).first()
    finally:
        session.close()


def get_user_sync(telegram_id):
    session = SessionLocal()
    try:
        return session.query(User).filter(
            User.telegram_id == telegram_id
        ).first()
    finally:
        session.close()


def get_or_create_user_sync(telegram_id, username):
    session = SessionLocal()
    try:
        user = session.query(User).filter(
            User.telegram_id == telegram_id
        ).first()
        if not user:
            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            session.commit()
            session.refresh(user)
        return user
    finally:
        session.close()


async def verify_bot_token(token):
    try:
        temp_bot = Bot(token=token)
        me = await temp_bot.get_me()
        await temp_bot.session.close()
        return {"username": me.username, "name": me.first_name}
    except Exception:
        return None


def check_payment_config(shop, method):
    if not shop:
        return False
    if method == "crypto":
        return bool(shop.crypto_bot_token)
    if method == "yoomoney":
        return bool(shop.yoomoney_wallet)
    if method == "rollypay":
        return bool(
            shop.rollypay_terminal_id
            and shop.rollypay_api_key
            and shop.rollypay_signing_secret
        )
    return False


async def complete_purchase(callback, session, purchase):
    purchase.status = "completed"
    product = session.query(Product).filter(
        Product.id == purchase.product_id
    ).first()
    if product:
        product.status = "sold"
    session.commit()

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔢 Запросить код",
            callback_data=f"get_code_{purchase.id}"
        )]
    ])

    await callback.message.answer(
        f"✅ Оплата получена!\n\n"
        f"📱 Номер телефона: {product.phone_number}\n"
        f"💵 Цена: {purchase.price} ₽\n\n"
        f"Нажмите кнопку ниже чтобы получить код подтверждения:",
        reply_markup=markup
    )


async def start_child_bot(shop):
    try:
        if shop.id in child_bots:
            return

        child_bot = Bot(
            token=shop.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        child_dp = Dispatcher(storage=MemoryStorage())
        child_router = Router()

        # Определяем тариф владельца
        owner = get_user_sync(shop.owner_id)
        owner_tariff = owner.tariff if owner else "free"

        @child_router.message(Command("start"))
        async def child_start(message: Message):
            session = SessionLocal()
            try:
                shop_data = session.query(Shop).filter(
                    Shop.id == shop.id
                ).first()
                if not shop_data:
                    await message.answer("❌ Магазин не найден")
                    return

                user = get_or_create_user_sync(
                    message.from_user.id,
                    message.from_user.username or ""
                )
                welcome_text = (
                    shop_data.welcome_message
                    or "👋 Добро пожаловать в наш магазин!"
                )

                await message.answer(
                    f"{welcome_text}\n\n"
                    f"💰 Ваш баланс: {user.balance} ₽",
                    reply_markup=kb_child(owner_tariff)
                )
            except Exception as e:
                logger.error(f"Ошибка child_start: {e}")
            finally:
                session.close()

        @child_router.message(F.text == "🛍 Купить аккаунт")
        async def child_buy(message: Message):
            session = SessionLocal()
            try:
                categories = session.query(Category).filter(
                    Category.shop_id == shop.id
                ).all()

                if not categories:
                    await message.answer("📂 Нет доступных категорий")
                    return

                builder = InlineKeyboardBuilder()
                for cat in categories:
                    builder.row(InlineKeyboardButton(
                        text=f"📁 {cat.name}",
                        callback_data=f"child_category_{cat.id}"
                    ))

                await message.answer(
                    "🛍 Выберите категорию:",
                    reply_markup=builder.as_markup()
                )
            except Exception as e:
                logger.error(f"Ошибка child_buy: {e}")
            finally:
                session.close()

        @child_router.message(F.text == "👤 Профиль")
        async def child_profile_handler(message: Message):
            user = get_user_sync(message.from_user.id)
            tariff_name = TARIFFS.get(
                owner_tariff, {}
            ).get("name", "Бесплатный")
            text = (
                f"👤 Профиль\n\n"
                f"🆔 ID: {user.telegram_id if user else 'Не найден'}\n"
                f"📛 Username: @{user.username if user else 'Нет'}\n"
                f"💰 Баланс: {user.balance if user else 0} ₽\n"
                f"⭐ Тариф магазина: {tariff_name}"
            )
            builder = InlineKeyboardBuilder()
            if owner_tariff == "free":
                builder.row(InlineKeyboardButton(
                    text="🤖 Хочу такого бота",
                    url=f"https://t.me/{MAIN_BOT_USERNAME}"
                ))
            await message.answer(
                text,
                reply_markup=builder.as_markup() if owner_tariff == "free" else None
            )

        @child_router.message(F.text == "📦 Мои покупки")
        async def child_purchases_list(message: Message):
            session = SessionLocal()
            try:
                purchases = session.query(Purchase).filter(
                    Purchase.user_id == message.from_user.id,
                    Purchase.status == "completed"
                ).order_by(
                    Purchase.created_at.desc()
                ).limit(10).all()

                if not purchases:
                    await message.answer("📦 У вас пока нет покупок")
                    return

                builder = InlineKeyboardBuilder()
                for purchase in purchases:
                    product = session.query(Product).filter(
                        Product.id == purchase.product_id
                    ).first()
                    phone = (
                        product.phone_number
                        if product else "Неизвестно"
                    )
                    builder.row(InlineKeyboardButton(
                        text=f"📱 {phone} — {purchase.price} ₽",
                        callback_data=f"get_code_{purchase.id}"
                    ))

                await message.answer(
                    "📦 Ваши последние покупки:\n"
                    "Нажмите чтобы получить новый код:",
                    reply_markup=builder.as_markup()
                )
            except Exception as e:
                logger.error(f"Ошибка child_purchases: {e}")
            finally:
                session.close()

        @child_router.message(F.text == "🎁 Промокод")
        async def child_promocode(message: Message, state: FSMContext):
            await message.answer(
                "🎁 Введите промокод:"
            )
            await state.set_state(PromoCodeActivateStates.waiting_code)

        @child_router.message(F.text == "🆘 Поддержка")
        async def child_support_handler(message: Message):
            session = SessionLocal()
            try:
                shop_data = session.query(Shop).filter(
                    Shop.id == shop.id
                ).first()
                if shop_data and shop_data.support_username:
                    await message.answer(
                        f"🆘 Поддержка: @{shop_data.support_username}"
                    )
                else:
                    await message.answer(
                        "🆘 Поддержка пока не настроена"
                    )
            finally:
                session.close()

        @child_router.message(F.text == "🤖 Хочу такого бота")
        async def child_want_bot(message: Message):
            await message.answer(
                f"🤖 Создайте своего бота в нашем основном боте!\n\n"
                f"👉 @{MAIN_BOT_USERNAME}\n\n"
                f"Там вы сможете создать магазин и "
                f"продавать свои аккаунты!"
            )

        @child_router.callback_query(
            F.data.startswith("child_category_")
        )
        async def child_category_products(callback: CallbackQuery):
            try:
                cat_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    products = session.query(Product).filter(
                        Product.category_id == cat_id,
                        Product.status == "available"
                    ).all()

                    if not products:
                        await callback.message.answer(
                            "📦 Нет товаров в этой категории"
                        )
                        return

                    builder = InlineKeyboardBuilder()
                    for product in products:
                        builder.row(InlineKeyboardButton(
                            text=(
                                f"🛍 {product.name} "
                                f"— {product.price} ₽"
                            ),
                            callback_data=f"child_product_{product.id}"
                        ))

                    await callback.message.edit_text(
                        "📦 Выберите товар:",
                        reply_markup=builder.as_markup()
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(
                    f"Ошибка child_category_products: {e}"
                )

        @child_router.callback_query(
            F.data.startswith("child_product_")
        )
        async def child_product_detail(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    product = session.query(Product).filter(
                        Product.id == product_id
                    ).first()

                    if not product:
                        await callback.message.answer(
                            "❌ Товар не найден"
                        )
                        return

                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(
                        text="💰 Баланс",
                        callback_data=f"pay_balance_{product.id}"
                    ))
                    builder.row(InlineKeyboardButton(
                        text="🪙 Crypto Bot",
                        callback_data=f"pay_crypto_{product.id}"
                    ))
                    builder.row(InlineKeyboardButton(
                        text="💳 ЮMoney",
                        callback_data=f"pay_yoomoney_{product.id}"
                    ))
                    builder.row(InlineKeyboardButton(
                        text="💳 СБП",
                        callback_data=f"pay_rollypay_{product.id}"
                    ))
                    builder.row(InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=(
                            f"child_category_{product.category_id}"
                        )
                    ))

                    await callback.message.edit_text(
                        f"📛 {product.name}\n"
                        f"📝 {product.description or 'Нет описания'}\n"
                        f"💵 Цена: {product.price} ₽\n\n"
                        f"Выберите способ оплаты:",
                        reply_markup=builder.as_markup()
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка child_product_detail: {e}")

        # Balance payment
        @child_router.callback_query(
            F.data.startswith("pay_balance_")
        )
        async def pay_balance(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    user = session.query(User).filter(
                        User.telegram_id == callback.from_user.id
                    ).first()
                    product = session.query(Product).filter(
                        Product.id == product_id
                    ).first()

                    if not product or product.status != "available":
                        await callback.message.answer(
                            "❌ Товар недоступен"
                        )
                        return

                    if not user or user.balance < product.price:
                        await callback.message.answer(
                            f"❌ Недостаточно средств!\n"
                            f"💰 Ваш баланс: "
                            f"{user.balance if user else 0} ₽\n"
                            f"💵 Цена: {product.price} ₽"
                        )
                        return

                    user.balance -= product.price
                    product.status = "sold"

                    purchase = Purchase(
                        user_id=user.telegram_id,
                        product_id=product.id,
                        shop_id=shop.id,
                        price=product.price,
                        payment_method="balance",
                        status="completed"
                    )
                    session.add(purchase)
                    session.commit()
                    session.refresh(purchase)

                    await callback.message.answer(
                        f"✅ Покупка успешна!\n\n"
                        f"📱 Номер телефона: {product.phone_number}\n"
                        f"💵 Цена: {product.price} ₽\n\n"
                        f"Нажмите кнопку ниже чтобы получить "
                        f"код подтверждения:",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[[
                                InlineKeyboardButton(
                                    text="🔢 Запросить код",
                                    callback_data=(
                                        f"get_code_{purchase.id}"
                                    )
                                )
                            ]]
                        )
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка pay_balance: {e}")

        # Crypto payment
        @child_router.callback_query(
            F.data.startswith("pay_crypto_")
        )
        async def pay_crypto(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    product = session.query(Product).filter(
                        Product.id == product_id
                    ).first()
                    shop_data = session.query(Shop).filter(
                        Shop.id == shop.id
                    ).first()

                    if not check_payment_config(shop_data, "crypto"):
                        await callback.message.answer(
                            "❌ Crypto Bot не настроен продавцом"
                        )
                        return

                    invoice = await CryptoBotAPI.create_invoice(
                        shop_data.crypto_bot_token,
                        product.price,
                        f"Покупка: {product.name}"
                    )

                    if not invoice:
                        await callback.message.answer(
                            "❌ Ошибка создания платежа"
                        )
                        return

                    purchase = Purchase(
                        user_id=callback.from_user.id,
                        product_id=product.id,
                        shop_id=shop.id,
                        price=product.price,
                        payment_method="crypto",
                        payment_id=str(invoice["invoice_id"]),
                        status="pending"
                    )
                    session.add(purchase)
                    session.commit()
                    session.refresh(purchase)

                    await callback.message.answer(
                        f"🪙 Счет создан!\n\n"
                        f"📛 Товар: {product.name}\n"
                        f"💵 Сумма: {product.price} ₽\n"
                        f"💎 Сумма в USDT: "
                        f"{invoice['amount_usdt']} USDT\n"
                        f"📊 Курс: 1 USDT = {invoice['rate']} ₽\n\n"
                        f"Нажмите кнопку для оплаты:",
                        reply_markup=kb_payment_check(
                            invoice["pay_url"],
                            f"check_crypto_{purchase.id}"
                        )
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка pay_crypto: {e}")

        @child_router.callback_query(
            F.data.startswith("check_crypto_")
        )
        async def check_crypto(callback: CallbackQuery):
            try:
                purchase_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    purchase = session.query(Purchase).filter(
                        Purchase.id == purchase_id
                    ).first()
                    shop_data = session.query(Shop).filter(
                        Shop.id == shop.id
                    ).first()

                    if not purchase or not shop_data:
                        await callback.message.answer(
                            "❌ Платеж не найден"
                        )
                        return

                    if purchase.status == "completed":
                        await callback.message.answer(
                            "✅ Платеж уже выполнен!"
                        )
                        return

                    payment = await CryptoBotAPI.check_payment(
                        shop_data.crypto_bot_token,
                        int(purchase.payment_id)
                    )

                    if payment and payment["status"] == "paid":
                        await complete_purchase(
                            callback,
                            session,
                            purchase
                        )
                    else:
                        await callback.message.answer(
                            "⏳ Платеж еще не получен. "
                            "Попробуйте позже."
                        )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка check_crypto: {e}")

        # YooMoney payment
        @child_router.callback_query(
            F.data.startswith("pay_yoomoney_")
        )
        async def pay_yoomoney(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    product = session.query(Product).filter(
                        Product.id == product_id
                    ).first()
                    shop_data = session.query(Shop).filter(
                        Shop.id == shop.id
                    ).first()

                    if not check_payment_config(shop_data, "yoomoney"):
                        await callback.message.answer(
                            "❌ ЮMoney не настроен продавцом"
                        )
                        return

                    label = str(uuid.uuid4())
                    payment_url = YooMoneyAPI.create_payment(
                        shop_data.yoomoney_wallet,
                        product.price,
                        f"Покупка: {product.name}",
                        label
                    )

                    if not payment_url:
                        await callback.message.answer(
                            "❌ Ошибка создания платежа"
                        )
                        return

                    purchase = Purchase(
                        user_id=callback.from_user.id,
                        product_id=product.id,
                        shop_id=shop.id,
                        price=product.price,
                        payment_method="yoomoney",
                        payment_id=label,
                        status="pending"
                    )
                    session.add(purchase)
                    session.commit()
                    session.refresh(purchase)

                    await callback.message.answer(
                        f"💳 Ссылка на оплату создана!\n\n"
                        f"📛 Товар: {product.name}\n"
                        f"💵 Сумма: {product.price} ₽\n\n"
                        f"Нажмите кнопку для оплаты:",
                        reply_markup=kb_payment_check(
                            payment_url,
                            f"check_yoomoney_{purchase.id}"
                        )
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка pay_yoomoney: {e}")

        @child_router.callback_query(
            F.data.startswith("check_yoomoney_")
        )
        async def check_yoomoney(callback: CallbackQuery):
            await callback.message.answer(
                "ℹ️ Проверьте оплату в приложении ЮMoney. "
                "Если оплатили - нажмите /start для обновления."
            )

        # RollyPay SBP payment
        @child_router.callback_query(
            F.data.startswith("pay_rollypay_")
        )
        async def pay_rollypay(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    product = session.query(Product).filter(
                        Product.id == product_id
                    ).first()
                    shop_data = session.query(Shop).filter(
                        Shop.id == shop.id
                    ).first()

                    if not check_payment_config(
                        shop_data, "rollypay"
                    ):
                        await callback.message.answer(
                            "❌ СБП не настроен продавцом"
                        )
                        return

                    order_id = str(uuid.uuid4())
                    payment = await RollyPayAPI.create_payment(
                        shop_data.rollypay_api_key,
                        shop_data.rollypay_terminal_id,
                        product.price,
                        order_id,
                        f"Покупка: {product.name}"
                    )

                    if not payment:
                        await callback.message.answer(
                            "❌ Ошибка создания платежа. "
                            "Проверьте реквизиты RollyPay "
                            "(Terminal ID, API Key, Signing Secret)."
                        )
                        return

                    purchase = Purchase(
                        user_id=callback.from_user.id,
                        product_id=product.id,
                        shop_id=shop.id,
                        price=product.price,
                        payment_method="rollypay",
                        payment_id=payment["payment_id"],
                        status="pending"
                    )
                    session.add(purchase)
                    session.commit()
                    session.refresh(purchase)

                    await callback.message.answer(
                        f"💳 Ссылка на оплату СБП создана!\n\n"
                        f"📛 Товар: {product.name}\n"
                        f"💵 Сумма: {product.price} ₽\n\n"
                        f"Нажмите кнопку для оплаты:",
                        reply_markup=kb_payment_check(
                            payment["pay_url"],
                            f"check_rollypay_{purchase.id}"
                        )
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка pay_rollypay: {e}")

        @child_router.callback_query(
            F.data.startswith("check_rollypay_")
        )
        async def check_rollypay(callback: CallbackQuery):
            try:
                purchase_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    purchase = session.query(Purchase).filter(
                        Purchase.id == purchase_id
                    ).first()
                    shop_data = session.query(Shop).filter(
                        Shop.id == shop.id
                    ).first()

                    if not purchase or not shop_data:
                        await callback.message.answer(
                            "❌ Платеж не найден"
                        )
                        return

                    if purchase.status == "completed":
                        await callback.message.answer(
                            "✅ Платеж уже выполнен!"
                        )
                        return

                    payment = await RollyPayAPI.check_payment(
                        shop_data.rollypay_api_key,
                        purchase.payment_id
                    )

                    if payment and payment.get("status") == "paid":
                        await complete_purchase(
                            callback,
                            session,
                            purchase
                        )
                    else:
                        status = (
                            payment.get("status", "неизвестно")
                            if payment else "неизвестно"
                        )
                        await callback.message.answer(
                            f"⏳ Платеж не получен.\n"
                            f"Статус: {status}\n"
                            f"Попробуйте позже."
                        )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка check_rollypay: {e}")

        @child_router.callback_query(
            F.data.startswith("get_code_")
        )
        async def get_code(callback: CallbackQuery):
            try:
                purchase_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    purchase = session.query(Purchase).filter(
                        Purchase.id == purchase_id
                    ).first()

                    if (
                        not purchase
                        or purchase.user_id != callback.from_user.id
                    ):
                        await callback.message.answer(
                            "❌ Покупка не найдена"
                        )
                        return

                    if purchase.status != "completed":
                        await callback.message.answer(
                            "❌ Покупка не оплачена"
                        )
                        return

                    product = session.query(Product).filter(
                        Product.id == purchase.product_id
                    ).first()

                    if not product or not product.session_string:
                        await callback.message.answer(
                            "❌ Невозможно получить код"
                        )
                        return

                    await callback.message.answer(
                        "🔍 Ищем свежий код подтверждения..."
                    )

                    code = await tm.get_latest_code(
                        product.session_string
                    )

                    if code:
                        response = (
                            f"📱 Номер: {product.phone_number}\n"
                            f"🔢 Код: {code}"
                        )
                        if product.has_2fa and product.password_2fa:
                            response += (
                                f"\n🔑 2FA пароль: "
                                f"{product.password_2fa}"
                            )
                        await callback.message.answer(response)
                    else:
                        await callback.message.answer(
                            "❌ Не удалось найти код. "
                            "Попробуйте позже или обратитесь "
                            "в поддержку."
                        )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Ошибка get_code: {e}")

        child_dp.include_router(child_router)
        asyncio.create_task(child_dp.start_polling(child_bot))
        child_bots[shop.id] = child_bot
        logger.info(f"✅ Дочерний бот {shop.bot_name} запущен")
    except Exception as e:
        logger.error(
            f"❌ Ошибка запуска дочернего бота {shop.id}: {e}"
        )


# ============ MAIN BOT HANDLERS ============

@router.message(Command("start"))
async def cmd_start(message: Message):
    get_or_create_user_sync(
        message.from_user.id,
        message.from_user.username or ""
    )
    await message.answer(
        "🤖 Vest Multi\n\nДобро пожаловать в главное меню!",
        reply_markup=kb_main()
    )


@router.message(Command("admin"))
async def admin_panel_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Нет доступа")
        return
    await message.answer("👑 Админ-панель", reply_markup=kb_admin())


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    await callback.message.answer(
        "🤖 Vest Multi\n\nГлавное меню:",
        reply_markup=kb_main()
    )


@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user = get_user_sync(message.from_user.id)
    if not user:
        await message.answer("❌ Пользователь не найден")
        return
    tariff_name = TARIFFS.get(user.tariff, {}).get(
        "name",
        "Бесплатный"
    )
    text = (
        f"👤 Профиль\n\n"
        f"🆔 Telegram ID: {user.telegram_id}\n"
        f"📛 Username: @{user.username or 'Нет'}\n"
        f"💰 Баланс: {user.balance} ₽\n"
        f"⭐ Тариф: {tariff_name}"
    )
    await message.answer(text, reply_markup=kb_profile())


@router.message(F.text == "🤖 Создать бота")
async def create_bot_start(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        user = session.query(User).filter(
            User.telegram_id == message.from_user.id
        ).first()
        max_bots = TARIFFS.get(
            user.tariff if user else "free",
            TARIFFS["free"]
        )["max_bots"]
        bot_count = session.query(Shop).filter(
            Shop.owner_id == message.from_user.id
        ).count()

        if bot_count >= max_bots:
            await message.answer(
                f"❌ Достигнут лимит ботов ({max_bots}) "
                f"для вашего тарифа!\n"
                f"⭐ Повысьте тариф в профиле"
            )
            return
    finally:
        session.close()

    await message.answer(
        "📌 Создание нового бота\n\n"
        "Шаг 1/3: Введите токен бота от @BotFather"
    )
    await state.set_state(CreateBotStates.waiting_token)


@router.message(F.text == "📋 Мои боты")
async def show_my_bots(message: Message):
    session = SessionLocal()
    try:
        shops = session.query(Shop).filter(
            Shop.owner_id == message.from_user.id
        ).all()
        if not shops:
            await message.answer(
                "📋 У вас пока нет созданных ботов",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🔙 Назад",
                            callback_data="main_menu"
                        )
                    ]]
                )
            )
            return

        builder = InlineKeyboardBuilder()
        for shop in shops:
            status_emoji = (
                "🟢" if shop.status == "active" else "🔴"
            )
            builder.row(InlineKeyboardButton(
                text=(
                    f"🤖 {shop.bot_name} "
                    f"(@{shop.bot_username}) — {status_emoji}"
                ),
                callback_data=f"shop_menu_{shop.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="main_menu"
        ))

        await message.answer(
            "📋 Ваши боты:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.message(F.text == "🆘 Поддержка")
async def support_handler(message: Message):
    await message.answer("🆘 Поддержка: @VestMultiSupport")


@router.message(F.text == "📖 Инструкции")
async def instructions_handler(message: Message):
    await message.answer("📖 Инструкции: @VestMultiTGK")


# ============ SUBSCRIPTIONS ============

@router.callback_query(F.data == "buy_sub")
async def buy_subscription(callback: CallbackQuery):
    await callback.message.edit_text(
        "⭐ Выберите тариф:",
        reply_markup=kb_subs()
    )


@router.callback_query(F.data.startswith("sub_"))
async def subscription_select(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[1]
    tariff = TARIFFS.get(tariff_key)
    if not tariff:
        await callback.message.answer("❌ Тариф не найден")
        return
    await callback.message.edit_text(
        f"⭐ Тариф: {tariff['name']}\n"
        f"💵 Цена: {tariff['price']}₽\n\n"
        f"Выберите способ оплаты:",
        reply_markup=kb_sub_pay(tariff_key)
    )


@router.callback_query(F.data.startswith("psub_crypto_"))
async def pay_sub_crypto(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[2]
    tariff = TARIFFS[tariff_key]
    admin_settings = get_admin_settings()

    if not admin_settings or not admin_settings.crypto_bot_token:
        await callback.message.answer(
            "❌ Администратор еще не настроил Crypto Bot"
        )
        return

    invoice = await CryptoBotAPI.create_invoice(
        admin_settings.crypto_bot_token,
        Decimal(tariff["price"]),
        f"Подписка {tariff['name']}"
    )

    if invoice:
        session = SessionLocal()
        try:
            sub = Subscription(
                user_id=callback.from_user.id,
                tariff=tariff_key,
                price=tariff["price"],
                payment_method="crypto",
                payment_id=str(invoice["invoice_id"]),
                status="pending"
            )
            session.add(sub)
            session.commit()
        finally:
            session.close()

        await callback.message.answer(
            f"🪙 Счет создан!\n\n"
            f"⭐ {tariff['name']}\n"
            f"💵 Сумма: {tariff['price']}₽\n"
            f"💎 Сумма в USDT: {invoice['amount_usdt']} USDT",
            reply_markup=kb_payment_check(
                invoice["pay_url"],
                f"check_sub_crypto_{tariff_key}"
            )
        )
    else:
        await callback.message.answer(
            "❌ Ошибка создания платежа"
        )


@router.callback_query(F.data.startswith("psub_ym_"))
async def pay_sub_yoomoney(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[2]
    tariff = TARIFFS[tariff_key]
    admin_settings = get_admin_settings()

    if not admin_settings or not admin_settings.yoomoney_wallet:
        await callback.message.answer(
            "❌ Администратор еще не настроил ЮMoney"
        )
        return

    label = str(uuid.uuid4())
    payment_url = YooMoneyAPI.create_payment(
        admin_settings.yoomoney_wallet,
        Decimal(tariff["price"]),
        f"Подписка {tariff['name']}",
        label
    )

    if payment_url:
        session = SessionLocal()
        try:
            sub = Subscription(
                user_id=callback.from_user.id,
                tariff=tariff_key,
                price=tariff["price"],
                payment_method="yoomoney",
                payment_id=label,
                status="pending"
            )
            session.add(sub)
            session.commit()
        finally:
            session.close()

        await callback.message.answer(
            f"💳 Ссылка создана!\n\n"
            f"⭐ {tariff['name']}\n"
            f"💵 Сумма: {tariff['price']}₽",
            reply_markup=kb_payment_check(
                payment_url,
                f"check_sub_ym_{tariff_key}"
            )
        )
    else:
        await callback.message.answer(
            "❌ Ошибка создания платежа"
        )


@router.callback_query(F.data.startswith("psub_rp_"))
async def pay_sub_rollypay(callback: CallbackQuery):
    tariff_key = callback.data.split("_")[2]
    tariff = TARIFFS[tariff_key]
    admin_settings = get_admin_settings()

    if (
        not admin_settings
        or not admin_settings.rollypay_terminal_id
        or not admin_settings.rollypay_api_key
    ):
        await callback.message.answer(
            "❌ Администратор еще не настроил СБП"
        )
        return

    order_id = str(uuid.uuid4())
    payment = await RollyPayAPI.create_payment(
        admin_settings.rollypay_api_key,
        admin_settings.rollypay_terminal_id,
        Decimal(tariff["price"]),
        order_id,
        f"Подписка {tariff['name']}"
    )

    if payment:
        session = SessionLocal()
        try:
            sub = Subscription(
                user_id=callback.from_user.id,
                tariff=tariff_key,
                price=tariff["price"],
                payment_method="rollypay",
                payment_id=payment["payment_id"],
                status="pending"
            )
            session.add(sub)
            session.commit()
        finally:
            session.close()

        await callback.message.answer(
            f"💳 СБП ссылка создана!\n\n"
            f"⭐ {tariff['name']}\n"
            f"💵 Сумма: {tariff['price']}₽",
            reply_markup=kb_payment_check(
                payment["pay_url"],
                f"check_sub_rp_{tariff_key}"
            )
        )
    else:
        await callback.message.answer(
            "❌ Ошибка создания платежа. "
            "Проверьте реквизиты RollyPay "
            "(Terminal ID, API Key, Signing Secret)."
        )


# ============ ADMIN PANEL ============

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    session = SessionLocal()
    try:
        users_count = session.query(User).count()
        shops_count = session.query(Shop).count()
        purchases_count = session.query(Purchase).filter(
            Purchase.status == "completed"
        ).count()
        total_revenue = (
            session.query(func.sum(Purchase.price))
            .filter(Purchase.status == "completed")
            .scalar() or 0
        )

        text = (
            f"📊 Статистика\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"🤖 Ботов: {shops_count}\n"
            f"🛍 Продаж: {purchases_count}\n"
            f"💰 Выручка: {total_revenue} ₽"
        )
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="main_menu"
                    )
                ]]
            )
        )
    finally:
        session.close()


@router.callback_query(F.data == "admin_give_sub")
async def admin_give_sub_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text(
        "👤 Введите Telegram ID пользователя:"
    )
    await state.set_state(AdminGiveSubStates.waiting_user_id)


@router.message(AdminGiveSubStates.waiting_user_id)
async def admin_give_sub_user(
    message: Message,
    state: FSMContext
):
    try:
        user_id = int(message.text.strip())
        await state.update_data(target_user_id=user_id)

        builder = InlineKeyboardBuilder()
        for key, tariff in TARIFFS.items():
            if key != "free":
                builder.row(InlineKeyboardButton(
                    text=(
                        f"{tariff['name']} - {tariff['price']}₽"
                    ),
                    callback_data=f"admin_set_sub_{key}"
                ))

        await message.answer(
            "⭐ Выберите тариф:",
            reply_markup=builder.as_markup()
        )
        await state.set_state(AdminGiveSubStates.waiting_tariff)
    except ValueError:
        await message.answer("❌ Неверный ID")


@router.callback_query(
    AdminGiveSubStates.waiting_tariff,
    F.data.startswith("admin_set_sub_")
)
async def admin_give_sub_finish(
    callback: CallbackQuery,
    state: FSMContext
):
    tariff_key = callback.data.split("_")[3]
    data = await state.get_data()
    session = SessionLocal()
    try:
        user = get_or_create_user_sync(
            data["target_user_id"],
            ""
        )
        user.tariff = tariff_key
        user.tariff_expires = None
        session.commit()
        await callback.message.answer(
            f"✅ Подписка {TARIFFS[tariff_key]['name']} "
            f"выдана пользователю {data['target_user_id']}"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data == "admin_pay_settings")
async def admin_payment_settings(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    admin_settings = get_admin_settings()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=(
            f"🪙 Crypto Bot Token "
            f"{'✅' if admin_settings and admin_settings.crypto_bot_token else '❌'}"
        ),
        callback_data="admin_set_crypto"
    ))
    builder.row(InlineKeyboardButton(
        text=(
            f"💳 ЮMoney "
            f"{'✅' if admin_settings and admin_settings.yoomoney_wallet else '❌'}"
        ),
        callback_data="admin_set_ym"
    ))
    builder.row(InlineKeyboardButton(
        text=(
            f"💳 СБП (RollyPay) "
            f"{'✅' if admin_settings and admin_settings.rollypay_terminal_id else '❌'}"
        ),
        callback_data="admin_set_rp"
    ))
    builder.row(InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="main_menu"
    ))

    await callback.message.edit_text(
        "💳 Платёжные реквизиты (для оплаты подписок)",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data == "admin_set_crypto")
async def admin_set_crypto(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text(
        "🪙 Введите токен Crypto Bot API:"
    )
    await state.set_state(
        AdminPaymentStates.waiting_crypto_token
    )


@router.message(AdminPaymentStates.waiting_crypto_token)
async def admin_save_crypto(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        admin_settings = session.query(AdminSettings).first()
        admin_settings.crypto_bot_token = message.text.strip()
        session.commit()
        await message.answer("✅ Токен Crypto Bot сохранён!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data == "admin_set_ym")
async def admin_set_yoomoney(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text(
        "💳 Введите номер кошелька ЮMoney:"
    )
    await state.set_state(
        AdminPaymentStates.waiting_yoomoney_wallet
    )


@router.message(AdminPaymentStates.waiting_yoomoney_wallet)
async def admin_save_yoomoney(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        admin_settings = session.query(AdminSettings).first()
        admin_settings.yoomoney_wallet = message.text.strip()
        session.commit()
        await message.answer("✅ Кошелёк ЮMoney сохранён!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data == "admin_set_rp")
async def admin_set_rollypay_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text(
        "💳 Введите Terminal ID:"
    )
    await state.set_state(
        AdminPaymentStates.waiting_rollypay_terminal
    )


@router.message(AdminPaymentStates.waiting_rollypay_terminal)
async def admin_set_rollypay_terminal(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        rp_terminal=message.text.strip()
    )
    await message.answer("🔑 Введите API Key:")
    await state.set_state(
        AdminPaymentStates.waiting_rollypay_api_key
    )


@router.message(AdminPaymentStates.waiting_rollypay_api_key)
async def admin_set_rollypay_api_key(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        rp_api_key=message.text.strip()
    )
    await message.answer("🔐 Введите Signing Secret:")
    await state.set_state(
        AdminPaymentStates.waiting_rollypay_secret
    )


@router.message(AdminPaymentStates.waiting_rollypay_secret)
async def admin_save_rollypay(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        admin_settings = session.query(AdminSettings).first()
        admin_settings.rollypay_terminal_id = data[
            "rp_terminal"
        ]
        admin_settings.rollypay_api_key = data["rp_api_key"]
        admin_settings.rollypay_signing_secret = (
            message.text.strip()
        )
        session.commit()
        await message.answer("✅ RollyPay сохранён!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data == "admin_brd_sel")
async def admin_broadcast_select(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    session = SessionLocal()
    try:
        shops = session.query(Shop).all()
        builder = InlineKeyboardBuilder()
        for shop in shops:
            builder.row(InlineKeyboardButton(
                text=f"🤖 {shop.bot_name}",
                callback_data=f"admin_broadcast_{shop.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="main_menu"
        ))
        await callback.message.edit_text(
            "📢 Выберите бота для рассылки:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("admin_broadcast_"))
async def admin_broadcast_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if callback.from_user.id != ADMIN_ID:
        return
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(broadcast_shop_id=shop_id)
    await callback.message.edit_text(
        "📢 Введите сообщение для рассылки:"
    )
    await state.set_state(BroadcastStates.waiting_message)


@router.message(BroadcastStates.waiting_message)
async def broadcast_execute(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()
    shop_id = data.get("broadcast_shop_id")
    session = SessionLocal()
    try:
        shop = session.query(Shop).filter(
            Shop.id == shop_id
        ).first()
        if not shop:
            await message.answer("❌ Бот не найден")
            return

        if shop.id in child_bots:
            child_bot = child_bots[shop.id]
            purchases = (
                session.query(Purchase.user_id)
                .filter(Purchase.shop_id == shop_id)
                .distinct()
                .all()
            )
            sent_count = 0
            for (user_id,) in purchases:
                try:
                    await child_bot.send_message(
                        user_id,
                        message.text
                    )
                    sent_count += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    continue
            await message.answer(
                f"✅ Отправлено: {sent_count}"
            )
        else:
            await message.answer("❌ Бот не запущен")
    finally:
        session.close()
        await state.clear()


# ============ BOT CREATION ============

@router.message(CreateBotStates.waiting_token)
async def process_bot_token(
    message: Message,
    state: FSMContext
):
    token = message.text.strip()
    bot_info = await verify_bot_token(token)

    if not bot_info:
        await message.answer(
            "❌ Токен недействителен, попробуйте снова"
        )
        return

    await state.update_data(
        token=token,
        bot_username=bot_info["username"]
    )
    await message.answer("🏷 Шаг 2/3: Введите название бота")
    await state.set_state(CreateBotStates.waiting_name)


@router.message(CreateBotStates.waiting_name)
async def process_bot_name(
    message: Message,
    state: FSMContext
):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "🎧 Шаг 3/3: Введите юзернейм поддержки (без @)"
    )
    await state.set_state(CreateBotStates.waiting_support)


@router.message(CreateBotStates.waiting_support)
async def process_bot_support(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        support_username = message.text.strip().replace("@", "")
        data = await state.get_data()

        shop = Shop(
            owner_id=message.from_user.id,
            bot_token=data["token"],
            bot_name=data["name"],
            bot_username=data["bot_username"],
            support_username=support_username,
            status="active"
        )
        session.add(shop)
        session.commit()
        session.refresh(shop)

        await start_child_bot(shop)

        text = (
            f"✅ Бот успешно создан и запущен!\n\n"
            f"🤖 Название: {shop.bot_name}\n"
            f"📛 Юзернейм: @{shop.bot_username}\n"
            f"🎧 Поддержка: @{shop.support_username}\n"
            f"🟢 Статус: Активен\n\n"
            f"Бот доступен по ссылке: "
            f"t.me/{shop.bot_username}"
        )
        await message.answer(text, reply_markup=kb_main())
    except Exception as e:
        logger.error(f"Ошибка создания бота: {e}")
        await message.answer("❌ Ошибка при создании бота")
    finally:
        session.close()
        await state.clear()


# ============ SHOP MANAGEMENT ============

@router.callback_query(F.data.startswith("shop_menu_"))
async def shop_menu(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[2])
    session = SessionLocal()
    try:
        shop = session.query(Shop).filter(
            Shop.id == shop_id,
            Shop.owner_id == callback.from_user.id
        ).first()
        if not shop:
            await callback.message.answer("❌ Бот не найден")
            return
        text = (
            f"🤖 {shop.bot_name} (@{shop.bot_username})\n"
            f"🟢 Статус: {shop.status}\n"
            f"🎧 Поддержка: @{shop.support_username}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=kb_shop(shop_id)
        )
    finally:
        session.close()


# Categories
@router.callback_query(F.data.startswith("cats_"))
async def manage_categories(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    try:
        categories = session.query(Category).filter(
            Category.shop_id == shop_id
        ).all()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="➕ Добавить категорию",
            callback_data=f"add_category_{shop_id}"
        ))
        for cat in categories:
            builder.row(InlineKeyboardButton(
                text=f"📁 {cat.name}",
                callback_data=f"edit_category_{cat.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"shop_menu_{shop_id}"
        ))
        await callback.message.edit_text(
            "📂 Управление категориями:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("add_category_"))
async def add_category_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "📂 Введите название категории:"
    )
    await state.set_state(AddCategoryStates.waiting_name)


@router.message(AddCategoryStates.waiting_name)
async def add_category_finish(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        category = Category(
            shop_id=shop_id,
            name=message.text.strip()
        )
        session.add(category)
        session.commit()
        await message.answer(
            f"✅ Категория {message.text} добавлена!"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("edit_category_"))
async def edit_category_menu(callback: CallbackQuery):
    session = SessionLocal()
    try:
        cat_id = int(callback.data.split("_")[2])
        category = session.query(Category).filter(
            Category.id == cat_id
        ).first()
        if not category:
            await callback.message.answer(
                "❌ Категория не найдена"
            )
            return
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"delete_category_{cat_id}"
        ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"cats_{category.shop_id}"
        ))
        await callback.message.edit_text(
            f"📁 Категория: {category.name}",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("delete_category_"))
async def delete_category(callback: CallbackQuery):
    session = SessionLocal()
    try:
        cat_id = int(callback.data.split("_")[2])
        category = session.query(Category).filter(
            Category.id == cat_id
        ).first()
        if not category:
            await callback.message.answer(
                "❌ Категория не найдена"
            )
            return
        shop_id = category.shop_id
        session.delete(category)
        session.commit()
        await callback.message.edit_text(
            "✅ Категория удалена",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=f"cats_{shop_id}"
                    )
                ]]
            )
        )
    finally:
        session.close()


# Products
@router.callback_query(F.data.startswith("prods_"))
async def manage_products(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    try:
        categories = session.query(Category).filter(
            Category.shop_id == shop_id
        ).all()
        if not categories:
            await callback.message.edit_text(
                "📂 Сначала добавьте категорию!",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="🔙 Назад",
                            callback_data=f"shop_menu_{shop_id}"
                        )
                    ]]
                )
            )
            return
        builder = InlineKeyboardBuilder()
        for cat in categories:
            builder.row(InlineKeyboardButton(
                text=f"📁 {cat.name}",
                callback_data=f"show_products_{cat.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"shop_menu_{shop_id}"
        ))
        await callback.message.edit_text(
            "📦 Выберите категорию:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("show_products_"))
async def show_products(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[2])
    session = SessionLocal()
    try:
        category = session.query(Category).filter(
            Category.id == cat_id
        ).first()
        if not category:
            await callback.message.answer(
                "❌ Категория не найдена"
            )
            return
        products = session.query(Product).filter(
            Product.category_id == cat_id
        ).all()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="➕ Добавить товар",
            callback_data=f"add_product_{cat_id}"
        ))
        for product in products:
            status = (
                "🟢" if product.status == "available" else "🔴"
            )
            builder.row(InlineKeyboardButton(
                text=(
                    f"{status} {product.name} "
                    f"— {product.price} ₽"
                ),
                callback_data=f"product_menu_{product.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"prods_{category.shop_id}"
        ))
        await callback.message.edit_text(
            f"📦 Товары в категории {category.name}:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("add_product_"))
async def add_product_start(
    callback: CallbackQuery,
    state: FSMContext
):
    cat_id = int(callback.data.split("_")[2])
    await state.update_data(category_id=cat_id)
    await callback.message.edit_text(
        "📛 Введите название товара:"
    )
    await state.set_state(AddProductStates.waiting_name)


@router.message(AddProductStates.waiting_name)
async def add_product_name(
    message: Message,
    state: FSMContext
):
    await state.update_data(name=message.text.strip())
    await message.answer("📝 Введите описание товара:")
    await state.set_state(AddProductStates.waiting_description)


@router.message(AddProductStates.waiting_description)
async def add_product_description(
    message: Message,
    state: FSMContext
):
    await state.update_data(description=message.text.strip())
    await message.answer("💵 Введите цену в рублях:")
    await state.set_state(AddProductStates.waiting_price)


@router.message(AddProductStates.waiting_price)
async def add_product_price(
    message: Message,
    state: FSMContext
):
    try:
        price = Decimal(
            message.text.strip().replace(",", ".")
        )
        if price <= 0:
            raise ValueError
        await state.update_data(price=price)
        await message.answer(
            "📱 Введите номер телефона "
            "(например +79991234567):"
        )
        await state.set_state(AddProductStates.waiting_phone)
    except ValueError:
        await message.answer("❌ Неверный формат цены")


@router.message(AddProductStates.waiting_phone)
async def add_product_phone(
    message: Message,
    state: FSMContext
):
    try:
        phone = message.text.strip()
        if not phone.startswith("+"):
            phone = "+" + phone

        client, phone_code_hash = await tm.send_code(phone)

        if not client:
            await message.answer(
                "❌ Не удалось отправить код"
            )
            return

        await state.update_data(
            phone=phone,
            client_session=client.session.save(),
            phone_code_hash=phone_code_hash
        )

        await message.answer(
            "🔢 Введите код подтверждения из SMS:"
        )
        await state.set_state(AddProductStates.waiting_code)
    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")


@router.message(AddProductStates.waiting_code)
async def add_product_code(
    message: Message,
    state: FSMContext
):
    try:
        data = await state.get_data()
        code = message.text.strip()

        client = TelegramClient(
            StringSession(data["client_session"]),
            tm.api_id,
            tm.api_hash
        )
        await client.connect()

        session_string, password_2fa, has_2fa = (
            await tm.sign_in(
                client,
                data["phone"],
                code,
                data["phone_code_hash"]
            )
        )

        if session_string is None and has_2fa:
            await state.update_data(
                client_session=client.session.save()
            )
            await message.answer(
                "🔐 Требуется пароль 2FA. Введите пароль:"
            )
            await state.set_state(
                AddProductStates.waiting_2fa
            )
            return

        if session_string is None:
            await message.answer(
                "❌ Неверный код подтверждения"
            )
            await client.disconnect()
            return

        await save_product(
            message,
            state,
            session_string,
            password_2fa,
            has_2fa
        )
        await client.disconnect()
    except Exception as e:
        logger.error(f"Ошибка входа: {e}")


@router.message(AddProductStates.waiting_2fa)
async def add_product_2fa(
    message: Message,
    state: FSMContext
):
    try:
        data = await state.get_data()
        password_2fa = message.text.strip()

        client = TelegramClient(
            StringSession(data["client_session"]),
            tm.api_id,
            tm.api_hash
        )
        await client.connect()

        session_string, saved_2fa, has_2fa = (
            await tm.sign_in(
                client,
                data["phone"],
                None,
                data["phone_code_hash"],
                password_2fa
            )
        )

        if session_string is None:
            await message.answer("❌ Неверный пароль 2FA")
            return

        await save_product(
            message,
            state,
            session_string,
            saved_2fa,
            has_2fa
        )
        await client.disconnect()
    except Exception as e:
        logger.error(f"Ошибка 2FA: {e}")


async def save_product(
    message,
    state,
    session_string,
    password_2fa,
    has_2fa
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        category = session.query(Category).filter(
            Category.id == data["category_id"]
        ).first()

        product = Product(
            category_id=data["category_id"],
            shop_id=category.shop_id,
            name=data["name"],
            description=data.get("description", ""),
            price=data["price"],
            phone_number=data["phone"],
            session_string=session_string,
            has_2fa=has_2fa,
            password_2fa=password_2fa,
            status="available"
        )
        session.add(product)
        session.commit()

        await message.answer(
            f"✅ Товар успешно добавлен!\n"
            f"📛 {product.name}\n"
            f"💵 {product.price} ₽\n"
            f"📱 {product.phone_number}\n"
            f"🔐 2FA: {'Есть' if product.has_2fa else 'Нет'}",
            reply_markup=kb_main()
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("product_menu_"))
async def product_menu(callback: CallbackQuery):
    session = SessionLocal()
    try:
        product_id = int(callback.data.split("_")[2])
        product = session.query(Product).filter(
            Product.id == product_id
        ).first()
        if not product:
            await callback.message.answer(
                "❌ Товар не найден"
            )
            return
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"delete_product_{product.id}"
        ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=(
                f"show_products_{product.category_id}"
            )
        ))
        text = (
            f"📛 {product.name}\n"
            f"📝 {product.description or 'Нет описания'}\n"
            f"💵 {product.price} ₽\n"
            f"📱 {product.phone_number}\n"
            f"🟢 {product.status}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("delete_product_"))
async def delete_product(callback: CallbackQuery):
    session = SessionLocal()
    try:
        product_id = int(callback.data.split("_")[2])
        product = session.query(Product).filter(
            Product.id == product_id
        ).first()
        if not product:
            await callback.message.answer(
                "❌ Товар не найден"
            )
            return
        cat_id = product.category_id
        session.delete(product)
        session.commit()
        await callback.message.edit_text(
            "✅ Товар удалён",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=(
                            f"show_products_{cat_id}"
                        )
                    )
                ]]
            )
        )
    finally:
        session.close()


# ============ PROMOCODES ============

@router.callback_query(F.data.startswith("promo_"))
async def promo_menu(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    await callback.message.edit_text(
        "🎁 Управление промокодами:",
        reply_markup=kb_promo_menu(shop_id)
    )


@router.callback_query(F.data.startswith("promo_create_"))
async def promo_create_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(promo_shop_id=shop_id)
    await callback.message.edit_text(
        "💵 Введите сумму для промокода:"
    )
    await state.set_state(PromoCodeStates.waiting_amount)


@router.message(PromoCodeStates.waiting_amount)
async def promo_create_amount(
    message: Message,
    state: FSMContext
):
    try:
        amount = Decimal(
            message.text.strip().replace(",", ".")
        )
        if amount <= 0:
            raise ValueError
        await state.update_data(promo_amount=amount)
        await message.answer(
            "🔢 Введите максимальное количество активаций:"
        )
        await state.set_state(
            PromoCodeStates.waiting_max_activations
        )
    except ValueError:
        await message.answer("❌ Неверная сумма")


@router.message(PromoCodeStates.waiting_max_activations)
async def promo_create_finish(
    message: Message,
    state: FSMContext
):
    try:
        max_activations = int(message.text.strip())
        if max_activations <= 0:
            raise ValueError

        data = await state.get_data()

        # Генерируем промокод
        code = ''.join(
            random.choices(
                string.ascii_uppercase + string.digits,
                k=8
            )
        )

        session = SessionLocal()
        try:
            promo = PromoCode(
                code=code,
                shop_id=data["promo_shop_id"],
                amount=data["promo_amount"],
                max_activations=max_activations
            )
            session.add(promo)
            session.commit()

            await message.answer(
                f"✅ Промокод создан!\n\n"
                f"🎁 Код: <code>{code}</code>\n"
                f"💵 Сумма: {data['promo_amount']} ₽\n"
                f"🔢 Активаций: {max_activations}"
            )
        finally:
            session.close()
    except ValueError:
        await message.answer("❌ Неверное число активаций")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("promo_list_"))
async def promo_list(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[2])
    session = SessionLocal()
    try:
        promos = session.query(PromoCode).filter(
            PromoCode.shop_id == shop_id
        ).all()

        if not promos:
            await callback.message.answer(
                "🎁 Нет созданных промокодов"
            )
            return

        builder = InlineKeyboardBuilder()
        for promo in promos:
            status = "🟢" if promo.is_active else "🔴"
            builder.row(InlineKeyboardButton(
                text=(
                    f"{status} {promo.code} — "
                    f"{promo.amount} ₽ "
                    f"({promo.activations}/{promo.max_activations})"
                ),
                callback_data=f"promo_del_{promo.id}"
            ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"promo_{shop_id}"
        ))

        await callback.message.edit_text(
            "🎁 Список промокодов:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("promo_del_"))
async def promo_delete(callback: CallbackQuery):
    promo_id = int(callback.data.split("_")[2])
    session = SessionLocal()
    try:
        promo = session.query(PromoCode).filter(
            PromoCode.id == promo_id
        ).first()
        if promo:
            shop_id = promo.shop_id
            promo.is_active = False
            session.commit()
            await callback.message.answer(
                "✅ Промокод деактивирован"
            )
    finally:
        session.close()


# Promocode activation (in child bot and main bot)
@router.message(PromoCodeActivateStates.waiting_code)
async def activate_promocode(
    message: Message,
    state: FSMContext
):
    code = message.text.strip().upper()
    session = SessionLocal()
    try:
        promo = session.query(PromoCode).filter(
            PromoCode.code == code,
            PromoCode.is_active == True
        ).first()

        if not promo:
            await message.answer(
                "❌ Промокод не найден или неактивен"
            )
            await state.clear()
            return

        if promo.activations >= promo.max_activations:
            await message.answer(
                "❌ Достигнут лимит активаций промокода"
            )
            await state.clear()
            return

        # Проверяем, не использовал ли пользователь этот промокод
        usage = session.query(PromoCodeUsage).filter(
            PromoCodeUsage.promocode_id == promo.id,
            PromoCodeUsage.user_id == message.from_user.id
        ).first()

        if usage:
            await message.answer(
                "❌ Вы уже использовали этот промокод"
            )
            await state.clear()
            return

        # Активируем промокод
        user = get_or_create_user_sync(
            message.from_user.id,
            message.from_user.username or ""
        )
        user.balance += promo.amount
        promo.activations += 1

        promo_usage = PromoCodeUsage(
            promocode_id=promo.id,
            user_id=message.from_user.id
        )
        session.add(promo_usage)
        session.commit()

        await message.answer(
            f"✅ Промокод активирован!\n\n"
            f"💰 На баланс начислено: {promo.amount} ₽\n"
            f"💵 Текущий баланс: {user.balance} ₽"
        )
    finally:
        session.close()
        await state.clear()


# ============ SHOP SETTINGS ============

@router.callback_query(F.data.startswith("del_"))
async def delete_bot_confirm(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="✅ Да, удалить",
        callback_data=f"confirm_delete_{shop_id}"
    ))
    builder.row(InlineKeyboardButton(
        text="❌ Нет, отмена",
        callback_data=f"shop_menu_{shop_id}"
    ))
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите удалить бота?",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("confirm_delete_"))
async def delete_bot_execute(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        shop = session.query(Shop).filter(
            Shop.id == shop_id,
            Shop.owner_id == callback.from_user.id
        ).first()
        if not shop:
            await callback.message.answer("❌ Бот не найден")
            return
        if shop.id in child_bots:
            child_bot = child_bots.pop(shop.id)
            await child_bot.session.close()
        session.delete(shop)
        session.commit()
        await callback.message.edit_text(
            "🗑 Бот удалён",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 В главное меню",
                        callback_data="main_menu"
                    )
                ]]
            )
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("wel_"))
async def edit_welcome_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[1])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "📝 Введите новый текст приветствия:"
    )
    await state.set_state(EditWelcomeStates.waiting_text)


@router.message(EditWelcomeStates.waiting_text)
async def edit_welcome_save(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(
            Shop.id == shop_id
        ).update(
            {"welcome_message": message.text.strip()}
        )
        session.commit()
        await message.answer(
            "✅ Приветственное сообщение обновлено!"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("sup_"))
async def edit_support_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[1])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "🎧 Введите новый юзернейм поддержки (без @):"
    )
    await state.set_state(
        EditSupportStates.waiting_username
    )


@router.message(EditSupportStates.waiting_username)
async def edit_support_save(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        username = message.text.strip().replace("@", "")
        session.query(Shop).filter(
            Shop.id == shop_id
        ).update({"support_username": username})
        session.commit()
        await message.answer(
            f"✅ Юзернейм поддержки обновлён: @{username}"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("bal_"))
async def give_balance_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[1])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "👤 Введите Telegram ID пользователя:"
    )
    await state.set_state(
        GiveBalanceStates.waiting_user_id
    )


@router.message(GiveBalanceStates.waiting_user_id)
async def give_balance_user(
    message: Message,
    state: FSMContext
):
    try:
        user_id = int(message.text.strip())
        await state.update_data(target_user_id=user_id)
        await message.answer("💰 Введите сумму в рублях:")
        await state.set_state(
            GiveBalanceStates.waiting_amount
        )
    except ValueError:
        await message.answer("❌ Неверный ID")


@router.message(GiveBalanceStates.waiting_amount)
async def give_balance_finish(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        amount = Decimal(
            message.text.strip().replace(",", ".")
        )
        data = await state.get_data()
        user = get_or_create_user_sync(
            data["target_user_id"],
            ""
        )
        user.balance += amount
        session.commit()
        await message.answer(
            f"✅ Баланс пользователя "
            f"{data['target_user_id']} "
            f"пополнен на {amount} ₽"
        )
    except ValueError:
        await message.answer("❌ Неверная сумма")
    finally:
        session.close()
        await state.clear()


# Payment settings
@router.callback_query(F.data.startswith("pay_"))
async def payment_settings(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    try:
        shop = session.query(Shop).filter(
            Shop.id == shop_id
        ).first()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=(
                f"🪙 Crypto Bot Token "
                f"{'✅' if shop.crypto_bot_token else '❌'}"
            ),
            callback_data=f"set_crypto_{shop_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=(
                f"💳 ЮMoney кошелёк "
                f"{'✅' if shop.yoomoney_wallet else '❌'}"
            ),
            callback_data=f"set_yoomoney_{shop_id}"
        ))
        builder.row(InlineKeyboardButton(
            text=(
                f"💳 СБП (RollyPay) "
                f"{'✅' if shop.rollypay_terminal_id else '❌'}"
            ),
            callback_data=f"set_rollypay_{shop_id}"
        ))
        builder.row(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"shop_menu_{shop_id}"
        ))
        await callback.message.edit_text(
            "💳 Платёжные реквизиты",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("set_crypto_"))
async def set_crypto_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "🪙 Введите токен Crypto Bot API:"
    )
    await state.set_state(
        PaymentSettingsStates.waiting_crypto_token
    )


@router.message(PaymentSettingsStates.waiting_crypto_token)
async def set_crypto_save(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(
            Shop.id == shop_id
        ).update(
            {"crypto_bot_token": message.text.strip()}
        )
        session.commit()
        await message.answer(
            "✅ Токен Crypto Bot сохранён!"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("set_yoomoney_"))
async def set_yoomoney_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "💳 Введите номер кошелька ЮMoney:"
    )
    await state.set_state(
        PaymentSettingsStates.waiting_yoomoney_wallet
    )


@router.message(PaymentSettingsStates.waiting_yoomoney_wallet)
async def set_yoomoney_save(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(
            Shop.id == shop_id
        ).update(
            {"yoomoney_wallet": message.text.strip()}
        )
        session.commit()
        await message.answer(
            "✅ Кошелёк ЮMoney сохранён!"
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("set_rollypay_"))
async def set_rollypay_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text(
        "💳 Введите Terminal ID:"
    )
    await state.set_state(
        PaymentSettingsStates.waiting_rollypay_terminal
    )


@router.message(
    PaymentSettingsStates.waiting_rollypay_terminal
)
async def set_rollypay_terminal(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        rollypay_terminal=message.text.strip()
    )
    await message.answer("🔑 Введите API Key:")
    await state.set_state(
        PaymentSettingsStates.waiting_rollypay_api_key
    )


@router.message(
    PaymentSettingsStates.waiting_rollypay_api_key
)
async def set_rollypay_api_key(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        rollypay_api_key=message.text.strip()
    )
    await message.answer("🔐 Введите Signing Secret:")
    await state.set_state(
        PaymentSettingsStates.waiting_rollypay_secret
    )


@router.message(
    PaymentSettingsStates.waiting_rollypay_secret
)
async def set_rollypay_save(
    message: Message,
    state: FSMContext
):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(
            Shop.id == shop_id
        ).update({
            "rollypay_terminal_id": data[
                "rollypay_terminal"
            ],
            "rollypay_api_key": data["rollypay_api_key"],
            "rollypay_signing_secret": (
                message.text.strip()
            )
        })
        session.commit()
        await message.answer("✅ RollyPay сохранён!")
    finally:
        session.close()
        await state.clear()


# Shop stats
@router.callback_query(F.data.startswith("st_"))
async def shop_stats(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    try:
        total_sales = session.query(Purchase).filter(
            Purchase.shop_id == shop_id,
            Purchase.status == "completed"
        ).count()
        total_revenue = (
            session.query(func.sum(Purchase.price))
            .filter(
                Purchase.shop_id == shop_id,
                Purchase.status == "completed"
            )
            .scalar() or 0
        )
        total_products = session.query(Product).filter(
            Product.shop_id == shop_id
        ).count()
        text = (
            f"📊 Статистика бота\n\n"
            f"🛍 Продаж: {total_sales}\n"
            f"💰 Выручка: {total_revenue} ₽\n"
            f"📦 Товаров: {total_products}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data=(
                            f"shop_menu_{shop_id}"
                        )
                    )
                ]]
            )
        )
    finally:
        session.close()


# Shop broadcast
@router.callback_query(F.data.startswith("brd_"))
async def broadcast_start(
    callback: CallbackQuery,
    state: FSMContext
):
    shop_id = int(callback.data.split("_")[1])
    await state.update_data(broadcast_shop_id=shop_id)
    await callback.message.edit_text(
        "📢 Введите сообщение для рассылки:"
    )
    await state.set_state(BroadcastStates.waiting_message)


# ============ STARTUP ============

async def on_startup():
    logger.info("🚀 Запуск Vest Multi...")
    init_db()
    session = SessionLocal()
    try:
        shops = session.query(Shop).filter(
            Shop.status == "active"
        ).all()
        for shop in shops:
            try:
                await start_child_bot(shop)
            except Exception as e:
                logger.error(
                    f"❌ Ошибка запуска бота {shop.id}: {e}"
                )
        logger.info(
            f"✅ Запущено {len(shops)} дочерних ботов"
        )
    finally:
        session.close()


async def main():
    await on_startup()
    logger.info("🤖 Основной бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
