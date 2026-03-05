# BOT TRAT — VERSION 6.0 (RENDER + POSTGRESQL)

"""
Исправления по сравнению с v5:
- PostgreSQL вместо SQLite (данные не теряются при рестарте Render)
- Состояние пользователя хранится в БД (таблица user_state)
- Webhook устанавливается автоматически при старте
- Участники и расходы привязаны к сессии чата (session_id по chat_id)
- Баланс показывает имена, а не ID
- Исправлен захардкоженный session_id=1
"""

# ============================================================
# 1. IMPORTS
# ============================================================

import os
import json
from collections import defaultdict
from flask import Flask, request
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
import psycopg2
from psycopg2.extras import RealDictCursor

# ============================================================
# 2. CONFIGURATION
# ============================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")          # задаётся в Render автоматически
WEBHOOK_HOST = os.environ.get("RENDER_EXTERNAL_URL")   # задаётся в Render автоматически

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================================
# 3. DATABASE CONNECTION
# ============================================================

def get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# ============================================================
# 4. DATABASE TABLES
# ============================================================

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
        id SERIAL PRIMARY KEY,
        chat_id BIGINT UNIQUE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS participants(
        id SERIAL PRIMARY KEY,
        session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
        name TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses(
        id SERIAL PRIMARY KEY,
        session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
        amount REAL,
        payer_id INTEGER REFERENCES participants(id) ON DELETE CASCADE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expense_shares(
        id SERIAL PRIMARY KEY,
        expense_id INTEGER REFERENCES expenses(id) ON DELETE CASCADE,
        participant_id INTEGER REFERENCES participants(id) ON DELETE CASCADE,
        share REAL
    )
    """)

    # Состояние пользователя — хранится в БД, переживает рестарт
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_state(
        chat_id BIGINT PRIMARY KEY,
        step TEXT,
        data JSONB
    )
    """)

    conn.commit()
    cur.close()
    conn.close()

# ============================================================
# 5. STATE HELPERS
# ============================================================

def get_state(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT step, data FROM user_state WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"step": row["step"], **(row["data"] or {})}
    return {}

def set_state(chat_id, step, data=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_state(chat_id, step, data)
        VALUES(%s, %s, %s)
        ON CONFLICT(chat_id) DO UPDATE SET step=EXCLUDED.step, data=EXCLUDED.data
    """, (chat_id, step, json.dumps(data or {})))
    conn.commit()
    cur.close()
    conn.close()

def clear_state(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_state WHERE chat_id=%s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()

# ============================================================
# 6. SESSION HELPERS
# ============================================================

def get_or_create_session(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM sessions WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    if row:
        session_id = row["id"]
    else:
        cur.execute("INSERT INTO sessions(chat_id) VALUES(%s) RETURNING id", (chat_id,))
        session_id = cur.fetchone()["id"]
        conn.commit()
    cur.close()
    conn.close()
    return session_id

# ============================================================
# 7. MAIN MENU
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
    markup.add(
        KeyboardButton("🔄 Новая сессия")
    )
    return markup

# ============================================================
# 8. START
# ============================================================

@bot.message_handler(commands=['start'])
def start(msg):
    get_or_create_session(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        "👋 Бот учёта расходов готов.\n\nНачните с /new_session чтобы добавить участников.",
        reply_markup=main_menu()
    )

# ============================================================
# 9. NEW SESSION
# ============================================================

@bot.message_handler(commands=['new_session'])
@bot.message_handler(func=lambda m: m.text == "🔄 Новая сессия")
def new_session(msg):
    # Удаляем старую сессию и создаём новую
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sessions WHERE chat_id=%s", (msg.chat.id,))
    cur.execute("INSERT INTO sessions(chat_id) VALUES(%s) RETURNING id", (msg.chat.id,))
    conn.commit()
    cur.close()
    conn.close()

    set_state(msg.chat.id, "participants")
    bot.send_message(msg.chat.id, "Введите участников через запятую.\nПример: Алиса, Боря, Витя")

# ============================================================
# 10. ADD PARTICIPANTS
# ============================================================

@bot.message_handler(func=lambda m: get_state(m.chat.id).get("step") == "participants")
def add_participants(msg):
    names = [x.strip() for x in msg.text.split(",") if x.strip()]
    if not names:
        bot.send_message(msg.chat.id, "Введите хотя бы одно имя")
        return

    session_id = get_or_create_session(msg.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    for name in names:
        cur.execute(
            "INSERT INTO participants(session_id, name) VALUES(%s, %s)",
            (session_id, name)
        )
    conn.commit()
    cur.close()
    conn.close()

    clear_state(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        f"✅ Участники добавлены: {', '.join(names)}",
        reply_markup=main_menu()
    )

# ============================================================
# 11. SHOW PARTICIPANTS
# ============================================================

@bot.message_handler(func=lambda m: m.text == "👥 Участники")
def show_participants(msg):
    session_id = get_or_create_session(msg.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM participants WHERE session_id=%s", (session_id,))
    people = cur.fetchall()
    cur.close()
    conn.close()

    if not people:
        bot.send_message(msg.chat.id, "Участников нет. Используйте /new_session")
        return

    names = "\n".join(f"• {p['name']}" for p in people)
    bot.send_message(msg.chat.id, f"👥 Участники:\n{names}")

# ============================================================
# 12. ADD EXPENSE — ENTER AMOUNT
# ============================================================

@bot.message_handler(func=lambda m: m.text == "➕ Расход")
def add_expense(msg):
    session_id = get_or_create_session(msg.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM participants WHERE session_id=%s", (session_id,))
    people = cur.fetchall()
    cur.close()
    conn.close()

    if not people:
        bot.send_message(msg.chat.id, "Сначала добавьте участников через /new_session")
        return

    set_state(msg.chat.id, "amount")
    bot.send_message(msg.chat.id, "Введите сумму расхода:")

# ============================================================
# 13. ENTER AMOUNT → CHOOSE PAYER
# ============================================================

@bot.message_handler(func=lambda m: get_state(m.chat.id).get("step") == "amount")
def enter_amount(msg):
    try:
        amount = float(msg.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(msg.chat.id, "Введите положительное число")
        return

    session_id = get_or_create_session(msg.chat.id)
    set_state(msg.chat.id, "payer", {"amount": amount, "session_id": session_id})

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM participants WHERE session_id=%s", (session_id,))
    people = cur.fetchall()
    cur.close()
    conn.close()

    markup = InlineKeyboardMarkup()
    for p in people:
        markup.add(InlineKeyboardButton(p["name"], callback_data=f"payer_{p['id']}"))

    bot.send_message(msg.chat.id, "Кто оплатил?", reply_markup=markup)

# ============================================================
# 14. CHOOSE PAYER → CHOOSE SHARES
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("payer_"))
def choose_payer(call):
    payer_id = int(call.data.split("_")[1])
    state = get_state(call.message.chat.id)

    set_state(call.message.chat.id, "shares", {
        "amount": state["amount"],
        "session_id": state["session_id"],
        "payer": payer_id,
        "shares": []
    })

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM participants WHERE session_id=%s", (state["session_id"],))
    people = cur.fetchall()
    cur.close()
    conn.close()

    markup = InlineKeyboardMarkup()
    for p in people:
        markup.add(InlineKeyboardButton(p["name"], callback_data=f"share_{p['id']}"))
    markup.add(InlineKeyboardButton("✅ Готово", callback_data="shares_done"))

    bot.edit_message_text(
        "Выберите участников расхода (можно несколько):",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ============================================================
# 15. TOGGLE SHARE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("share_"))
def toggle_share(call):
    pid = int(call.data.split("_")[1])
    state = get_state(call.message.chat.id)
    shares = state.get("shares", [])

    if pid in shares:
        shares.remove(pid)
        action = "убран"
    else:
        shares.append(pid)
        action = "добавлен"

    set_state(call.message.chat.id, "shares", {
        "amount": state["amount"],
        "session_id": state["session_id"],
        "payer": state["payer"],
        "shares": shares
    })

    bot.answer_callback_query(call.id, f"Участник {action} ({'выбрано: ' + str(len(shares))})")

# ============================================================
# 16. SAVE EXPENSE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "shares_done")
def save_expense(call):
    state = get_state(call.message.chat.id)

    if not state:
        bot.answer_callback_query(call.id, "Сессия устарела, начните заново")
        return

    shares = state.get("shares", [])
    if not shares:
        bot.answer_callback_query(call.id, "Выберите хотя бы одного участника")
        return

    amount = state["amount"]
    payer = state["payer"]
    session_id = state["session_id"]
    share_amount = amount / len(shares)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses(session_id, amount, payer_id) VALUES(%s, %s, %s) RETURNING id",
        (session_id, amount, payer)
    )
    expense_id = cur.fetchone()["id"]

    for p in shares:
        cur.execute(
            "INSERT INTO expense_shares(expense_id, participant_id, share) VALUES(%s, %s, %s)",
            (expense_id, p, share_amount)
        )

    conn.commit()
    cur.close()
    conn.close()

    clear_state(call.message.chat.id)
    bot.edit_message_text(
        f"✅ Расход {amount} сохранён между {len(shares)} участниками.",
        call.message.chat.id,
        call.message.message_id
    )

# ============================================================
# 17. LIST EXPENSES
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🧾 Расходы")
def list_expenses(msg):
    session_id = get_or_create_session(msg.chat.id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.amount, p.name AS payer_name
        FROM expenses e
        JOIN participants p ON p.id = e.payer_id
        WHERE e.session_id=%s
        ORDER BY e.id
    """, (session_id,))
    expenses = cur.fetchall()
    cur.close()
    conn.close()

    if not expenses:
        bot.send_message(msg.chat.id, "Расходов нет")
        return

    markup = InlineKeyboardMarkup()
    for e in expenses:
        markup.add(InlineKeyboardButton(
            f"❌ {e['payer_name']}: {e['amount']}",
            callback_data=f"delete_{e['id']}"
        ))

    bot.send_message(msg.chat.id, "🧾 Расходы (нажмите чтобы удалить):", reply_markup=markup)

# ============================================================
# 18. DELETE EXPENSE
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("delete_"))
def delete_expense(call):
    expense_id = int(call.data.split("_")[1])

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id=%s", (expense_id,))
    conn.commit()
    cur.close()
    conn.close()

    bot.edit_message_text("🗑 Расход удалён", call.message.chat.id, call.message.message_id)

# ============================================================
# 19. SMART BALANCE — С ИМЕНАМИ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📊 Баланс")
def balance(msg):
    session_id = get_or_create_session(msg.chat.id)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM participants WHERE session_id=%s", (session_id,))
    people = {row["id"]: row["name"] for row in cur.fetchall()}

    if not people:
        bot.send_message(msg.chat.id, "Участников нет. Используйте /new_session")
        conn.close()
        return

    balances = defaultdict(float)

    cur.execute("SELECT payer_id, amount FROM expenses WHERE session_id=%s", (session_id,))
    for e in cur.fetchall():
        balances[e["payer_id"]] += e["amount"]

    cur.execute("""
        SELECT es.participant_id, es.share
        FROM expense_shares es
        JOIN expenses e ON e.id = es.expense_id
        WHERE e.session_id=%s
    """, (session_id,))
    for s in cur.fetchall():
        balances[s["participant_id"]] -= s["share"]

    cur.close()
    conn.close()

    creditors = [[pid, bal] for pid, bal in balances.items() if bal > 0.01]
    debtors = [[pid, -bal] for pid, bal in balances.items() if bal < -0.01]

    result = []

    while creditors and debtors:
        c = creditors[0]
        d = debtors[0]
        pay = min(c[1], d[1])
        debtor_name = people.get(d[0], f"#{d[0]}")
        creditor_name = people.get(c[0], f"#{c[0]}")
        result.append(f"💸 {debtor_name} → {creditor_name}: {round(pay, 2)}")
        c[1] -= pay
        d[1] -= pay
        if c[1] < 0.01:
            creditors.pop(0)
        if d[1] < 0.01:
            debtors.pop(0)

    if not result:
        bot.send_message(msg.chat.id, "✅ Все расчёты закрыты, никто никому не должен")
        return

    bot.send_message(msg.chat.id, "📊 Кто кому платит:\n\n" + "\n".join(result))

# ============================================================
# 20. WEBHOOK
# ============================================================

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

@app.route("/")
def index():
    return "bot running"

# ============================================================
# 21. START — WEBHOOK SETUP
# ============================================================

if __name__ == "__main__":
    init_db()

    if WEBHOOK_HOST:
        webhook_url = f"{WEBHOOK_HOST}/{TOKEN}"
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        print(f"Webhook set: {webhook_url}")
    else:
        print("RENDER_EXTERNAL_URL not set — webhook not configured")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
