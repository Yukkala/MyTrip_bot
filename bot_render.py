import os
import telebot
import sqlite3
from flask import Flask, request

# =====================
# НАСТРОЙКА ТОКЕНА
# =====================

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден")

bot = telebot.TeleBot(TOKEN)

# =====================
# БАЗА ДАННЫХ
# =====================

def get_db():
    conn = sqlite3.connect("expenses.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        name TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        name TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        payer TEXT,
        amount REAL,
        description TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# =====================
# ЛОГИКА БОТА
# =====================

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Бот учёта расходов работает 💰")

@bot.message_handler(commands=["new"])
def new_session(message):
    name = message.text.replace("/new", "").strip()
    if not name:
        bot.send_message(message.chat.id, "Напиши: /new Название встречи")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sessions (chat_id, name) VALUES (?, ?)",
                   (message.chat.id, name))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, f"Создана встреча: {name}")

@bot.message_handler(commands=["sessions"])
def list_sessions(message):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sessions WHERE chat_id = ?",
                   (message.chat.id,))
    sessions = cursor.fetchall()
    conn.close()

    if not sessions:
        bot.send_message(message.chat.id, "Нет встреч")
        return

    text = "Твои встречи:\n"
    for s in sessions:
        text += f"{s['id']}. {s['name']}\n"

    bot.send_message(message.chat.id, text)

# =====================
# WEBHOOK
# =====================

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

# =====================
# ЗАПУСК
# =====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not render_url:
        raise ValueError("RENDER_EXTERNAL_URL не найден")

    bot.remove_webhook()
    bot.set_webhook(url=f"{render_url}/{TOKEN}")

    app.run(host="0.0.0.0", port=port)
