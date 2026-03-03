import os
import telebot
import psycopg2
from flask import Flask, request

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

if not TOKEN:
    raise ValueError("Нет TELEGRAM_TOKEN")

if not DATABASE_URL:
    raise ValueError("Нет DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# -------------------
# БАЗА ДАННЫХ
# -------------------

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT,
        name TEXT
        is_active BOOLEAN
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        id SERIAL PRIMARY KEY,
        session_id INTEGER,
        name TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        session_id INTEGER,
        name TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY,
        session_id INTEGER,
        payer TEXT,
        amount NUMERIC,
        description TEXT,
        category_id INTEGER
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expense_shares (
        id SERIAL PRIMARY KEY,
        expense_id INTEGER,
        participant_id INTEGER
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# -------------------
# ВРЕМЕННОЕ ХРАНЕНИЕ СОСТОЯНИЯ
# -------------------

user_state = {}

# -------------------
# ЛОГИКА
# -------------------

# -------------------
# Главное меню
# -------------------

from telebot import types

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Новая встреча")
    markup.add("📂 Мои встречи")
    return markup


@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "Добро пожаловать 💚\nВыбери действие:",
        reply_markup=main_menu()
    )

# -------------------
# Новая встреча
# -------------------

@bot.message_handler(func=lambda m: m.text == "➕ Новая встреча")
def create_session(message):
    msg = bot.send_message(message.chat.id, "Введите название встречи:")
    bot.register_next_step_handler(msg, save_session)


def save_session(message):
    name = message.text
    chat_id = message.chat.id

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO sessions (chat_id, name, is_active) VALUES (%s, %s, %s)",
        (chat_id, name, True)
    )

    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, f"Встреча '{name}' создана 🎉", reply_markup=main_menu())

# -------------------
# Список встреч
# -------------------

@bot.message_handler(func=lambda m: m.text == "📂 Мои встречи")
def list_sessions(message):
    chat_id = message.chat.id

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM sessions WHERE chat_id = %s", (chat_id,))
    sessions = cur.fetchall()

    cur.close()
    conn.close()

    if not sessions:
        bot.send_message(message.chat.id, "У тебя пока нет встреч.", reply_markup=main_menu())
        return

    markup = types.InlineKeyboardMarkup()

    for session in sessions:
        markup.add(
            types.InlineKeyboardButton(
                session[1],
                callback_data=f"open_session_{session[0]}"
            )
        )

    bot.send_message(message.chat.id, "Твои встречи:", reply_markup=markup)
    
# -------------------
# Открытие встречи
# -------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("open_session_"))
def open_session(call):
    session_id = int(call.data.split("_")[2])

    user_state[call.message.chat.id] = {"session_id": session_id}

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("👥 Участники")
    markup.add("💰 Добавить расход")
    markup.add("📊 Баланс")
    markup.add("⬅ Назад")

    bot.send_message(
        call.message.chat.id,
        "Выбери действие:",
        reply_markup=markup
    )

# -------------------
# Кнопка назад
# -------------------

@bot.message_handler(func=lambda m: m.text == "⬅ Назад")
def back_to_menu(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu())

# -------------------
# WEBHOOK
# -------------------

app = Flask(__name__)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def home():
    return "Бот работает", 200

# -------------------
# ЗАПУСК
# -------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/{TOKEN}")

    app.run(host="0.0.0.0", port=port)
