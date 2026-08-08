import os
import html
import sqlite3
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = -1003809545859
PORT = int(os.getenv("PORT", 10000))
# На Render диски стираются, если не подключить Persistent Disk. 
# Оставляем возможность задать путь к БД через переменные окружения.
DB_PATH = os.getenv("DB_PATH", "database.db") 

if not BOT_TOKEN:
    print("Ошибка: Переменная окружения BOT_TOKEN не задана!")
    exit(1)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- In-Memory Хранилища ---
user_states = {}       # {user_id: {"state": "...", "context": "..."}}
spam_tracker = {}      # {user_id: [timestamp1, timestamp2, ...]}
mutes = {}             # {user_id: unban_timestamp}
admin_cache = {}       # {user_id: (is_admin_bool, timestamp)}
ADMIN_CACHE_TTL = 300   # 5 минут

# --- Веб-сервер для порта Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"WebSoq CRM is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    server_address = ("0.0.0.0", PORT)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    httpd.serve_forever()

# --- Инициализация БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            user_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            custom_name TEXT,
            user_username TEXT,
            service_name TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- Автосоздание темы для логов оплат ---
def get_or_create_payments_thread():
    thread_id = get_setting("payments_thread_id")
    if thread_id:
        return int(thread_id)
    
    topic_res = api_request("createForumTopic", {"chat_id": GROUP_ID, "name": "💎 История оплат"})
    if topic_res and topic_res.get("ok"):
        new_thread_id = topic_res["result"]["message_thread_id"]
        set_setting("payments_thread_id", new_thread_id)
        send_message(
            GROUP_ID, 
            "📌 <b>Тема успешно создана!</b>\nСюда будут автоматически отправляться все чеки и история успешных оплат.", 
            message_thread_id=new_thread_id
        )
        return new_thread_id
    return None

# --- Антиспам ---
def check_spam(user_id):
    now = time.time()
    if user_id in mutes:
        if now < mutes[user_id]:
            return "muted"
        else:
            del mutes[user_id]
            
    if user_id not in spam_tracker:
        spam_tracker[user_id] = []
        
    spam_tracker[user_id] = [t for t in spam_tracker[user_id] if now - t < 3.0]
    spam_tracker[user_id].append(now)
    
    msg_count = len(spam_tracker[user_id])
    if msg_count >= 5:
        mutes[user_id] = now + 60
        return "mute_now"
    elif msg_count >= 3:
        return "warn"
    return "ok"

# --- Проверка прав администратора группы ---
def is_group_admin(user_id):
    now = time.time()
    cached = admin_cache.get(user_id)
    if cached and now - cached[1] < ADMIN_CACHE_TTL:
        return cached[0]

    res = api_request("getChatMember", {"chat_id": GROUP_ID, "user_id": user_id})
    is_admin = False
    if res and res.get("ok"):
        status = res["result"].get("status")
        is_admin = status in ("administrator", "creator")
    admin_cache[user_id] = (is_admin, now)
    return is_admin

# --- API Запросы ---
def api_request(method, data=None):
    url = f"{API_URL}/{method}"
    try:
        response = requests.post(url, json=data, timeout=40)
        return response.json()
    except Exception as e:
        if "Read timed out" not in str(e):
            print(f"Ошибка запроса {method}: {e}")
        return None

def send_message(chat_id, text, reply_markup=None, message_thread_id=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: data["reply_markup"] = reply_markup
    if message_thread_id: data["message_thread_id"] = message_thread_id
    return api_request("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: data["reply_markup"] = reply_markup
    return api_request("editMessageText", data)

def answer_callback(callback_query_id, text="", show_alert=False):
    api_request("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert})

def forward_with_prefix(prefix, from_chat_id, to_chat_id, message_id, message_thread_id=None):
    send_message(to_chat_id, prefix, message_thread_id=message_thread_id)
    copy_data = {
        "chat_id": to_chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id
    }
    if message_thread_id:
        copy_data["message_thread_id"] = message_thread_id
    api_request("copyMessage", copy_data)

# --- Клавиатуры ---
def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "🌐 Разработка сайтов", "callback_data": "cat_sites"}],
            [{"text": "🤖 Telegram-боты", "callback_data": "cat_bots"}],
            [{"text": "💎 Прайс-лист", "callback_data": "price_list"}],
            [{"text": "💬 Консультация / Вопрос", "callback_data": "srv_consult"}],
        ]
    }

def back_to_menu():
    return {"inline_keyboard": [[{"text": "◀️ Назад в меню", "callback_data": "main_menu"}]]}

def skip_name_kb():
    return {"inline_keyboard": [[{"text": "⏭ Пропустить", "callback_data": "skip_name"}], [{"text": "◀️ Отмена", "callback_data": "main_menu"}]]}

def admin_close_kb():
    return {"inline_keyboard": [[{"text": "🔒 Закрыть тикет", "callback_data": "admin_close"}]]}

SERVICES = {
    "srv_consult": "Консультация / Вопрос",
    "srv_site_new": "Разработка сайтов с нуля",
    "srv_site_fix": "Правки и исправления на сайтах",
    "srv_bot_new": "Разработка и исправление ботов"
}

# --- Создание тикета ---
def create_ticket(user_id, chat_id, user_obj, custom_name, service_key):
    service_name = SERVICES.get(service_key, "Неизвестная услуга")
    safe_name = html.escape(custom_name)
    
    topic_res = api_request("createForumTopic", {"chat_id": GROUP_ID, "name": f"{custom_name} | {service_name[:15]}"})
    if not topic_res or not topic_res.get("ok"):
        send_message(chat_id, "❌ Ошибка создания тикета. Попробуйте позже.")
        return
        
    thread_id = topic_res["result"]["message_thread_id"]
    username_str = f"@{user_obj.get('username')}" if user_obj.get('username') else "Скрыт"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tickets (user_id, thread_id, custom_name, user_username, service_name) VALUES (?, ?, ?, ?, ?)",
                   (user_id, thread_id, custom_name, user_obj.get('username', ''), service_name))
    conn.commit()
    conn.close()

    user_states[user_id] = {"state": "chatting"}
    
    admin_text = (
        f"🚨 <b>Новый тикет открыт!</b>\n\n"
        f"👤 Клиент: <b>{safe_name}</b>\n"
        f"🔗 Username: {username_str}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📌 Услуга: <b>{service_name}</b>\n\n"
        f"<i>Команда для выставления счета:</i>\n<code>/invoice 100</code>"
    )
    send_message(GROUP_ID, admin_text, reply_markup=admin_close_kb(), message_thread_id=thread_id)
    send_message(chat_id, f"✅ <b>Тикет создан!</b>\nВаш запрос: <i>{service_name}</i>\n\nНапишите ваше сообщение (текст, фото, файлы), и я отвечу вам в ближайшее время.")

# --- Обработчик событий ---
def handle_update(update):
    if "pre_checkout_query" in update:
        query_id = update["pre_checkout_query"]["id"]
        api_request("answerPreCheckoutQuery", {"pre_checkout_query_id": query_id, "ok": True})
        return

    if "callback_query" in update:
        cq = update["callback_query"]
        user = cq["from"]
        user_id = user["id"]
        data = cq["data"]
        msg = cq.get("message")
        if not msg: return
        chat_id = msg["chat"]["id"]
        message_id = msg["message_id"]

        if data == "main_menu":
            user_states.pop(user_id, None)
            edit_message(chat_id, message_id, "Главное меню <b>WebSoq</b>:", main_menu())
            
        elif data == "price_list":
            text = (
                "💎 <b>Прайс-лист WebSoq</b>\n\n"
                "🌐 <b>Создание сайтов с нуля (База):</b>\n"
                "• Сайт с нуля — 6 000 – 9 000 ₽ / TON (~3 800 – 5 800 ⭐️)\n"
                "• Создание сайтов на Tilda — 4 200 – 7 200 ₽ / TON (~2 700 – 4 600 ⭐️)\n\n"
                "🎨 <b>Доработка существующих сайтов (Тюнинг):</b>\n"
                "• CSS (оформление, стили, верстка) — 1 800 – 3 000 ₽ / TON (~1 200 – 1 900 ⭐️)\n"
                "• JavaScript (интерактив, анимации) — 2 200 – 3 600 ₽ / TON (~1 400 – 2 300 ⭐️)\n"
                "• Адаптация под мобильные устройства — 1 800 – 3 000 ₽ / TON (~1 200 – 1 900 ⭐️)\n\n"
                "🛠 <b>Правки и исправления на сайтах:</b>\n"
                "• Исправление текста / Замена картинок — 700 – 1 200 ₽ / TON (~450 – 770 ⭐️)\n"
                "• Изменение стилей и элементов — 1 200 – 2 400 ₽ / TON (~770 – 1 500 ⭐️)\n"
                "• Поиск багов / уязвимостей — 1 800 – 3 600 ₽ / TON (~1 200 – 2 300 ⭐️)\n\n"
                "🤖 <b>Разработка и исправление Telegram-ботов (Лёгкие и средние задачи):</b>\n"
                "• Лёгкий бот (автоответчик, FAQ, визитка) — 3 500 – 6 000 ₽ / TON (~2 200 – 3 800 ⭐️)\n"
                "• Средний бот (заявки, категории, тикеты) — 8 000 – 15 000 ₽ / TON (~5 100 – 9 600 ⭐️)\n"
                "• Исправление чужого / сломанного кода — от 2 000 ₽ / TON (от ~1 300 ⭐️)\n"
                "• Сложные проекты — от 18 000 ₽ / TON (индивидуально)\n\n"
                "💎 <b>Условия и способы оплаты</b>\n"
                "Я принимаю оплату цифровыми активами: TON, USDT или Telegram Stars ⭐️.\n\n"
                "• Оплата в TON / USDT:
