# BOT TRAT — VERSION 8.1 DEBUG
"""
Версия с подробным логированием для диагностики.
Смотрите логи в Render Dashboard → ваш сервис → Logs.
"""

import os
import json
import logging
import threading
import time
import requests as http_requests
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
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
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
# DB
# ============================================================

def get_conn():
    # sslmode=require обязателен для Render PostgreSQL
    # connect_timeout предотвращает бесконечное зависание
    url = DATABASE_URL
    if "sslmode" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "sslmode=require"
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)
    return conn

def init_db():
    log.info("init_db: start")
    conn = get_conn()
    cur = conn.cursor()

    # events — основная таблица, не трогаем если уже есть
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # participants — пересоздаём если нет event_id (старая схема)
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='participants' AND column_name='event_id'
    """)
    has_event_id = cur.fetchone()
    if not has_event_id:
        log.info("init_db: participants missing event_id — recreating")
        cur.execute("DROP TABLE IF EXISTS expense_shares CASCADE")
        cur.execute("DROP TABLE IF EXISTS expenses CASCADE")
        cur.execute("DROP TABLE IF EXISTS participants CASCADE")

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
    log.info("init_db: done")

# ============================================================
# STATE
# ============================================================

def get_state(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT step, data FROM user_state WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        result = {"step": row["step"], **(row["data"] or {})}
        log.info(f"get_state({chat_id}): {result}")
        return result
    log.info(f"get_state({chat_id}): empty")
    return {}

def set_state(chat_id, step, data=None):
    log.info(f"set_state({chat_id}): step={step} data={data}")
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
    log.info(f"clear_state({chat_id})")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_state WHERE chat_id=%s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()

# ============================================================
# ACTIVE EVENT
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
    log.info(f"get_active_event({chat_id}): {dict(row) if row else None}")
    return row

def set_active_event(chat_id, event_id):
    log.info(f"set_active_event({chat_id}): event_id={event_id}")
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
# MENU
# ============================================================

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("📅 Мои встречи"), KeyboardButton("➕ Новая встреча"))
    markup.row(KeyboardButton("➕ Расход"),       KeyboardButton("🧾 Расходы"))
    markup.row(KeyboardButton("👥 Участники"),    KeyboardButton("📊 Итоги"))
    return markup

# ============================================================
# /start
# ============================================================

@bot.message_handler(commands=['start'])
def cmd_start(msg):
    log.info(f"cmd_start: chat_id={msg.chat.id}")
    clear_state(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        "👋 Привет! Начните с кнопки *➕ Новая встреча*.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ============================================================
# ЕДИНЫЙ ДИСПЕТЧЕР
# ============================================================

@bot.message_handler(func=lambda m: True)
def dispatch(msg):
    chat_id = msg.chat.id
    text = msg.text or ""
    log.info(f"dispatch: chat_id={chat_id} text={repr(text)}")

    # Кнопки меню — приоритет
    if text == "➕ Новая встреча":
        return handle_new_event(msg)
    if text == "📅 Мои встречи":
        return handle_my_events(msg)
    if text == "👥 Участники":
        return handle_show_participants(msg)
    if text == "➕ Расход":
        return handle_add_expense_start(msg)
    if text == "🧾 Расходы":
        return handle_list_expenses(msg)
    if text == "📊 Итоги":
        return handle_balance(msg)

    # Один запрос к БД
    state = get_state(chat_id)
    step = state.get("step")
    log.info(f"dispatch: step={step}")

    if step == "event_title":
        return handle_event_title(msg, state)
    if step == "add_participants":
        return handle_save_participants(msg, state)
    if step == "expense_amount":
        return handle_expense_amount(msg, state)

    bot.send_message(chat_id, "Используйте кнопки меню.", reply_markup=main_menu())

# ============================================================
# НОВАЯ ВСТРЕЧА
# ============================================================

def handle_new_event(msg):
    log.info(f"handle_new_event: chat_id={msg.chat.id}")
    clear_state(msg.chat.id)
    set_state(msg.chat.id, "event_title")
    bot.send_message(msg.chat.id, "Введите название встречи:")

def handle_event_title(msg, state):
    title = msg.text.strip()
    log.info(f"handle_event_title: chat_id={msg.chat.id} title={repr(title)}")
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
    log.info(f"handle_event_title: created event_id={event_id}")

    set_active_event(msg.chat.id, event_id)
    set_state(msg.chat.id, "add_participants", {"event_id": event_id})

    bot.send_message(
        msg.chat.id,
        f"✅ Встреча *{title}* создана!\n\n"
        f"Введите участников через запятую.\n"
        f"Пример: Алиса, Боря, Витя",
        parse_mode="Markdown"
    )

# ============================================================
# УЧАСТНИКИ
# ============================================================

def handle_save_participants(msg, state):
    log.info(f"handle_save_participants: chat_id={msg.chat.id} text={repr(msg.text)} state={state}")
    names = [x.strip() for x in msg.text.split(",") if x.strip()]
    if not names:
        bot.send_message(msg.chat.id, "Введите хотя бы одно имя через запятую:")
        return

    event_id = state.get("event_id")
    log.info(f"handle_save_participants: event_id={event_id} names={names}")

    if not event_id:
        log.error(f"handle_save_participants: event_id is None! state={state}")
        bot.send_message(msg.chat.id, "Ошибка: встреча не найдена. Создайте новую.")
        clear_state(msg.chat.id)
        return

    try:
        log.info("handle_save_participants: opening connection")
        conn = get_conn()
        log.info("handle_save_participants: connection opened")
        cur = conn.cursor()
        for name in names:
            cur.execute(
                "INSERT INTO participants(event_id, name) VALUES(%s, %s)",
                (event_id, name)
            )
            log.info(f"handle_save_participants: inserted {name}")
        conn.commit()
        log.info("handle_save_participants: committed")
        cur.close()
        conn.close()
        log.info("handle_save_participants: connection closed")
    except Exception as e:
        log.error(f"handle_save_participants: DB ERROR: {e}")
        bot.send_message(msg.chat.id, f"Ошибка базы данных: {e}")
        return

    clear_state(msg.chat.id)
    bot.send_message(
        msg.chat.id,
        f"✅ Участники добавлены: *{', '.join(names)}*\n\nМожно добавлять расходы!",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

def handle_show_participants(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
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
# МОИ ВСТРЕЧИ
# ============================================================

def handle_my_events(msg):
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
        bot.send_message(msg.chat.id, "Нет встреч. Нажмите *➕ Новая встреча*.", parse_mode="Markdown")
        return
    active = get_active_event(msg.chat.id)
    markup = InlineKeyboardMarkup()
    for ev in events:
        label = f"{'✅ ' if active and active['id'] == ev['id'] else ''}{ev['title']}"
        markup.add(InlineKeyboardButton(label, callback_data=f"sev_{ev['id']}"))
    bot.send_message(msg.chat.id, "Выберите активную встречу:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("sev_"))
def cb_switch_event(call):
    event_id = int(call.data.split("_")[1])
    log.info(f"cb_switch_event: chat_id={call.message.chat.id} event_id={event_id}")
    set_active_event(call.message.chat.id, event_id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT title FROM events WHERE id=%s", (event_id,))
    ev = cur.fetchone()
    cur.close()
    conn.close()
    bot.edit_message_text(
        f"✅ Активная встреча: *{ev['title']}*",
        call.message.chat.id, call.message.message_id,
        parse_mode="Markdown"
    )

# ============================================================
# РАСХОД
# ============================================================

def handle_add_expense_start(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала выберите встречу через *📅 Мои встречи*.", parse_mode="Markdown")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM participants WHERE event_id=%s", (ev["id"],))
    people = cur.fetchall()
    cur.close()
    conn.close()
    if not people:
        bot.send_message(msg.chat.id, "Сначала добавьте участников через 👥 Участники.")
        return
    clear_state(msg.chat.id)
    set_state(msg.chat.id, "expense_amount", {"event_id": ev["id"]})
    bot.send_message(msg.chat.id, f"📅 *{ev['title']}*\n\nВведите сумму расхода:", parse_mode="Markdown")

def handle_expense_amount(msg, state):
    log.info(f"handle_expense_amount: chat_id={msg.chat.id} text={repr(msg.text)}")
    try:
        amount = float(msg.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(msg.chat.id, "Введите положительное число, например: 1500 или 350.50")
        return
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

@bot.callback_query_handler(func=lambda c: c.data.startswith("ep_"))
def cb_expense_payer(call):
    payer_id = int(call.data.split("_")[1])
    log.info(f"cb_expense_payer: chat_id={call.message.chat.id} payer_id={payer_id}")
    state = get_state(call.message.chat.id)
    set_state(call.message.chat.id, "expense_category", {
        "event_id": state["event_id"],
        "amount": state["amount"],
        "payer_id": payer_id
    })
    markup = InlineKeyboardMarkup()
    for emoji, name in CATEGORIES:
        markup.add(InlineKeyboardButton(f"{emoji} {name}", callback_data=f"ec_{name}"))
    bot.edit_message_text("Выберите категорию:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("ec_"))
def cb_expense_category(call):
    category = call.data[3:]
    log.info(f"cb_expense_category: chat_id={call.message.chat.id} category={category}")
    state = get_state(call.message.chat.id)
    set_state(call.message.chat.id, "expense_split", {
        "event_id": state["event_id"],
        "amount": state["amount"],
        "payer_id": state["payer_id"],
        "category": category
    })
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👥 Поровну на всех",    callback_data="split_all"))
    markup.add(InlineKeyboardButton("✅ Выбрать участников", callback_data="split_pick"))
    bot.edit_message_text("Как разделить расход?", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data == "split_all")
def cb_split_all(call):
    log.info(f"cb_split_all: chat_id={call.message.chat.id}")
    state = get_state(call.message.chat.id)
    event_id = state["event_id"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM participants WHERE event_id=%s", (event_id,))
    people = [row["id"] for row in cur.fetchall()]
    share = state["amount"] / len(people)
    cur.execute(
        "INSERT INTO expenses(event_id,amount,payer_id,category,split_type) VALUES(%s,%s,%s,%s,'all') RETURNING id",
        (event_id, state["amount"], state["payer_id"], state["category"])
    )
    expense_id = cur.fetchone()["id"]
    for pid in people:
        cur.execute(
            "INSERT INTO expense_shares(expense_id,participant_id,share) VALUES(%s,%s,%s)",
            (expense_id, pid, share)
        )
    conn.commit()
    cur.close()
    conn.close()
    clear_state(call.message.chat.id)
    cat_emoji = next((e for e, n in CATEGORIES if n == state["category"]), "")
    bot.edit_message_text(
        f"✅ Сохранено!\n\n{cat_emoji} {state['category']} — {state['amount']} руб.\n"
        f"Разделён поровну на {len(people)} чел. ({round(share,2)} руб. каждый)",
        call.message.chat.id, call.message.message_id
    )

def build_pick_markup(people, selected_ids):
    markup = InlineKeyboardMarkup()
    for p in people:
        icon = "✅" if p["id"] in selected_ids else "☐"
        markup.add(InlineKeyboardButton(f"{icon} {p['name']}", callback_data=f"st_{p['id']}"))
    markup.add(InlineKeyboardButton("💾 Сохранить", callback_data="split_save"))
    return markup

@bot.callback_query_handler(func=lambda c: c.data == "split_pick")
def cb_split_pick(call):
    log.info(f"cb_split_pick: chat_id={call.message.chat.id}")
    state = get_state(call.message.chat.id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM participants WHERE event_id=%s ORDER BY id", (state["event_id"],))
    people = [{"id": p["id"], "name": p["name"]} for p in cur.fetchall()]
    cur.close()
    conn.close()
    set_state(call.message.chat.id, "expense_split_pick", {**state, "people": people, "selected": []})
    bot.edit_message_text(
        "Отметьте участников расхода:",
        call.message.chat.id, call.message.message_id,
        reply_markup=build_pick_markup(people, [])
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("st_"))
def cb_split_toggle(call):
    pid = int(call.data.split("_")[1])
    state = get_state(call.message.chat.id)
    selected = state.get("selected", [])
    if pid in selected:
        selected.remove(pid)
    else:
        selected.append(pid)
    state["selected"] = selected
    set_state(call.message.chat.id, "expense_split_pick", state)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id,
        reply_markup=build_pick_markup(state["people"], selected)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "split_save")
def cb_split_save(call):
    log.info(f"cb_split_save: chat_id={call.message.chat.id}")
    state = get_state(call.message.chat.id)
    selected = state.get("selected", [])
    if not selected:
        bot.answer_callback_query(call.id, "Выберите хотя бы одного участника!")
        return
    share = state["amount"] / len(selected)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses(event_id,amount,payer_id,category,split_type) VALUES(%s,%s,%s,%s,'selected') RETURNING id",
        (state["event_id"], state["amount"], state["payer_id"], state["category"])
    )
    expense_id = cur.fetchone()["id"]
    for pid in selected:
        cur.execute(
            "INSERT INTO expense_shares(expense_id,participant_id,share) VALUES(%s,%s,%s)",
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
        f"✅ Сохранено!\n\n{cat_emoji} {state['category']} — {state['amount']} руб.\n"
        f"Участники: {names}\n({round(share,2)} руб. каждый)",
        call.message.chat.id, call.message.message_id
    )

# ============================================================
# СПИСОК РАСХОДОВ
# ============================================================

def handle_list_expenses(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала выберите встречу.", parse_mode="Markdown")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.amount, e.category, e.split_type, p.name AS payer
        FROM expenses e JOIN participants p ON p.id=e.payer_id
        WHERE e.event_id=%s ORDER BY e.id
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
        lines.append(f"{emoji} {e['category']} — {e['amount']} руб. ({e['payer']}, {split_label})")
        markup.add(InlineKeyboardButton(f"❌ {e['category']} {e['amount']} руб.", callback_data=f"dex_{e['id']}"))
    text = f"🧾 *{ev['title']}*\n\n" + "\n".join(lines) + "\n\n_Нажмите чтобы удалить_"
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dex_"))
def cb_delete_expense(call):
    expense_id = int(call.data.split("_")[1])
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id=%s", (expense_id,))
    conn.commit()
    cur.close()
    conn.close()
    bot.edit_message_text("🗑 Расход удалён.", call.message.chat.id, call.message.message_id)

# ============================================================
# ИТОГИ
# ============================================================

def handle_balance(msg):
    ev = get_active_event(msg.chat.id)
    if not ev:
        bot.send_message(msg.chat.id, "Сначала выберите встречу.", parse_mode="Markdown")
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
        SELECT es.participant_id, es.share FROM expense_shares es
        JOIN expenses e ON e.id=es.expense_id WHERE e.event_id=%s
    """, (ev["id"],))
    for s in cur.fetchall():
        owed[s["participant_id"]] += s["share"]
    cur.execute("""
        SELECT category, SUM(amount) as total FROM expenses
        WHERE event_id=%s GROUP BY category ORDER BY total DESC
    """, (ev["id"],))
    cats = cur.fetchall()
    cur.close()
    conn.close()
    balance = {pid: paid[pid] - owed[pid] for pid in set(list(paid) + list(owed))}
    creditors = sorted([[pid, b]  for pid, b in balance.items() if b >  0.01], key=lambda x: -x[1])
    debtors   = sorted([[pid, -b] for pid, b in balance.items() if b < -0.01], key=lambda x: -x[1])
    transfers = []
    while creditors and debtors:
        c, d = creditors[0], debtors[0]
        pay = min(c[1], d[1])
        transfers.append(f"💸 {people.get(d[0],'?')} → {people.get(c[0],'?')}: {round(pay,2)} руб.")
        c[1] -= pay; d[1] -= pay
        if c[1] < 0.01: creditors.pop(0)
        if d[1] < 0.01: debtors.pop(0)
    cat_map  = {n: e for e, n in CATEGORIES}
    total    = sum(e["amount"] for e in expenses)
    cat_text = "\n".join(f"{cat_map.get(c['category'],'')} {c['category']}: {round(c['total'],2)} руб." for c in cats)
    text  = f"📊 *Итоги: {ev['title']}*\n\n💰 Всего: *{round(total,2)} руб.*\n\n*По категориям:*\n{cat_text}\n\n"
    text += ("*Кто кому платит:*\n" + "\n".join(transfers)) if transfers else "✅ Все расчёты закрыты!"
    bot.send_message(msg.chat.id, text, parse_mode="Markdown")

# ============================================================
# WEBHOOK
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

@app.route("/setup")
def setup():
    """Вызовите один раз после деплоя: https://your-app.onrender.com/setup"""
    try:
        init_db()
        if WEBHOOK_HOST:
            webhook_url = f"{WEBHOOK_HOST}/{TOKEN}"
            bot.remove_webhook()
            result = bot.set_webhook(url=webhook_url)
            msg = f"Webhook set: {webhook_url} | result={result}"
        else:
            msg = "RENDER_EXTERNAL_URL not set"
        log.info(f"setup: {msg}")
        return msg, 200
    except Exception as e:
        log.error(f"setup error: {e}")
        return f"Error: {e}", 500

@app.route("/status")
def status():
    """Проверить текущий вебхук."""
    info = bot.get_webhook_info()
    return {
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_message": info.last_error_message,
    }, 200

# ============================================================
# START
# ============================================================

def self_ping():
    """Пингует сервис каждые 10 минут чтобы Render не усыплял его."""
    if not WEBHOOK_HOST:
        return
    while True:
        time.sleep(600)  # 10 минут
        try:
            http_requests.get(f"{WEBHOOK_HOST}/", timeout=10)
            log.info("self_ping: ok")
        except Exception as e:
            log.warning(f"self_ping: failed: {e}")

# Запускаем self-ping в фоне
ping_thread = threading.Thread(target=self_ping, daemon=True)
ping_thread.start()

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
