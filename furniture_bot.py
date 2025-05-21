#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот - Каталог мебели
Современный бот для продажи мебели с удобным администрированием
Оптимизирован для развертывания на Railway.app
"""

import logging
import sqlite3
import asyncio
import json
import os
import sys
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from contextlib import asynccontextmanager

# Загрузка переменных окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv не установлен, пропускаем
    pass

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# Настройка логирования для Railway
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Константы для состояний диалога
(
    MAIN_MENU, CATALOG, PRODUCT_VIEW, ADMIN_MENU, ADD_PRODUCT, EDIT_PRODUCT,
    WAITING_NAME, WAITING_DESCRIPTION, WAITING_PRICE, WAITING_CATEGORY,
    WAITING_IMAGES, WAITING_EDIT_FIELD, USER_PROFILE, CART_VIEW,
    ORDER_PHONE, ORDER_ADDRESS, ORDER_COMMENT
) = range(17)

# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()]
DB_NAME = os.getenv("DATABASE_PATH", "furniture_bot.db")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Проверка конфигурации
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения!")
    sys.exit(1)

if not ADMIN_IDS:
    logger.warning("⚠️ ADMIN_IDS не настроен. Добавьте ваши Telegram ID в переменные окружения.")

logger.info(f"🚀 Конфигурация загружена:")
logger.info(f"📊 База данных: {DB_NAME}")
logger.info(f"👥 Администраторы: {len(ADMIN_IDS)} ID(s)")
logger.info(f"🐛 Отладка: {'Включена' if DEBUG else 'Отключена'}")

@dataclass
class Product:
    """Модель товара"""
    id: Optional[int] = None
    name: str = ""
    description: str = ""
    price: float = 0.0
    category: str = ""
    images: List[str] = None
    created_at: Optional[datetime] = None
    is_active: bool = True
    
    def __post_init__(self):
        if self.images is None:
            self.images = []

@dataclass
class User:
    """Модель пользователя"""
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    is_admin: bool = False
    created_at: Optional[datetime] = None

@dataclass
class CartItem:
    """Элемент корзины"""
    product_id: int
    quantity: int
    product_name: str
    product_price: float

class Database:
    """Класс для работы с базой данных"""
    
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица товаров
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                category TEXT NOT NULL,
                images TEXT,  -- JSON массив
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Таблица корзины
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, product_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')
        
        # Таблица заказов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                products TEXT,  -- JSON
                total_amount REAL,
                phone TEXT,
                address TEXT,
                comment TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Таблица категорий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                emoji TEXT DEFAULT '🪑'
            )
        ''')
        
        # Добавляем базовые категории
        categories = [
            ('Диваны и кресла', 'Мягкая мебель для гостиной', '🛋️'),
            ('Столы и стулья', 'Мебель для кухни и столовой', '🪑'),
            ('Шкафы и комоды', 'Мебель для хранения', '🗄️'),
            ('Кровати', 'Мебель для спальни', '🛏️'),
            ('Детская мебель', 'Мебель для детских комнат', '🎨'),
            ('Офисная мебель', 'Мебель для работы', '💼')
        ]
        
        cursor.executemany('''
            INSERT OR IGNORE INTO categories (name, description, emoji) 
            VALUES (?, ?, ?)
        ''', categories)
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[User]:
        """Получить пользователя по ID"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return User(
                id=row[0], username=row[1], first_name=row[2],
                last_name=row[3], phone=row[4], is_admin=bool(row[5]),
                created_at=row[6]
            )
        return None
    
    def save_user(self, user: User):
        """Сохранить пользователя"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (id, username, first_name, last_name, phone, is_admin) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user.id, user.username, user.first_name, user.last_name, 
              user.phone, user.is_admin))
        
        conn.commit()
        conn.close()
    
    def get_categories(self) -> List[Dict]:
        """Получить все категории"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT name, description, emoji FROM categories ORDER BY name')
        rows = cursor.fetchall()
        conn.close()
        
        return [{'name': row[0], 'description': row[1], 'emoji': row[2]} for row in rows]
    
    def get_products_by_category(self, category: str) -> List[Product]:
        """Получить товары по категории"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, description, price, category, images, created_at, is_active 
            FROM products WHERE category = ? AND is_active = TRUE 
            ORDER BY created_at DESC
        ''', (category,))
        
        rows = cursor.fetchall()
        conn.close()
        
        products = []
        for row in rows:
            images = json.loads(row[5]) if row[5] else []
            products.append(Product(
                id=row[0], name=row[1], description=row[2], price=row[3],
                category=row[4], images=images, created_at=row[6], is_active=bool(row[7])
            ))
        
        return products
    
    def get_product_by_id(self, product_id: int) -> Optional[Product]:
        """Получить товар по ID"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, description, price, category, images, created_at, is_active 
            FROM products WHERE id = ?
        ''', (product_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            images = json.loads(row[5]) if row[5] else []
            return Product(
                id=row[0], name=row[1], description=row[2], price=row[3],
                category=row[4], images=images, created_at=row[6], is_active=bool(row[7])
            )
        return None
    
    def save_product(self, product: Product) -> int:
        """Сохранить товар"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        images_json = json.dumps(product.images)
        
        if product.id:
            cursor.execute('''
                UPDATE products SET name = ?, description = ?, price = ?, 
                category = ?, images = ?, is_active = ? WHERE id = ?
            ''', (product.name, product.description, product.price, 
                  product.category, images_json, product.is_active, product.id))
            product_id = product.id
        else:
            cursor.execute('''
                INSERT INTO products (name, description, price, category, images, is_active) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (product.name, product.description, product.price, 
                  product.category, images_json, product.is_active))
            product_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        return product_id
    
    def delete_product(self, product_id: int):
        """Удалить товар (мягкое удаление)"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE products SET is_active = FALSE WHERE id = ?', (product_id,))
        
        conn.commit()
        conn.close()
    
    def get_cart_items(self, user_id: int) -> List[CartItem]:
        """Получить товары из корзины"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT c.product_id, c.quantity, p.name, p.price 
            FROM cart c 
            JOIN products p ON c.product_id = p.id 
            WHERE c.user_id = ? AND p.is_active = TRUE
        ''', (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [CartItem(row[0], row[1], row[2], row[3]) for row in rows]
    
    def add_to_cart(self, user_id: int, product_id: int, quantity: int = 1):
        """Добавить товар в корзину"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO cart (user_id, product_id, quantity) 
            VALUES (?, ?, COALESCE((SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?), 0) + ?)
        ''', (user_id, product_id, user_id, product_id, quantity))
        
        conn.commit()
        conn.close()
    
    def remove_from_cart(self, user_id: int, product_id: int):
        """Удалить товар из корзины"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM cart WHERE user_id = ? AND product_id = ?', (user_id, product_id))
        
        conn.commit()
        conn.close()
    
    def clear_cart(self, user_id: int):
        """Очистить корзину"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM cart WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
    
    def create_order(self, user_id: int, products: List[Dict], total: float, 
                    phone: str, address: str, comment: str = "") -> int:
        """Создать заказ"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO orders (user_id, products, total_amount, phone, address, comment) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, json.dumps(products), total, phone, address, comment))
        
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

# Инициализация базы данных
db = Database()

class FurnitureBot:
    """Основной класс бота"""
    
    def __init__(self):
        self.db = db
        self.user_states = {}  # Временное хранилище состояний
    
    def is_admin(self, user_id: int) -> bool:
        """Проверка прав администратора"""
        user = self.db.get_user(user_id)
        return user_id in ADMIN_IDS or (user and user.is_admin)
    
    def format_price(self, price: float) -> str:
        """Форматирование цены"""
        return f"{price:,.0f} ₽".replace(",", " ")
    
    def get_main_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Главная клавиатура"""
        keyboard = [
            [InlineKeyboardButton("📋 Каталог", callback_data="catalog")],
            [InlineKeyboardButton("🛒 Корзина", callback_data="cart"),
             InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        ]
        
        if self.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data="admin")])
        
        return InlineKeyboardMarkup(keyboard)
    
    def get_categories_keyboard(self) -> InlineKeyboardMarkup:
        """Клавиатура категорий"""
        categories = self.db.get_categories()
        keyboard = []
        
        # Распределяем категории по 2 в ряд
        for i in range(0, len(categories), 2):
            row = []
            for j in range(i, min(i + 2, len(categories))):
                cat = categories[j]
                row.append(InlineKeyboardButton(
                    f"{cat['emoji']} {cat['name']}", 
                    callback_data=f"category_{cat['name']}"
                ))
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
        return InlineKeyboardMarkup(keyboard)
    
    def get_products_keyboard(self, category: str, page: int = 0) -> InlineKeyboardMarkup:
        """Клавиатура товаров категории"""
        products = self.db.get_products_by_category(category)
        keyboard = []
        
        # Пагинация по 10 товаров на странице
        items_per_page = 10
        start_idx = page * items_per_page
        end_idx = start_idx + items_per_page
        page_products = products[start_idx:end_idx]
        
        for product in page_products:
            keyboard.append([InlineKeyboardButton(
                f"{product.name} - {self.format_price(product.price)}", 
                callback_data=f"product_{product.id}"
            )])
        
        # Навигация по страницам
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"products_{category}_{page-1}"))
        if end_idx < len(products):
            nav_row.append(InlineKeyboardButton("➡️", callback_data=f"products_{category}_{page+1}"))
        
        if nav_row:
            keyboard.append(nav_row)
        
        keyboard.append([
            InlineKeyboardButton("📋 Категории", callback_data="catalog"),
            InlineKeyboardButton("🏠 Главная", callback_data="main_menu")
        ])
        
        return InlineKeyboardMarkup(keyboard)
    
    def get_product_keyboard(self, product_id: int, user_id: int) -> InlineKeyboardMarkup:
        """Клавиатура просмотра товара"""
        keyboard = [
            [InlineKeyboardButton("🛒 В корзину", callback_data=f"add_cart_{product_id}")],
            [InlineKeyboardButton("📋 К товарам", callback_data="back_to_products")],
        ]
        
        if self.is_admin(user_id):
            keyboard.insert(1, [
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_product_{product_id}"),
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_product_{product_id}")
            ])
        
        return InlineKeyboardMarkup(keyboard)
    
    def get_admin_keyboard(self) -> InlineKeyboardMarkup:
        """Админ клавиатура"""
        keyboard = [
            [InlineKeyboardButton("➕ Добавить товар", callback_data="add_product")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
             InlineKeyboardButton("📋 Заказы", callback_data="admin_orders")],
            [InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Команда /start"""
        user = update.effective_user
        
        # Регистрируем пользователя
        db_user = User(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_admin=user.id in ADMIN_IDS
        )
        self.db.save_user(db_user)
        
        welcome_text = f"""
🌟 *Добро пожаловать в каталог мебели!*

Привет, {user.first_name}! 
Здесь вы найдете качественную мебель для дома и офиса.

🛋️ Просматривайте каталог
🛒 Добавляйте товары в корзину
📞 Оформляйте заказы

Выберите действие:
"""
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_keyboard(user.id)
        )
        
        return MAIN_MENU
    
    async def main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Главное меню"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        
        text = """
🏠 *Главное меню*

Добро пожаловать в наш каталог мебели!
Выберите нужный раздел:
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_keyboard(user.id)
        )
        
        return MAIN_MENU
    
    async def catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать каталог категорий"""
        query = update.callback_query
        await query.answer()
        
        text = """
📋 *Каталог товаров*

Выберите категорию:
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_categories_keyboard()
        )
        
        return CATALOG
    
    async def show_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать товары категории"""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        
        if callback_data.startswith("category_"):
            category = callback_data.replace("category_", "")
            page = 0
        elif callback_data.startswith("products_"):
            parts = callback_data.split("_")
            category = "_".join(parts[1:-1])
            page = int(parts[-1])
        
        # Сохраняем текущую категорию
        context.user_data['current_category'] = category
        
        products = self.db.get_products_by_category(category)
        
        if not products:
            text = f"📋 *{category}*\n\nВ этой категории пока нет товаров."
        else:
            text = f"📋 *{category}*\n\nВыберите товар:"
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_products_keyboard(category, page)
        )
        
        return CATALOG
    
    async def show_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать детали товара"""
        query = update.callback_query
        await query.answer()
        
        product_id = int(query.data.replace("product_", ""))
        product = self.db.get_product_by_id(product_id)
        
        if not product:
            await query.edit_message_text("❌ Товар не найден")
            return CATALOG
        
        # Сохраняем ID товара
        context.user_data['current_product'] = product_id
        
        text = f"""
🛋️ *{product.name}*

📝 {product.description}

💰 *Цена: {self.format_price(product.price)}*

🏷️ Категория: {product.category}
"""
        
        if product.images:
            try:
                # Отправляем первое изображение с описанием
                await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo=product.images[0],
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self.get_product_keyboard(product_id, query.from_user.id)
                )
                
                # Если есть дополнительные изображения, отправляем их
                if len(product.images) > 1:
                    media_group = []
                    for img_url in product.images[1:]:
                        media_group.append(InputMediaPhoto(media=img_url))
                    
                    await context.bot.send_media_group(
                        chat_id=query.message.chat.id,
                        media=media_group
                    )
                
                # Удаляем предыдущее сообщение
                await query.delete_message()
                
            except Exception as e:
                logger.error(f"Error sending product images: {e}")
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self.get_product_keyboard(product_id, query.from_user.id)
                )
        else:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_product_keyboard(product_id, query.from_user.id)
            )
        
        return PRODUCT_VIEW
    
    async def add_to_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Добавить товар в корзину"""
        query = update.callback_query
        await query.answer()
        
        product_id = int(query.data.replace("add_cart_", ""))
        user_id = query.from_user.id
        
        product = self.db.get_product_by_id(product_id)
        if not product:
            await query.answer("❌ Товар не найден", show_alert=True)
            return PRODUCT_VIEW
        
        self.db.add_to_cart(user_id, product_id)
        
        await query.answer(f"✅ {product.name} добавлен в корзину!", show_alert=True)
        
        return PRODUCT_VIEW
    
    async def show_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать корзину"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        cart_items = self.db.get_cart_items(user_id)
        
        if not cart_items:
            text = "🛒 *Корзина пуста*\n\nДобавьте товары из каталога!"
            keyboard = [
                [InlineKeyboardButton("📋 Каталог", callback_data="catalog")],
                [InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]
            ]
        else:
            text = "🛒 *Ваша корзина:*\n\n"
            total = 0
            
            for item in cart_items:
                item_total = item.product_price * item.quantity
                total += item_total
                text += f"• {item.product_name}\n"
                text += f"  {item.quantity} x {self.format_price(item.product_price)} = {self.format_price(item_total)}\n\n"
            
            text += f"💰 *Итого: {self.format_price(total)}*"
            
            keyboard = [
                [InlineKeyboardButton("🚚 Оформить заказ", callback_data="checkout")],
                [InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart")],
                [InlineKeyboardButton("📋 Продолжить покупки", callback_data="catalog")],
                [InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]
            ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return CART_VIEW
    
    async def clear_cart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Очистить корзину"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        self.db.clear_cart(user_id)
        
        await query.answer("🗑️ Корзина очищена", show_alert=True)
        return await self.show_cart(update, context)
    
    async def checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начать оформление заказа"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        cart_items = self.db.get_cart_items(user_id)
        
        if not cart_items:
            await query.answer("❌ Корзина пуста", show_alert=True)
            return await self.show_cart(update, context)
        
        # Сохраняем данные заказа
        context.user_data['order_items'] = cart_items
        
        text = "📞 *Оформление заказа*\n\nВведите ваш номер телефона:"
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        
        return ORDER_PHONE
    
    async def get_order_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить телефон для заказа"""
        phone = update.message.text.strip()
        
        # Простая валидация телефона
        if len(phone) < 10:
            await update.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
            return ORDER_PHONE
        
        context.user_data['order_phone'] = phone
        
        await update.message.reply_text(
            "📍 *Адрес доставки*\n\nВведите адрес доставки:",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return ORDER_ADDRESS
    
    async def get_order_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить адрес для заказа"""
        address = update.message.text.strip()
        
        if len(address) < 10:
            await update.message.reply_text("❌ Пожалуйста, введите полный адрес доставки:")
            return ORDER_ADDRESS
        
        context.user_data['order_address'] = address
        
        keyboard = [
            [InlineKeyboardButton("✅ Оформить без комментария", callback_data="finish_order")],
            [InlineKeyboardButton("💬 Добавить комментарий", callback_data="add_comment")]
        ]
        
        await update.message.reply_text(
            "💬 *Комментарий к заказу*\n\nХотите добавить комментарий к заказу?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return ORDER_COMMENT
    
    async def get_order_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить комментарий к заказу"""
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            
            if query.data == "add_comment":
                await query.edit_message_text(
                    "💬 Введите комментарий к заказу:",
                    parse_mode=ParseMode.MARKDOWN
                )
                return ORDER_COMMENT
            else:  # finish_order
                context.user_data['order_comment'] = ""
                return await self.finish_order(update, context)
        else:
            # Получили текстовый комментарий
            comment = update.message.text.strip()
            context.user_data['order_comment'] = comment
            
            keyboard = [[InlineKeyboardButton("✅ Оформить заказ", callback_data="finish_order")]]
            
            await update.message.reply_text(
                "✅ Комментарий добавлен!\n\nПодтвердите оформление заказа:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return ORDER_COMMENT
    
    async def finish_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершить оформление заказа"""
        query = update.callback_query
        if query:
            await query.answer()
        
        user_id = update.effective_user.id
        
        # Получаем данные заказа
        cart_items = context.user_data.get('order_items', [])
        phone = context.user_data.get('order_phone', '')
        address = context.user_data.get('order_address', '')
        comment = context.user_data.get('order_comment', '')
        
        if not cart_items:
            text = "❌ Ошибка: корзина пуста"
            keyboard = [[InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]]
        else:
            # Подготавливаем данные заказа
            order_products = []
            total = 0
            
            for item in cart_items:
                order_products.append({
                    'product_id': item.product_id,
                    'name': item.product_name,
                    'price': item.product_price,
                    'quantity': item.quantity,
                    'total': item.product_price * item.quantity
                })
                total += item.product_price * item.quantity
            
            # Создаем заказ
            order_id = self.db.create_order(user_id, order_products, total, phone, address, comment)
            
            # Очищаем корзину
            self.db.clear_cart(user_id)
            
            # Уведомляем администраторов
            await self.notify_admins_new_order(context, order_id, update.effective_user)
            
            text = f"""
✅ *Заказ #{order_id} успешно оформлен!*

📞 Телефон: {phone}
📍 Адрес: {address}
{f'💬 Комментарий: {comment}' if comment else ''}

💰 *Сумма заказа: {self.format_price(total)}*

Наш менеджер свяжется с вами в ближайшее время для подтверждения заказа.

Спасибо за покупку! 🙏
"""
            
            keyboard = [[InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]]
        
        # Очищаем данные заказа
        for key in ['order_items', 'order_phone', 'order_address', 'order_comment']:
            context.user_data.pop(key, None)
        
        if query:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        return MAIN_MENU
    
    async def notify_admins_new_order(self, context: ContextTypes.DEFAULT_TYPE, order_id: int, user):
        """Уведомить администраторов о новом заказе"""
        text = f"""
🆕 *НОВЫЙ ЗАКАЗ #{order_id}*

👤 Клиент: {user.first_name}
📱 Username: @{user.username or 'Не указан'}
🆔 ID: {user.id}

Заказ ожидает обработки!
"""
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    
    async def show_profile(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показать профиль пользователя"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        db_user = self.db.get_user(user.id)
        
        text = f"""
👤 *Ваш профиль*

🆔 ID: {user.id}
👤 Имя: {user.first_name or 'Не указано'}
📱 Username: @{user.username or 'Не указан'}
📞 Телефон: {db_user.phone or 'Не указан'}
"""
        
        keyboard = [
            [InlineKeyboardButton("📞 Изменить телефон", callback_data="change_phone")],
            [InlineKeyboardButton("🏠 Главная", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return USER_PROFILE
    
    # АДМИН ФУНКЦИИ
    
    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Админ меню"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.answer("❌ Нет доступа", show_alert=True)
            return MAIN_MENU
        
        text = """
⚙️ *Панель администратора*

Управление каталогом и заказами:
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_admin_keyboard()
        )
        
        return ADMIN_MENU
    
    async def start_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начать добавление товара"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.answer("❌ Нет доступа", show_alert=True)
            return ADMIN_MENU
        
        # Инициализируем новый товар
        context.user_data['new_product'] = Product()
        
        await query.edit_message_text(
            "➕ *Добавление нового товара*\n\nВведите название товара:",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return WAITING_NAME
    
    async def get_product_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить название товара"""
        name = update.message.text.strip()
        
        if len(name) < 3:
            await update.message.reply_text("❌ Название должно содержать минимум 3 символа:")
            return WAITING_NAME
        
        context.user_data['new_product'].name = name
        
        await update.message.reply_text(
            f"✅ Название: {name}\n\nТеперь введите описание товара:",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return WAITING_DESCRIPTION
    
    async def get_product_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить описание товара"""
        description = update.message.text.strip()
        
        if len(description) < 10:
            await update.message.reply_text("❌ Описание должно содержать минимум 10 символов:")
            return WAITING_DESCRIPTION
        
        context.user_data['new_product'].description = description
        
        await update.message.reply_text(
            f"✅ Описание добавлено\n\nТеперь введите цену товара (только цифры):",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return WAITING_PRICE
    
    async def get_product_price(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить цену товара"""
        try:
            price = float(update.message.text.strip().replace(" ", "").replace(",", "."))
            if price <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ Введите корректную цену (например: 15000):")
            return WAITING_PRICE
        
        context.user_data['new_product'].price = price
        
        # Показываем категории
        categories = self.db.get_categories()
        keyboard = []
        
        for cat in categories:
            keyboard.append([InlineKeyboardButton(
                f"{cat['emoji']} {cat['name']}", 
                callback_data=f"select_cat_{cat['name']}"
            )])
        
        await update.message.reply_text(
            f"✅ Цена: {self.format_price(price)}\n\nВыберите категорию:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return WAITING_CATEGORY
    
    async def get_product_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить категорию товара"""
        query = update.callback_query
        await query.answer()
        
        category = query.data.replace("select_cat_", "")
        context.user_data['new_product'].category = category
        
        await query.edit_message_text(
            f"✅ Категория: {category}\n\nТеперь отправьте изображения товара (можно несколько). Когда закончите, нажмите кнопку ниже:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Завершить добавление изображений", callback_data="finish_images")]
            ])
        )
        
        context.user_data['new_product'].images = []
        
        return WAITING_IMAGES
    
    async def get_product_images(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получить изображения товара"""
        if update.callback_query:
            # Завершаем добавление изображений
            query = update.callback_query
            await query.answer()
            
            return await self.save_new_product(update, context)
        
        if update.message.photo:
            # Получаем файл изображения
            photo = update.message.photo[-1]  # Берем самое большое изображение
            file = await context.bot.get_file(photo.file_id)
            
            # Сохраняем file_id как ссылку на изображение
            context.user_data['new_product'].images.append(photo.file_id)
            
            count = len(context.user_data['new_product'].images)
            await update.message.reply_text(f"✅ Изображение {count} добавлено!")
            
            return WAITING_IMAGES
        else:
            await update.message.reply_text("❌ Пожалуйста, отправьте изображение или завершите добавление.")
            return WAITING_IMAGES
    
    async def save_new_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохранить новый товар"""
        query = update.callback_query
        if query:
            await query.answer()
        
        product = context.user_data.get('new_product')
        if not product:
            text = "❌ Ошибка: данные товара не найдены"
        else:
            try:
                product_id = self.db.save_product(product)
                text = f"""
✅ *Товар успешно добавлен!*

🆔 ID: {product_id}
📦 Название: {product.name}
💰 Цена: {self.format_price(product.price)}
🏷️ Категория: {product.category}
🖼️ Изображений: {len(product.images)}
"""
            except Exception as e:
                logger.error(f"Error saving product: {e}")
                text = "❌ Ошибка при сохранении товара"
        
        # Очищаем данные
        context.user_data.pop('new_product', None)
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить еще товар", callback_data="add_product")],
            [InlineKeyboardButton("⚙️ Админ панель", callback_data="admin")]
        ]
        
        if query:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        return ADMIN_MENU
    
    async def delete_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Удалить товар"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.answer("❌ Нет доступа", show_alert=True)
            return PRODUCT_VIEW
        
        product_id = int(query.data.replace("delete_product_", ""))
        product = self.db.get_product_by_id(product_id)
        
        if not product:
            await query.answer("❌ Товар не найден", show_alert=True)
            return PRODUCT_VIEW
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{product_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"product_{product_id}")]
        ]
        
        await query.edit_message_text(
            f"🗑️ *Удаление товара*\n\nВы уверены, что хотите удалить:\n\n📦 {product.name}\n💰 {self.format_price(product.price)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return PRODUCT_VIEW
    
    async def confirm_delete_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Подтвердить удаление товара"""
        query = update.callback_query
        await query.answer()
        
        product_id = int(query.data.replace("confirm_delete_", ""))
        
        try:
            self.db.delete_product(product_id)
            await query.answer("✅ Товар удален", show_alert=True)
            
            # Возвращаемся к категории
            category = context.user_data.get('current_category')
            if category:
                return await self.show_category(update, context)
            else:
                return await self.catalog(update, context)
                
        except Exception as e:
            logger.error(f"Error deleting product: {e}")
            await query.answer("❌ Ошибка при удалении", show_alert=True)
            return PRODUCT_VIEW
    
    async def back_to_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Вернуться к списку товаров"""
        query = update.callback_query
        await query.answer()
        
        category = context.user_data.get('current_category')
        if category:
            return await self.show_category(update, context)
        else:
            return await self.catalog(update, context)
    
    # Обработка ошибок и отмены
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена текущего действия"""
        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=ReplyKeyboardRemove()
        )
        return await self.start(update, context)
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок"""
        logger.error(f"Update {update} caused error {context.error}")
        
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка. Попробуйте еще раз или обратитесь к администратору."
                )
            except Exception:
                pass

def main():
    """Главная функция запуска бота"""
    
    if not BOT_TOKEN:
        logger.error("❌ Ошибка: Не указан токен бота!")
        logger.error("Установите переменную окружения BOT_TOKEN")
        sys.exit(1)
    
    if not ADMIN_IDS:
        logger.warning("⚠️ Внимание: Не настроены ID администраторов!")
        logger.warning("Установите переменную окружения ADMIN_IDS")
    
    # Создаем экземпляр бота
    bot = FurnitureBot()
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Настройка диалогов
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", bot.start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(bot.catalog, pattern="^catalog$"),
                CallbackQueryHandler(bot.show_cart, pattern="^cart$"),
                CallbackQueryHandler(bot.show_profile, pattern="^profile$"),
                CallbackQueryHandler(bot.admin_menu, pattern="^admin$"),
            ],
            CATALOG: [
                CallbackQueryHandler(bot.main_menu, pattern="^main_menu$"),
                CallbackQueryHandler(bot.show_category, pattern="^category_"),
                CallbackQueryHandler(bot.show_category, pattern="^products_"),
                CallbackQueryHandler(bot.show_product, pattern="^product_"),
            ],
            PRODUCT_VIEW: [
                CallbackQueryHandler(bot.add_to_cart, pattern="^add_cart_"),
                CallbackQueryHandler(bot.back_to_products, pattern="^back_to_products$"),
                CallbackQueryHandler(bot.delete_product, pattern="^delete_product_"),
                CallbackQueryHandler(bot.confirm_delete_product, pattern="^confirm_delete_"),
                CallbackQueryHandler(bot.main_menu, pattern="^main_menu$"),
            ],
            CART_VIEW: [
                CallbackQueryHandler(bot.checkout, pattern="^checkout$"),
                CallbackQueryHandler(bot.clear_cart, pattern="^clear_cart$"),
                CallbackQueryHandler(bot.catalog, pattern="^catalog$"),
                CallbackQueryHandler(bot.main_menu, pattern="^main_menu$"),
            ],
            USER_PROFILE: [
                CallbackQueryHandler(bot.main_menu, pattern="^main_menu$"),
            ],
            ADMIN_MENU: [
                CallbackQueryHandler(bot.start_add_product, pattern="^add_product$"),
                CallbackQueryHandler(bot.main_menu, pattern="^main_menu$"),
            ],
            WAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_product_name),
            ],
            WAITING_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_product_description),
            ],
            WAITING_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_product_price),
            ],
            WAITING_CATEGORY: [
                CallbackQueryHandler(bot.get_product_category, pattern="^select_cat_"),
            ],
            WAITING_IMAGES: [
                MessageHandler(filters.PHOTO, bot.get_product_images),
                CallbackQueryHandler(bot.get_product_images, pattern="^finish_images$"),
            ],
            ORDER_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_order_phone),
            ],
            ORDER_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_order_address),
            ],
            ORDER_COMMENT: [
                CallbackQueryHandler(bot.get_order_comment, pattern="^(add_comment|finish_order)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.get_order_comment),
            ],
        },
        fallbacks=[CommandHandler("cancel", bot.cancel)],
        persistent=False
    )
    
    # Добавляем обработчики
    application.add_handler(conversation_handler)
    
    # Обработчик ошибок
    application.add_error_handler(bot.error_handler)
    
    # Информация о запуске
    logger.info("🚀 Бот запущен!")
    logger.info(f"📊 База данных: {DB_NAME}")
    logger.info(f"👥 Администраторы: {ADMIN_IDS}")
    logger.info("🌐 Режим: Railway Cloud Hosting")
    
    # Запускаем бота
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True  # Игнорируем старые обновления при запуске
        )
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
