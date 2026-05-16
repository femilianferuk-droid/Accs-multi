"""
Vest Multi - Telegram Bot
Aiogram 3.x + PostgreSQL (psycopg2) + Telethon
"""

import asyncio
import logging
import re
import sys
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Numeric,
    Boolean, DateTime, ForeignKey, select, update, and_, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PhoneCodeExpiredError, PasswordHashInvalidError
)
from telethon.sessions import StringSession

import os
from dotenv import load_dotenv

# ============ КОНФИГУРАЦИЯ ============
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/vest_multi")

# API для Telethon
API_ID = 32480523
API_HASH = "147839735c9fa4e83451209e9b55cfc5"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ============ БАЗА ДАННЫХ (СИНХРОННАЯ) ============
Base = declarative_base()

# Используем синхронный psycopg2
DATABASE_URL = DATABASE_URL.replace("+asyncpg", "").replace("+psycopg", "")
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
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255))
    balance = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    shops = relationship("Shop", back_populates="owner")
    purchases = relationship("Purchase", back_populates="user")


class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    bot_token = Column(String(255), unique=True, nullable=False)
    bot_name = Column(String(255), nullable=False)
    bot_username = Column(String(255))
    support_username = Column(String(255))
    welcome_message = Column(Text, default="👋 Добро пожаловать в наш магазин!")
    crypto_bot_token = Column(String(255))
    yoomoney_wallet = Column(String(255))
    status = Column(String(50), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="shops")
    categories = relationship("Category", back_populates="shop", cascade="all, delete-orphan")
    products = relationship("Product", back_populates="shop", cascade="all, delete-orphan")
    purchases = relationship("Purchase", back_populates="shop")


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    shop = relationship("Shop", back_populates="categories")
    products = relationship("Product", back_populates="category", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    price = Column(Numeric(10, 2), nullable=False)
    phone_number = Column(String(20))
    session_string = Column(Text)
    has_2fa = Column(Boolean, default=False)
    password_2fa = Column(String(255))
    status = Column(String(50), default="available")
    created_at = Column(DateTime, default=datetime.utcnow)

    category = relationship("Category", back_populates="products")
    shop = relationship("Shop", back_populates="products")
    purchases = relationship("Purchase", back_populates="product")


class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="purchases")
    product = relationship("Product", back_populates="purchases")
    shop = relationship("Shop", back_populates="purchases")


# Создаем таблицы при запуске
Base.metadata.create_all(bind=engine)

# ============ FSM СОСТОЯНИЯ ============
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


# ============ КЛАВИАТУРЫ ============
def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🤖 Создать бота", callback_data="create_bot"))
    builder.row(InlineKeyboardButton(text="📋 Мои боты", callback_data="my_bots"))
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url="https://t.me/VestMultiSupport"))
    builder.row(InlineKeyboardButton(text="📖 Инструкции", url="https://t.me/VestMultiTGK"))
    return builder.as_markup()


def get_profile_menu(balance):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="top_up_balance"))
    builder.row(InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data="main_menu"))
    return builder.as_markup()


def get_shop_menu(shop_id):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📂 Управление категориями", callback_data=f"manage_cats_{shop_id}"))
    builder.row(InlineKeyboardButton(text="📦 Управление товарами", callback_data=f"manage_prods_{shop_id}"))
    builder.row(InlineKeyboardButton(text="💳 Платёжные реквизиты", callback_data=f"payment_settings_{shop_id}"))
    builder.row(InlineKeyboardButton(text="📝 Приветственное сообщение", callback_data=f"edit_welcome_{shop_id}"))
    builder.row(InlineKeyboardButton(text="🎧 Изменить юзернейм поддержки", callback_data=f"edit_support_{shop_id}"))
    builder.row(InlineKeyboardButton(text="👤 Выдать баланс пользователю", callback_data=f"give_balance_{shop_id}"))
    builder.row(InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"delete_bot_{shop_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="my_bots"))
    return builder.as_markup()


def get_child_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛍 Купить аккаунт", callback_data="child_buy"))
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="child_profile"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", callback_data="child_support"))
    builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="child_topup"))
    return builder.as_markup()


# ============ ТЕЛЕТОН ============
class TelethonManager:
    def __init__(self):
        self.api_id = API_ID
        self.api_hash = API_HASH

    async def send_code(self, phone_number):
        try:
            client = TelegramClient(StringSession(), self.api_id, self.api_hash)
            await client.connect()
            sent = await client.send_code_request(phone_number)
            return client, sent.phone_code_hash
        except Exception as e:
            logger.error(f"❌ Ошибка отправки кода: {e}")
            return None, None

    async def sign_in(self, client, phone_number, code, phone_code_hash, password_2fa=None):
        try:
            await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
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
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await client.disconnect()
            return None, None, False

    async def get_latest_code(self, session_string):
        client = None
        try:
            client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
            await client.connect()

            if not await client.is_user_authorized():
                return None

            dialogs = await client.get_dialogs(limit=20)

            for dialog in dialogs:
                if not hasattr(dialog.entity, 'username') or dialog.entity.username != 'Telegram':
                    messages = await client.get_messages(dialog, limit=10)
                    for message in messages:
                        if message.text:
                            codes = re.findall(r'\b\d{5}\b', message.text)
                            if codes:
                                return codes[0]
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка получения кода: {e}")
            return None
        finally:
            if client:
                await client.disconnect()


telethon_manager = TelethonManager()

# ============ БОТ ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

child_bots: Dict[int, Bot] = {}


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (СИНХРОННЫЕ) ============
def get_user_sync(telegram_id: int) -> Optional[User]:
    session = SessionLocal()
    try:
        return session.query(User).filter(User.telegram_id == telegram_id).first()
    finally:
        session.close()


def get_or_create_user_sync(telegram_id: int, username: str) -> User:
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            session.commit()
            session.refresh(user)
        return user
    finally:
        session.close()


async def verify_bot_token(token: str) -> Optional[Dict]:
    try:
        temp_bot = Bot(token=token)
        me = await temp_bot.get_me()
        await temp_bot.session.close()
        return {"username": me.username, "name": me.first_name}
    except Exception:
        return None


async def start_child_bot(shop: Shop):
    try:
        if shop.id in child_bots:
            return

        child_bot = Bot(token=shop.bot_token)
        child_dp = Dispatcher(storage=MemoryStorage())
        child_router = Router()

        @child_router.message(Command("start"))
        async def child_start(message: Message):
            try:
                session = SessionLocal()
                try:
                    shop_data = session.query(Shop).filter(Shop.id == shop.id).first()
                    if not shop_data:
                        await message.answer("❌ Магазин не найден")
                        return

                    user = get_or_create_user_sync(message.from_user.id, message.from_user.username or "")
                    welcome_text = shop_data.welcome_message or "👋 Добро пожаловать!"

                    await message.answer(
                        f"{welcome_text}\n\n💰 Ваш баланс: {user.balance} ₽",
                        reply_markup=get_child_main_menu()
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_start: {e}")

        @child_router.callback_query(F.data == "child_buy")
        async def child_buy(callback: CallbackQuery):
            try:
                session = SessionLocal()
                try:
                    categories = session.query(Category).filter(Category.shop_id == shop.id).all()

                    if not categories:
                        await callback.message.answer("📂 Нет доступных категорий")
                        return

                    builder = InlineKeyboardBuilder()
                    for cat in categories:
                        builder.row(InlineKeyboardButton(
                            text=f"📁 {cat.name}",
                            callback_data=f"child_cat_{cat.id}"
                        ))
                    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="child_back"))

                    await callback.message.edit_text("🛍 Выберите категорию:", reply_markup=builder.as_markup())
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_buy: {e}")

        @child_router.callback_query(F.data.startswith("child_cat_"))
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
                        await callback.message.answer("📦 Нет товаров в этой категории")
                        return

                    builder = InlineKeyboardBuilder()
                    for product in products:
                        builder.row(InlineKeyboardButton(
                            text=f"🛍 {product.name} — 💵 {product.price} ₽",
                            callback_data=f"child_product_{product.id}"
                        ))
                    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="child_buy"))

                    await callback.message.edit_text("📦 Выберите товар:", reply_markup=builder.as_markup())
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_category_products: {e}")

        @child_router.callback_query(F.data.startswith("child_product_"))
        async def child_product_detail(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    product = session.query(Product).filter(Product.id == product_id).first()

                    if not product:
                        await callback.message.answer("❌ Товар не найден")
                        return

                    text = f"""
📛 <b>{product.name}</b>
📝 {product.description or 'Нет описания'}
💵 Цена: {product.price} ₽

Выберите способ оплаты:
"""
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text="💰 Баланс", callback_data=f"pay_balance_{product.id}"))
                    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"child_cat_{product.category_id}"))

                    await callback.message.edit_text(text, reply_markup=builder.as_markup())
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_product_detail: {e}")

        @child_router.callback_query(F.data.startswith("pay_balance_"))
        async def pay_balance(callback: CallbackQuery):
            try:
                product_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()
                    product = session.query(Product).filter(Product.id == product_id).first()

                    if not product or product.status != "available":
                        await callback.message.answer("❌ Товар недоступен")
                        return

                    if not user or user.balance < product.price:
                        await callback.message.answer(
                            f"❌ Недостаточно средств!\n💰 Ваш баланс: {user.balance if user else 0} ₽\n💵 Цена: {product.price} ₽"
                        )
                        return

                    user.balance -= product.price
                    product.status = "sold"

                    purchase = Purchase(
                        user_id=user.telegram_id,
                        product_id=product.id,
                        shop_id=shop.id,
                        price=product.price,
                        payment_method="balance"
                    )
                    session.add(purchase)
                    session.commit()

                    await callback.message.answer(
                        f"""
✅ <b>Покупка успешна!</b>

📱 Номер телефона: <code>{product.phone_number}</code>
💵 Цена: {product.price} ₽

Нажмите кнопку ниже чтобы получить код подтверждения:
""",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🔢 Запросить код", callback_data=f"get_code_{purchase.id}")]
                        ])
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка pay_balance: {e}")

        @child_router.callback_query(F.data.startswith("get_code_"))
        async def get_code(callback: CallbackQuery):
            try:
                purchase_id = int(callback.data.split("_")[2])
                session = SessionLocal()
                try:
                    purchase = session.query(Purchase).filter(Purchase.id == purchase_id).first()

                    if not purchase or purchase.user_id != callback.from_user.id:
                        await callback.message.answer("❌ Покупка не найдена")
                        return

                    product = session.query(Product).filter(Product.id == purchase.product_id).first()

                    if not product or not product.session_string:
                        await callback.message.answer("❌ Невозможно получить код")
                        return

                    code = await telethon_manager.get_latest_code(product.session_string)

                    if code:
                        response = f"📱 Номер: <code>{product.phone_number}</code>\n🔢 Код: <code>{code}</code>"
                        if product.has_2fa and product.password_2fa:
                            response += f"\n🔑 2FA пароль: <code>{product.password_2fa}</code>"
                        await callback.message.answer(response)
                    else:
                        await callback.message.answer("❌ Не удалось найти код. Попробуйте позже.")
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка get_code: {e}")

        @child_router.callback_query(F.data == "child_profile")
        async def child_profile_handler(callback: CallbackQuery):
            try:
                session = SessionLocal()
                try:
                    user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()
                    purchases = session.query(Purchase).filter(
                        Purchase.user_id == callback.from_user.id
                    ).order_by(Purchase.created_at.desc()).limit(10).all()

                    text = f"""
👤 <b>Профиль</b>

🆔 ID: <code>{user.telegram_id if user else 'Не найден'}</code>
📛 Username: @{user.username if user else 'Нет'}
💰 Баланс: {user.balance if user else 0} ₽
"""
                    builder = InlineKeyboardBuilder()
                    if purchases:
                        builder.row(InlineKeyboardButton(text="📦 Мои покупки", callback_data="child_purchases"))
                    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="child_back"))

                    await callback.message.edit_text(text, reply_markup=builder.as_markup())
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_profile: {e}")

        @child_router.callback_query(F.data == "child_purchases")
        async def child_purchases_list(callback: CallbackQuery):
            try:
                session = SessionLocal()
                try:
                    purchases = session.query(Purchase).filter(
                        Purchase.user_id == callback.from_user.id
                    ).order_by(Purchase.created_at.desc()).limit(10).all()

                    if not purchases:
                        await callback.message.answer("📦 У вас пока нет покупок")
                        return

                    builder = InlineKeyboardBuilder()
                    for purchase in purchases:
                        product = session.query(Product).filter(Product.id == purchase.product_id).first()
                        phone = product.phone_number if product else "Неизвестно"
                        builder.row(InlineKeyboardButton(
                            text=f"📱 {phone} — 💵 {purchase.price} ₽",
                            callback_data=f"get_code_{purchase.id}"
                        ))
                    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="child_profile"))

                    await callback.message.edit_text(
                        "📦 <b>Ваши последние покупки:</b>\nНажмите чтобы получить код:",
                        reply_markup=builder.as_markup()
                    )
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"❌ Ошибка child_purchases: {e}")

        @child_router.callback_query(F.data == "child_support")
        async def child_support_handler(callback: CallbackQuery):
            session = SessionLocal()
            try:
                shop_data = session.query(Shop).filter(Shop.id == shop.id).first()
                if shop_data and shop_data.support_username:
                    await callback.message.answer(f"🆘 Поддержка: @{shop_data.support_username}")
                else:
                    await callback.message.answer("🆘 Поддержка пока не настроена")
            finally:
                session.close()

        @child_router.callback_query(F.data == "child_back")
        async def child_back(callback: CallbackQuery):
            session = SessionLocal()
            try:
                user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()
                shop_data = session.query(Shop).filter(Shop.id == shop.id).first()
                welcome_text = shop_data.welcome_message if shop_data else "👋 Добро пожаловать!"

                await callback.message.edit_text(
                    f"{welcome_text}\n\n💰 Ваш баланс: {user.balance if user else 0} ₽",
                    reply_markup=get_child_main_menu()
                )
            finally:
                session.close()

        child_dp.include_router(child_router)
        asyncio.create_task(child_dp.start_polling(child_bot))
        child_bots[shop.id] = child_bot
        logger.info(f"✅ Дочерний бот {shop.bot_name} запущен")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска дочернего бота {shop.id}: {e}")


# ============ ОСНОВНЫЕ ХЕНДЛЕРЫ ============
@router.message(Command("start"))
async def cmd_start(message: Message):
    try:
        get_or_create_user_sync(message.from_user.id, message.from_user.username or "")
        await message.answer(
            "🤖 <b>Vest Multi</b>\n\nДобро пожаловать в главное меню!",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        logger.error(f"❌ Ошибка start: {e}")


@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🤖 <b>Vest Multi</b>\n\nГлавное меню:",
            reply_markup=get_main_menu()
        )
    except Exception:
        await callback.message.answer("🤖 <b>Vest Multi</b>\n\nГлавное меню:", reply_markup=get_main_menu())


@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()
        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        text = f"""
👤 <b>Профиль</b>

🆔 Telegram ID: <code>{user.telegram_id}</code>
📛 Username: @{user.username or 'Нет'}
💰 Баланс: {user.balance} ₽
"""
        await callback.message.edit_text(text, reply_markup=get_profile_menu(user.balance))
    finally:
        session.close()


@router.callback_query(F.data == "create_bot")
async def create_bot_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📌 <b>Создание нового бота</b>\n\nШаг 1/3: Введите токен бота от @BotFather")
    await state.set_state(CreateBotStates.waiting_token)


@router.message(CreateBotStates.waiting_token)
async def process_bot_token(message: Message, state: FSMContext):
    try:
        token = message.text.strip()
        bot_info = await verify_bot_token(token)

        if not bot_info:
            await message.answer("❌ Токен недействителен, попробуйте снова")
            return

        await state.update_data(token=token, bot_username=bot_info["username"])
        await message.answer("🏷 <b>Шаг 2/3:</b> Введите название бота")
        await state.set_state(CreateBotStates.waiting_name)
    except Exception as e:
        logger.error(f"❌ Ошибка process_bot_token: {e}")


@router.message(CreateBotStates.waiting_name)
async def process_bot_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("🎧 <b>Шаг 3/3:</b> Введите юзернейм поддержки (без @)")
    await state.set_state(CreateBotStates.waiting_support)


@router.message(CreateBotStates.waiting_support)
async def process_bot_support(message: Message, state: FSMContext):
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

        text = f"""
✅ <b>Бот успешно создан и запущен!</b>

🤖 Название: {shop.bot_name}
📛 Юзернейм: @{shop.bot_username}
🎧 Поддержка: @{shop.support_username}
🟢 Статус: Активен

Бот доступен по ссылке: t.me/{shop.bot_username}
"""
        await message.answer(text, reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"❌ Ошибка создания бота: {e}")
        await message.answer("❌ Ошибка при создании бота")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data == "my_bots")
async def show_my_bots(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shops = session.query(Shop).filter(Shop.owner_id == callback.from_user.id).all()

        if not shops:
            await callback.message.edit_text(
                "📋 У вас пока нет созданных ботов",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
                ])
            )
            return

        builder = InlineKeyboardBuilder()
        for shop in shops:
            status_emoji = "🟢" if shop.status == "active" else "🔴"
            builder.row(InlineKeyboardButton(
                text=f"🤖 {shop.bot_name} (@{shop.bot_username}) — {status_emoji}",
                callback_data=f"shop_menu_{shop.id}"
            ))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))

        await callback.message.edit_text("📋 <b>Ваши боты:</b>", reply_markup=builder.as_markup())
    finally:
        session.close()


@router.callback_query(F.data.startswith("shop_menu_"))
async def shop_menu(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        shop = session.query(Shop).filter(Shop.id == shop_id).first()

        if not shop or shop.owner_id != callback.from_user.id:
            await callback.message.answer("❌ Бот не найден")
            return

        text = f"""
🤖 <b>{shop.bot_name}</b> (@{shop.bot_username})
🟢 Статус: {shop.status}
🎧 Поддержка: @{shop.support_username}
"""
        await callback.message.edit_text(text, reply_markup=get_shop_menu(shop_id))
    finally:
        session.close()


@router.callback_query(F.data.startswith("manage_cats_"))
async def manage_categories(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        categories = session.query(Category).filter(Category.shop_id == shop_id).all()

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="➕ Добавить категорию", callback_data=f"add_category_{shop_id}"))

        for cat in categories:
            builder.row(InlineKeyboardButton(text=f"📁 {cat.name}", callback_data=f"edit_cat_{cat.id}"))

        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"shop_menu_{shop_id}"))

        await callback.message.edit_text("📂 <b>Управление категориями:</b>", reply_markup=builder.as_markup())
    finally:
        session.close()


@router.callback_query(F.data.startswith("add_category_"))
async def add_category_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("📂 Введите название категории:")
    await state.set_state(AddCategoryStates.waiting_name)


@router.message(AddCategoryStates.waiting_name)
async def add_category_finish(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        category = Category(shop_id=shop_id, name=message.text.strip())
        session.add(category)
        session.commit()
        await message.answer(f"✅ Категория <b>{message.text}</b> добавлена!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("manage_prods_"))
async def manage_products(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        categories = session.query(Category).filter(Category.shop_id == shop_id).all()

        if not categories:
            await callback.message.edit_text(
                "📂 Сначала добавьте категорию!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data=f"shop_menu_{shop_id}")]
                ])
            )
            return

        builder = InlineKeyboardBuilder()
        for cat in categories:
            builder.row(InlineKeyboardButton(text=f"📁 {cat.name}", callback_data=f"show_products_{cat.id}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"shop_menu_{shop_id}"))

        await callback.message.edit_text("📦 Выберите категорию:", reply_markup=builder.as_markup())
    finally:
        session.close()


@router.callback_query(F.data.startswith("show_products_"))
async def show_products(callback: CallbackQuery):
    session = SessionLocal()
    try:
        cat_id = int(callback.data.split("_")[2])
        category = session.query(Category).filter(Category.id == cat_id).first()

        if not category:
            await callback.message.answer("❌ Категория не найдена")
            return

        products = session.query(Product).filter(Product.category_id == cat_id).all()

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"add_product_{cat_id}"))

        for product in products:
            status = "🟢" if product.status == "available" else "🔴"
            builder.row(InlineKeyboardButton(
                text=f"{status} {product.name} — {product.price} ₽",
                callback_data=f"product_menu_{product.id}"
            ))

        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"manage_prods_{category.shop_id}"))

        await callback.message.edit_text(
            f"📦 Товары в категории <b>{category.name}</b>:",
            reply_markup=builder.as_markup()
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("add_product_"))
async def add_product_start(callback: CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[2])
    await state.update_data(category_id=cat_id)
    await callback.message.edit_text("📛 Введите название товара:")
    await state.set_state(AddProductStates.waiting_name)


@router.message(AddProductStates.waiting_name)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("📝 Введите описание товара:")
    await state.set_state(AddProductStates.waiting_description)


@router.message(AddProductStates.waiting_description)
async def add_product_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("💵 Введите цену в рублях:")
    await state.set_state(AddProductStates.waiting_price)


@router.message(AddProductStates.waiting_price)
async def add_product_price(message: Message, state: FSMContext):
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
        await state.update_data(price=price)
        await message.answer("📱 Введите номер телефона (например +79991234567):")
        await state.set_state(AddProductStates.waiting_phone)
    except ValueError:
        await message.answer("❌ Неверный формат цены")


@router.message(AddProductStates.waiting_phone)
async def add_product_phone(message: Message, state: FSMContext):
    try:
        phone = message.text.strip()
        if not phone.startswith("+"):
            phone = "+" + phone

        client, phone_code_hash = await telethon_manager.send_code(phone)

        if not client:
            await message.answer("❌ Не удалось отправить код")
            return

        await state.update_data(
            phone=phone,
            client_session=client.session.save(),
            phone_code_hash=phone_code_hash
        )

        await message.answer("🔢 Введите код подтверждения из SMS:")
        await state.set_state(AddProductStates.waiting_code)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки кода: {e}")


@router.message(AddProductStates.waiting_code)
async def add_product_code(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        code = message.text.strip()

        client = TelegramClient(
            StringSession(data["client_session"]),
            telethon_manager.api_id,
            telethon_manager.api_hash
        )
        await client.connect()

        session_string, password_2fa, has_2fa = await telethon_manager.sign_in(
            client, data["phone"], code, data["phone_code_hash"]
        )

        if session_string is None and has_2fa:
            await state.update_data(client_session=client.session.save())
            await message.answer("🔐 Требуется пароль 2FA. Введите пароль:")
            await state.set_state(AddProductStates.waiting_2fa)
            return

        if session_string is None:
            await message.answer("❌ Неверный код подтверждения")
            await client.disconnect()
            return

        await save_product(message, state, session_string, password_2fa, has_2fa)
        await client.disconnect()
    except Exception as e:
        logger.error(f"❌ Ошибка входа: {e}")


@router.message(AddProductStates.waiting_2fa)
async def add_product_2fa(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        password_2fa = message.text.strip()

        client = TelegramClient(
            StringSession(data["client_session"]),
            telethon_manager.api_id,
            telethon_manager.api_hash
        )
        await client.connect()

        session_string, saved_2fa, has_2fa = await telethon_manager.sign_in(
            client, data["phone"], None, data["phone_code_hash"], password_2fa
        )

        if session_string is None:
            await message.answer("❌ Неверный пароль 2FA")
            return

        await save_product(message, state, session_string, saved_2fa, has_2fa)
        await client.disconnect()
    except Exception as e:
        logger.error(f"❌ Ошибка 2FA: {e}")


async def save_product(message: Message, state: FSMContext, session_string: str,
                       password_2fa: str, has_2fa: bool):
    session = SessionLocal()
    try:
        data = await state.get_data()
        category = session.query(Category).filter(Category.id == data["category_id"]).first()

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
            f"""
✅ <b>Товар успешно добавлен!</b>
📛 {product.name}
💵 {product.price} ₽
📱 {product.phone_number}
🔐 2FA: {"Есть" if product.has_2fa else "Нет"}
""",
            reply_markup=get_main_menu()
        )
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("product_menu_"))
async def product_menu(callback: CallbackQuery):
    session = SessionLocal()
    try:
        product_id = int(callback.data.split("_")[2])
        product = session.query(Product).filter(Product.id == product_id).first()

        if not product:
            await callback.message.answer("❌ Товар не найден")
            return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_product_{product.id}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"show_products_{product.category_id}"))

        text = f"""
📛 <b>{product.name}</b>
📝 {product.description or 'Нет описания'}
💵 {product.price} ₽
📱 {product.phone_number}
🟢 {product.status}
"""
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
    finally:
        session.close()


@router.callback_query(F.data.startswith("delete_product_"))
async def delete_product(callback: CallbackQuery):
    session = SessionLocal()
    try:
        product_id = int(callback.data.split("_")[2])
        product = session.query(Product).filter(Product.id == product_id).first()

        if not product:
            await callback.message.answer("❌ Товар не найден")
            return

        cat_id = product.category_id
        session.delete(product)
        session.commit()

        await callback.message.edit_text(
            "✅ Товар удалён",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data=f"show_products_{cat_id}")]
            ])
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("delete_bot_"))
async def delete_bot_confirm(callback: CallbackQuery):
    shop_id = int(callback.data.split("_")[2])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{shop_id}"))
    builder.row(InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"shop_menu_{shop_id}"))
    await callback.message.edit_text("⚠️ <b>Вы уверены, что хотите удалить бота?</b>", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("confirm_delete_"))
async def delete_bot_execute(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        shop = session.query(Shop).filter(Shop.id == shop_id).first()

        if not shop or shop.owner_id != callback.from_user.id:
            await callback.message.answer("❌ Бот не найден")
            return

        if shop.id in child_bots:
            child_bot = child_bots.pop(shop.id)
            await child_bot.session.close()

        session.delete(shop)
        session.commit()

        await callback.message.edit_text(
            "🗑 Бот удалён",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
            ])
        )
    finally:
        session.close()


@router.callback_query(F.data.startswith("edit_welcome_"))
async def edit_welcome_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("📝 Введите новый текст приветствия:")
    await state.set_state(EditWelcomeStates.waiting_text)


@router.message(EditWelcomeStates.waiting_text)
async def edit_welcome_save(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(Shop.id == shop_id).update({"welcome_message": message.text.strip()})
        session.commit()
        await message.answer("✅ Приветственное сообщение обновлено!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("edit_support_"))
async def edit_support_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("🎧 Введите новый юзернейм поддержки (без @):")
    await state.set_state(EditSupportStates.waiting_username)


@router.message(EditSupportStates.waiting_username)
async def edit_support_save(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        username = message.text.strip().replace("@", "")
        session.query(Shop).filter(Shop.id == shop_id).update({"support_username": username})
        session.commit()
        await message.answer(f"✅ Юзернейм поддержки обновлён: @{username}")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("give_balance_"))
async def give_balance_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("👤 Введите Telegram ID пользователя:")
    await state.set_state(GiveBalanceStates.waiting_user_id)


@router.message(GiveBalanceStates.waiting_user_id)
async def give_balance_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(target_user_id=user_id)
        await message.answer("💰 Введите сумму в рублях:")
        await state.set_state(GiveBalanceStates.waiting_amount)
    except ValueError:
        await message.answer("❌ Неверный ID")


@router.message(GiveBalanceStates.waiting_amount)
async def give_balance_finish(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        data = await state.get_data()
        
        user = session.query(User).filter(User.telegram_id == data["target_user_id"]).first()
        if not user:
            user = User(telegram_id=data["target_user_id"], username="", balance=amount)
            session.add(user)
        else:
            user.balance += amount
        session.commit()
        
        await message.answer(f"✅ Баланс пользователя {data['target_user_id']} пополнен на {amount} ₽")
    except ValueError:
        await message.answer("❌ Неверная сумма")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("payment_settings_"))
async def payment_settings(callback: CallbackQuery):
    session = SessionLocal()
    try:
        shop_id = int(callback.data.split("_")[2])
        shop = session.query(Shop).filter(Shop.id == shop_id).first()

        crypto_status = "✅" if shop and shop.crypto_bot_token else "❌"
        yoomoney_status = "✅" if shop and shop.yoomoney_wallet else "❌"

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=f"🪙 Crypto Bot Token ({crypto_status})", callback_data=f"set_crypto_{shop_id}"))
        builder.row(InlineKeyboardButton(text=f"💳 ЮMoney кошелёк ({yoomoney_status})", callback_data=f"set_yoomoney_{shop_id}"))
        builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data=f"shop_menu_{shop_id}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"shop_menu_{shop_id}"))

        await callback.message.edit_text("💳 <b>Платёжные реквизиты</b>", reply_markup=builder.as_markup())
    finally:
        session.close()


@router.callback_query(F.data.startswith("set_crypto_"))
async def set_crypto_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("🪙 Введите токен Crypto Bot API:")
    await state.set_state(PaymentSettingsStates.waiting_crypto_token)


@router.message(PaymentSettingsStates.waiting_crypto_token)
async def set_crypto_save(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(Shop.id == shop_id).update({"crypto_bot_token": message.text.strip()})
        session.commit()
        await message.answer("✅ Токен Crypto Bot сохранён!")
    finally:
        session.close()
        await state.clear()


@router.callback_query(F.data.startswith("set_yoomoney_"))
async def set_yoomoney_start(callback: CallbackQuery, state: FSMContext):
    shop_id = int(callback.data.split("_")[2])
    await state.update_data(shop_id=shop_id)
    await callback.message.edit_text("💳 Введите номер кошелька ЮMoney:")
    await state.set_state(PaymentSettingsStates.waiting_yoomoney_wallet)


@router.message(PaymentSettingsStates.waiting_yoomoney_wallet)
async def set_yoomoney_save(message: Message, state: FSMContext):
    session = SessionLocal()
    try:
        data = await state.get_data()
        shop_id = data["shop_id"]
        session.query(Shop).filter(Shop.id == shop_id).update({"yoomoney_wallet": message.text.strip()})
        session.commit()
        await message.answer("✅ Кошелёк ЮMoney сохранён!")
    finally:
        session.close()
        await state.clear()


# ============ ЗАПУСК ============
async def on_startup():
    logger.info("🚀 Запуск Vest Multi...")

    session = SessionLocal()
    try:
        shops = session.query(Shop).filter(Shop.status == "active").all()
        for shop in shops:
            try:
                await start_child_bot(shop)
            except Exception as e:
                logger.error(f"❌ Ошибка запуска бота {shop.id}: {e}")
        logger.info(f"✅ Запущено {len(shops)} дочерних ботов")
    finally:
        session.close()


async def main():
    await on_startup()
    logger.info("🤖 Основной бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
