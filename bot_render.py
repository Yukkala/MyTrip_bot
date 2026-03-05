# BOT TRAT — VERSION 5.0 (STABLE RENDER EDITION)

"""
This file contains a simplified and stable architecture for the expense bot.
All blocks are clearly marked so future edits are easy.
Compatible with Render deployment.
"""

# ============================================================
# 1. IMPORTS
# ============================================================

import os
import sqlite3
from collections import defaultdict
from flask import Flask, request
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ============================================================
# 2. CONFIGURATION
# ============================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE = "bot.db"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================================
# 3. DATABASE CONNECTION
# ============================================================

def get_conn():
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================
# 4. DATABASE TABLES
# ============================================================

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS participants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        name TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        amount REAL,
        payer_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expense_shares(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_id INTEGER,
        participant_id INTEGER,
        share REAL
    )
    """)

    conn.commit()
    conn.close()

# ============================================================
# 5. USER STATE (TEMP MEMORY)
# ============================================================

user_state = {}

# ============================================================
# 6. MAIN MENU
# ============================================================

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        KeyboardButton("➕ Расход"),
        KeyboardButton("📊 Баланс")
    )
    markup.add(
        KeyboardButton("🧾 Расходы"),
        KeyboardButton("👥 Участники")
    )
    return markup

# ============================================================
# 7. START
# ============================================================

@bot.message_handler(commands=['start'])
def start(msg):
    bot.send_message(msg.chat.id, "Бот учёта расходов готов.", reply_markup=main_menu())

# ============================================================
# 8. CREATE SESSION
# ============================================================

@bot.message_handler(commands=['new_session'])
def new_session(msg):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("INSERT INTO sessions(chat_id) VALUES(?)", (msg.chat.id,))
    session_id = cur.lastrowid

    conn.commit()
    conn.close()

    user_state[msg.chat.id] = {
        "session_id": session_id,
        "step": "participants"
    }

    bot.send_message(msg.chat.id, "Введите участников через запятую")

# ============================================================
# 9. ADD PARTICIPANTS
# ============================================================

@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "participants")
def add_participants(msg):

    names = [x.strip() for x in msg.text.split(",")]
    session_id = user_state[msg.chat.id]["session_id"]

    conn = get_conn()
    cur = conn.cursor()

    for name in names:
        cur.execute(
            "INSERT INTO participants(session_id,name) VALUES(?,?)",
            (session_id, name)
        )

    conn.commit()
    conn.close()

    user_state[msg.chat.id]["step"] = None

    bot.send_message(msg.chat.id, "Участники добавлены", reply_markup=main_menu())

# ============================================================
# 10. ADD EXPENSE
# ============================================================

@bot.message_handler(func=lambda m: m.text == "➕ Расход")
def add_expense(msg):

    user_state[msg.chat.id] = {
        "step": "amount"
    }

    bot.send_message(msg.chat.id, "Введите сумму расхода")

# ============================================================
# 11. ENTER AMOUNT
# ============================================================

@bot.message_handler(func=lambda m: user_state.get(m.chat.id, {}).get("step") == "amount")
def enter_amount(msg):

    try:
        amount = float(msg.text)
    except:
        bot.send_message(msg.chat.id, "Введите число")
        return

    user_state[msg.chat.id]["amount"] = amount

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id,name FROM participants")
    people = cur.fetchall()

    conn.close()

    markup = InlineKeyboardMarkup()

    for p in people:
        markup.add(
            InlineKeyboardButton(
                p['name'],
                callback_data=f"payer_{p['id']}"
            )
        )

    user_state[msg.chat.id]["step"] = "payer"

    bot.send_message(msg.chat.id, "Кто оплатил?", reply_markup=markup)

# ============================================================
# 12. CHOOSE PAYER
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("payer_"))
def choose_payer(call):

    payer_id = int(call.data.split("_")[1])

    user_state[call.message.chat.id]["payer"] = payer_id

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id,name FROM participants")
    people = cur.fetchall()

    conn.close()

    markup = InlineKeyboardMarkup()

    for p in people:
        markup.add(
            InlineKeyboardButton(
                p['name'],
                callback_data=f"share_{p['id']}"
            )
        )

    markup.add(InlineKeyboardButton("Готово", callback_data="shares_done"))

    user_state[call.message.chat.id]["shares"] = []

    bot.edit_message_text(
        "Выберите участников расхода",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ============================================================
# 13. TOGGLE SHARE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("share_"))
def toggle_share(call):

    pid = int(call.data.split("_")[1])

    shares = user_state[call.message.chat.id]["shares"]

    if pid in shares:
        shares.remove(pid)
    else:
        shares.append(pid)

    bot.answer_callback_query(call.id, "обновлено")

# ============================================================
# 14. SAVE EXPENSE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "shares_done")
def save_expense(call):

    data = user_state.get(call.message.chat.id)

    if not data:
        return

    amount = data["amount"]
    payer = data["payer"]
    shares = data["shares"]

    if not shares:
        bot.answer_callback_query(call.id, "Выберите участников")
        return

    share_amount = amount / len(shares)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses(session_id,amount,payer_id) VALUES(1,?,?)",
        (amount, payer)
    )

    expense_id = cur.lastrowid

    for p in shares:
        cur.execute(
            "INSERT INTO expense_shares(expense_id,participant_id,share) VALUES(?,?,?)",
            (expense_id, p, share_amount)
        )

    conn.commit()
    conn.close()

    user_state[call.message.chat.id] = {}

    bot.edit_message_text("Расход сохранён", call.message.chat.id, call.message.message_id)

# ============================================================
# 15. LIST EXPENSES
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🧾 Расходы")
def list_expenses(msg):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id,amount FROM expenses")
    expenses = cur.fetchall()

    conn.close()

    if not expenses:
        bot.send_message(msg.chat.id, "Нет расходов")
        return

    markup = InlineKeyboardMarkup()

    for e in expenses:
        markup.add(
            InlineKeyboardButton(
                f"❌ удалить {e['amount']}",
                callback_data=f"delete_{e['id']}"
            )
        )

    bot.send_message(msg.chat.id, "Расходы:", reply_markup=markup)

# ============================================================
# 16. DELETE EXPENSE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("delete_"))
def delete_expense(call):

    expense_id = int(call.data.split("_")[1])

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM expense_shares WHERE expense_id=?", (expense_id,))
    cur.execute("DELETE FROM expenses WHERE id=?", (expense_id,))

    conn.commit()
    conn.close()

    bot.edit_message_text("Расход удалён", call.message.chat.id, call.message.message_id)

# ============================================================
# 17. SMART BALANCE
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📊 Баланс")
def balance(msg):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id,name FROM participants")
    people = cur.fetchall()

    balances = defaultdict(float)

    cur.execute("SELECT * FROM expenses")

    for e in cur.fetchall():
        balances[e['payer_id']] += e['amount']

    cur.execute("SELECT * FROM expense_shares")

    for s in cur.fetchall():
        balances[s['participant_id']] -= s['share']

    conn.close()

    creditors = []
    debtors = []

    for pid, bal in balances.items():
        if bal > 0:
            creditors.append([pid, bal])
        elif bal < 0:
            debtors.append([pid, -bal])

    result = []

    while creditors and debtors:
        c = creditors[0]
        d = debtors[0]

        pay = min(c[1], d[1])

        result.append(f"{d[0]} → {c[0]} : {round(pay,2)}")

        c[1] -= pay
        d[1] -= pay

        if c[1] == 0:
            creditors.pop(0)
        if d[1] == 0:
            debtors.pop(0)

    if not result:
        bot.send_message(msg.chat.id, "Все расчёты закрыты")
        return

    bot.send_message(msg.chat.id, "\n".join(result))

# ============================================================
# 18. WEBHOOK FOR RENDER
# ============================================================

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():

    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)

    bot.process_new_updates([update])

    return "!", 200

# ============================================================
# 19. SERVER START
# ============================================================

@app.route("/")
def index():
    return "bot running"

# ============================================================
# 20. RUN
# ============================================================

if __name__ == "__main__":

    init_db()

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
