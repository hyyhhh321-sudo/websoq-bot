import os
import sqlite3
import time
import requests

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = -1003809545859

if not BOT_TOKEN:
    print("Ошибка: Переменная окружения BOT_TOKEN не задана!")
    exit(1)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_NAME = "database.db"

# --- Инициализация БД ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            user_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            user_name TEXT,
            user_username TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT state FROM user_states WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_state(user_id, state):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if state:
        cursor.execute("INSERT OR REPLACE INTO user_states (user_id, state) VALUES (?, ?)", (user_id, state))
    else:
        cursor.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# --- API Запросы к Telegram ---
def api_request(method, data=None):
    url = f"{API_URL}/{method}"
    try:
        response = requests.post(url, json=data, timeout=30)
        return response.json()
    except Exception as e:
        print(f"Ошибка запроса {method}: {e}")
        return None

def send_message(chat_id, text, reply_markup=None, message_thread_id=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    if message_thread_id:
        data["message_thread_id"] = message_thread_id
    return api_request("sendMessage", data)

def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return api_request("editMessageText", data)

def answer_callback(callback_query_id, text=""):
    api_request("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

# --- Клавиатуры ---
def main_menu():
    return {
        "inline_keyboard": [
            [{"text": "🤖 Услуги с Telegram-ботами", "callback_data": "category_bots"}],
            [{"text": "💻 Услуги с сайтами", "callback_data": "category_sites"}],
            [{"text": "💎 Прайс-лист", "callback_data": "price_list"}],
            [{"text": "💬 Задать вопрос / Заказать", "callback_data": "open_ticket"}],
        ]
    }

def back_to_menu():
    return {
        "inline_keyboard": [
            [{"text": "◀️ Назад в меню", "callback_data": "main_menu"}]
        ]
    }

def cancel_ticket_kb():
    return {
        "inline_keyboard": [
            [{"text": "❌ Завершить диалог / тикет", "callback_data": "close_ticket"}]
        ]
    }

# --- Логика обработки событий ---
def handle_update(update):
    if "callback_query" in update:
        cq = update["callback_query"]
        user = cq["from"]
        user_id = user["id"]
        data = cq["data"]
        msg = cq["message"]
        chat_id = msg["chat"]["id"]
        message_id = msg["message_id"]

        if data == "main_menu":
            set_state(user_id, None)
            edit_message(chat_id, message_id, "Главное меню **WebSoq**:", main_menu())
            answer_callback(cq["id"])

        elif data == "price_list":
            text = (
                "💎 **Прайс-лист студии WebSoq**\n\n"
                "🌐 **Разработка сайтов и верстка:**\n"
                "• База сайта (HTML верстка) — 3 000 – 5 000 ⭐️ (~4 500 – 7 500 ₽)\n"
                "• CSS (оформление и стили) — 2 000 – 3 000 ⭐️ (~3 000 – 4 500 ₽)\n"
                "• JavaScript (интерактив, анимации) — 2 000 – 3 500 ⭐️ (~3 000 – 5 000 ₽)\n"
                "• Адаптация под мобильные устройства — 2 000 – 3 000 ⭐️ (~3 000 – 4 500 ₽)\n"
                "• Создание сайтов на Tilda — 5 000 – 8 000 ⭐️ (~7 500 – 12 000 ₽)\n\n"
                "🛠 **Правки и исправления на сайтах:**\n"
                "• Исправление текста / Замена картинок — 800 – 1 500 ⭐️ (~1 200 – 2 200 ₽)\n"
                "• Изменение стилей и элементов — 1 500 – 3 000 ⭐️ (~2 200 – 4 500 ₽)\n"
                "• Поиск багов / уязвимостей — 2 000 – 4 000 ⭐️ (~3 000 – 6 000 ₽)\n\n"
                "🤖 **Telegram-боты:**\n"
                "• Лёгкий бот (автоответчик, FAQ, визитка) — 5 000 – 8 000 ⭐️ (~7 500 – 12 000 ₽)\n"
                "• Средний бот (заявки, категории, тикеты) — 12 000 – 20 000 ⭐️ (~18 000 – 30 000 ₽)\n"
                "• Исправление чужого / сломанного кода — от 3 000 ⭐️ (~4 500 ₽)\n"
                "• Сложные проекты — от 25 000 ⭐️ (индивидуально)"
            )
            edit_message(chat_id, message_id, text, back_to_menu())
            answer_callback(cq["id"])

        elif data == "category_sites":
            kb = {
                "inline_keyboard": [
                    [{"text": "🌐 Создать сайт с нуля и доработки", "callback_data": "order_site"}],
                    [{"text": "🔧 Правки и исправления на сайтах", "callback_data": "order_site_fix"}],
                    [{"text": "◀️ Назад в меню", "callback_data": "main_menu"}],
                ]
            }
            edit_message(chat_id, message_id, "💻 Выберите направление по сайтам:", kb)
            answer_callback(cq["id"])

        elif data == "category_bots":
            kb = {
                "inline_keyboard": [
                    [{"text": "🤖 Разработка и исправление ботов", "callback_data": "order_bot"}],
                    [{"text": "◀️ Назад в меню", "callback_data": "main_menu"}],
                ]
            }
            edit_message(chat_id, message_id, "🤖 Выберите направление по Telegram-ботам:", kb)
            answer_callback(cq["id"])

        elif data in ["open_ticket", "order_site", "order_site_fix", "order_bot"]:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT thread_id FROM tickets WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()

            service_names = {
                "open_ticket": "Задать вопрос / Консультация",
                "order_site": "Разработка сайтов с нуля и доработки",
                "order_site_fix": "Правки и исправления на сайтах",
                "order_bot": "Разработка и исправление Telegram-ботов",
            }
            service_title = service_names.get(data, "Запрос из бота")

            if row:
                edit_message(chat_id, message_id, "У вас уже открыт активный тикет! Напишите сообщение сюда, и оно передастся специалистам.", cancel_ticket_kb())
                set_state(user_id, "waiting_for_message")
                answer_callback(cq["id"])
                return

            topic_res = api_request("createForumTopic", {"chat_id": GROUP_ID, "name": f"Тикет: {user.get('first_name', 'User')} ({user_id})"})
            if not topic_res or not topic_res.get("ok"):
                edit_message(chat_id, message_id, "Не удалось создать тикет. Попробуйте позже.", back_to_menu())
                answer_callback(cq["id"])
                return

            thread_id = topic_res["result"]["message_thread_id"]

            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO tickets (user_id, thread_id, user_name, user_username) VALUES (?, ?, ?, ?)",
                           (user_id, thread_id, user.get('first_name', ''), user.get('username', 'нет')))
            conn.commit()
            conn.close()

            username_str = f"@{user.get('username')}" if user.get('username') else "отсутствует"
            group_text = (
                f"🚨 **Новый клиент / Тикет!**\n\n"
                f"👤 Имя: {user.get('first_name', '')}\n"
                f"🔗 Юзернейм: {username_str}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📌 Услуга: **{service_title}**\n\n"
                f"💡 *Чтобы отправить чек, напишите команду:* `/invoice (кол-во звезд)`"
            )
            send_message(GROUP_ID, group_text, message_thread_id=thread_id)

            edit_message(chat_id, message_id, f"💬 **Тикет успешно открыт!**\nВы выбрали: *{service_title}*.\n\nНапишите ваше сообщение прямо здесь.", cancel_ticket_kb())
            set_state(user_id, "waiting_for_message")
            answer_callback(cq["id"])

        elif data == "close_ticket":
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT thread_id FROM tickets WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                send_message(GROUP_ID, "🔒 Клиент закрыл этот тикет.", message_thread_id=row[0])
                cursor.execute("DELETE FROM tickets WHERE user_id = ?", (user_id,))
                conn.commit()
            conn.close()

            set_state(user_id, None)
            edit_message(chat_id, message_id, "✅ Тикет успешно закрыт. Спасибо, что обратились в **WebSoq**!", main_menu())
            answer_callback(cq["id"])

    elif "message" in update:
        msg = update["message"]
        chat = msg["chat"]
        chat_id = chat["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if chat_id == GROUP_ID:
            thread_id = msg.get("message_thread_id")
            if not thread_id:
                return

            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM tickets WHERE thread_id = ?", (thread_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                return

            client_id = row[0]

            if text.startswith("/invoice"):
                parts = text.replace("(", "").replace(")", "").split()
                if len(parts) < 2 or not parts[1].isdigit():
                    send_message(GROUP_ID, "⚠️ Ошибка! Формат: `/invoice 100`", message_thread_id=thread_id)
                    return
                stars = int(parts[1])
                inv_data = {
                    "chat_id": client_id,
                    "title": "Оплата услуг WebSoq",
                    "description": f"Оплата заказа на сумму {stars} Telegram Stars ⭐",
                    "payload": f"websoq_{client_id}",
                    "currency": "XTR",
                    "prices": [{"label": "Услуга WebSoq", "amount": stars}]
                }
                res = api_request("sendInvoice", inv_data)
                if res and res.get("ok"):
                    send_message(GROUP_ID, f"✅ Чек (инвойс) на {stars} ⭐ отправлен клиенту!", message_thread_id=thread_id)
                else:
                    send_message(GROUP_ID, f"❌ Не удалось отправить чек.", message_thread_id=thread_id)
                return

            api_request("sendMessage", {"chat_id": client_id, "text": text})

        elif chat["type"] == "private":
            if text == "/start":
                set_state(user_id, None)
                send_message(chat_id, "Приветствую! Добро пожаловать в **WebSoq**.\n\nВыберите интересующий вас раздел:", main_menu())
                return

            state = get_state(user_id)
            if state == "waiting_for_message":
                if text.startswith("/"):
                    return
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT thread_id FROM tickets WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                conn.close()

                if not row:
                    send_message(chat_id, "Ваш тикет закрыт. Нажмите /start")
                    set_state(user_id, None)
                    return

                thread_id = row[0]
                api_request("sendMessage", {"chat_id": GROUP_ID, "message_thread_id": thread_id, "text": text})

def main():
    init_db()
    print("Бот WebSoq успешно запущен!")
    offset = 0
    while True:
        updates_data = api_request("getUpdates", {"offset": offset, "timeout": 30})
        if updates_data and updates_data.get("ok"):
            for update in updates_data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"Ошибка: {e}")
        time.sleep(1)

if __name__ == "__main__":
    main()
