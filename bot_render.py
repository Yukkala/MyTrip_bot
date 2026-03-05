# BOT TRAT — VERSION 7.0
"""
Функционал:
- Создание встреч с названием
- Несколько встреч одновременно, переключение между ними
- Добавление участников
- Добавление расходов: сумма → кто платил → категория → способ разделения
- Способы разделения: поровну на всех / поровну на выбранных
- Категории: Еда, Транспорт, Жильё, Развлечения, Покупки
- Список расходов с удалением
- Итоговый баланс с именами + статистика по категориям
- Всё через кнопки, без команд
- PostgreSQL + состояние в БД
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
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_HOST = os.environ.get("RENDER_EXTERNAL_URL")

CATEGORIES = [
    ("🍕", "Еда"),
    ("🚗", "Транспорт"),
    ("🏨", "Жильё"),
    ("🎉", "Развлечения"),
    ("🛒", "Покупки"),
]

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================================
# 3. DB CONNECTION
# ============================================================

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ============================================================
# 4. INIT DB
# ============================================================

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            name TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            payer_id INTEGER REFERENCES participants(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            split_type TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_shares (
            id SERIAL PRIMARY KEY,
            expense_id INTEGER REFERENCES expenses(id) ON DELETE CASCADE,
            participant_id INTEGER REFERENCES participants(id) ON DELETE CASCADE,
            share REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_event (
            chat_id BIGINT PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE SET NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_state (
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
        INSERT INTO user_state(chat_id, step, data) VALUES(%s, %s, %s)
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
# 6. ACTIVE EVENT HELPERS
# ============================================================

def get_active_event(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.title FROM active_event ae
        JOIN events e ON e.id = ae.event_id
        WHERE ae.chat_id=%s
    """, (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def set_active_event(chat_id, event_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO active_event(chat_id, event_id) VALUES(%s, %s)
        ON CONFLICT(chat_id) DO UPDATE SET event_id=EXCLUDED.event_id
    """, (chat_id, event_id))
    conn.commit()
    cur.close()
    conn.close()

# ============================================================
# 7. MENUS
# ============================================================

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📅 Мои встречи"), KeyboardButton("➕ Новая встреча"))
    markup.row(KeyboardButton("➕ Расход"), KeyboardButton("🧾 Расходы"))
    markup.row(KeyboardButton("👥 Участники"), KeyboardButton("📊 Итоги"))
    return markup

# ============================================================
# 8. /start
# ============================================================

@bot.message_handler(commands=['start'])
def cmd_start(msg):
    clear_state(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        "👋 Привет! Я помогу учитывать расходы на встречах и делить их между участниками.\n\n"
        "Начните с кнопки *➕ Новая встреча*.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ============================================================
# 9. НОВАЯ ВСТРЕЧА
# ============================================================

@bot.message_handler(func=lambda m: m.text == "➕ Новая встреча")
def new_event(msg):
    clear_state(msg.chat.id)
    set_state(msg.chat.id, "event_title")
    bot.send_message(msg.chat.id, "Введите название встречи:")

@bot.message_handler(func=lambda m: get_state(m.chat.id).get("step") == "event_title")
def save_event_title(msg):
    title = msg.text.strip()
    if not title:
        bot.send_message(msg.chat.id, "Название не может быть пустым:")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO events(chat_id, title) VALUES(%s, %s) RETURNING id",
        (msg.chat.id, title)
    )
    event_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()

    set_active_event(msg.chat.id, event_id)
    set_state(msg.chat.id, "add_participants", {"event_id": event_id})

    bot.send_message(
        msg.chat.id,
        f"✅ Встреча *{title}* создана!\n\nВведите участников через запятую.\nПример: Алиса, Боря, Витя",
        parse_mode="Markdown"
    )

# ============================================================
# 10. ДОБАВЛЕНИЕ УЧАСТНИКОВ
# ============================================================

@bot.message_handler(func=lambda m: get_state(m.chat.id).get("step") == "add_participants")
def save_participants(msg):
    names = [x.strip() for x in msg.text.split(",") if x.strip()]
    if not names:
        bot.send_message(msg.chat.id, "Введите хотя бы одно имя через запятую:")
        return

    state = get_state(msg.chat.id)
    event_id = state.get("event_id")
    if not event_id:
        bot.send_message(msg.chat.id, "Ошибка: встреча не найдена. Создайте новую.")
        clear_state(msg.chat.id)
        return

    conn = get_conn()
    cur = conn.cursor()
    for name in names:
        cur.execute(
            "INSERT INTO participants(event_id, name) VALUES(%s, %s)",
            (event_id, name)
        )
    conn.commit()
    cur.close()
    conn.close()

    clear_state(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        f"✅ Участники добавлены: {', '.join(names)}\n\nМожно добавлять расходы!",
        reply_markup=main_menu()
    )

# ============================================================
# 11. МОИ ВСТРЕЧИ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📅 Мои встречи")
def my_events(msg):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title FROM events WHERE chat_id=%s ORDER BY created_at DESC",
        (msg.chat.id,)
    )
    events = cur.fetchall()
    cur.close()
    conn.close()

    if not events:
        bot.send_message(
            msg.chat.id,
            "У вас нет встреч. Нажмите *➕ Новая встреча*.",
            parse_mode="Markdown"
        )
        return

    active = get_active_event(msg.chat.id)
    markup = InlineKeyboardMarkup()
    for ev in events:
        is_active = active and active["id"] == ev["id"]
        label = f"{'✅ ' if is_active else ''}{ev['title']}"
        markup.add(InlineKeyboardButton(label, callback_data=f"sev_{ev['id']}"))

    bot.send_message(msg.chat.id, "Выберите активную встречу:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("sev_"))
def switch_event(call):
    event_id = int(call.data.split("_")[1])
    set_active_event(call.message.chat.id, event_id)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT title FROM events WHERE id=%s", (event_id,))
    ev = cur.fetchone()
    cur.close()
    conn.close()

    bot.edit_message_text(
        f"✅ Активная встреча: *{ev['title']}*",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown"
    )

# ============================================================
# 12. УЧАСТНИКИ ТЕКУЩЕЙ ВСТРЕЧИ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "👥 Участники")
def show_participants(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала создайте или выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM participants WHERE event_id=%s ORDER BY id", (ev["id"],))
    people = cur.fetchall()
    cur.close()
    conn.close()

    if not people:
        bot.send_message(msg.chat.id, f"В встрече *{ev['title']}* нет участников.", parse_mode="Markdown")
        return

    names = "\n".join(f"• {p['name']}" for p in people)
    bot.send_message(msg.chat.id, f"👥 *{ev['title']}*\n\n{names}", parse_mode="Markdown")

# ============================================================
# 13. ДОБАВИТЬ РАСХОД — СУММА
# ============================================================

@bot.message_handler(func=lambda m: m.text == "➕ Расход")
def add_expense_start(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала создайте или выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM participants WHERE event_id=%s", (ev["id"],))
    people = cur.fetchall()
    cur.close()
    conn.close()

    if not people:
        bot.send_message(msg.chat.id, "Сначала добавьте участников через кнопку 👥 Участники.")
        return

    clear_state(msg.chat.id)
    set_state(msg.chat.id, "expense_amount", {"event_id": ev["id"]})
    bot.send_message(msg.chat.id, f"📅 *{ev['title']}*\n\nВведите сумму расхода:", parse_mode="Markdown")

# ============================================================
# 14. СУММА → КТО ПЛАТИЛ
# ============================================================

@bot.message_handler(func=lambda m: get_state(m.chat.id).get("step") == "expense_amount")
def expense_amount(msg):
    try:
        amount = float(msg.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(msg.chat.id, "Введите положительное число, например: 1500 или 350.50")
        return

    state = get_state(msg.chat.id)
    event_id = state["event_id"]
    set_state(msg.chat.id, "expense_payer", {"event_id": event_id, "amount": amount})

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM participants WHERE event_id=%s ORDER BY id", (event_id,))
    people = cur.fetchall()
    cur.close()
    conn.close()

    markup = InlineKeyboardMarkup()
    for p in people:
        markup.add(InlineKeyboardButton(p["name"], callback_data=f"ep_{p['id']}"))

    bot.send_message(msg.chat.id, f"Сумма: *{amount} руб.*\n\nКто оплатил?", parse_mode="Markdown", reply_markup=markup)

# ============================================================
# 15. КТО ПЛАТИЛ → КАТЕГОРИЯ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("ep_"))
def expense_payer(call):
    payer_id = int(call.data.split("_")[1])
    state = get_state(call.message.chat.id)

    set_state(call.message.chat.id, "expense_category", {
        "event_id": state["event_id"],
        "amount": state["amount"],
        "payer_id": payer_id
    })

    markup = InlineKeyboardMarkup()
    for emoji, name in CATEGORIES:
        markup.add(InlineKeyboardButton(f"{emoji} {name}", callback_data=f"ec_{name}"))

    bot.edit_message_text(
        "Выберите категорию расхода:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ============================================================
# 16. КАТЕГОРИЯ → СПОСОБ РАЗДЕЛЕНИЯ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("ec_"))
def expense_category(call):
    category = call.data[3:]
    state = get_state(call.message.chat.id)

    set_state(call.message.chat.id, "expense_split", {
        "event_id": state["event_id"],
        "amount": state["amount"],
        "payer_id": state["payer_id"],
        "category": category
    })

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👥 Поровну на всех", callback_data="split_all"))
    markup.add(InlineKeyboardButton("✅ Выбрать участников", callback_data="split_selected"))

    bot.edit_message_text(
        "Как разделить расход?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ============================================================
# 17. ПОРОВНУ НА ВСЕХ → СОХРАНИТЬ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "split_all")
def split_all(call):
    state = get_state(call.message.chat.id)
    event_id = state["event_id"]

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM participants WHERE event_id=%s", (event_id,))
    people = [row["id"] for row in cur.fetchall()]
    share = state["amount"] / len(people)

    cur.execute(
        "INSERT INTO expenses(event_id, amount, payer_id, category, split_type) VALUES(%s,%s,%s,%s,'all') RETURNING id",
        (event_id, state["amount"], state["payer_id"], state["category"])
    )
    expense_id = cur.fetchone()["id"]

    for pid in people:
        cur.execute(
            "INSERT INTO expense_shares(expense_id, participant_id, share) VALUES(%s,%s,%s)",
            (expense_id, pid, share)
        )

    conn.commit()
    cur.close()
    conn.close()
    clear_state(call.message.chat.id)

    cat_emoji = next((e for e, n in CATEGORIES if n == state["category"]), "")
    bot.edit_message_text(
        f"✅ Расход сохранён!\n\n"
        f"{cat_emoji} {state['category']} — {state['amount']} руб.\n"
        f"Разделён поровну на {len(people)} участников ({round(share, 2)} руб. каждый)",
        call.message.chat.id,
        call.message.message_id
    )

# ============================================================
# 18. ВЫБРАТЬ УЧАСТНИКОВ — ТОГЛЫ
# ============================================================

def build_split_markup(people, selected_ids):
    markup = InlineKeyboardMarkup()
    for p in people:
        check = "✅" if p["id"] in selected_ids else "☐"
        markup.add(InlineKeyboardButton(
            f"{check} {p['name']}",
            callback_data=f"st_{p['id']}"
        ))
    markup.add(InlineKeyboardButton("💾 Сохранить", callback_data="split_save"))
    return markup

@bot.callback_query_handler(func=lambda c: c.data == "split_selected")
def split_selected(call):
    state = get_state(call.message.chat.id)
    event_id = state["event_id"]

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM participants WHERE event_id=%s ORDER BY id", (event_id,))
    people = [{"id": p["id"], "name": p["name"]} for p in cur.fetchall()]
    cur.close()
    conn.close()

    set_state(call.message.chat.id, "expense_split_selected", {
        **state,
        "people": people,
        "selected": []
    })

    bot.edit_message_text(
        "Выберите участников расхода:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=build_split_markup(people, [])
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("st_"))
def split_toggle(call):
    pid = int(call.data.split("_")[1])
    state = get_state(call.message.chat.id)
    selected = state.get("selected", [])

    if pid in selected:
        selected.remove(pid)
    else:
        selected.append(pid)

    state["selected"] = selected
    set_state(call.message.chat.id, "expense_split_selected", state)

    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=build_split_markup(state["people"], selected)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "split_save")
def split_save(call):
    state = get_state(call.message.chat.id)
    selected = state.get("selected", [])

    if not selected:
        bot.answer_callback_query(call.id, "Выберите хотя бы одного участника!")
        return

    share = state["amount"] / len(selected)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses(event_id, amount, payer_id, category, split_type) VALUES(%s,%s,%s,%s,'selected') RETURNING id",
        (state["event_id"], state["amount"], state["payer_id"], state["category"])
    )
    expense_id = cur.fetchone()["id"]

    for pid in selected:
        cur.execute(
            "INSERT INTO expense_shares(expense_id, participant_id, share) VALUES(%s,%s,%s)",
            (expense_id, pid, share)
        )

    conn.commit()
    cur.close()
    conn.close()
    clear_state(call.message.chat.id)

    people_map = {p["id"]: p["name"] for p in state["people"]}
    names = ", ".join(people_map[pid] for pid in selected if pid in people_map)
    cat_emoji = next((e for e, n in CATEGORIES if n == state["category"]), "")

    bot.edit_message_text(
        f"✅ Расход сохранён!\n\n"
        f"{cat_emoji} {state['category']} — {state['amount']} руб.\n"
        f"Разделён между: {names}\n"
        f"({round(share, 2)} руб. каждый)",
        call.message.chat.id,
        call.message.message_id
    )

# ============================================================
# 19. СПИСОК РАСХОДОВ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🧾 Расходы")
def list_expenses(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала создайте или выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.amount, e.category, e.split_type, p.name AS payer
        FROM expenses e
        JOIN participants p ON p.id = e.payer_id
        WHERE e.event_id=%s
        ORDER BY e.id
    """, (ev["id"],))
    expenses = cur.fetchall()
    cur.close()
    conn.close()

    if not expenses:
        bot.send_message(msg.chat.id, f"В встрече *{ev['title']}* пока нет расходов.", parse_mode="Markdown")
        return

    cat_map = {n: e for e, n in CATEGORIES}
    markup = InlineKeyboardMarkup()
    lines = []

    for e in expenses:
        emoji = cat_map.get(e["category"], "")
        split_label = "все" if e["split_type"] == "all" else "выбранные"
        lines.append(f"{emoji} {e['category']} — {e['amount']} руб. (платил {e['payer']}, {split_label})")
        markup.add(InlineKeyboardButton(
            f"❌  {e['category']} {e['amount']} руб.",
            callback_data=f"dex_{e['id']}"
        ))

    text = f"🧾 *{ev['title']}*\n\n" + "\n".join(lines) + "\n\n_Нажмите на расход чтобы удалить_"
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dex_"))
def delete_expense(call):
    expense_id = int(call.data.split("_")[1])
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id=%s", (expense_id,))
    conn.commit()
    cur.close()
    conn.close()
    bot.edit_message_text("🗑 Расход удалён.", call.message.chat.id, call.message.message_id)

# ============================================================
# 20. ИТОГИ / БАЛАНС
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📊 Итоги")
def show_balance(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала создайте или выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
        return

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM participants WHERE event_id=%s", (ev["id"],))
    people = {row["id"]: row["name"] for row in cur.fetchall()}

    if not people:
        bot.send_message(msg.chat.id, "Нет участников.")
        conn.close()
        return

    cur.execute("SELECT id, amount, payer_id FROM expenses WHERE event_id=%s", (ev["id"],))
    expenses = cur.fetchall()

    if not expenses:
        bot.send_message(msg.chat.id, f"В встрече *{ev['title']}* нет расходов.", parse_mode="Markdown")
        conn.close()
        return

    paid = defaultdict(float)
    owed = defaultdict(float)

    for e in expenses:
        paid[e["payer_id"]] += e["amount"]

    cur.execute("""
        SELECT es.participant_id, es.share
        FROM expense_shares es
        JOIN expenses e ON e.id = es.expense_id
        WHERE e.event_id=%s
    """, (ev["id"],))
    for s in cur.fetchall():
        owed[s["participant_id"]] += s["share"]

    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM expenses WHERE event_id=%s
        GROUP BY category ORDER BY total DESC
    """, (ev["id"],))
    cats = cur.fetchall()

    cur.close()
    conn.close()

    balance = defaultdict(float)
    for pid in set(list(paid.keys()) + list(owed.keys())):
        balance[pid] = paid[pid] - owed[pid]

    creditors = sorted([[pid, bal] for pid, bal in balance.items() if bal > 0.01], key=lambda x: -x[1])
    debtors = sorted([[pid, -bal] for pid, bal in balance.items() if bal < -0.01], key=lambda x: -x[1])

    transfers = []
    while creditors and debtors:
        c = creditors[0]
        d = debtors[0]
        pay = min(c[1], d[1])
        transfers.append(
            f"💸 {people.get(d[0], '?')} → {people.get(c[0], '?')}: {round(pay, 2)} руб."
        )
        c[1] -= pay
        d[1] -= pay
        if c[1] < 0.01:
            creditors.pop(0)
        if d[1] < 0.01:
            debtors.pop(0)

    cat_map = {n: e for e, n in CATEGORIES}
    total = sum(e["amount"] for e in expenses)
    cat_lines = "\n".join(
        f"{cat_map.get(c['category'], '')} {c['category']}: {round(c['total'], 2)} руб."
        for c in cats
    )

    text = f"📊 *Итоги: {ev['title']}*\n\n"
    text += f"💰 Всего потрачено: *{round(total, 2)} руб.*\n\n"
    text += f"*По категориям:*\n{cat_lines}\n\n"

    if transfers:
        text += "*Кто кому платит:*\n" + "\n".join(transfers)
    else:
        text += "✅ Все расчёты закрыты, никто никому не должен!"

    bot.send_message(msg.chat.id, text, parse_mode="Markdown")

# ============================================================
# 21. WEBHOOK
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
# 22. START
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
