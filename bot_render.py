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
# ЛОГИКА
# -------------------

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Бот учёта расходов работает 💰")

@bot.message_handler(commands=["new"])
def new_session(message):
    name = message.text.replace("/new", "").strip()

    if not name:
        bot.send_message(message.chat.id, "Напиши: /new Название встречи")
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO sessions (chat_id, name) VALUES (%s, %s)",
        (message.chat.id, name)
    )

    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, f"Создана встреча: {name}")

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
