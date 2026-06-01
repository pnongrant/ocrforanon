import telebot
from telebot import types
import requests
import io
import logging
from config import BOT_TOKEN, GOOGLE_VISION_API_KEY
from ocr_processor import process_image, format_results, format_csv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище сессий {user_id: [{'iccid': ..., 'puk': ...}]}
user_sessions = {}


def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📊 Экспорт CSV"))
    markup.add(types.KeyboardButton("🗑 Очистить"), types.KeyboardButton("ℹ️ Помощь"))
    return markup


@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    user_sessions[user_id] = []

    text = (
        "👋 *Привет! Я бот для распознавания SIM-карт.*\n\n"
        "📸 Отправьте фото SIM-карт, и я автоматически извлеку:\n"
        "• ICCID номер\n"
        "• PUK код\n\n"
        "💡 *Советы для лучшего результата:*\n"
        "• Фото должно быть чётким\n"
        "• Хорошее освещение\n"
        "• Можно отправлять несколько карт на одном фото\n\n"
        "📤 Просто отправьте фото!"
    )

    bot.send_message(
        message.chat.id,
        text,
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )


@bot.message_handler(commands=['help'])
def help_handler(message):
    text = (
        "📖 *Инструкция:*\n\n"
        "1️⃣ Отправьте фото SIM-карты\n"
        "2️⃣ Бот распознает ICCID и PUK\n"
        "3️⃣ Нажмите *Экспорт CSV* для сохранения всех данных\n"
        "4️⃣ Нажмите *Очистить* для сброса сессии\n\n"
        "📋 *Команды:*\n"
        "/start - Начало работы\n"
        "/export - Экспорт в CSV\n"
        "/clear - Очистить историю\n"
        "/stats - Статистика сессии"
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['export'])
def export_handler(message):
    user_id = message.from_user.id

    if user_id not in user_sessions or not user_sessions[user_id]:
        bot.send_message(message.chat.id, "❌ Нет данных для экспорта. Отправьте фото карт.")
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
        bot.send_message(message.chat.id, "❌ Ошибка при создании CSV")


@bot.message_handler(commands=['clear'])
def clear_handler(message):
    user_id = message.from_user.id
    count = len(user_sessions.get(user_id, []))
    user_sessions[user_id] = []
    bot.send_message(message.chat.id, f"🗑 Очищено. Удалено записей: {count}")


@bot.message_handler(commands=['stats'])
def stats_handler(message):
    user_id = message.from_user.id
    cards = user_sessions.get(user_id, [])
    bot.send_message(
        message.chat.id,
        f"📊 *Статистика сессии:*\n\nВсего карт: {len(cards)}",
        parse_mode='Markdown'
    )


@bot.message_handler(content_types=['text'])
def text_handler(message):
    text = message.text

    if text == "📊 Экспорт CSV":
        export_handler(message)
    elif text == "🗑 Очистить":
        clear_handler(message)
    elif text == "ℹ️ Помощь":
        help_handler(message)
    else:
        bot.send_message(
            message.chat.id,
            "📸 Отправьте фото SIM-карты для распознавания",
            reply_markup=get_main_keyboard()
        )


def handle_image(message, image_bytes):
    """Общая функция обработки изображения"""
    user_id = message.from_user.id

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    processing_msg = bot.send_message(message.chat.id, "⏳ Распознаю данные...")

    try:
        results, raw_text, error = process_image(image_bytes, GOOGLE_VISION_API_KEY)

        bot.delete_message(message.chat.id, processing_msg.message_id)

        if error:
            bot.send_message(
                message.chat.id,
                f"❌ Ошибка: {error}",
                reply_markup=get_main_keyboard()
            )
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
                f"💾 В сессии накоплено *{total}* карт. Нажмите *Экспорт CSV* для сохранения.",
                parse_mode='Markdown'
            )

        logger.info(f"User {user_id}: найдено {len(results)} карт, всего в сессии {len(user_sessions[user_id])}")

    except Exception as e:
        logger.error(f"Ошибка обработки для user {user_id}: {e}")
        bot.delete_message(message.chat.id, processing_msg.message_id)
        bot.send_message(
            message.chat.id,
            f"❌ Ошибка при обработке: {str(e)}\nПопробуйте ещё раз.",
            reply_markup=get_main_keyboard()
        )


@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    """Обработка фото (со сжатием Telegram)"""
    try:
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response = requests.get(file_url, timeout=30)
        image_bytes = response.content
        handle_image(message, image_bytes)
    except Exception as e:
        logger.error(f"Ошибка получения фото: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка получения фото: {str(e)}")


@bot.message_handler(content_types=['document'])
def document_handler(message):
    """Обработка документов (фото без сжатия — лучшее качество)"""
    if not message.document.mime_type or not message.document.mime_type.startswith('image/'):
        bot.send_message(message.chat.id, "❌ Отправьте изображение")
        return

    try:
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response = requests.get(file_url, timeout=30)
        image_bytes = response.content
        handle_image(message, image_bytes)
    except Exception as e:
        logger.error(f"Ошибка получения документа: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")


if __name__ == '__main__':
    logger.info("Бот запущен")
    print("🤖 Бот запущен... Нажмите Ctrl+C для остановки")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
