import telebot
from datetime import datetime
import sqlite3
from collections import defaultdict
from telebot import types
import os
import time
from flask import Flask, request
import threading
import logging
import sys

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Токен берется из переменных окружения
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не найден в переменных окружения!")
    raise ValueError("❌ TELEGRAM_TOKEN не найден в переменных окружения!")

logger.info("✅ Токен получен")
bot = telebot.TeleBot(TOKEN)

# Словарь для временного хранения данных
user_data = {}

# Создаем Flask приложение
app = Flask(__name__)

# --- Работа с базой данных ---

def get_db_connection():
    """Создаёт соединение с базой данных"""
    try:
        db_path = os.path.join(os.path.dirname(__file__), 'expenses.db')
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise

def init_database():
    """Создает таблицы в базе данных"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Таблица для сессий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                name TEXT,
                created_date TEXT,
                UNIQUE(chat_id, name)
            )
        ''')
        
        # Таблица для участников
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS travelers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                session_id INTEGER,
                name TEXT,
                UNIQUE(session_id, name)
            )
        ''')
        
        # Таблица для категорий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                emoji TEXT
            )
        ''')
        
        # Проверяем таблицу expenses
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expenses'")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            cursor.execute('''
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    session_id INTEGER,
                    payer TEXT,
                    amount REAL,
                    description TEXT,
                    category TEXT,
                    date TEXT,
                    participants TEXT
                )
            ''')
            logger.info("✅ Таблица expenses создана")
        else:
            cursor.execute("PRAGMA table_info(expenses)")
            columns = cursor.fetchall()
            column_names = [col['name'] for col in columns]
            
            if 'category' not in column_names:
                cursor.execute("ALTER TABLE expenses ADD COLUMN category TEXT DEFAULT 'другое'")
                logger.info("✅ Поле category добавлено в таблицу expenses")
        
        conn.commit()
        
        # Добавляем стандартные категории
        default_categories = [
            ("🍕 Еда", "еда"),
            ("🥤 Напитки", "напитки"),
            ("🛒 Продукты", "продукты"),
            ("🚖 Такси", "такси"),
            ("🏨 Проживание", "проживание"),
            ("🎟 Экскурсии", "экскурсии"),
            ("🪩 Клуб", "клуб"),
            ("🎫 Билеты", "билеты"),
            ("🛍 Покупки", "покупки"),
            ("💰 Другое", "другое")
        ]
        
        for emoji_name, cat_name in default_categories:
            try:
                cursor.execute(
                    "INSERT INTO categories (name, emoji) VALUES (?, ?)",
                    (cat_name, emoji_name)
                )
            except sqlite3.IntegrityError:
                pass
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных готова")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise

# Инициализируем БД при запуске
init_database()

# --- Клавиатуры (копируем из твоего bot.py) ---
# ВНИМАНИЕ: СЮДА НУЖНО СКОПИРОВАТЬ ВСЕ ФУНКЦИИ КЛАВИАТУР ИЗ ТВОЕГО bot.py
# Например:
def main_keyboard(has_sessions=False):
    """Главная клавиатура"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    if has_sessions:
        buttons = [
            types.KeyboardButton("➕ Новая встреча"),
            types.KeyboardButton("📋 Выбрать встречу"),
            types.KeyboardButton("👥 Добавить участницу"),
            types.KeyboardButton("💰 Добавить расход"),
            types.KeyboardButton("📊 Все расходы"),
            types.KeyboardButton("📊 По категориям"),
            types.KeyboardButton("❌ Удалить встречу")
        ]
    else:
        buttons = [
            types.KeyboardButton("➕ Новая встреча")
        ]
    
    keyboard.add(*buttons)
    return keyboard

# [ЗДЕСЬ ДОЛЖНЫ БЫТЬ ВСЕ ОСТАЛЬНЫЕ ФУНКЦИИ ИЗ ТВОЕГО bot.py]
# КОПИРУЙ ИХ СЮДА ПОЛНОСТЬЮ!
# - cancel_keyboard
# - split_options_keyboard
# - categories_keyboard
# - sessions_keyboard
# - confirm_delete_keyboard
# - participants_keyboard
# - get_current_session
# - get_session_name
# - все обработчики (@bot.message_handler и @bot.callback_query_handler)
# - calculate_balances

# [ЗДЕСЬ ВСЕ ОБРАБОТЧИКИ КОМАНД]

# --- Запуск бота ---
def run_bot():
    """Запускает бота в фоновом потоке с очисткой вебхуков"""
    logger.info("🤖 Запуск бота...")
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            logger.info("🔄 Очистка вебхуков и очереди обновлений...")
            
            # 1. Удаляем вебхук
            bot.remove_webhook()
            time.sleep(1)
            
            # 2. Закрываем все активные сессии
            bot.close()
            time.sleep(1)
            
            # 3. Проверяем, что вебхук действительно удален
            webhook_info = bot.get_webhook_info()
            logger.info(f"📊 Информация о вебхуке: {webhook_info}")
            
            # 4. Удаляем ожидающие обновления (важно!)
            updates = bot.get_updates(offset=-1, timeout=1)
            if updates:
                logger.info(f"🧹 Найдены ожидающие обновления: {len(updates)}")
                # Подтверждаем получение обновлений
                bot.get_updates(offset=updates[-1].update_id + 1, timeout=1)
            
            time.sleep(2)
            
            # 5. Запускаем polling с правильными параметрами
            logger.info("✅ Запуск polling...")
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                restart_on_change=True,  # Важно!
                skip_pending=True  # Пропускаем старые обновления
            )
        except Exception as e:
            retry_count += 1
            logger.error(f"❌ Ошибка в боте (попытка {retry_count}/{max_retries}): {e}")
            logger.error(f"Тип ошибки: {type(e).__name__}")
            if "409" in str(e):
                logger.error("⚠️ Конфликт: бот запущен в другом месте! Проверь локальный запуск.")
                logger.error("💡 Закрой терминал с локальным ботом и подожди 30 секунд")
            time.sleep(10)
    
    logger.error("❌ Бот остановлен после максимального числа попыток")
    
# --- Flask маршруты ---
@app.route('/')
def home():
    """Главная страница"""
    return "🤖 Бот работает! Это служебная страница."

@app.route('/health')
def health():
    """Health check"""
    return "OK", 200

@app.route('/ping')
def ping():
    """Для UptimeRobot"""
    return "pong", 200

@app.route('/debug')
def debug():
    """Отладочная информация"""
    return {
        'status': 'running',
        'bot_started': bot_thread.is_alive() if 'bot_thread' in globals() else False,
        'db_initialized': True
    }

# --- Запуск ---
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    logger.info("✅ Поток бота запущен")
    
    # Получаем порт из переменных окружения Render
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌍 Запуск веб-сервера на порту {port}")
    
    # Для production используем параметры, рекомендованные Render
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,  # Важно: выключаем debug режим
        use_reloader=False,  # Отключаем автоперезагрузку
        threaded=True
    )
