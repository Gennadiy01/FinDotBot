import logging
import os
from datetime import datetime, timedelta
import asyncio
import tempfile
import subprocess
from typing import Optional, Dict, Any
import re

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from google.cloud import speech
from googleapiclient.discovery import build
from google.oauth2 import service_account
import config

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=getattr(logging, config.LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger(__name__)

# Глобальні змінні
family_budget_amount = 0
user_last_actions = {}

class FinDotBot:
    def __init__(self):
        self.sheets_service = None
        self.speech_client = None
        self.setup_google_services()
        
    def setup_google_services(self):
        """Налаштування Google API сервісів"""
        try:
            credentials = service_account.Credentials.from_service_account_file(
                config.SERVICE_ACCOUNT_FILE,
                scopes=['https://www.googleapis.com/auth/spreadsheets',
                       'https://www.googleapis.com/auth/cloud-platform']
            )
            
            self.sheets_service = build('sheets', 'v4', credentials=credentials)
            self.speech_client = speech.SpeechClient(credentials=credentials)
            
            logger.info("Google API сервіси успішно налаштовані")
            
        except Exception as e:
            logger.error(f"Помилка налаштування Google API: {e}")
            raise

    def get_user_identifier(self, user) -> str:
        """Отримання ідентифікатора користувача"""
        if user.username:
            return user.username
        return user.first_name or str(user.id)

    async def save_expense(self, category: str, amount: float, user_identifier: str, comment: str = "") -> bool:
        """Збереження витрати в Google Sheets"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            values = [[current_time, category, amount, user_identifier, comment]]
            
            body = {'values': values}
            
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=config.SPREADSHEET_ID,
                range=config.RANGE_NAME,
                valueInputOption='RAW',
                body=body
            ).execute()
            
            logger.info(f"Збережено витрату: {category} {amount} для {user_identifier}")
            return True
            
        except Exception as e:
            logger.error(f"Помилка збереження витрати: {e}")
            return False

    async def get_expenses_data(self, user_filter: str = None, period_filter: str = None) -> list:
        """Отримання даних про витрати з фільтрацією"""
        try:
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=config.SPREADSHEET_ID,
                range=config.RANGE_NAME
            ).execute()
            
            values = result.get('values', [])
            if not values or len(values) < 2:
                return []
            
            # Пропускаємо заголовки
            data = values[1:]
            filtered_data = []
            
            for row in data:
                if len(row) < 4:
                    continue
                    
                # Пропускаємо ігноровані записи
                if len(row) > 4 and '[IGNORED]' in str(row[4]):
                    continue
                
                # Фільтр по користувачу
                if user_filter and row[3] != user_filter:
                    continue
                
                # Фільтр по періоду
                if period_filter:
                    try:
                        record_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                        now = datetime.now()
                        
                        if period_filter == 'today':
                            if record_date.date() != now.date():
                                continue
                        elif period_filter == 'week':
                            start_of_week = now - timedelta(days=now.weekday())
                            if record_date.date() < start_of_week.date():
                                continue
                        elif period_filter == 'month':
                            if record_date.year != now.year or record_date.month != now.month:
                                continue
                        elif period_filter == 'year':
                            if record_date.year != now.year:
                                continue
                                
                    except ValueError:
                        continue
                
                filtered_data.append(row)
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"Помилка отримання даних: {e}")
            return []

    async def convert_ogg_to_wav(self, ogg_path: str) -> Optional[str]:
        """Конвертація OGG в WAV для Google Speech API"""
        try:
            wav_path = ogg_path.replace('.ogg', '.wav')
            
            command = [
                'ffmpeg', '-i', ogg_path,
                '-acodec', 'pcm_s16le',
                '-ar', '16000',
                '-ac', '1',
                '-y', wav_path
            ]
            
            result = subprocess.run(command, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("Аудіо файл успішно конвертовано")
                return wav_path
            else:
                logger.error(f"Помилка конвертації FFmpeg: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Помилка конвертації аудіо: {e}")
            return None

    async def recognize_speech(self, audio_path: str) -> Optional[str]:
        """Розпізнавання мови з аудіо файлу"""
        try:
            with open(audio_path, 'rb') as audio_file:
                content = audio_file.read()
            
            audio = speech.RecognitionAudio(content=content)
            config_speech = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=config.SPEECH_LANGUAGE,
                enable_automatic_punctuation=True,
            )
            
            response = self.speech_client.recognize(config=config_speech, audio=audio)
            
            if response.results:
                transcript = response.results[0].alternatives[0].transcript
                confidence = response.results[0].alternatives[0].confidence
                logger.info(f"Розпізнано текст: '{transcript}' (впевненість: {confidence:.2f})")
                return transcript
            else:
                logger.warning("Не вдалося розпізнати мову")
                return None
                
        except Exception as e:
            logger.error(f"Помилка розпізнавання мови: {e}")
            return None

bot = FinDotBot()

# Створення клавіатур
def get_main_keyboard():
    """Головна клавіатура з основними кнопками"""
    keyboard = [
        [KeyboardButton("📊 Моя статистика"), KeyboardButton("📅 За сьогодні")],
        [KeyboardButton("📈 За тиждень"), KeyboardButton("📆 За місяць")],
        [KeyboardButton("👫 Сімейний бюджет"), KeyboardButton("🏆 Топ категорій")],
        [KeyboardButton("📝 Мої записи"), KeyboardButton("⚙️ Управління")],
        [KeyboardButton("ℹ️ Довідка")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_management_keyboard():
    """Inline клавіатура для управління"""
    keyboard = [
        [InlineKeyboardButton("🔄 Скасувати останній", callback_data="undo")],
        [InlineKeyboardButton("🔕 Ігнорувати останній", callback_data="ignore")],
        [InlineKeyboardButton("👥 Порівняти користувачів", callback_data="compare")],
        [InlineKeyboardButton("💰 Статус бюджету", callback_data="budget_status")],
        [InlineKeyboardButton("🏅 Хто більше витратив", callback_data="whospent")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Обробники команд та кнопок
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start з клавіатурою"""
    welcome_text = """
🤖 **Привіт! Я FinDotBot** - ваш особистий помічник для відстеження витрат!

💡 **Як користуватися:**

📝 **Записати витрату:**
• Текстом: `Категорія Сума [Коментар]`
• Приклад: `Їжа 250 Обід у ресторані`
• Голосом: Запишіть голосове повідомлення у тому ж форматі

📊 **Переглянути статистику:**
• Використовуйте кнопки нижче для швидкого доступу до всіх функцій

⚙️ **Управління записами:**
• Скасувати, ігнорувати записи
• Порівнювати витрати між користувачами

🎯 **Готовий почати? Виберіть дію з меню або просто напишіть свою витрату!**
"""
    
    await update.message.reply_text(
        welcome_text, 
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда допомоги"""
    help_text = """
ℹ️ **Довідка FinDotBot**

📝 **Формат запису витрат:**
• `Категорія Сума` - базовий формат
• `Категорія Сума Коментар` - з коментарем
• Приклади:
  - `Їжа 150`
  - `Транспорт 50 Таксі додому`
  - `Розваги 300 Кіно з друзями`

🎤 **Голосові команди:**
• Запишіть голосове повідомлення у тому ж форматі
• Говоріть чітко українською мовою
• Максимум 60 секунд

📊 **Статистика:**
• **За сьогодні** - витрати за поточний день
• **За тиждень** - з понеділка поточного тижня
• **За місяць** - поточний місяць
• **Моя статистика** - особиста статистика за місяць
• **Топ категорій** - найбільші витрати за місяць

👫 **Для пар/сімей:**
• **Сімейний бюджет** - загальна статистика всіх користувачів
• **Порівняти користувачів** - детальне порівняння витрат
• **Хто більше витратив** - рейтинг витрат за період

⚙️ **Управління:**
• **Скасувати останній** - видалити останній запис (до 10 хв)
• **Ігнорувати останній** - приховати запис зі статистики
• **Мої записи** - показати останні 5 записів

💡 **Поради:**
• Використовуйте короткі назви категорій
• Коментарі допомагають згадати деталі витрат
• Перевіряйте статистику регулярно для контролю бюджету
"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка натискань кнопок та текстових повідомлень"""
    text = update.message.text
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    # Обробка кнопок
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
        await show_management(update, context)
    elif text == "ℹ️ Довідка":
        await help_command(update, context)
    else:
        # Обробка запису витрат
        await handle_expense_text(update, context)

async def show_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати меню управління"""
    await update.message.reply_text(
        "⚙️ **Управління записами**\n\nВиберіть дію:",
        reply_markup=get_management_keyboard(),
        parse_mode='Markdown'
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка inline кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "undo":
        await undo_last_expense(update, context)
    elif query.data == "ignore":
        await ignore_last_expense(update, context)
    elif query.data == "compare":
        await compare_users(update, context)
    elif query.data == "budget_status":
        await budget_status(update, context)
    elif query.data == "whospent":
        await who_spent_more(update, context)

async def handle_expense_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстового запису витрат"""
    text = update.message.text.strip()
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    # Парсинг тексту: Категорія Сума [Коментар]
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Неправильний формат!\n\n"
            "📝 Використовуйте: `Категорія Сума [Коментар]`\n"
            "📝 Приклад: `Їжа 250 Обід у ресторані`",
            parse_mode='Markdown'
        )
        return
    
    category = parts[0].lower()
    
    try:
        amount = float(parts[1])
        if amount <= 0:
            raise ValueError("Сума повинна бути більше 0")
    except ValueError:
        await update.message.reply_text(
            "❌ Некоректна сума!\n\n"
            "💡 Сума повинна бути числом більше 0\n"
            "📝 Приклад: `Транспорт 50`"
        )
        return
    
    comment = ' '.join(parts[2:]) if len(parts) > 2 else ""
    
    # Збереження витрати
    success = await bot.save_expense(category, amount, user_identifier, comment)
    
    if success:
        # Збереження для можливості скасування
        user_last_actions[user_identifier] = {
            'type': 'expense',
            'category': category,
            'amount': amount,
            'comment': comment,
            'timestamp': datetime.now()
        }
        
        response = f"✅ **Витрату збережено!**\n\n"
        response += f"📂 Категорія: {category}\n"
        response += f"💰 Сума: {amount:.2f} грн\n"
        if comment:
            response += f"💬 Коментар: {comment}\n"
        response += f"👤 Користувач: {user_identifier}"
        
        await update.message.reply_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Виникла помилка при записі даних. Спробуйте ще раз.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка голосових повідомлень"""
    try:
        # Перевірка тривалості
        if update.message.voice.duration > config.MAX_VOICE_DURATION:
            await update.message.reply_text(
                f"❌ Голосове повідомлення занадто довге!\n\n"
                f"⏱️ Максимум: {config.MAX_VOICE_DURATION} секунд\n"
                f"📝 Ваше: {update.message.voice.duration} секунд"
            )
            return
        
        await update.message.reply_text("🎤 Обробляю голосове повідомлення...")
        
        # Завантаження файлу
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            ogg_path = os.path.join(temp_dir, "voice.ogg")
            await voice_file.download_to_drive(ogg_path)
            
            # Конвертація OGG в WAV
            wav_path = await bot.convert_ogg_to_wav(ogg_path)
            if not wav_path:
                await update.message.reply_text("❌ Помилка конвертації аудіо файлу")
                return
            
            # Розпізнавання мови
            recognized_text = await bot.recognize_speech(wav_path)
            if not recognized_text:
                await update.message.reply_text("❌ Не вдалося розпізнати голосове повідомлення")
                return
            
            # Створення fake update з розпізнаним текстом
            update.message.text = recognized_text
            await update.message.reply_text(f"🎤 Розпізнано: *{recognized_text}*", parse_mode='Markdown')
            
            # Обробка як звичайний текст
            await handle_expense_text(update, context)
            
    except Exception as e:
        logger.error(f"Помилка обробки голосу: {e}")
        await update.message.reply_text("❌ Виникла помилка при обробці голосового повідомлення")

# Функції статистики
async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Особиста статистика за місяць"""
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    data = await bot.get_expenses_data(user_filter=user_identifier, period_filter='month')
    
    if not data:
        await update.message.reply_text("📊 У вас поки немає записів за поточний місяць")
        return
    
    total_amount = sum(float(row[2]) for row in data)
    record_count = len(data)
    avg_expense = total_amount / record_count if record_count > 0 else 0
    
    # Групування по категоріях
    categories = {}
    for row in data:
        category = row[1]
        amount = float(row[2])
        categories[category] = categories.get(category, 0) + amount
    
    # Сортування категорій за сумою
    sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    
    response = f"📊 **Ваша статистика за поточний місяць:**\n\n"
    response += f"💰 Загальна сума: {total_amount:.2f} грн\n"
    response += f"📝 Кількість записів: {record_count}\n"
    response += f"📅 Середня витрата: {avg_expense:.2f} грн\n\n"
    response += f"📂 **По категоріях:**\n"
    
    for category, amount in sorted_categories:
        percentage = (amount / total_amount) * 100
        response += f"• {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def today_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сьогодні"""
    data = await bot.get_expenses_data(period_filter='today')
    
    if not data:
        await update.message.reply_text("📅 Сьогодні ще немає записів про витрати")
        return
    
    total_amount = sum(float(row[2]) for row in data)
    record_count = len(data)
    
    # Групування по користувачах
    users = {}
    for row in data:
        user = row[3]
        amount = float(row[2])
        users[user] = users.get(user, 0) + amount
    
    response = f"📅 **Витрати за сьогодні:**\n\n"
    response += f"💰 Загальна сума: {total_amount:.2f} грн\n"
    response += f"📝 Кількість записів: {record_count}\n\n"
    
    if len(users) > 1:
        response += f"👥 **По користувачах:**\n"
        for user, amount in sorted(users.items(), key=lambda x: x[1], reverse=True):
            response += f"• {user}: {amount:.2f} грн\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def week_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за тиждень"""
    data = await bot.get_expenses_data(period_filter='week')
    
    if not data:
        await update.message.reply_text("📈 За поточний тиждень ще немає записів")
        return
    
    total_amount = sum(float(row[2]) for row in data)
    record_count = len(data)
    
    response = f"📈 **Витрати за поточний тиждень:**\n\n"
    response += f"💰 Загальна сума: {total_amount:.2f} грн\n"
    response += f"📝 Кількість записів: {record_count}\n"
    
    # Прогноз на місяць
    days_in_week = 7
    avg_per_day = total_amount / days_in_week
    monthly_projection = avg_per_day * 30
    
    response += f"📊 Середньо за день: {avg_per_day:.2f} грн\n"
    response += f"🔮 Прогноз на місяць: {monthly_projection:.2f} грн"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def month_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за місяць"""
    data = await bot.get_expenses_data(period_filter='month')
    
    if not data:
        await update.message.reply_text("📆 За поточний місяць ще немає записів")
        return
    
    total_amount = sum(float(row[2]) for row in data)
    record_count = len(data)
    
    # Групування по категоріях
    categories = {}
    for row in data:
        category = row[1]
        amount = float(row[2])
        categories[category] = categories.get(category, 0) + amount
    
    sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    
    response = f"📆 **Витрати за поточний місяць:**\n\n"
    response += f"💰 Загальна сума: {total_amount:.2f} грн\n"
    response += f"📝 Кількість записів: {record_count}\n\n"
    response += f"📂 **Топ категорій:**\n"
    
    for i, (category, amount) in enumerate(sorted_categories[:5], 1):
        percentage = (amount / total_amount) * 100
        response += f"{i}. {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def top_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Топ категорій за місяць"""
    data = await bot.get_expenses_data(period_filter='month')
    
    if not data:
        await update.message.reply_text("🏆 За поточний місяць ще немає даних для аналізу")
        return
    
    # Групування по категоріях
    categories = {}
    category_counts = {}
    
    for row in data:
        category = row[1]
        amount = float(row[2])
        categories[category] = categories.get(category, 0) + amount
        category_counts[category] = category_counts.get(category, 0) + 1
    
    sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    total_amount = sum(categories.values())
    
    response = f"🏆 **Топ категорій за поточний місяць:**\n\n"
    
    for i, (category, amount) in enumerate(sorted_categories, 1):
        percentage = (amount / total_amount) * 100
        count = category_counts[category]
        avg_per_record = amount / count
        
        response += f"**{i}. {category.upper()}**\n"
        response += f"💰 Сума: {amount:.2f} грн ({percentage:.1f}%)\n"
        response += f"📝 Записів: {count}\n"
        response += f"📊 Середня витрата: {avg_per_record:.2f} грн\n\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def recent_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Останні записи користувача"""
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    data = await bot.get_expenses_data(user_filter=user_identifier)
    
    if not data:
        await update.message.reply_text("📝 У вас поки немає записів")
        return
    
    # Сортування за датою (найновіші першими)
    data.sort(key=lambda x: x[0], reverse=True)
    recent_data = data[:5]
    
    response = f"📝 **Ваші останні 5 записів:**\n\n"
    
    for i, row in enumerate(recent_data, 1):
        date_time = row[0]
        category = row[1]
        amount = float(row[2])
        comment = row[4] if len(row) > 4 and row[4] else ""
        
        # Форматування дати
        try:
            dt = datetime.strptime(date_time, "%Y-%m-%d %H:%M:%S")
            formatted_date = dt.strftime("%d.%m %H:%M")
        except:
            formatted_date = date_time
        
        response += f"**{i}. {category}** - {amount:.2f} грн\n"
        response += f"🕐 {formatted_date}"
        if comment and not '[IGNORED]' in comment:
            response += f" | 💬 {comment}"
        response += "\n\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

# Функції для пар/сімей
async def family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сімейний бюджет"""
    data = await bot.get_expenses_data(period_filter='month')
    
    if not data:
        await update.message.reply_text("👫 За поточний місяць ще немає сімейних витрат")
        return
    
    # Групування по користувачах
    users = {}
    total_amount = 0
    
    for row in data:
        user = row[3]
        amount = float(row[2])
        users[user] = users.get(user, 0) + amount
        total_amount += amount
    
    # Статистика за тиждень
    week_data = await bot.get_expenses_data(period_filter='week')
    week_total = sum(float(row[2]) for row in week_data)
    
    # Прогноз на місяць
    days_in_month = 30
    days_passed = datetime.now().day
    daily_avg = total_amount / days_passed if days_passed > 0 else 0
    monthly_projection = daily_avg * days_in_month
    
    response = f"👫 **Сімейний бюджет за місяць:**\n\n"
    response += f"💰 Загальні витрати: {total_amount:.2f} грн\n"
    response += f"📈 За тиждень: {week_total:.2f} грн\n"
    response += f"🔮 Прогноз на місяць: {monthly_projection:.2f} грн\n\n"
    
    if len(users) > 1:
        response += f"👥 **Розподіл по членах сім'ї:**\n"
        for user, amount in sorted(users.items(), key=lambda x: x[1], reverse=True):
            percentage = (amount / total_amount) * 100
            response += f"• {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
        
        # Різниця між першим і другим
        if len(users) >= 2:
            sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
            difference = sorted_users[0][1] - sorted_users[1][1]
            response += f"\n💡 Різниця: {difference:.2f} грн"
    
    # Основні категорії
    categories = {}
    for row in data:
        category = row[1]
        amount = float(row[2])
        categories[category] = categories.get(category, 0) + amount
    
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:3]
    response += f"\n\n📂 **Основні категорії:**\n"
    for category, amount in top_categories:
        percentage = (amount / total_amount) * 100
        response += f"• {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def compare_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Порівняння витрат користувачів"""
    data = await bot.get_expenses_data(period_filter='month')
    
    if not data:
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("👥 За поточний місяць ще немає даних для порівняння")
        else:
            await update.message.reply_text("👥 За поточний місяць ще немає даних для порівняння")
        return
    
    # Групування по користувачах
    users_stats = {}
    
    for row in data:
        user = row[3]
        amount = float(row[2])
        category = row[1]
        
        if user not in users_stats:
            users_stats[user] = {
                'total': 0,
                'count': 0,
                'categories': {}
            }
        
        users_stats[user]['total'] += amount
        users_stats[user]['count'] += 1
        users_stats[user]['categories'][category] = users_stats[user]['categories'].get(category, 0) + amount
    
    response = f"👥 **Порівняння користувачів за місяць:**\n\n"
    
    for user, stats in sorted(users_stats.items(), key=lambda x: x[1]['total'], reverse=True):
        avg_expense = stats['total'] / stats['count']
        top_category = max(stats['categories'].items(), key=lambda x: x[1])
        
        response += f"**👤 {user}:**\n"
        response += f"💰 Загалом: {stats['total']:.2f} грн\n"
        response += f"📝 Записів: {stats['count']}\n"
        response += f"📊 Середня витрата: {avg_expense:.2f} грн\n"
        response += f"🏆 Топ категорія: {top_category[0]} ({top_category[1]:.2f} грн)\n\n"
    
    # Загальна статистика
    total_all = sum(stats['total'] for stats in users_stats.values())
    response += f"💫 **Загальна сума всіх: {total_all:.2f} грн**"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text(response, parse_mode='Markdown')

async def who_spent_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рейтинг витрат користувачів"""
    # Визначення періоду
    period = 'month'  # За замовчуванням місяць
    if context.args and len(context.args) > 0:
        period_arg = context.args[0].lower()
        if period_arg in ['today', 'week', 'month', 'year']:
            period = period_arg
    
    data = await bot.get_expenses_data(period_filter=period)
    
    if not data:
        message_text = f"🏅 За період '{period}' ще немає витрат для рейтингу"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    # Групування по користувачах
    users = {}
    for row in data:
        user = row[3]
        amount = float(row[2])
        users[user] = users.get(user, 0) + amount
    
    if len(users) < 2:
        message_text = "🏅 Потрібно принаймні 2 користувачі для рейтингу"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    
    period_names = {
        'today': 'сьогодні',
        'week': 'тиждень',
        'month': 'місяць',
        'year': 'рік'
    }
    
    response = f"🏅 **Рейтинг витрат за {period_names.get(period, period)}:**\n\n"
    
    medals = ['🥇', '🥈', '🥉']
    for i, (user, amount) in enumerate(sorted_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        response += f"{medal} **{user}**: {amount:.2f} грн\n"
    
    # Різниця між першим і другим
    if len(sorted_users) >= 2:
        difference = sorted_users[0][1] - sorted_users[1][1]
        response += f"\n💡 Різниця між 1-м і 2-м місцем: {difference:.2f} грн"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text(response, parse_mode='Markdown')

async def set_family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановлення сімейного бюджету"""
    global family_budget_amount
    
    if context.args and len(context.args) > 0:
        try:
            budget = float(context.args[0])
            if budget <= 0:
                raise ValueError("Бюджет повинен бути більше 0")
            
            family_budget_amount = budget
            
            response = f"💰 **Сімейний бюджет встановлено!**\n\n"
            response += f"🎯 Місячний бюджет: {budget:.2f} грн\n"
            response += f"📊 Використовуйте кнопку 'Статус бюджету' для контролю"
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
        except ValueError:
            await update.message.reply_text(
                "❌ Некоректна сума бюджету!\n\n"
                "📝 Використання: Натисніть 'Статус бюджету' або напишіть: /budget 5000"
            )
    else:
        await update.message.reply_text(
            "💰 **Встановлення сімейного бюджету**\n\n"
            "📝 Використання: /budget [сума]\n"
            "📝 Приклад: /budget 5000"
        )

async def budget_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус виконання бюджету"""
    global family_budget_amount
    
    if family_budget_amount <= 0:
        message_text = (
            "💰 **Бюджет не встановлено**\n\n"
            "📝 Встановіть сімейний бюджет командою:\n"
            "/budget [сума]\n\n"
            "📝 Приклад: /budget 5000"
        )
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text, parse_mode='Markdown')
        else:
            await update.message.reply_text(message_text, parse_mode='Markdown')
        return
    
    # Витрати за поточний місяць
    data = await bot.get_expenses_data(period_filter='month')
    spent_amount = sum(float(row[2]) for row in data) if data else 0
    
    remaining = family_budget_amount - spent_amount
    spent_percentage = (spent_amount / family_budget_amount) * 100
    
    # Розрахунок денного ліміту
    now = datetime.now()
    days_in_month = (datetime(now.year, now.month + 1, 1) - timedelta(days=1)).day
    days_remaining = days_in_month - now.day
    daily_limit = remaining / days_remaining if days_remaining > 0 else 0
    
    # Прогрес-бар
    progress_length = 10
    filled_length = int(progress_length * spent_percentage / 100)
    progress_bar = "█" * filled_length + "░" * (progress_length - filled_length)
    
    response = f"💰 **Статус сімейного бюджету:**\n\n"
    response += f"🎯 Бюджет: {family_budget_amount:.2f} грн\n"
    response += f"💸 Витрачено: {spent_amount:.2f} грн\n"
    response += f"💚 Залишок: {remaining:.2f} грн\n\n"
    response += f"📊 Виконання: {spent_percentage:.1f}%\n"
    response += f"[{progress_bar}]\n\n"
    
    if days_remaining > 0:
        response += f"📅 Днів до кінця місяця: {days_remaining}\n"
        response += f"💡 Денний ліміт: {daily_limit:.2f} грн"
        
        if daily_limit < 0:
            response += "\n\n⚠️ **Увага!** Бюджет перевищено!"
        elif spent_percentage > 80:
            response += "\n\n🔶 **Обережно!** Витрачено більше 80% бюджету"
    else:
        response += "\n🏁 Місяць завершився"
    
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text(response, parse_mode='Markdown')

# Функції управління записами
async def undo_last_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасування останнього запису"""
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    # Перевірка чи є збережена дія
    if user_identifier not in user_last_actions:
        message_text = "❌ Немає записів для скасування"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    last_action = user_last_actions[user_identifier]
    
    # Перевірка часу (до 10 хвилин)
    time_diff = datetime.now() - last_action['timestamp']
    if time_diff.total_seconds() > 600:  # 10 хвилин
        message_text = "❌ Час для скасування минув (максимум 10 хвилин)"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    
    try:
        # Отримання всіх записів
        result = bot.sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID,
            range=config.RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if len(values) < 2:
            message_text = "❌ Немає записів для видалення"
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        
        # Пошук останнього запису користувача
        last_row_index = None
        for i in range(len(values) - 1, 0, -1):  # Йдемо з кінця, пропускаємо заголовки
            row = values[i]
            if (len(row) >= 4 and 
                row[3] == user_identifier and 
                row[1] == last_action['category'] and 
                float(row[2]) == last_action['amount']):
                last_row_index = i + 1  # Google Sheets використовує 1-based indexing
                break
        
        if last_row_index is None:
            message_text = "❌ Останній запис не знайдено"
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        
        # Видалення рядка
        bot.sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=config.SPREADSHEET_ID,
            body={
                'requests': [{
                    'deleteDimension': {
                        'range': {
                            'sheetId': 0,
                            'dimension': 'ROWS',
                            'startIndex': last_row_index - 1,
                            'endIndex': last_row_index
                        }
                    }
                }]
            }
        ).execute()
        
        # Видалення з локального кешу
        del user_last_actions[user_identifier]
        
        response = f"✅ **Запис скасовано!**\n\n"
        response += f"📂 Категорія: {last_action['category']}\n"
        response += f"💰 Сума: {last_action['amount']:.2f} грн"
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(response, parse_mode='Markdown')
        else:
            await update.message.reply_text(response, parse_mode='Markdown')
        
        logger.info(f"Скасовано запис для {user_identifier}: {last_action}")
        
    except Exception as e:
        logger.error(f"Помилка скасування запису: {e}")
        message_text = "❌ Виникла помилка при скасуванні запису"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)

async def ignore_last_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ігнорування останнього запису"""
    user = update.effective_user
    user_identifier = bot.get_user_identifier(user)
    
    try:
        # Отримання всіх записів
        result = bot.sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID,
            range=config.RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if len(values) < 2:
            message_text = "❌ Немає записів для ігнорування"
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        
        # Пошук останнього запису користувача
        last_row_index = None
        last_row_data = None
        
        for i in range(len(values) - 1, 0, -1):
            row = values[i]
            if len(row) >= 4 and row[3] == user_identifier:
                # Перевіряємо, чи не ігнорований вже
                if len(row) <= 4 or '[IGNORED]' not in str(row[4]):
                    last_row_index = i + 1
                    last_row_data = row
                    break
        
        if last_row_index is None:
            message_text = "❌ Немає записів для ігнорування або останній запис вже ігнорований"
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        
        # Оновлення коментаря з міткою [IGNORED]
        current_comment = last_row_data[4] if len(last_row_data) > 4 else ""
        new_comment = f"{current_comment} [IGNORED]".strip()
        
        # Оновлення рядка
        bot.sheets_service.spreadsheets().values().update(
            spreadsheetId=config.SPREADSHEET_ID,
            range=f"E{last_row_index}",
            valueInputOption='RAW',
            body={'values': [[new_comment]]}
        ).execute()
        
        response = f"🔕 **Запис ігноровано!**\n\n"
        response += f"📂 Категорія: {last_row_data[1]}\n"
        response += f"💰 Сума: {float(last_row_data[2]):.2f} грн\n"
        response += f"💡 Запис не буде враховуватися в статистиці"
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(response, parse_mode='Markdown')
        else:
            await update.message.reply_text(response, parse_mode='Markdown')
        
        logger.info(f"Ігноровано запис для {user_identifier}")
        
    except Exception as e:
        logger.error(f"Помилка ігнорування запису: {e}")
        message_text = "❌ Виникла помилка при ігноруванні запису"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message_text)
        else:
            await update.message.reply_text(message_text)

async def test_sheets_access():
    """Тестування доступу до Google Sheets"""
    try:
        result = bot.sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID,
            range="A1:A1"
        ).execute()
        logger.info("✅ Доступ до Google Sheets працює")
        return True
    except Exception as e:
        logger.error(f"❌ Помилка доступу до Google Sheets: {e}")
        return False

async def main():
    """Запускає бота"""
    if not os.path.exists(config.SERVICE_ACCOUNT_FILE):
        logger.error(f"Файл сервісного акаунту не знайдено: {config.SERVICE_ACCOUNT_FILE}")
        return
    
    # Тестуємо доступ до Google Sheets
    try:
        await test_sheets_access()
    except Exception as e:
        logger.error(f"Не вдалося протестувати доступ до Google Sheets: {e}")
    
    app = Application.builder().token(config.TOKEN).build()

    # Додаємо обробники команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("budget", set_family_budget))
    
    # Обробник inline кнопок
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Обробник текстових повідомлень та кнопок
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text))
    
    # Обробник голосових повідомлень
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Бот запускається...")
    logger.info("✅ FinDotBot з кнопками успішно запущено!")
    
    # Запуск бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 FinDotBot зупинено користувачем")
    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}")
        raise