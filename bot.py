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
        # Отключаем спам health-check пингами в логах
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

# --- API Запросы ---
def api_request(method, data=None):
    url = f"{API_URL}/{method}"
    try:
        # Увеличен timeout до 40, чтобы избежать спама ошибками "Read timed out" от Render
        response = requests.post(url, json=data, timeout=40)
        return response.json()
    except Exception as e:
        if "Read timed out" not in str(e): # Скрываем нормальные таймауты Telegram
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
    """Отправляет подпись-заголовок, а затем копирует исходное сообщение (текст/фото/файл/голосовое)."""
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
        f"👤 Клиент: <b>{safe_name}</b>\Привет! Я обновил раздел с прайс-листом в твоем коде. 

Новые цены на веб-разработку и правки сайтов были взяты из первого скриншота. Цены на разработку и исправление Telegram-ботов обновлены в соответствии со вторым скриншотом. Также в самом конце прайса я добавил строчку о том, что оплата принимается только в TON и Telegram Stars ⭐️. Остальной функционал бота остался без изменений.

Вот готовый обновленный код:

```python
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

API_URL = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){BOT_TOKEN}"

# --- In-Memory Хранилища ---
user_states = {}       # {user_id: {"state": "...", "context": "..."}}
spam_tracker = {}      # {user_id: [timestamp1, timestamp2, ...]}
mutes = {}             # {user_id: unban_timestamp}

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
        # Отключаем спам health-check пингами в логах
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

# --- API Запросы ---
def api_request(method, data=None):
    url = f"{API_URL}/{method}"
    try:
        # Увеличен timeout до 40, чтобы избежать спама ошибками "Read timed out" от Render
        response = requests.post(url, json=data, timeout=40)
        return response.json()
    except Exception as e:
        if "Read timed out" not in str(e): # Скрываем нормальные таймауты Telegram
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
    """Отправляет подпись-заголовок, а затем копирует исходное сообщение (текст/фото/файл/голосовое)."""
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
    send_message(chat_id, f"✅ <b>Тикет создан!</b>\nВаш запрос: <i>{service_name}</i>\n\nНапишите ваше сообщение (текст, фото, файлы), и специалист ответит вам в ближайшее время.")

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
                "🌐 <b>Создание сайтов с нуля:</b>\n"
                "• Сайт с нуля (верстка по макету) — 5 000 – 7 500 ⭐️ (~6 000 – 9 000 ₽ / TON)\n"
                "• Создание сайтов на Tilda — 3 500 – 6 000 ⭐️ (~4 200 – 7 200 ₽ / TON)\n\n"
                "🎨 <b>Доработка существующих сайтов (Тюнинг):</b>\n"
                "• CSS (оформление, стили, верстка) — 1 500 – 2 500 ⭐️ (~1 800 – 3 000 ₽ / TON)\n"
                "• JavaScript (интерактив, анимации) — 1 800 – 3 000 ⭐️ (~2 200 – 3 600 ₽ / TON)\n"
                "• Адаптация под мобильные устройства — 1 500 – 2 500 ⭐️ (~1 800 – 3 000 ₽ / TON)\n\n"
                "🛠 <b>Правки и исправления на сайтах:</b>\n"
                "• Исправление текста / Замена картинок — 600 – 1 000 ⭐️ (~700 – 1 200 ₽ / TON)\n"
                "• Изменение стилей и элементов — 1 000 – 2 000 ⭐️ (~1 200 – 2 400 ₽ / TON)\n"
                "• Поиск багов / уязвимостей — 1 500 – 3 000 ⭐️ (~1 800 – 3 600 ₽ / TON)\n\n"
                "🤖 <b>Разработка и исправление Telegram-ботов:</b>\n"
                "• Лёгкий бот (автоответчик, FAQ, визитка) — 3 500 – 6 000 ⭐️ (~4 200 – 7 200 ₽ / TON)\n"
                "• Средний бот (заявки, категории, тикеты) — 8 000 – 15 000 ⭐️ (~9 600 – 18 000 ₽ / TON)\n"
                "• Исправление чужого / сломанного кода — от 2 000 ⭐️ (~2 400 ₽ / TON)\n"
                "• Сложные проекты — от 18 000 ⭐️ (индивидуально)\n\n"
                "💳 <b>Оплата принимается ТОЛЬКО в TON и Telegram Stars ⭐️</b>"
            )
            edit_message(chat_id, message_id, text, back_to_menu())

        elif data == "cat_sites":
            kb = {"inline_keyboard": [
                [{"text": "🌐 Создать сайт с нуля", "callback_data": "srv_site_new"}],
                [{"text": "🔧 Правки и исправления", "callback_data": "srv_site_fix"}],
                [{"text": "◀️ Назад", "callback_data": "main_menu"}]
            ]}
            edit_message(chat_id, message_id, "💻 <b>Направление: Сайты</b>", kb)

        elif data == "cat_bots":
            kb = {"inline_keyboard": [
                [{"text": "🤖 Разработка и исправление", "callback_data": "srv_bot_new"}],
                [{"text": "◀️ Назад", "callback_data": "main_menu"}]
            ]}
            edit_message(chat_id, message_id, "🤖 <b>Направление: Боты</b>", kb)

        elif data.startswith("srv_"):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT thread_id FROM tickets WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                answer_callback(cq["id"], "У вас уже есть открытый тикет!", show_alert=True)
                return

            user_states[user_id] = {"state": "waiting_name", "context": data}
            edit_message(chat_id, message_id, "✍️ Как мы можем к вам обращаться?\n\n<i>Напишите имя в чат или нажмите «Пропустить»</i>", skip_name_kb())

        elif data == "skip_name":
            state_data = user_states.get(user_id)
            if state_data and state_data.get("state") == "waiting_name":
                srv = state_data["context"]
                edit_message(chat_id, message_id, "⏳ Создаем тикет...")
                create_ticket(user_id, chat_id, user, user.get("first_name", "Клиент"), srv)

        elif data == "admin_close":
            thread_id = msg.get("message_thread_id")
            if not thread_id: return
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, custom_name FROM tickets WHERE thread_id = ?", (thread_id,))
            row = cursor.fetchone()
            
            if row:
                client_id, c_name = row
                send_message(client_id, "✅ Ваш тикет был успешно закрыт. Спасибо, что выбрали <b>WebSoq</b>!", main_menu())
                user_states.pop(client_id, None)
                cursor.execute("DELETE FROM tickets WHERE thread_id = ?", (thread_id,))
                conn.commit()
                edit_message(chat_id, message_id, f"🔒 Тикет клиента <b>{html.escape(c_name or '')}</b> закрыт администратором.")
            conn.close()

        answer_callback(cq["id"])

    elif "message" in update:
        msg = update["message"]
        chat = msg["chat"]
        chat_id = chat["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")
        message_id = msg["message_id"]

        # --- ОБРАБОТКА ОПЛАТЫ ---
        if "successful_payment" in msg:
            payment = msg["successful_payment"]
            stars = payment.get("total_amount", 0)
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT thread_id, custom_name, user_username, service_name FROM tickets WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()
            
            send_message(chat_id, f"🎉 <b>Оплата получена!</b>\nСпасибо за оплату в размере {stars} ⭐️. Специалист уже уведомлен.")
            
            user_obj = msg["from"]
            username_str = f"@{user_obj.get('username')}" if user_obj.get('username') else "Скрыт"
            client_name = row[1] if row and row[1] else user_obj.get("first_name", "Клиент")
            safe_client_name = html.escape(client_name or "Клиент")
            service_name = row[3] if row and row[3] else "Не указана"

            if row and row[0]:
                send_message(GROUP_ID, f"💰 <b>ОПЛАТА ПОЛУЧЕНА!</b>\nКлиент только что оплатил инвойс на <b>{stars} ⭐️</b>.", message_thread_id=row[0])
            
            log_text = (
                f"💎 <b>Новая успешная оплата!</b>\n\n"
                f"👤 Клиент: <b>{safe_client_name}</b>\n"
                f"🔗 Username: {username_str}\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"📌 Услуга: <b>{service_name}</b>\n"
                f"⭐ Сумма: <b>{stars} Telegram Stars</b>"
            )
            
            payments_thread_id = get_or_create_payments_thread()
            if payments_thread_id:
                res = send_message(GROUP_ID, log_text, message_thread_id=payments_thread_id)
                if not res or not res.get("ok"):
                    set_setting("payments_thread_id", "") 
                    new_thread_id = get_or_create_payments_thread() 
                    if new_thread_id:
                        send_message(GROUP_ID, log_text, message_thread_id=new_thread_id)
            return

        # --- СООБЩЕНИЯ В ГРУППЕ (ОТ АДМИНОВ) ---
        if chat_id == GROUP_ID:
            thread_id = msg.get("message_thread_id")
            if not thread_id: return

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM tickets WHERE thread_id = ?", (thread_id,))
            row = cursor.fetchone()
            conn.close()
            if not row: return
            client_id = row[0]

            # Выставление счета
            if text.startswith("/invoice"):
                parts = text.split()
                if len(parts) < 2 or not parts[1].isdigit():
                    send_message(GROUP_ID, "⚠️ Формат: <code>/invoice 100</code>", message_thread_id=thread_id)
                    return
                stars = int(parts[1])
                inv_data = {
                    "chat_id": client_id,
                    "title": "Оплата услуг WebSoq",
                    "description": f"Счет на оплату услуг (Сумма: {stars} Telegram Stars)",
                    "payload": f"websoq_pay_{client_id}_{int(time.time())}",
                    "currency": "XTR",
                    "provider_token": "",
                    "prices": [{"label": "Услуги WebSoq", "amount": stars}]
                }
                res = api_request("sendInvoice", inv_data)
                if res and res.get("ok"):
                    send_message(GROUP_ID, f"🧾 <b>Инвойс отправлен!</b>\nСчет на {stars} ⭐️ успешно доставлен клиенту.", message_thread_id=thread_id)
                else:
                    send_message(GROUP_ID, "❌ Ошибка отправки инвойса.", message_thread_id=thread_id)
                return

            # Игнорирование сервисных сообщений Telegram (например, закрепление)
            if "is_automatic_forward" in msg or "forum_topic_created" in msg:
                return

            # ВНУТРЕННИЕ ЗАМЕТКИ АДМИНОВ (начинаются с ! или .)
            if text and (text.startswith("!") or text.startswith(".")):
                return 

            # ИДЕАЛЬНАЯ ПЕРЕСЫЛКА: копируем любое сообщение клиента (текст, фото, голосовые, файлы)
            forward_with_prefix("🧑‍💻 Поддержка:", GROUP_ID, client_id, message_id)

        # --- СООБЩЕНИЯ В ЛИЧКЕ (ОТ КЛИЕНТОВ) ---
        elif chat["type"] == "private":
            spam_status = check_spam(user_id)
            if spam_status == "muted":
                return
            elif spam_status == "mute_now":
                send_message(chat_id, "🛑 <b>Вы отправляете сообщения слишком быстро! Включен режим тишины на 1 минуту.</b>")
                return
            elif spam_status == "warn":
                send_message(chat_id, "⚠️ <b>Пожалуйста, не отправляйте сообщения так часто.</b>")
                return

            if text == "/start":
                user_states.pop(user_id, None)
                welcome_text = (
                    "👋 <b>Добро пожаловать в студию WebSoq!</b>\n\n"
                    "Мы — команда профи, готовая воплотить ваши идеи в реальность. "
                    "Специализируемся на разработке современных сайтов и умных Telegram-ботов любой сложности. 🚀\n\n"
                    "👇 <i>Выберите интересующий вас раздел в меню ниже:</i>"
                )
                send_message(chat_id, welcome_text, main_menu())
                return

            state_data = user_states.get(user_id, {})
            current_state = state_data.get("state")

            if current_state == "waiting_name":
                if not text:
                    send_message(chat_id, "⚠️ Пожалуйста, отправьте ваше имя текстом или нажмите кнопку «Пропустить».")
                    return
                srv = state_data.get("context")
                name = text.strip()[:30]
                create_ticket(user_id, chat_id, msg["from"], name, srv)
                return

            if current_state == "chatting":
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT thread_id FROM tickets WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                conn.close()

                if row:
                    # ИДЕАЛЬНАЯ ПЕРЕСЫЛКА: клиент может слать фото багов, ТЗ файлами и голосовые
                    forward_with_prefix("💻 Клиент:", chat_id, GROUP_ID, message_id, message_thread_id=row[0])
                else:
                    user_states.pop(user_id, None)
                    send_message(chat_id, "Ваш тикет закрыт. Нажмите /start для возврата в меню.", main_menu())

def main():
    init_db()
    threading.Thread(target=run_web_server, daemon=True).start()
    print(f"WebSoq CRM: Бот запущен! Медиа-движок активен. БД: {DB_PATH}")
    
    offset = 0
    while True:
        updates = api_request("getUpdates", {"offset": offset, "timeout": 30})
