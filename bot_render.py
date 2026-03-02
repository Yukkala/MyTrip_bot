import telebot
from datetime import datetime
import sqlite3
from collections import defaultdict
from telebot import types
import os
import time
from flask import Flask, request
import threading

# Токен берется из переменных окружения (настроим позже на Render)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не найден в переменных окружения!")

bot = telebot.TeleBot(TOKEN)

# Словарь для временного хранения данных
user_data = {}

# Создаем Flask приложение для веб-сервера
app = Flask(__name__)

# --- Работа с базой данных ---

def get_db_connection():
    """Создаёт соединение с базой данных"""
    # Используем абсолютный путь для Render
    db_path = os.path.join(os.path.dirname(__file__), 'expenses.db')
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Создает таблицы в базе данных"""
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
    
    # Проверяем, есть ли уже таблица expenses
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
        print("✅ Таблица expenses создана")
    else:
        cursor.execute("PRAGMA table_info(expenses)")
        columns = cursor.fetchall()
        column_names = [col['name'] for col in columns]
        
        if 'category' not in column_names:
            cursor.execute("ALTER TABLE expenses ADD COLUMN category TEXT DEFAULT 'другое'")
            print("✅ Поле category добавлено")
    
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
    print("✅ База данных готова")

init_database()

# --- Здесь идут все функции бота (как в твоем файле) ---
# Копируем все функции из твоего bot.py:
# main_keyboard, cancel_keyboard, split_options_keyboard, categories_keyboard,
# sessions_keyboard, confirm_delete_keyboard, participants_keyboard,
# get_current_session, get_session_name,
# и все обработчики команд (@bot.message_handler и @bot.callback_query_handler)
# и функцию calculate_balances

# ВАЖНО: Копируй ВСЕ функции, которые были в твоем bot.py, кроме самого запуска (bot.polling)

# --- Запуск бота в отдельном потоке ---
def run_bot():
    """Запускает бота в фоновом потоке"""
    print("🤖 Бот запускается...")
    try:
        # Удаляем вебхук на всякий случай
        bot.remove_webhook()
        time.sleep(0.5)
        # Запускаем polling в этом потоке
        bot.infinity_polling()
    except Exception as e:
        print(f"❌ Ошибка в боте: {e}")
        time.sleep(5)
        run_bot()  # Перезапускаем при ошибке

# --- Flask маршруты для Render ---
@app.route('/')
def home():
    """Главная страница для проверки работы"""
    return "🤖 Бот работает! Это служебная страница."

@app.route('/health')
def health():
    """Health check для Render"""
    return "OK", 200

@app.route('/ping')
def ping():
    """Для внешних сервисов, чтобы бот не засыпал"""
    return "pong", 200

# --- Запуск ---
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Запускаем Flask сервер
    port = int(os.environ.get('PORT', 5000))
    print(f"🌍 Веб-сервер запущен на порту {port}")
    app.run(host='0.0.0.0', port=port)