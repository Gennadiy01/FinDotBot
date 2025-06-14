import logging
import datetime
import os
import tempfile
import subprocess
import async def family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сімейний бюджет з детальною розбивкою"""
    expenses = get_all_expenses()
    
    # Статистика за тиждень
    week_expenses = filter_expenses_by_period(expenses, "week")
    week_total = sum(exp['amount'] for exp in week_expenses)
    
    # Статистика за місяць
    month_expenses = filter_expenses_by_period(expenses, "month")
    month_total = sum(exp['amount'] for exp in month_expenses)
    
    if not month_expenses:
        await update.message.reply_text("Немає витрат за поточний місяць.")
        return
    
    # По користувачах за місяць
    users_month = {}
    for exp in month_expenses:
        user = exp['user']
        users_month[user] = users_month.get(user, 0) + exp['amount']
    
    # По категоріях за місяць
    categories_month = {}
    for exp in month_expenses:
        category = exp['category']
        categories_month[category] = categories_month.get(category, 0) + exp['amount']
    
    # Формуємо звіт
    message = "💼 Сімейний бюджет:\n\n"
    
    message += f"📅 За тиждень: {week_total:.2f} грн\n"
    message += f"📅 За місяць: {month_total:.2f} грн\n"
    
    if week_total > 0:
        projected_month = (week_total / 7) * 30
        message += f"📈 Прогноз на місяць: {projected_month:.2f} грн\n"
    
    message += "\n👥 Розподіл по сім'ї:\n"
    for user, amount in sorted(users_month.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / month_total) * 100
        message += f"• {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    message += "\n📂 Основні категорії:\n"
    for category, amount in sorted(categories_month.items(), key=lambda x: x[1], reverse=True)[:5]:
        percentage = (amount / month_total) * 100
        message += f"• {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    await update.message.reply_text(message)

async def who_spent_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хто більше витратив за період"""
    # Отримуємо параметр періоду з команди
    period = "month"  # За замовчуванням місяць
    
    if context.args:
        period_arg = context.args[0].lower()
        if period_arg in ["today", "week", "month", "year"]:
            period = period_arg if period_arg != "today" else "day"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, period)
    
    if not filtered_expenses:
        period_names = {"day": "сьогодні", "week": "тиждень", "month": "місяць", "year": "рік"}
        await update.message.reply_text(f"Немає витрат за {period_names.get(period, period)}.")
        return
    
    # Рахуємо по користувачах
    users = {}
    for exp in filtered_expenses:
        user = exp['user']
        users[user] = users.get(user, 0) + exp['amount']
    
    if len(users) < 2:
        await update.message.reply_text("Потрібно мінімум 2 користувачі для порівняння.")
        return
    
    # Сортуємо користувачів
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    total = sum(users.values())
    
    period_names = {"day": "сьогодні", "week": "цього тижня", "month": "цього місяця", "year": "цього року"}
    period_name = period_names.get(period, period)
    
    message = f"🏆 Рейтинг витрат {period_name}:\n\n"
    
    for i, (user, amount) in enumerate(sorted_users, 1):
        percentage = (amount / total) * 100
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        message += f"{emoji} {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    # Додаємо різницю між першим і другим
    if len(sorted_users) >= 2:
        difference = sorted_users[0][1] - sorted_users[1][1]
        message += f"\n💸 Різниця: {difference:.2f} грн"
        
        if difference > 0:
            message += f"\n💡 {sorted_users[0][0]} витратив більше на {difference:.2f} грн"
    
    await update.message.reply_text(message)

async def set_family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановлення сімейного бюджету"""
    global family_budget_amount
    
    if not context.args:
        await update.message.reply_text(
            "💰 Встановіть сімейний бюджет:\n"
            "/budget 15000 - встановити бюджет 15000 грн на місяць\n"
            "/budget - подивитись поточний бюджет"
        )
        return
    
    try:
        budget_amount = float(context.args[0])
        family_budget_amount = budget_amount
        
        await update.message.reply_text(
            f"💰 Сімейний бюджет встановлено: {budget_amount:.2f} грн на місяць\n"
            f"💡 Використайте /budget_status для перевірки виконання бюджету"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Введіть коректну суму. Приклад: /budget 15000")

async def budget_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус виконання сімейного бюджету"""
    global family_budget_amount
    
    if family_budget_amount == 0:
        await update.message.reply_text(
            "❌ Бюджет не встановлено.\n"
            "Використайте /budget СУМА для встановлення бюджету."
        )
        return
    
    expenses = get_all_expenses()
    month_expenses = filter_expenses_by_period(expenses, "month")
    spent = sum(exp['amount'] for exp in month_expenses)
    
    remaining = family_budget_amount - spent
    percentage = (spent / family_budget_amount) * 100
    
    message = f"💰 Статус сімейного бюджету:\n\n"
    message += f"📊 Бюджет на місяць: {family_budget_amount:.2f} грн\n"
    message += f"💸 Витрачено: {spent:.2f} грн ({percentage:.1f}%)\n"
    
    if remaining > 0:
        message += f"✅ Залишилось: {remaining:.2f} грн\n"
        
        # Розрахунок денного бюджету
        import calendar
        now = datetime.datetime.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        days_passed = now.day
        days_remaining = days_in_month - days_passed
        
        if days_remaining > 0:
            daily_budget = remaining / days_remaining
            message += f"📅 Можна витрачати {daily_budget:.2f} грн на день\n"
    else:
        message += f"⚠️ Перевищення бюджету: {abs(remaining):.2f} грн\n"
    
    # Прогрес бар
    progress_length = 10
    filled_length = int(progress_length * percentage / 100)
    bar = "█" * filled_length + "░" * (progress_length - filled_length)
    message += f"\n📊 Прогрес: {bar} {percentage:.1f}%"
    
    await update.message.reply_text(message)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    voice = update.message.voice
    
    # Перевіряємо чи доступний FFmpeg
    if FFMPEG_PATH is None:
        await update.message.reply_text(
            "❌ Обробка голосових повідомлень недоступна.\n"
            "FFmpeg не встановлено. Використовуйте текстові повідомлення."
        )
        return
    
    # Перевіряємо тривалість голосового повідомлення
    if voice.duration > MAX_VOICE_DURATION:
        await update.message.reply_text(
            f"❌ Голосове повідомлення занадто довге. Максимальна тривалість: {MAX_VOICE_DURATION} секунд."
        )
        return
    
    # Відправляємо повідомлення про початок обробки
    processing_message = await update.message.reply_text("🎤 Обробляю голосове повідомлення...")
    
    try:
        # Завантажуємо голосове повідомлення
        file = await context.bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf_ogg:
            await file.download_to_drive(custom_path=tf_ogg.name)
            ogg_path = tf_ogg.name

        # Конвертуємо OGG у WAV
        wav_path = ogg_path.replace(".ogg", ".wav")
        
        try:
            subprocess.run([
                FFMPEG_PATH, "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            await processing_message.edit_text("❌ Помилка конвертації аудіо.")
            logger.error(f"ffmpeg error: {e}")
            os.unlink(ogg_path)
            return
        
        os.unlink(ogg_path)

        # Читаємо WAV файл
        with open(wav_path, "rb") as audio_file:
            content = audio_file.read()
        os.unlink(wav_path)

        # Налаштування для розпізнавання мови
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=SPEECH_LANGUAGE,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=False
        )

        # Розпізнаємо мову
        response = speech_client.recognize(config=config, audio=audio)
        
        if not response.results:
            await processing_message.edit_text("❌ Не вдалося розпізнати голосове повідомлення. Спробуйте говорити чіткіше.")
            return
        
        recognized_text = response.results[0].alternatives[0].transcript
        confidence = response.results[0].alternatives[0].confidence
        
        logger.info(f"Розпізнано: '{recognized_text}' (впевненість: {confidence:.2f})")
        
        # Видаляємо повідомлення про обробку
        await processing_message.delete()
        
        # Показуємо розпізнаний текст користувачу
        await update.message.reply_text(f"🎤 Розпізнано: \"{recognized_text}\"")
        
        # Обробляємо розпізнаний текст
        await process_and_save(recognized_text, user, update)
        
    except Exception as e:
        logger.error(f"Google Speech-to-Text error: {e}")
        await processing_message.edit_text("❌ Помилка при розпізнаванні голосу. Спробуйте пізніше.")

def parse_expense_text(text):
    """Розбирає текст витрати з підтримкою різних форматів"""
    # Видаляємо зайві пробіли та приводимо до нижнього регістру для аналізу
    text = text.strip()
    
    # Варіант 1: Категорія Сума Коментар
    parts = text.split(maxsplit=2)
    if len(parts) >= 2:
        category = parts[0]
        amount_str = parts[1]
        comment = parts[2] if len(parts) == 3 else ""
        
        # Спробуємо витягнути число з рядка
        amount_match = re.search(r'(\d+(?:[.,]\d+)?)', amount_str)
        if amount_match:
            amount_str = amount_match.group(1).replace(',', '.')
            try:
                amount = float(amount_str)
                return category, amount, comment
            except ValueError:
                pass
    
    return None, None, None

async def process_and_save(text, user, update):
    """Обробляє та зберігає витрату"""
    category, amount, comment = parse_expense_text(text)
    
    if category is None or amount is None:
        await update.message.reply_text(
            "❌ Невірний формат. Введи у форматі:\n"
            "Категорія Сума Коментар\n"
            "Приклад: Їжа 250 Обід"
        )
        return

    if amount <= 0:
        await update.message.reply_text("❌ Сума має бути більше нуля.")
        return

    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_name = user.username or user.first_name or "Unknown"

    values = [[date_str, category, amount, user_name, comment]]

    try:
        # Спочатку перевіримо доступ до таблиці
        logger.info(f"Спроба запису до таблиці {SPREADSHEET_ID}")
        
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME,
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()
        
        logger.info(f"Запис успішний: {result}")
        
        # Зберігаємо інформацію про останню дію користувача
        user_last_actions[user.id] = {
            'action': 'add',
            'date': date_str,
            'category': category,
            'amount': amount,
            'comment': comment,
            'row_range': result.get('updates', {}).get('updatedRange', ''),
            'timestamp': datetime.datetime.now()
        }
        
        success_message = (
            f"✅ Запис додано:\n"
            f"📂 Категорія: {category}\n"
            f"💰 Сума: {amount:.2f} грн\n"
            f"👤 Користувач: {user_name}"
        )
        if comment:
            success_message += f"\n💬 Коментар: {comment}"
        
        success_message += f"\n\n💡 Якщо помилились, використайте /undo для скасування"
            
        await update.message.reply_text(success_message)
        
    except Exception as e:
        logger.error(f"Детальна помилка при записі до Google Sheets: {e}")
        logger.error(f"Тип помилки: {type(e).__name__}")
        await update.message.reply_text("❌ Виникла помилка при записі даних. Перевірте доступ до таблиці.")

def test_sheets_access():
    """Тестує доступ до Google Sheets"""
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="A1:A1"
        ).execute()
        logger.info("✅ Доступ до Google Sheets працює")
        return True
    except Exception as e:
        logger.error(f"❌ Помилка доступу до Google Sheets: {e}")
        return False

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник помилок"""
    logger.error(f'Update {update} caused error {context.error}')

async def main():
    """Запускає бота"""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logger.error(f"Файл сервісного акаунту не знайдено: {SERVICE_ACCOUNT_FILE}")
        return
    
    # Тестуємо доступ до Google Sheets
    try:
        test_sheets_access()
    except Exception as e:
        logger.error(f"Не вдалося протестувати доступ до Google Sheets: {e}")
    
    app = ApplicationBuilder().token(TOKEN).build()

    # Додаємо обробники команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("today", today_stats))
    app.add_handler(CommandHandler("week", week_stats))
    app.add_handler(CommandHandler("month", month_stats))
    app.add_handler(CommandHandler("year", year_stats))
    app.add_handler(CommandHandler("mystats", my_stats))
    app.add_handler(CommandHandler("top", top_categories))
    
    # КОМАНДИ управління записами
    app.add_handler(CommandHandler("undo", undo_last))
    app.add_handler(CommandHandler("ignore", ignore_last))
    app.add_handler(CommandHandler("recent", recent_records))
    
    # НОВІ КОМАНДИ для пар
    app.add_handler(CommandHandler("compare", compare_users))
    app.add_handler(CommandHandler("family", family_budget))
    app.add_handler(CommandHandler("whospent", who_spent_more))
    app.add_handler(CommandHandler("budget", set_family_budget))
    app.add_handler(CommandHandler("budget_status", budget_status))
    
    # Обробники повідомлень та кнопок
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Додаємо обробник помилок
    app.add_error_handler(error_handler)

    logger.info("Бот запускається...")
    if FFMPEG_PATH:
        logger.info("Голосові повідомлення увімкнені")
    else:
        logger.warning("Голосові повідомлення вимкнені (FFmpeg не знайдено)")
    
    # ВИПРАВЛЕНИЙ ЗАПУСК - замість app.run_polling()
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        # Тримаємо бота живим
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Помилка запуску бота: {e}")
        raise
    finally:
        # Коректне зупинення
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.error(f"Помилка зупинки бота: {e}")io
import re
import platform
from datetime import timedelta

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.helpers import escape_markdown

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.cloud import speech

# Імпорт конфігурації
from config import (
    TOKEN, 
    SPREADSHEET_ID, 
    RANGE_NAME, 
    SERVICE_ACCOUNT_FILE,
    SPEECH_LANGUAGE,
    LOG_LEVEL,
    MAX_VOICE_DURATION
)

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# Глобальний словник для зберігання останніх дій користувачів
user_last_actions = {}

# Функція для знаходження FFmpeg
def get_ffmpeg_path():
    """Знаходить FFmpeg у системі або локальній папці"""
    # Спробуємо локальний FFmpeg
    local_ffmpeg = os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        logger.info(f"Використовую локальний FFmpeg: {local_ffmpeg}")
        return local_ffmpeg
    
    # Спробуємо системний FFmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, text=True)
        logger.info("Використовую системний FFmpeg")
        return "ffmpeg"
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("FFmpeg не знайдено")
        return None

# Глобальна змінна для шляху FFmpeg
FFMPEG_PATH = get_ffmpeg_path()

# Підключення до Google Sheets API
try:
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    logger.info("Google Sheets API підключено успішно")
except Exception as e:
    logger.error(f"Помилка підключення до Google Sheets API: {e}")
    raise

# Підключення до Google Speech-to-Text API
try:
    speech_client = speech.SpeechClient.from_service_account_file(SERVICE_ACCOUNT_FILE)
    logger.info("Google Speech-to-Text API підключено успішно")
except Exception as e:
    logger.error(f"Помилка підключення до Google Speech-to-Text API: {e}")
    raise

def get_all_expenses():
    """Отримує всі записи витрат з Google Sheets"""
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return []
        
        # Пропускаємо заголовок та фільтруємо валідні записи
        expenses = []
        for row in values[1:]:
            if len(row) >= 3:
                try:
                    date_str = row[0]
                    category = row[1]
                    amount = float(row[2])
                    user = row[3] if len(row) > 3 else "Unknown"
                    comment = row[4] if len(row) > 4 else ""
                    
                    # Парсимо дату
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    
                    expenses.append({
                        'date': date_obj,
                        'category': category,
                        'amount': amount,
                        'user': user,
                        'comment': comment
                    })
                except (ValueError, IndexError) as e:
                    logger.warning(f"Пропускаю невалідний запис: {row}, помилка: {e}")
                    continue
        
        return expenses
    except Exception as e:
        logger.error(f"Помилка отримання витрат: {e}")
        return []

def filter_expenses_by_period(expenses, period_type, user_filter=None, include_ignored=False):
    """Фільтрує витрати за періодом"""
    now = datetime.datetime.now()
    
    if period_type == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_type == "week":
        # Тиждень починається з понеділка
        days_since_monday = now.weekday()
        start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period_type == "month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period_type == "year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return expenses
    
    filtered = [exp for exp in expenses if exp['date'] >= start_date]
    
    # Фільтр по користувачу
    if user_filter:
        filtered = [exp for exp in filtered if exp['user'] == user_filter]
    
    # Виключаємо ігноровані записи (якщо не запитали їх включити)
    if not include_ignored:
        filtered = [exp for exp in filtered if not ('[IGNORED]' in exp.get('comment', ''))]
    
    return filtered

def generate_stats_message(expenses, period_name, user_filter=None):
    """Генерує повідомлення зі статистикою"""
    if not expenses:
        return f"Немає витрат за {period_name.lower()}."
    
    # Загальна сума
    total = sum(exp['amount'] for exp in expenses)
    
    # Статистика по категоріях
    categories = {}
    for exp in expenses:
        category = exp['category']
        categories[category] = categories.get(category, 0) + exp['amount']
    
    # Статистика по користувачах
    users = {}
    for exp in expenses:
        user = exp['user']
        users[user] = users.get(user, 0) + exp['amount']
    
    # Формуємо повідомлення
    message = f"📊 Статистика за {period_name}"
    if user_filter:
        message += f" (користувач: {user_filter})"
    message += ":\n\n"
    
    message += f"💰 Загальна сума: {total:.2f} грн\n"
    message += f"📝 Кількість записів: {len(expenses)}\n"
    message += f"📅 Середня витрата: {total/len(expenses):.2f} грн\n\n"
    
    # По категоріях
    message += "📂 По категоріях:\n"
    for category, amount in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        message += f"• {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    # По користувачах (якщо не фільтрується по одному)
    if not user_filter and len(users) > 1:
        message += "\n👤 По користувачах:\n"
        for user, amount in sorted(users.items(), key=lambda x: x[1], reverse=True):
            percentage = (amount / total) * 100
            message += f"• {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    return message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ffmpeg_status = "✅ Доступно" if FFMPEG_PATH else "❌ Не встановлено"
    
    welcome_message = (
        "🤖 Привіт! Я допоможу вести сімейний бюджет.\n\n"
        "📝 Для запису надішли повідомлення у форматі:\n"
        "Категорія Сума Коментар\n"
        "Приклад: Їжа 250 Обід у ресторані\n\n"
        f"🎤 Голосові повідомлення: {ffmpeg_status}\n\n"
        "Використовуйте кнопки нижче або команди:"
    )
    
    # Створюємо клавіатуру з кнопками
    keyboard = [
        [KeyboardButton("📊 Моя статистика"), KeyboardButton("📅 За сьогодні")],
        [KeyboardButton("📈 За тиждень"), KeyboardButton("📆 За місяць")],
        [KeyboardButton("👫 Сімейний бюджет"), KeyboardButton("🏆 Топ категорій")],
        [KeyboardButton("📝 Мої записи"), KeyboardButton("⚙️ Управління")],
        [KeyboardButton("ℹ️ Довідка")]
    ]
    
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,  # Автоматично підганяє розмір
        one_time_keyboard=False  # Клавіатура залишається видимою
    )
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує повну довідку"""
    help_text = (
        "📖 Повна довідка по боту:\n\n"
        "📝 **Запис витрат:**\n"
        "Їжа 250 Обід - текстом\n"
        "🎤 Голосове повідомлення\n\n"
        "📊 **Статистика:**\n"
        "/today - за сьогодні\n"
        "/week - за тиждень\n"
        "/month - за місяць\n"
        "/mystats - особиста\n\n"
        "👫 **Сімейні функції:**\n"
        "/family - сімейний бюджет\n"
        "/compare - порівняння\n"
        "/whospent - рейтинг витрат\n\n"
        "🛠️ **Управління:**\n"
        "/undo - скасувати останній\n"
        "/ignore - ігнорувати\n"
        "/recent - останні записи\n\n"
        "💰 **Бюджет:**\n"
        "/budget 15000 - встановити\n"
        "/budget_status - статус"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def show_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує меню управління записами"""
    
    # Inline кнопки для управління
    keyboard = [
        [InlineKeyboardButton("🔄 Скасувати останній", callback_data='undo')],
        [InlineKeyboardButton("🔕 Ігнорувати останній", callback_data='ignore')],
        [InlineKeyboardButton("👥 Порівняти користувачів", callback_data='compare')],
        [InlineKeyboardButton("💰 Статус бюджету", callback_data='budget_status')],
        [InlineKeyboardButton("🏅 Хто більше витратив", callback_data='whospent')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ Оберіть дію:",
        reply_markup=reply_markup
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання inline кнопок"""
    query = update.callback_query
    await query.answer()  # Підтверджуємо натискання
    
    # Створюємо fake update для сумісності з існуючими функціями
    fake_update = Update(
        update_id=update.update_id,
        message=query.message
    )
    
    if query.data == 'undo':
        await undo_last_action(fake_update, context)
    elif query.data == 'ignore':
        await mark_as_ignored(fake_update, context)
    elif query.data == 'compare':
        await compare_users(fake_update, context)
    elif query.data == 'budget_status':
        await budget_status(fake_update, context)
    elif query.data == 'whospent':
        await who_spent_more(fake_update, context)

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    text = update.message.text
    
    # Перевіряємо, чи це кнопка або звичайний текст
    if text == "📊 Моя статистика":
        await my_stats(update, context)
    elif text == "📅 За сьогодні":
        await today_stats(update, context)
    elif text == "📈 За тиждень":
        await week_stats(update, context)
    elif text == "📆 За місяць":
        await month_stats(update, context)
    elif text == "👫 Сімейний бюджет":
        await family_budget(update, context)
    elif text == "🏆 Топ категорій":
        await top_categories(update, context)
    elif text == "📝 Мої записи":
        await recent_records(update, context)
    elif text == "⚙️ Управління":
        await show_management_menu(update, context)
    elif text == "ℹ️ Довідка":
        await help_command(update, context)
    else:
        # Якщо це не кнопка, обробляємо як витрату
        await process_and_save(text, update.message.from_user, update)

async def today_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сьогодні"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "day")
    message = generate_stats_message(filtered_expenses, "сьогодні")
    await update.message.reply_text(message)

async def week_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за тиждень"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "week")
    message = generate_stats_message(filtered_expenses, "поточний тиждень")
    await update.message.reply_text(message)

async def month_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    message = generate_stats_message(filtered_expenses, "поточний місяць")
    await update.message.reply_text(message)

async def year_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за рік"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "year")
    message = generate_stats_message(filtered_expenses, "поточний рік")
    await update.message.reply_text(message)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Особиста статистика користувача за місяць"""
    user = update.message.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month", user_name)
    message = generate_stats_message(filtered_expenses, "поточний місяць", user_name)
    await update.message.reply_text(message)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стара функція статистики - тепер перенаправляє на month_stats"""
    await month_stats(update, context)

async def top_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Топ категорій за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        await update.message.reply_text("Немає витрат за поточний місяць.")
        return
    
    # Рахуємо по категоріях
    categories = {}
    for exp in filtered_expenses:
        category = exp['category']
        categories[category] = categories.get(category, 0) + exp['amount']
    
    total = sum(categories.values())
    
    message = "🏆 Топ категорій за місяць:\n\n"
    for i, (category, amount) in enumerate(sorted(categories.items(), key=lambda x: x[1], reverse=True), 1):
        percentage = (amount / total) * 100
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        message += f"{emoji} {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    await update.message.reply_text(message)

async def undo_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасовує останню дію користувача"""
    user = update.message.from_user
    
    if user.id not in user_last_actions:
        await update.message.reply_text("❌ Немає дій для скасування.")
        return
    
    last_action = user_last_actions[user.id]
    
    # Перевіряємо, чи не застара дія (більше 10 хвилин)
    if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
        await update.message.reply_text("❌ Час для скасування минув (максимум 10 хвилин).")
        return
    
    try:
        # Отримуємо всі записи
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await update.message.reply_text("❌ Таблиця порожня.")
            return
        
        # Шукаємо запис для видалення
        user_name = user.username or user.first_name or "Unknown"
        row_to_delete = None
        
        for i, row in enumerate(values):
            if len(row) >= 4:
                if (row[0] == last_action['date'] and 
                    row[1] == last_action['category'] and 
                    float(row[2]) == last_action['amount'] and
                    row[3] == user_name):
                    row_to_delete = i + 1  # +1 тому що Google Sheets починає з 1
                    break
        
        if row_to_delete is None:
            await update.message.reply_text("❌ Запис не знайдено для скасування.")
            return
        
        # Видаляємо рядок
        requests = [{
            'deleteDimension': {
                'range': {
                    'sheetId': 0,  # Перший аркуш
                    'dimension': 'ROWS',
                    'startIndex': row_to_delete - 1,  # 0-based index
                    'endIndex': row_to_delete
                }
            }
        }]
        
        sheet.batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': requests}
        ).execute()
        
        # Видаляємо з кешу
        del user_last_actions[user.id]
        
        await update.message.reply_text(
            f"✅ Запис скасовано:\n"
            f"📂 Категорія: {last_action['category']}\n"
            f"💰 Сума: {last_action['amount']:.2f} грн"
        )
        
    except Exception as e:
        logger.error(f"Помилка скасування: {e}")
        await update.message.reply_text("❌ Помилка при скасуванні запису.")

async def ignore_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Позначає останній запис як ігнорований для статистики"""
    user = update.message.from_user
    
    if user.id not in user_last_actions:
        await update.message.reply_text("❌ Немає дій для позначення.")
        return
    
    last_action = user_last_actions[user.id]
    
    # Перевіряємо, чи не застара дія
    if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
        await update.message.reply_text("❌ Час для позначення минув (максимум 10 хвилин).")
        return
    
    try:
        # Отримуємо всі записи
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await update.message.reply_text("❌ Таблиця порожня.")
            return
        
        # Шукаємо запис для позначення
        user_name = user.username or user.first_name or "Unknown"
        row_to_update = None
        
        for i, row in enumerate(values):
            if len(row) >= 4:
                if (row[0] == last_action['date'] and 
                    row[1] == last_action['category'] and 
                    float(row[2]) == last_action['amount'] and
                    row[3] == user_name):
                    row_to_update = i + 1  # +1 тому що Google Sheets починає з 1
                    break
        
        if row_to_update is None:
            await update.message.reply_text("❌ Запис не знайдено для позначення.")
            return
        
        # Додаємо префікс [IGNORED] до коментаря
        current_comment = last_action.get('comment', '')
        new_comment = f"[IGNORED] {current_comment}".strip()
        
        # Оновлюємо коментар
        range_to_update = f"'Аркуш1'!E{row_to_update}"
        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=range_to_update,
            valueInputOption='USER_ENTERED',
            body={'values': [[new_comment]]}
        ).execute()
        
        # Видаляємо з кешу
        del user_last_actions[user.id]
        
        await update.message.reply_text(
            f"🔕 Запис позначено як ігнорований:\n"
            f"📂 Категорія: {last_action['category']}\n"
            f"💰 Сума: {last_action['amount']:.2f} грн\n"
            f"💡 Він не буде враховуватись у статистиці"
        )
        
    except Exception as e:
        logger.error(f"Помилка позначення: {e}")
        await update.message.reply_text("❌ Помилка при позначенні запису.")

async def recent_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує останні 5 записів користувача"""
    user = update.message.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    try:
        # Отримуємо всі записи
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await update.message.reply_text("❌ Немає записів.")
            return
        
        # Фільтруємо записи користувача
        user_expenses = []
        for i, row in enumerate(values[1:], 2):  # Починаємо з 2-го рядка
            if len(row) >= 4 and row[3] == user_name:
                try:
                    date_obj = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                    user_expenses.append({
                        'row': i,
                        'date': date_obj,
                        'category': row[1],
                        'amount': float(row[2]),
                        'comment': row[4] if len(row) > 4 else "",
                        'is_ignored': len(row) > 4 and '[IGNORED]' in row[4]
                    })
                except (ValueError, IndexError):
                    continue
        
        if not user_expenses:
            await update.message.reply_text("❌ У вас немає записів.")
            return
        
        # Сортуємо за датою (найновіші спочатку) і беремо останні 5
        user_expenses.sort(key=lambda x: x['date'], reverse=True)
        recent_expenses = user_expenses[:5]
        
        message = "📝 Ваші останні записи:\n\n"
        for i, exp in enumerate(recent_expenses, 1):
            ignored_mark = "🔕 " if exp['is_ignored'] else ""
            message += f"{i}. {ignored_mark}{exp['category']}: {exp['amount']:.2f} грн"
            if exp['comment'] and not exp['is_ignored']:
                message += f" ({exp['comment']})"
            message += f"\n   📅 {exp['date'].strftime('%d.%m %H:%M')}\n\n"
        
        message += "💡 Використайте /undo для скасування останньої дії\n"
        message += "💡 Використайте /ignore для позначення як ігнорований"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Помилка отримання записів: {e}")
        await update.message.reply_text("❌ Помилка при отриманні записів.")

# Глобальна змінна для сімейного бюджету
family_budget_amount = 0

async def compare_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Порівняння витрат між користувачами за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        await update.message.reply_text("Немає витрат за поточний місяць.")
        return
    
    # Збираємо статистику по користувачах
    users_stats = {}
    total_amount = 0
    
    for exp in filtered_expenses:
        user = exp['user']
        if user not in users_stats:
            users_stats[user] = {
                'total': 0,
                'count': 0,
                'categories': {}
            }
        
        users_stats[user]['total'] += exp['amount']
        users_stats[user]['count'] += 1
        total_amount += exp['amount']
        
        category = exp['category']
        if category not in users_stats[user]['categories']:
            users_stats[user]['categories'][category] = 0
        users_stats[user]['categories'][category] += exp['amount']
    
    # Формуємо повідомлення
    message = "👫 Порівняння витрат за місяць:\n\n"
    message += f"💰 Загальний бюджет сім'ї: {total_amount:.2f} грн\n\n"
    
    # Сортуємо користувачів за сумою витрат
    sorted_users = sorted(users_stats.items(), key=lambda x: x[1]['total'], reverse=True)
    
    for i, (user, stats) in enumerate(sorted_users, 1):
        percentage = (stats['total'] / total_amount) * 100
        avg_expense = stats['total'] / stats['count']
        
        message += f"{i}. 👤 {user}:\n"
        message += f"   💰 {stats['total']:.2f} грн ({percentage:.1f}%)\n"
        message += f"   📝 {stats['count']} записів\n"
        message += f"   📊 Середня витрата: {avg_expense:.2f} грн\n"
        
        # Топ-3 категорії користувача
        top_categories = sorted(stats['categories'].items(), key=lambda x: x[1], reverse=True)[:3]
        message += "   🏆 Топ категорії: "
        message += ", ".join([f"{cat} ({amt:.0f}₴)" for cat, amt in top_categories])
        message += "\n\n"
    
    await update.message.reply_text(message)

async