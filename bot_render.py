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
        name TEXT,
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
    ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS session_id INTEGER;
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

    cur.execute("""
    ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
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
    try:
        name = message.text
        chat_id = message.chat.id

        print("СОЗДАЮ ВСТРЕЧУ:", name)

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO sessions (chat_id, name, is_active) VALUES (%s, %s, %s)",
            (chat_id, name, True)
        )

        conn.commit()
        cur.close()
        conn.close()

        bot.send_message(
            chat_id,
            f"Встреча '{name}' создана 🎉",
            reply_markup=main_menu()
        )

    except Exception as e:
        print("ОШИБКА:", e)
        bot.send_message(message.chat.id, f"Ошибка: {e}")
        
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
# Кнопка "Участники"
# -------------------

@bot.message_handler(func=lambda m: m.text == "👥 Участники")
def participants_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Добавить участника")
    markup.add("📋 Список участников")
    markup.add("⬅ Назад")

    bot.send_message(message.chat.id, "Управление участниками:", reply_markup=markup)

# -------------------
# Добавить участника
# -------------------

@bot.message_handler(func=lambda m: m.text == "➕ Добавить участника")
def add_participant(message):
    msg = bot.send_message(message.chat.id, "Введите имя участника:")
    bot.register_next_step_handler(msg, save_participant)


def save_participant(message):
    name = message.text
    chat_id = message.chat.id

    session_id = user_state.get(chat_id, {}).get("session_id")

    if not session_id:
        bot.send_message(chat_id, "Сначала открой встречу.")
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO participants (session_id, name) VALUES (%s, %s)",
        (session_id, name)
    )

    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(chat_id, f"Участник {name} добавлен ✅")
    
# -------------------
# Список участников
# -------------------

@bot.message_handler(func=lambda m: m.text == "📋 Список участников")
def list_participants(message):
    chat_id = message.chat.id
    session_id = user_state.get(chat_id, {}).get("session_id")

    if not session_id:
        bot.send_message(chat_id, "Сначала открой встречу.")
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM participants WHERE session_id = %s",
        (session_id,)
    )

    participants = cur.fetchall()

    cur.close()
    conn.close()

    if not participants:
        bot.send_message(chat_id, "Участников пока нет.")
        return

    text = "Участники:\n\n"
    for p in participants:
        text += f"• {p[0]}\n"

    bot.send_message(chat_id, text)

# -------------------
# Кнопка "Добавить расход"
# -------------------

@bot.message_handler(func=lambda m: m.text == "💰 Добавить расход")
def add_expense_start(message):
    msg = bot.send_message(message.chat.id, "Введите сумму расхода:")
    bot.register_next_step_handler(msg, save_expense_amount)

# -------------------
# Сохраняем сумму
# -------------------

def save_expense_amount(message):
    chat_id = message.chat.id

    try:
        amount = float(message.text.replace(",", "."))
    except:
        bot.send_message(chat_id, "Введите корректную сумму числом.")
        return

    user_state.setdefault(chat_id, {})
    user_state[chat_id]["expense_amount"] = amount

    session_id = user_state.get(chat_id, {}).get("session_id")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name FROM participants WHERE session_id = %s",
        (session_id,)
    )
    participants = cur.fetchall()

    cur.close()
    conn.close()

    if not participants:
        bot.send_message(chat_id, "Сначала добавьте участников.")
        return

    markup = types.InlineKeyboardMarkup()

    for p in participants:
        markup.add(
            types.InlineKeyboardButton(
                p[1],
                callback_data=f"payer_{p[0]}"
            )
        )

    bot.send_message(chat_id, "Кто оплатил?", reply_markup=markup)

# -------------------
# Выбор кто оплатил
# -------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("payer_"))
def select_payer(call):
    try:
        chat_id = call.message.chat.id
        payer_id = int(call.data.split("_")[1])

        print("PAYER CLICKED:", payer_id)

        user_state.setdefault(chat_id, {})
        user_state[chat_id]["payer_id"] = payer_id

        session_id = user_state.get(chat_id, {}).get("session_id")

        print("SESSION_ID:", session_id)

        if not session_id:
            bot.send_message(chat_id, "Ошибка: встреча не выбрана.")
            return

        ensure_default_categories(session_id)

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT id, name FROM categories WHERE session_id = %s",
            (session_id,)
        )
        categories = cur.fetchall()

        print("CATEGORIES:", categories)

        cur.close()
        conn.close()

        if not categories:
            bot.send_message(chat_id, "Категории не найдены.")
            return

        markup = types.InlineKeyboardMarkup()

        for c in categories:
            markup.add(
                types.InlineKeyboardButton(
                    c[1],
                    callback_data=f"category_{c[0]}"
                )
            )

        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Выберите категорию:", reply_markup=markup)

    except Exception as e:
        print("ERROR IN select_payer:", e)
        bot.send_message(call.message.chat.id, f"Ошибка: {e}")

# -------------------
# Если категорий нет, создаём базовые
# -------------------

def ensure_default_categories(session_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM categories WHERE session_id = %s",
        (session_id,)
    )
    count = cur.fetchone()[0]

    if count == 0:
        defaults = ["Еда", "Транспорт", "Жильё", "Развлечения", "Другое"]
        for cat in defaults:
            cur.execute(
                "INSERT INTO categories (session_id, name) VALUES (%s, %s)",
                (session_id, cat)
            )

    conn.commit()
    cur.close()
    conn.close()
            
# -------------------
# Сохраняем выбранную категорию
# -------------------
    
@bot.callback_query_handler(func=lambda call: call.data.startswith("category_"))
def select_category(call):
    chat_id = call.message.chat.id
    category_id = int(call.data.split("_")[1])

    user_state.setdefault(chat_id, {})
    user_state[chat_id]["category_id"] = category_id

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👥 Разделить на всех", callback_data="split_all"))
    markup.add(types.InlineKeyboardButton("✏️ Разделить выборочно", callback_data="split_custom"))

    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "Как разделить расход?", reply_markup=markup)

# -------------------
# Если выбрали «Разделить на всех»
# -------------------

@bot.callback_query_handler(func=lambda call: call.data == "split_all")
def split_all(call):
    chat_id = call.message.chat.id
    data = user_state.get(chat_id, {})
    session_id = data.get("session_id")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM participants WHERE session_id = %s",
        (session_id,)
    )
    participants = cur.fetchall()

    cur.close()
    conn.close()

    selected_ids = [p[0] for p in participants]
    user_state[chat_id]["selected_participants"] = selected_ids

    bot.answer_callback_query(call.id)
    save_expense_to_db(chat_id)

# -------------------
# Если выбрали «Разделить выборочно»
# -------------------

@bot.callback_query_handler(func=lambda call: call.data == "split_custom")
def split_custom(call):
    chat_id = call.message.chat.id
    session_id = user_state[chat_id]["session_id"]

    user_state[chat_id]["selected_participants"] = []

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name FROM participants WHERE session_id = %s",
        (session_id,)
    )
    participants = cur.fetchall()

    cur.close()
    conn.close()

    markup = types.InlineKeyboardMarkup()

    for p in participants:
        markup.add(
            types.InlineKeyboardButton(
                f"⬜ {p[1]}",
                callback_data=f"toggle_{p[0]}"
            )
        )

    markup.add(types.InlineKeyboardButton("✅ Готово", callback_data="finish_expense"))

    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "Выберите участников:", reply_markup=markup)

# -------------------
# Вынесем сохранение в отдельную функцию
# -------------------

def save_expense_to_db(chat_id):
    data = user_state.get(chat_id, {})
    selected = data.get("selected_participants", [])

    if not selected:
        bot.send_message(chat_id, "Нет выбранных участников.")
        return

    session_id = data["session_id"]
    amount = data["expense_amount"]
    payer_id = data["payer_id"]
    category_id = data["category_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (session_id, payer, amount, category_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (session_id, payer_id, amount, category_id)
    )

    expense_id = cur.fetchone()[0]

    for participant_id in selected:
        cur.execute(
            "INSERT INTO expense_shares (expense_id, participant_id) VALUES (%s, %s)",
            (expense_id, participant_id)
        )

    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(chat_id, "Расход сохранён 💰✅")

@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_"))
def toggle_participant(call):
    chat_id = call.message.chat.id
    participant_id = int(call.data.split("_")[1])

    selected = user_state[chat_id].get("selected_participants", [])

    if participant_id in selected:
        selected.remove(participant_id)
    else:
        selected.append(participant_id)

    user_state[chat_id]["selected_participants"] = selected

    # Перерисовываем кнопки
    session_id = user_state[chat_id]["session_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name FROM participants WHERE session_id = %s",
        (session_id,)
    )
    participants = cur.fetchall()

    cur.close()
    conn.close()

    markup = types.InlineKeyboardMarkup()

    for p in participants:
        if p[0] in selected:
            text = f"✅ {p[1]}"
        else:
            text = f"⬜ {p[1]}"

        markup.add(
            types.InlineKeyboardButton(
                text,
                callback_data=f"toggle_{p[0]}"
            )
        )

    markup.add(types.InlineKeyboardButton("✅ Готово", callback_data="finish_expense"))

    bot.edit_message_reply_markup(
        chat_id=chat_id,
        message_id=call.message.message_id,
        reply_markup=markup
    )

    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "finish_expense")
def finish_expense(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    save_expense_to_db(chat_id)

    selected = data.get("selected_participants", [])

    if not selected:
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Выберите хотя бы одного участника.")
        return

    session_id = data["session_id"]
    amount = data["expense_amount"]
    payer_id = data["payer_id"]
    category_id = data["category_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (session_id, payer, amount, category_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (session_id, payer_id, amount, category_id)
    )

    expense_id = cur.fetchone()[0]

    for participant_id in selected:
        cur.execute(
            "INSERT INTO expense_shares (expense_id, participant_id) VALUES (%s, %s)",
            (expense_id, participant_id)
        )

    conn.commit()
    cur.close()
    conn.close()

    bot.answer_callback_query(call.id)
    bot.send_message(chat_id, "Расход сохранён 💰✅")

   
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
