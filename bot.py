import telebot
from telebot import types
import requests
import io
import logging
import time
from config import BOT_TOKEN, GOOGLE_VISION_API_KEY, ADMIN_IDS
from ocr_processor import process_image, format_results, format_csv
from database import (
    is_allowed, is_banned, add_user, remove_user,
    ban_user, unban_user, get_all_users, get_banned_users, get_stats
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

user_sessions = {}


# ===== ПРОВЕРКИ ДОСТУПА =====

def is_admin(user_id):
    return user_id in ADMIN_IDS


def check_access(message):
    """
    Проверка доступа пользователя
    Возвращает True если доступ разрешён
    """
    user_id = message.from_user.id

    # Админ всегда имеет доступ
    if is_admin(user_id):
        return True

    # Проверка бана
    if is_banned(user_id):
        bot.send_message(
            message.chat.id,
            "🚫 Вы заблокированы. Обратитесь к администратору."
        )
        return False

    # Проверка разрешения
    if not is_allowed(user_id):
        bot.send_message(
            message.chat.id,
            "⛔ У вас нет доступа к боту.\n\n"
            "Обратитесь к администратору для получения доступа.\n\n"
            f"Ваш ID: `{user_id}`",
            parse_mode='Markdown'
        )
        return False

    return True


# ===== КЛАВИАТУРЫ =====

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📊 Экспорт CSV"))
    markup.add(types.KeyboardButton("🗑 Очистить"), types.KeyboardButton("ℹ️ Помощь"))
    return markup


def get_admin_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("👥 Пользователи"), types.KeyboardButton("📊 Статистика"))
    markup.add(types.KeyboardButton("➕ Добавить"), types.KeyboardButton("➖ Удалить"))
    markup.add(types.KeyboardButton("🚫 Бан"), types.KeyboardButton("✅ Разбан"))
    markup.add(types.KeyboardButton("🔙 Главное меню"))
    return markup


# ===== ОБЫЧНЫЕ КОМАНДЫ =====

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""

    # Логируем попытку входа
    logger.info(f"Пользователь {user_id} (@{username}) запустил бота")

    if not check_access(message):
        return

    user_sessions[user_id] = []

    text = (
        "👋 *Привет! Я бот для распознавания SIM-карт.*\n\n"
        "📸 Отправьте фото SIM-карт:\n"
        "• ICCID номер\n"
        "• PUK код\n\n"
        "💡 *Советы:*\n"
        "• Чёткое фото\n"
        "• Хорошее освещение\n"
        "• Можно несколько карт на фото\n\n"
        "📤 Отправьте фото!"
    )

    keyboard = get_main_keyboard()

    # Если админ — показываем кнопку админки
    if is_admin(user_id):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("📊 Экспорт CSV"))
        markup.add(types.KeyboardButton("🗑 Очистить"), types.KeyboardButton("ℹ️ Помощь"))
        markup.add(types.KeyboardButton("⚙️ Админ панель"))
        keyboard = markup

    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


@bot.message_handler(commands=['help'])
def help_handler(message):
    if not check_access(message):
        return
    text = (
        "📖 *Инструкция:*\n\n"
        "1️⃣ Отправьте фото SIM-карты\n"
        "2️⃣ Бот распознает ICCID и PUK\n"
        "3️⃣ *Экспорт CSV* — сохранить данные\n"
        "4️⃣ *Очистить* — сбросить сессию\n\n"
        "📋 *Команды:*\n"
        "/start - Начало\n"
        "/export - Экспорт CSV\n"
        "/clear - Очистить\n"
        "/stats - Статистика"
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['export'])
def export_handler(message):
    if not check_access(message):
        return
    user_id = message.from_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        bot.send_message(message.chat.id, "❌ Нет данных для экспорта.")
        return
    csv_content = format_csv(user_sessions[user_id])
    if csv_content:
        csv_bytes = csv_content.encode('utf-8')
        csv_file = io.BytesIO(csv_bytes)
        csv_file.name = 'sim_cards.csv'
        bot.send_document(
            message.chat.id,
            csv_file,
            caption=f"📊 Экспорт: {len(user_sessions[user_id])} SIM-карт"
        )
    else:
        bot.send_message(message.chat.id, "❌ Ошибка CSV")


@bot.message_handler(commands=['clear'])
def clear_handler(message):
    if not check_access(message):
        return
    user_id = message.from_user.id
    count = len(user_sessions.get(user_id, []))
    user_sessions[user_id] = []
    bot.send_message(message.chat.id, f"🗑 Очищено. Удалено: {count}")


@bot.message_handler(commands=['stats'])
def stats_handler(message):
    if not check_access(message):
        return
    user_id = message.from_user.id
    cards = user_sessions.get(user_id, [])
    bot.send_message(
        message.chat.id,
        f"📊 *Статистика сессии:*\n\nВсего карт: {len(cards)}",
        parse_mode='Markdown'
    )


# ===== АДМИН КОМАНДЫ =====

@bot.message_handler(commands=['admin'])
def admin_handler(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return
    bot.send_message(
        message.chat.id,
        "⚙️ *Админ панель*\n\nВыберите действие:",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )


@bot.message_handler(commands=['adduser'])
def adduser_command(message):
    """
    Добавить пользователя
    Использование: /adduser 123456789 username
    """
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.send_message(
            message.chat.id,
            "❌ Использование: `/adduser USER_ID username`\n\n"
            "Пример: `/adduser 123456789 ivan`",
            parse_mode='Markdown'
        )
        return

    try:
        user_id = int(args[1])
        username = args[2] if len(args) > 2 else ""

        add_user(user_id, username, added_by=message.from_user.id)

        bot.send_message(
            message.chat.id,
            f"✅ Пользователь добавлен!\n\n"
            f"ID: `{user_id}`\n"
            f"Username: @{username}",
            parse_mode='Markdown'
        )

        # Уведомляем пользователя
        try:
            bot.send_message(
                user_id,
                "✅ Вам выдан доступ к боту!\n\n"
                "Нажмите /start для начала работы."
            )
        except:
            bot.send_message(message.chat.id, "⚠️ Не удалось уведомить пользователя")

    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат ID")


@bot.message_handler(commands=['removeuser'])
def removeuser_command(message):
    """
    Удалить пользователя
    Использование: /removeuser 123456789
    """
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.send_message(
            message.chat.id,
            "❌ Использование: `/removeuser USER_ID`\n\n"
            "Пример: `/removeuser 123456789`",
            parse_mode='Markdown'
        )
        return

    try:
        user_id = int(args[1])
        if remove_user(user_id):
            bot.send_message(
                message.chat.id,
                f"✅ Пользователь `{user_id}` удалён",
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                message.chat.id,
                f"❌ Пользователь `{user_id}` не найден",
                parse_mode='Markdown'
            )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат ID")


@bot.message_handler(commands=['banuser'])
def banuser_command(message):
    """
    Забанить пользователя
    Использование: /banuser 123456789
    """
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.send_message(
            message.chat.id,
            "❌ Использование: `/banuser USER_ID`",
            parse_mode='Markdown'
        )
        return

    try:
        user_id = int(args[1])
        ban_user(user_id)
        bot.send_message(
            message.chat.id,
            f"🚫 Пользователь `{user_id}` заблокирован",
            parse_mode='Markdown'
        )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат ID")


@bot.message_handler(commands=['unbanuser'])
def unbanuser_command(message):
    """
    Разбанить пользователя
    Использование: /unbanuser 123456789
    """
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    args = message.text.split()
    if len(args) < 2:
        bot.send_message(
            message.chat.id,
            "❌ Использование: `/unbanuser USER_ID`",
            parse_mode='Markdown'
        )
        return

    try:
        user_id = int(args[1])
        if unban_user(user_id):
            bot.send_message(
                message.chat.id,
                f"✅ Пользователь `{user_id}` разблокирован",
                parse_mode='Markdown'
            )
        else:
            bot.send_message(
                message.chat.id,
                f"❌ Пользователь `{user_id}` не в бане",
                parse_mode='Markdown'
            )
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат ID")


@bot.message_handler(commands=['users'])
def users_command(message):
    """Список всех пользователей"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    users = get_all_users()

    if not users:
        bot.send_message(message.chat.id, "👥 Нет пользователей")
        return

    text = f"👥 *Пользователи ({len(users)}):*\n\n"
    for uid, info in users.items():
        username = f"@{info['username']}" if info.get('username') else "без username"
        added_at = info.get('added_at', 'неизвестно')
        text += f"• `{uid}` {username}\n  📅 {added_at}\n\n"

    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['adminstats'])
def adminstats_command(message):
    """Статистика для админа"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Нет доступа")
        return

    stats = get_stats()
    text = (
        f"📊 *Статистика бота:*\n\n"
        f"👥 Пользователей: {stats['total_allowed']}\n"
        f"🚫 Заблокировано: {stats['total_banned']}\n"
        f"🤖 Активных сессий: {len(user_sessions)}"
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


# ===== ОБРАБОТКА КНОПОК =====

# Состояния для диалогов
user_states = {}


@bot.message_handler(content_types=['text'])
def text_handler(message):
    user_id = message.from_user.id
    text = message.text

    # Обработка состояний (диалоги добавления/удаления)
    if user_id in user_states:
        handle_state(message)
        return

    # Кнопки главного меню
    if text == "📊 Экспорт CSV":
        export_handler(message)
    elif text == "🗑 Очистить":
        clear_handler(message)
    elif text == "ℹ️ Помощь":
        help_handler(message)

    # Кнопки админки
    elif text == "⚙️ Админ панель" and is_admin(user_id):
        bot.send_message(
            message.chat.id,
            "⚙️ *Админ панель*",
            parse_mode='Markdown',
            reply_markup=get_admin_keyboard()
        )

    elif text == "👥 Пользователи" and is_admin(user_id):
        users_command(message)

    elif text == "📊 Статистика" and is_admin(user_id):
        adminstats_command(message)

    elif text == "➕ Добавить" and is_admin(user_id):
        user_states[user_id] = 'waiting_add_id'
        bot.send_message(
            message.chat.id,
            "➕ Введите ID пользователя для добавления:\n\n"
            "_(Пользователь может узнать свой ID у @userinfobot)_",
            parse_mode='Markdown',
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
                types.KeyboardButton("❌ Отмена")
            )
        )

    elif text == "➖ Удалить" and is_admin(user_id):
        user_states[user_id] = 'waiting_remove_id'
        bot.send_message(
            message.chat.id,
            "➖ Введите ID пользователя для удаления:",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
                types.KeyboardButton("❌ Отмена")
            )
        )

    elif text == "🚫 Бан" and is_admin(user_id):
        user_states[user_id] = 'waiting_ban_id'
        bot.send_message(
            message.chat.id,
            "🚫 Введите ID пользователя для блокировки:",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
                types.KeyboardButton("❌ Отмена")
            )
        )

    elif text == "✅ Разбан" and is_admin(user_id):
        user_states[user_id] = 'waiting_unban_id'
        bot.send_message(
            message.chat.id,
            "✅ Введите ID пользователя для разблокировки:",
            reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(
                types.KeyboardButton("❌ Отмена")
            )
        )

    elif text == "🔙 Главное меню":
        start_handler(message)

    else:
        if check_access(message):
            bot.send_message(
                message.chat.id,
                "📸 Отправьте фото SIM-карты",
                reply_markup=get_main_keyboard()
            )


def handle_state(message):
    """Обработка диалоговых состояний"""
    user_id = message.from_user.id
    text = message.text
    state = user_states.get(user_id)

    # Отмена
    if text == "❌ Отмена":
        del user_states[user_id]
        bot.send_message(
            message.chat.id,
            "❌ Отменено",
            reply_markup=get_admin_keyboard()
        )
        return

    # Добавление пользователя
    if state == 'waiting_add_id':
        try:
            new_user_id = int(text.strip())
            add_user(new_user_id, added_by=user_id)
            del user_states[user_id]

            bot.send_message(
                message.chat.id,
                f"✅ Пользователь `{new_user_id}` добавлен!",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )

            # Уведомляем пользователя
            try:
                bot.send_message(
                    new_user_id,
                    "✅ Вам выдан доступ к боту!\n"
                    "Нажмите /start для начала работы."
                )
            except:
                bot.send_message(
                    message.chat.id,
                    "⚠️ Пользователь не найден в Telegram или не запускал бота"
                )

        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите числовой ID")

    # Удаление пользователя
    elif state == 'waiting_remove_id':
        try:
            rem_user_id = int(text.strip())
            if remove_user(rem_user_id):
                bot.send_message(
                    message.chat.id,
                    f"✅ Пользователь `{rem_user_id}` удалён",
                    parse_mode='Markdown',
                    reply_markup=get_admin_keyboard()
                )
            else:
                bot.send_message(
                    message.chat.id,
                    f"❌ Пользователь `{rem_user_id}` не найден",
                    parse_mode='Markdown'
                )
            del user_states[user_id]
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите числовой ID")

    # Бан
    elif state == 'waiting_ban_id':
        try:
            ban_user_id = int(text.strip())
            ban_user(ban_user_id)
            del user_states[user_id]
            bot.send_message(
                message.chat.id,
                f"🚫 Пользователь `{ban_user_id}` заблокирован",
                parse_mode='Markdown',
                reply_markup=get_admin_keyboard()
            )
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите числовой ID")

    # Разбан
    elif state == 'waiting_unban_id':
        try:
            unban_user_id = int(text.strip())
            if unban_user(unban_user_id):
                bot.send_message(
                    message.chat.id,
                    f"✅ Пользователь `{unban_user_id}` разблокирован",
                    parse_mode='Markdown',
                    reply_markup=get_admin_keyboard()
                )
            else:
                bot.send_message(
                    message.chat.id,
                    f"❌ Пользователь `{unban_user_id}` не в бане",
                    parse_mode='Markdown'
                )
            del user_states[user_id]
        except ValueError:
            bot.send_message(message.chat.id, "❌ Введите числовой ID")


# ===== ОБРАБОТКА ФОТО =====

def download_file_safe(file_id):
    for attempt in range(3):
        try:
            file_info = bot.get_file(file_id)
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            response = requests.get(file_url, timeout=60)
            response.raise_for_status()
            return response.content, None
        except Exception as e:
            logger.warning(f"Попытка {attempt + 1}/3: {e}")
            if attempt < 2:
                time.sleep(3)
            else:
                return None, str(e)


def handle_image(message, file_id):
    if not check_access(message):
        return

    user_id = message.from_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    processing_msg = bot.send_message(message.chat.id, "⏳ Распознаю данные...")

    try:
        image_bytes, error = download_file_safe(file_id)

        if error:
            bot.delete_message(message.chat.id, processing_msg.message_id)
            bot.send_message(message.chat.id, f"❌ Ошибка скачивания: {error}")
            return

        results, raw_text, error = process_image(image_bytes, GOOGLE_VISION_API_KEY)

        bot.delete_message(message.chat.id, processing_msg.message_id)

        if error:
            bot.send_message(message.chat.id, f"❌ Ошибка OCR: {error}")
            return

        if results:
            user_sessions[user_id].extend(results)

        result_text = format_results(results)
        bot.send_message(
            message.chat.id,
            result_text,
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )

        total = len(user_sessions[user_id])
        if total > 0 and total % 10 == 0:
            bot.send_message(
                message.chat.id,
                f"💾 Накоплено *{total}* карт. Нажмите *Экспорт CSV*.",
                parse_mode='Markdown'
            )

        logger.info(f"User {user_id}: найдено {len(results)}, всего {total}")

    except Exception as e:
        logger.error(f"Ошибка user {user_id}: {e}")
        try:
            bot.delete_message(message.chat.id, processing_msg.message_id)
        except:
            pass
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")


@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    handle_image(message, message.photo[-1].file_id)


@bot.message_handler(content_types=['document'])
def document_handler(message):
    if not message.document.mime_type or not message.document.mime_type.startswith('image/'):
        bot.send_message(message.chat.id, "❌ Отправьте изображение")
        return
    handle_image(message, message.document.file_id)


# ===== ЗАПУСК =====

def start_bot():
    while True:
        try:
            logger.info("Запуск polling...")
            bot.remove_webhook()
            time.sleep(1)
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=25,
                skip_pending=True,
                allowed_updates=['message']
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(10)


if __name__ == '__main__':
    logger.info("Бот запущен")
    print("🤖 Бот запущен...")
    start_bot()
