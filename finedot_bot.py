import logging
import datetime
import os
import tempfile
import subprocess
import asyncio
import re
import platform
import signal
import sys
import time
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.helpers import escape_markdown
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError
from telegram.error import Conflict

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
    MAX_VOICE_DURATION,
    FFMPEG_TIMEOUT,
    GOOGLE_API_TIMEOUT
)

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# Глобальний словник для зберігання останніх дій користувачів (з обмеженням для запобігання memory leak)
from collections import OrderedDict
user_last_actions = OrderedDict()
MAX_USER_ACTIONS = 50  # Максимум 50 записів користувачів

def add_user_action(user_id, action):
    """Додає дію користувача з автоматичним очищенням старих записів"""
    if len(user_last_actions) >= MAX_USER_ACTIONS:
        # Видаляємо найстарший запис
        user_last_actions.popitem(last=False)
        logger.debug(f"Очищено старі записи user_last_actions, залишилось: {len(user_last_actions)}")
    
    user_last_actions[user_id] = action

def cleanup_old_actions():
    """Примусове очищення старих дій"""
    while len(user_last_actions) > MAX_USER_ACTIONS // 2:  # Залишаємо тільки половину
        user_last_actions.popitem(last=False)
    logger.info(f"Виконано cleanup user_last_actions, залишилось: {len(user_last_actions)}")

# Глобальна змінна для сімейного бюджету
family_budget_amount = 0

# Клас для моніторингу з'єднань
class ConnectionMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
    
    def log_request(self):
        self.request_count += 1
        if self.request_count % 100 == 0:
            uptime = time.time() - self.start_time
            logger.info(f"📊 Статистика: {self.request_count} запитів, "
                       f"{self.error_count} помилок, uptime: {uptime/3600:.1f}h")
    
    def log_error(self):
        self.error_count += 1

# Створюємо глобальний монітор
monitor = ConnectionMonitor()

# === ФУНКЦІЇ ДЛЯ СТВОРЕННЯ REPLY КНОПОК ===

def create_persistent_keyboard():
    """Створює постійну reply клавіатуру з кнопкою Меню"""
    keyboard = [
        [KeyboardButton("🟩 📋 МЕНЮ 📋 🟩")]
    ]
    return ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,  # Кнопки будуть компактними
        one_time_keyboard=False  # Клавіатура залишається після натискання
    )

def remove_keyboard():
    """Видаляє reply клавіатуру (якщо потрібно)"""
    from telegram import ReplyKeyboardRemove
    return ReplyKeyboardRemove()

# === ФУНКЦІЇ ДЛЯ СТВОРЕННЯ INLINE КНОПОК ===

def create_main_menu():
    """Створює головне меню з inline кнопками"""
    keyboard = [
        [InlineKeyboardButton("📊 Моя статистика", callback_data="menu_my_stats")],
        [InlineKeyboardButton("👫 Сімейна статистика", callback_data="menu_family_stats")],
        [InlineKeyboardButton("📅 За періодами", callback_data="menu_periods")],
        [InlineKeyboardButton("💰 Бюджет", callback_data="menu_budget")],
        [InlineKeyboardButton("🛠️ Управління", callback_data="menu_management")],
        [InlineKeyboardButton("❓ Довідка", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_my_stats_menu():
    """Підменю особистої статистики"""
    keyboard = [
        [InlineKeyboardButton("📊 Моя статистика за місяць", callback_data="cmd_mystats")],
        [InlineKeyboardButton("📝 Мої останні записи", callback_data="cmd_recent")],
        [InlineKeyboardButton("← Назад", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_family_stats_menu():
    """Підменю сімейної статистики"""
    keyboard = [
        [InlineKeyboardButton("💼 Сімейний бюджет", callback_data="cmd_family")],
        [InlineKeyboardButton("👫 Порівняння витрат", callback_data="cmd_compare")],
        [InlineKeyboardButton("🏆 Хто більше витратив", callback_data="cmd_whospent")],
        [InlineKeyboardButton("← Назад", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_periods_menu():
    """Підменю статистики за періодами"""
    keyboard = [
        [InlineKeyboardButton("📅 Сьогодні", callback_data="cmd_today")],
        [InlineKeyboardButton("📅 Тиждень", callback_data="cmd_week")],
        [InlineKeyboardButton("📅 Місяць", callback_data="cmd_month")],
        [InlineKeyboardButton("🏆 Топ категорій", callback_data="cmd_top")],
        [InlineKeyboardButton("← Назад", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_budget_menu():
    """Підменю бюджету"""
    keyboard = [
        [InlineKeyboardButton("💰 Статус бюджету", callback_data="cmd_budget_status")],
        [InlineKeyboardButton("⚙️ Встановити бюджет", callback_data="help_budget")],
        [InlineKeyboardButton("← Назад", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_management_menu():
    """Підменю управління записами"""
    keyboard = [
        [InlineKeyboardButton("↶ Скасувати останній запис", callback_data="cmd_undo")],
        [InlineKeyboardButton("🔕 Позначити як ігнорований", callback_data="cmd_ignore")],
        [InlineKeyboardButton("← Назад", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# === БЕЗПЕЧНІ ФУНКЦІЇ ===

# Функція для безпечного виконання операцій бота
async def safe_bot_operation(operation, max_retries=3):
    """Безпечне виконання операцій бота з покращеною retry логікою"""
    for attempt in range(max_retries):
        try:
            monitor.log_request()
            return await operation()
            
        except Exception as e:
            monitor.log_error()
            error_msg = str(e).lower()
            
            # Обробка конфліктів
            if "conflict" in error_msg or "terminated by other getupdates" in error_msg:
                logger.warning(f"🔄 Конфлікт на спробі {attempt + 1}: {e}")
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt + 5  # 5, 7, 11 секунд
                    logger.info(f"⏰ Чекаємо {wait_time} секунд перед повтором...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ Всі спроби вичерпано для операції (конфлікт)")
                    raise
            
            # Обробка timeout помилок
            elif "timeout" in error_msg or "timed out" in error_msg:
                logger.warning(f"⏰ Timeout на спробі {attempt + 1}: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    logger.error("❌ Всі спроби вичерпано (timeout)")
                    raise
            
            # Обробка мережевих помилок (включаючи httpx.ReadError)
            elif any(keyword in error_msg for keyword in ["network", "connection", "unreachable", "failed to connect", "readerror", "readtimeout"]):
                logger.warning(f"🌐 Мережева помилка на спробі {attempt + 1}: {e}")
                
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt + 1  # 1, 3, 5 секунд
                    logger.info(f"⏰ Чекаємо {wait_time} секунд перед повтором...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ Всі спроби вичерпано (мережа)")
                    raise
            
            # Обробка rate limit помилок
            elif "rate limit" in error_msg or "too many requests" in error_msg:
                logger.warning(f"🚦 Rate limit на спробі {attempt + 1}: {e}")
                
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)  # 5, 10, 15 секунд
                    logger.info(f"⏰ Rate limit: чекаємо {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ Всі спроби вичерпано (rate limit)")
                    raise
            
            # Інші помилки - не повторюємо
            else:
                logger.error(f"❌ Неочікувана помилка в операції: {e}")
                raise

# Безпечна відправка повідомлень з постійною клавіатурою
async def safe_send_message(update, context, text, **kwargs):
    """Безпечна відправка повідомлень з постійною клавіатурою"""
    async def send_operation():
        # Якщо не передано reply_markup, додаємо постійну клавіатуру
        if 'reply_markup' not in kwargs:
            kwargs['reply_markup'] = create_persistent_keyboard()
        return await update.message.reply_text(text, **kwargs)
    
    try:
        return await safe_bot_operation(send_operation)
    except Exception as e:
        logger.error(f"Не вдалося відправити повідомлення: {e}")
        # Fallback - спробувати простий текст
        try:
            return await update.message.reply_text("❌ Виникла помилка при обробці запиту")
        except:
            logger.error("Критична помилка з'єднання з Telegram")

# Безпечна відправка повідомлень для callback
async def safe_send_callback_message(query, text, **kwargs):
    """Безпечна відправка повідомлень через callback query"""
    try:
        if 'reply_markup' in kwargs:
            return await query.edit_message_text(text, **kwargs)
        else:
            return await query.edit_message_text(text)
    except Exception as e:
        logger.error(f"Не вдалося відправити callback повідомлення: {e}")
        try:
            await query.answer("❌ Виникла помилка при обробці запиту")
        except:
            logger.error("Критична помилка з callback query")

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
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, text=True, timeout=10)
        logger.info("Використовую системний FFmpeg")
        return "ffmpeg"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("FFmpeg не знайдено або timeout")
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

def create_application():
    """Створює Application з покращеними налаштуваннями"""
    from config import TELEGRAM_POOL_SIZE, TELEGRAM_TIMEOUT, TELEGRAM_READ_TIMEOUT
    
    # Створення HTTPXRequest з правильними налаштуваннями
    request = HTTPXRequest(
        connection_pool_size=TELEGRAM_POOL_SIZE,
        read_timeout=TELEGRAM_READ_TIMEOUT,
        write_timeout=TELEGRAM_TIMEOUT,
        connect_timeout=TELEGRAM_TIMEOUT
    )
    
    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .build()
    )
    
    logger.info(f"Application створено з HTTPXRequest налаштуваннями (pool_size={TELEGRAM_POOL_SIZE}, timeout={TELEGRAM_TIMEOUT})")
    return application

def signal_handler(signum, frame):
    """Обробник сигналів для graceful shutdown"""
    logger.info("🛑 Отримано сигнал зупинки. Завершення роботи...")
    sys.exit(0)

# === ФУНКЦІЇ РОБОТИ З GOOGLE SHEETS ===

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
    elif period_type == "prev_month":
        # Попередній місяць
        if now.month == 1:
            start_date = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period_type == "year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return expenses
    
    # Фільтруємо по періоду
    if period_type == "prev_month":
        filtered = [exp for exp in expenses if start_date <= exp['date'] < end_date]
    else:
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

# === НОВА ФУНКЦІЯ ДЛЯ ОБРОБКИ КНОПКИ МЕНЮ ===

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує головне меню при натисканні кнопки Меню"""
    menu_message = "🤖 Головне меню:\n\nВиберіть потрібну опцію:"
    
    # Відправляємо меню з inline кнопками, але зберігаємо reply клавіатуру
    await update.message.reply_text(
        menu_message, 
        reply_markup=create_main_menu()
    )

# === ОБРОБНИК CALLBACK ЗАПИТІВ ===

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискань на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        # Закриття меню
        if data == "close_menu":
            await query.delete_message()
            return
        
        # Головне меню
        if data == "main_menu":
            await safe_send_callback_message(
                query, 
                "🤖 Головне меню:\n\nВиберіть потрібну опцію:", 
                reply_markup=create_main_menu()
            )
        
        # Підменю з кнопкою закриття
        elif data == "menu_my_stats":
            keyboard = [
                [InlineKeyboardButton("📊 Моя статистика за місяць", callback_data="cmd_mystats")],
                [InlineKeyboardButton("📅 Моя статистика за попередній місяць", callback_data="cmd_mystats_prev")],
                [InlineKeyboardButton("📝 Мої останні записи", callback_data="cmd_recent")],
                [InlineKeyboardButton("← Назад", callback_data="main_menu")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            menu_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query, 
                "📊 Моя статистика:", 
                reply_markup=menu_markup
            )
        
        elif data == "menu_family_stats":
            keyboard = [
                [InlineKeyboardButton("💼 Сімейний бюджет", callback_data="cmd_family")],
                [InlineKeyboardButton("📅 Сімейний бюджет за попередній місяць", callback_data="cmd_family_prev")],
                [InlineKeyboardButton("👫 Порівняння витрат", callback_data="cmd_compare")],
                [InlineKeyboardButton("👫 Порівняння за попередній місяць", callback_data="cmd_compare_prev")],
                [InlineKeyboardButton("🏆 Хто більше витратив", callback_data="cmd_whospent")],
                [InlineKeyboardButton("🏆 Хто більше витратив за попередній місяць", callback_data="cmd_whospent_prev")],
                [InlineKeyboardButton("← Назад", callback_data="main_menu")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            menu_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query, 
                "👫 Сімейна статистика:", 
                reply_markup=menu_markup
            )
        
        elif data == "menu_periods":
            keyboard = [
                [InlineKeyboardButton("📅 Сьогодні", callback_data="cmd_today")],
                [InlineKeyboardButton("📅 Тиждень", callback_data="cmd_week")],
                [InlineKeyboardButton("📅 Місяць", callback_data="cmd_month")],
                [InlineKeyboardButton("📅 Попередній місяць", callback_data="cmd_prev_month")],
                [InlineKeyboardButton("🏆 Топ категорій", callback_data="cmd_top")],
                [InlineKeyboardButton("← Назад", callback_data="main_menu")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            menu_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query, 
                "📅 Статистика за періодами:", 
                reply_markup=menu_markup
            )
        
        elif data == "menu_budget":
            keyboard = [
                [InlineKeyboardButton("💰 Статус бюджету", callback_data="cmd_budget_status")],
                [InlineKeyboardButton("⚙️ Встановити бюджет", callback_data="help_budget")],
                [InlineKeyboardButton("← Назад", callback_data="main_menu")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            menu_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query, 
                "💰 Управління бюджетом:", 
                reply_markup=menu_markup
            )
        
        elif data == "menu_management":
            keyboard = [
                [InlineKeyboardButton("↶ Скасувати останній запис", callback_data="cmd_undo")],
                [InlineKeyboardButton("🔕 Позначити як ігнорований", callback_data="cmd_ignore")],
                [InlineKeyboardButton("← Назад", callback_data="main_menu")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            menu_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query, 
                "🛠️ Управління записами:", 
                reply_markup=menu_markup
            )
        
        # Команди
        elif data.startswith("cmd_"):
            command = data.replace("cmd_", "")
            await execute_command_from_callback(query, command, context)
        
        # Довідка
        elif data == "help":
            await show_help(query)
        
        elif data == "help_budget":
            keyboard = [
                [InlineKeyboardButton("← Назад", callback_data="menu_budget")],
                [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
            ]
            help_markup = InlineKeyboardMarkup(keyboard)
            await safe_send_callback_message(
                query,
                "💰 Встановлення бюджету:\n\n"
                "Для встановлення бюджету використайте команду:\n"
                "/budget 15000\n\n"
                "Приклад: /budget 20000 встановить бюджет 20000 грн на місяць",
                reply_markup=help_markup
            )
        
    except Exception as e:
        logger.error(f"Помилка обробки callback: {e}")
        await query.answer("❌ Виникла помилка. Спробуйте ще раз.")

async def execute_command_from_callback(query, command, context):
    """Виконує команду з callback кнопки"""
    if command == "mystats":
        await my_stats_callback(query, context)
    elif command == "mystats_prev":
        await my_stats_prev_month_callback(query, context)
    elif command == "recent":
        await show_recent_expenses_callback(query, context)
    elif command == "family":
        await family_budget_callback(query, context)
    elif command == "family_prev":
        await family_budget_prev_month_callback(query, context)
    elif command == "compare":
        await compare_users_callback(query, context)
    elif command == "compare_prev":
        await compare_users_prev_month_callback(query, context)
    elif command == "whospent":
        await who_spent_more_callback(query, context)
    elif command == "whospent_prev":
        await who_spent_more_prev_month_callback(query, context)
    elif command == "today":
        await stats_today_callback(query, context)
    elif command == "week":
        await stats_week_callback(query, context)
    elif command == "month":
        await stats_month_callback(query, context)
    elif command == "prev_month":
        await stats_prev_month_callback(query, context)
    elif command == "top":
        await top_categories_callback(query, context)
    elif command == "budget_status":
        await budget_status_callback(query, context)
    elif command == "undo":
        await undo_last_action_callback(query, context)
    elif command == "ignore":
        await mark_as_ignored_callback(query, context)

# === CALLBACK ФУНКЦІЇ ===

async def my_stats_callback(query, context):
    """Особиста статистика через callback"""
    user = query.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month", user_name)
    message = generate_stats_message(filtered_expenses, "поточний місяць", user_name)
    
    keyboard = [
        [InlineKeyboardButton("← Назад", callback_data="menu_my_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    back_button = InlineKeyboardMarkup(keyboard)
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def my_stats_prev_month_callback(query, context):
    """Особиста статистика за попередній місяць через callback"""
    user = query.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "prev_month", user_name)
    message = generate_stats_message(filtered_expenses, "попередній місяць", user_name)
    
    keyboard = [
        [InlineKeyboardButton("← Назад", callback_data="menu_my_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    back_button = InlineKeyboardMarkup(keyboard)
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def show_recent_expenses_callback(query, context):
    """Показує останні записи через callback"""
    user = query.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            message = "❌ Немає записів."
        else:
            user_expenses = []
            for i, row in enumerate(values[1:], 2):
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
                message = "❌ У вас немає записів."
            else:
                user_expenses.sort(key=lambda x: x['date'], reverse=True)
                recent_expenses = user_expenses[:5]
                
                message = "📝 Ваші останні записи:\n\n"
                for i, exp in enumerate(recent_expenses, 1):
                    ignored_mark = "🔕 " if exp['is_ignored'] else ""
                    message += f"{i}. {ignored_mark}{exp['category']}: {exp['amount']:.2f} грн"
                    if exp['comment'] and not exp['is_ignored']:
                        message += f" ({exp['comment']})"
                    message += f"\n   📅 {exp['date'].strftime('%d.%m %H:%M')}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("← Назад", callback_data="menu_my_stats")],
            [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
        ]
        back_button = InlineKeyboardMarkup(keyboard)
        await safe_send_callback_message(query, message, reply_markup=back_button)
        
    except Exception as e:
        logger.error(f"Помилка отримання записів: {e}")
        keyboard = [
            [InlineKeyboardButton("← Назад", callback_data="menu_my_stats")],
            [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
        ]
        back_button = InlineKeyboardMarkup(keyboard)
        await safe_send_callback_message(query, "❌ Помилка при отриманні записів.", reply_markup=back_button)

async def family_budget_callback(query, context):
    """Сімейний бюджет через callback"""
    expenses = get_all_expenses()
    
    week_expenses = filter_expenses_by_period(expenses, "week")
    week_total = sum(exp['amount'] for exp in week_expenses)
    
    month_expenses = filter_expenses_by_period(expenses, "month")
    month_total = sum(exp['amount'] for exp in month_expenses)
    
    if not month_expenses:
        message = "Немає витрат за поточний місяць."
    else:
        users_month = {}
        for exp in month_expenses:
            user = exp['user']
            users_month[user] = users_month.get(user, 0) + exp['amount']
        
        categories_month = {}
        for exp in month_expenses:
            category = exp['category']
            categories_month[category] = categories_month.get(category, 0) + exp['amount']
        
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
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def family_budget_prev_month_callback(query, context):
    """Сімейний бюджет за попередній місяць через callback"""
    expenses = get_all_expenses()
    
    prev_month_expenses = filter_expenses_by_period(expenses, "prev_month")
    prev_month_total = sum(exp['amount'] for exp in prev_month_expenses)
    
    if not prev_month_expenses:
        message = "Немає витрат за попередній місяць."
    else:
        users_prev_month = {}
        for exp in prev_month_expenses:
            user = exp['user']
            users_prev_month[user] = users_prev_month.get(user, 0) + exp['amount']
        
        categories_prev_month = {}
        for exp in prev_month_expenses:
            category = exp['category']
            categories_prev_month[category] = categories_prev_month.get(category, 0) + exp['amount']
        
        message = f"💼 Сімейний бюджет за попередній місяць:\n\n"
        message += f"💰 Загальна сума: {prev_month_total:.2f} грн\n"
        message += f"📝 Кількість записів: {len(prev_month_expenses)}\n\n"
        
        # По користувачах
        message += "👥 По користувачах:\n"
        for user, amount in sorted(users_prev_month.items(), key=lambda x: x[1], reverse=True):
            percentage = (amount / prev_month_total) * 100
            message += f"• {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
        
        # Топ категорії
        message += "\n🏆 Топ категорії:\n"
        top_categories = sorted(categories_prev_month.items(), key=lambda x: x[1], reverse=True)[:5]
        for category, amount in top_categories:
            percentage = (amount / prev_month_total) * 100
            message += f"• {category}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def compare_users_callback(query, context):
    """Порівняння користувачів через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        message = "Немає витрат за поточний місяць."
    else:
        users_stats = {}
        total_amount = 0
        
        for exp in filtered_expenses:
            user = exp['user']
            if user not in users_stats:
                users_stats[user] = {'total': 0, 'count': 0, 'categories': {}}
            
            users_stats[user]['total'] += exp['amount']
            users_stats[user]['count'] += 1
            total_amount += exp['amount']
            
            category = exp['category']
            if category not in users_stats[user]['categories']:
                users_stats[user]['categories'][category] = 0
            users_stats[user]['categories'][category] += exp['amount']
        
        message = "👫 Порівняння витрат за місяць:\n\n"
        message += f"💰 Загальний бюджет сім'ї: {total_amount:.2f} грн\n\n"
        
        sorted_users = sorted(users_stats.items(), key=lambda x: x[1]['total'], reverse=True)
        
        for i, (user, stats) in enumerate(sorted_users, 1):
            percentage = (stats['total'] / total_amount) * 100
            avg_expense = stats['total'] / stats['count']
            
            message += f"{i}. 👤 {user}:\n"
            message += f"   💰 {stats['total']:.2f} грн ({percentage:.1f}%)\n"
            message += f"   📝 {stats['count']} записів\n"
            message += f"   📊 Середня витрата: {avg_expense:.2f} грн\n"
            
            top_categories = sorted(stats['categories'].items(), key=lambda x: x[1], reverse=True)[:3]
            message += "   🏆 Топ категорії: "
            message += ", ".join([f"{cat} ({amt:.0f}₴)" for cat, amt in top_categories])
            message += "\n\n"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def compare_users_prev_month_callback(query, context):
    """Порівняння користувачів за попередній місяць через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "prev_month")
    
    if not filtered_expenses:
        message = "Немає витрат за попередній місяць для порівняння."
    else:
        users = {}
        for exp in filtered_expenses:
            user = exp['user']
            users[user] = users.get(user, 0) + exp['amount']
        
        total = sum(users.values())
        
        message = "👫 Порівняння витрат за попередній місяць:\n\n"
        
        for user, amount in sorted(users.items(), key=lambda x: x[1], reverse=True):
            percentage = (amount / total) * 100
            message += f"👤 {user}:\n"
            message += f"   💰 Сума: {amount:.2f} грн ({percentage:.1f}%)\n"
            
            # Детальна статистика по кожному користувачу
            user_expenses = [exp for exp in filtered_expenses if exp['user'] == user]
            user_categories = {}
            for exp in user_expenses:
                category = exp['category']
                user_categories[category] = user_categories.get(category, 0) + exp['amount']
            
            top_categories = sorted(user_categories.items(), key=lambda x: x[1], reverse=True)[:3]
            message += "   🏆 Топ категорії: "
            message += ", ".join([f"{cat} ({amt:.0f}₴)" for cat, amt in top_categories])
            message += "\n\n"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

    # === РЕШТА CALLBACK ФУНКЦІЙ ===

async def who_spent_more_callback(query, context):
    """Хто більше витратив через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        message = "Немає витрат за поточний місяць."
    else:
        users = {}
        for exp in filtered_expenses:
            user = exp['user']
            users[user] = users.get(user, 0) + exp['amount']
        
        if len(users) < 2:
            message = "Потрібно мінімум 2 користувачі для порівняння."
        else:
            sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
            total = sum(users.values())
            
            message = f"🏆 Рейтинг витрат цього місяця:\n\n"
            
            for i, (user, amount) in enumerate(sorted_users, 1):
                percentage = (amount / total) * 100
                emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                message += f"{emoji} {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
            
            if len(sorted_users) >= 2:
                difference = sorted_users[0][1] - sorted_users[1][1]
                message += f"\n💸 Різниця: {difference:.2f} грн"
                
                if difference > 0:
                    message += f"\n💡 {sorted_users[0][0]} витратив більше на {difference:.2f} грн"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def who_spent_more_prev_month_callback(query, context):
    """Хто більше витратив за попередній місяць через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "prev_month")
    
    if not filtered_expenses:
        message = "Немає витрат за попередній місяць для рейтингу."
    else:
        users = {}
        for exp in filtered_expenses:
            user = exp['user']
            users[user] = users.get(user, 0) + exp['amount']
        
        total = sum(users.values())
        
        message = "🏆 Рейтинг витрат за попередній місяць:\n\n"
        
        for position, (user, amount) in enumerate(sorted(users.items(), key=lambda x: x[1], reverse=True), 1):
            percentage = (amount / total) * 100
            
            if position == 1:
                emoji = "🥇"
            elif position == 2:
                emoji = "🥈"
            elif position == 3:
                emoji = "🥉"
            else:
                emoji = f"{position}."
                
            message += f"{emoji} {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
        
        message += f"\n💰 Загальна сума: {total:.2f} грн"
        message += f"\n📝 Всього записів: {len(filtered_expenses)}"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_family_stats")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def stats_today_callback(query, context):
    """Статистика за сьогодні через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "day")
    message = generate_stats_message(filtered_expenses, "сьогодні")
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_periods")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def stats_week_callback(query, context):
    """Статистика за тиждень через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "week")
    message = generate_stats_message(filtered_expenses, "поточний тиждень")
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_periods")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def stats_month_callback(query, context):
    """Статистика за місяць через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    message = generate_stats_message(filtered_expenses, "поточний місяць")
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_periods")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def stats_prev_month_callback(query, context):
    """Статистика за попередній місяць через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "prev_month")
    message = generate_stats_message(filtered_expenses, "попередній місяць")
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_periods")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def top_categories_callback(query, context):
    """Топ категорій через callback"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        message = "Немає витрат за поточний місяць."
    else:
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
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_periods")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def budget_status_callback(query, context):
    """Статус бюджету через callback"""
    global family_budget_amount
    
    if family_budget_amount == 0:
        message = ("❌ Бюджет не встановлено.\n"
                  "Використайте /budget СУМА для встановлення бюджету.")
    else:
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
        
        progress_length = 10
        filled_length = int(progress_length * percentage / 100)
        bar = "█" * filled_length + "░" * (progress_length - filled_length)
        message += f"\n📊 Прогрес: {bar} {percentage:.1f}%"
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_budget")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def undo_last_action_callback(query, context):
    """Скасування останньої дії через callback"""
    user = query.from_user
    
    if user.id not in user_last_actions:
        message = "❌ Немає дій для скасування."
    else:
        last_action = user_last_actions[user.id]
        
        if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
            message = "❌ Час для скасування минув (максимум 10 хвилин)."
        else:
            try:
                result = sheet.values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=RANGE_NAME
                ).execute()
                
                values = result.get('values', [])
                if not values:
                    message = "❌ Таблиця порожня."
                else:
                    user_name = user.username or user.first_name or "Unknown"
                    row_to_delete = None
                    
                    for i, row in enumerate(values):
                        if len(row) >= 4:
                            if (row[0] == last_action['date'] and 
                                row[1] == last_action['category'] and 
                                float(row[2]) == last_action['amount'] and
                                row[3] == user_name):
                                row_to_delete = i + 1
                                break
                    
                    if row_to_delete is None:
                        message = "❌ Запис не знайдено для скасування."
                    else:
                        requests = [{
                            'deleteDimension': {
                                'range': {
                                    'sheetId': 0,
                                    'dimension': 'ROWS',
                                    'startIndex': row_to_delete - 1,
                                    'endIndex': row_to_delete
                                }
                            }
                        }]
                        
                        sheet.batchUpdate(
                            spreadsheetId=SPREADSHEET_ID,
                            body={'requests': requests}
                        ).execute()
                        
                        del user_last_actions[user.id]
                        
                        message = (f"✅ Запис скасовано:\n"
                                 f"📂 Категорія: {last_action['category']}\n"
                                 f"💰 Сума: {last_action['amount']:.2f} грн")
                        
            except Exception as e:
                logger.error(f"Помилка скасування: {e}")
                message = "❌ Помилка при скасуванні запису."
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_management")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def mark_as_ignored_callback(query, context):
    """Позначення як ігнорований через callback"""
    user = query.from_user
    
    if user.id not in user_last_actions:
        message = "❌ Немає дій для позначення."
    else:
        last_action = user_last_actions[user.id]
        
        if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
            message = "❌ Час для позначення минув (максимум 10 хвилин)."
        else:
            try:
                result = sheet.values().get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=RANGE_NAME
                ).execute()
                
                values = result.get('values', [])
                if not values:
                    message = "❌ Таблиця порожня."
                else:
                    user_name = user.username or user.first_name or "Unknown"
                    row_to_update = None
                    
                    for i, row in enumerate(values):
                        if len(row) >= 4:
                            if (row[0] == last_action['date'] and 
                                row[1] == last_action['category'] and 
                                float(row[2]) == last_action['amount'] and
                                row[3] == user_name):
                                row_to_update = i + 1
                                break
                    
                    if row_to_update is None:
                        message = "❌ Запис не знайдено для позначення."
                    else:
                        current_comment = last_action.get('comment', '')
                        new_comment = f"[IGNORED] {current_comment}".strip()
                        
                        range_to_update = f"'Аркуш1'!E{row_to_update}"
                        sheet.values().update(
                            spreadsheetId=SPREADSHEET_ID,
                            range=range_to_update,
                            valueInputOption='USER_ENTERED',
                            body={'values': [[new_comment]]}
                        ).execute()
                        
                        del user_last_actions[user.id]
                        
                        message = (f"🔕 Запис позначено як ігнорований:\n"
                                 f"📂 Категорія: {last_action['category']}\n"
                                 f"💰 Сума: {last_action['amount']:.2f} грн\n"
                                 f"💡 Він не буде враховуватись у статистиці")
                        
            except Exception as e:
                logger.error(f"Помилка позначення: {e}")
                message = "❌ Помилка при позначенні запису."
    
    back_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data="menu_management")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ])
    await safe_send_callback_message(query, message, reply_markup=back_button)

async def show_help(query):
    """Показує повну довідку"""
    ffmpeg_status = "✅ Доступно" if FFMPEG_PATH else "❌ Не встановлено"
    
    help_message = (
        "🤖 Привіт! Я допоможу вести сімейний бюджет.\n\n"
        "📝 Для запису надішли повідомлення у форматі:\n"
        "Категорія Сума Коментар\n"
        "Приклад: Їжа 250 Обід у ресторані\n\n"
        f"🎤 Голосові повідомлення: {ffmpeg_status}\n\n"
        "📊 Особиста статистика:\n"
        "/mystats - твоя статистика за місяць\n"
        "/recent - твої останні 5 записів\n\n"
        "👫 Сімейна статистика:\n"
        "/family - загальний сімейний бюджет\n"
        "/compare - порівняння витрат між вами\n"
        "/whospent - хто більше витратив\n\n"
        "📅 Статистика за періоди:\n"
        "/today - витрати за сьогодні\n"
        "/week - витрати за тиждень\n"
        "/month - витрати за місяць\n"
        "/prevmonth - витрати за попередній місяць\n"
        "/top - топ категорій\n\n"
        "💰 Планування бюджету:\n"
        "/budget 15000 - встановити бюджет\n"
        "/budget_status - статус бюджету\n\n"
        "🛠️ Управління записами:\n"
        "/undo - скасувати останній запис\n"
        "/ignore - позначити як ігнорований\n\n"
        "💡 Натисніть «📋 Меню» внизу для швидкого доступу!"
    )
    
    keyboard = [
        [InlineKeyboardButton("← Головне меню", callback_data="main_menu")],
        [InlineKeyboardButton("✖️ Закрити", callback_data="close_menu")]
    ]
    back_button = InlineKeyboardMarkup(keyboard)
    await safe_send_callback_message(query, help_message, reply_markup=back_button)

# === ОНОВЛЕНА ФУНКЦІЯ START ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start з постійною кнопкою меню"""
    welcome_message = (
        "🤖 Привіт! Я допоможу вести сімейний бюджет.\n\n"
        "📝 Для запису надішли повідомлення у форматі:\n"
        "Категорія Сума Коментар\n"
        "Приклад: Їжа 250 Обід у ресторані\n\n"
        "💡 Натисніть кнопку «📋 Меню» внизу для доступу до всіх функцій!"
    )
    
    # Відправляємо привітання з постійною клавіатурою
    await safe_send_message(update, context, welcome_message)

# === ОНОВЛЕНИЙ ОБРОБНИК ПОВІДОМЛЕНЬ ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник текстових повідомлень"""
    text = update.message.text.strip()
    user = update.message.from_user
    
    # Перевіряємо чи це натискання кнопки "Меню"
    if text == "🟩 📋 МЕНЮ 📋 🟩":
        await show_main_menu(update, context)
        return
    
    # Інакше обробляємо як запис витрати
    await process_and_save(text, user, update, context)

# === ФУНКЦІЇ ОБРОБКИ ТЕКСТІВ ТА ЗБЕРЕЖЕННЯ ===


def normalize_category(category):
    """Нормалізує категорію до стандартного формату"""
    if not category:
        return category
    
    # Очищуємо зайві пробіли
    category = category.strip()
    
    # Конвертуємо до Title Case (Перша Літера Кожного Слова Велика)
    # Це зробить: "продукти" → "Продукти", "ПРОДУКТИ" → "Продукти"
    normalized = category.title()
    
    # Виправляємо деякі особливості української мови
    # Замінюємо типові помилки Title Case для українських слів
    corrections = {
        "На": "на",      # "Обіди На Роботі" → "Обіди на Роботі"
        "До": "до",      # "Дорога До Дому" → "Дорога до Дому"  
        "В": "в",        # "Їжа В Кафе" → "Їжа в Кафе"
        "З": "з",        # "Подарунки З Магазину" → "Подарунки з Магазину"
        "І": "і",        # "Хліб І Молоко" → "Хліб і Молоко"
        "Та": "та",      # "Фрукти Та Овочі" → "Фрукти та Овочі"
        "Для": "для",    # "Подарунки Для Дітей" → "Подарунки для Дітей"
        "По": "по",      # "Витрати По Дому" → "Витрати по Дому"
    }
    
    # Застосовуємо виправлення
    for wrong, correct in corrections.items():
        normalized = normalized.replace(f" {wrong} ", f" {correct} ")
    
    return normalized

def parse_expense_text(text):
    """Розбирає текст витрати з підтримкою багатослівних категорій (до 3 слів)"""
    text = text.strip()
    
    # Шукаємо перше число в тексті (це буде сума)
    amount_pattern = r'\b(\d+(?:[.,]\d+)?)\b'
    amount_match = re.search(amount_pattern, text)
    
    if not amount_match:
        return None, None, None
    
    # Позиція де знайшли суму
    amount_start = amount_match.start()
    amount_str = amount_match.group(1).replace(',', '.')
    
    try:
        amount = float(amount_str)
    except ValueError:
        return None, None, None
    
    # Все до суми - це потенційна категорія
    category_text = text[:amount_start].strip()
    
    # Все після суми - це коментар
    comment_start = amount_match.end()
    comment = text[comment_start:].strip()
    
    # Обробляємо категорію - не більше 3 слів
    if not category_text:
        return None, None, None
    
    category_words = category_text.split()
    
    # Обмежуємо до 3 слів максимум
    if len(category_words) > 3:
        category = ' '.join(category_words[:3])
        # Решту слів додаємо до коментаря
        remaining_words = ' '.join(category_words[3:])
        if comment:
            comment = remaining_words + ' ' + comment
        else:
            comment = remaining_words
    else:
        category = category_text
    
    # НОРМАЛІЗУЄМО КАТЕГОРІЮ
    category = normalize_category(category)
    
    # Валідація суми
    if amount <= 0:
        return None, None, None
    
    return category, amount, comment

async def process_and_save(text, user, update, context):
    """Обробляє та зберігає витрату"""
    category, amount, comment = parse_expense_text(text)
    
    if category is None or amount is None:
        await safe_send_message(update, context,
            "❌ Невірний формат. Введи у форматі:\n"
            "Категорія Сума Коментар\n"
            "Приклад: Їжа 250 Обід"
        )
        return

    if amount <= 0:
        await safe_send_message(update, context, "❌ Сума має бути більше нуля.")
        return

    # ВИПРАВЛЕННЯ ЧАСОВОГО ПОЯСУ - Київський час (UTC + 3)
    utc_now = datetime.datetime.utcnow()
    kyiv_time = utc_now + timedelta(hours=3)  # UTC + 3 години = Київський час
    date_str = kyiv_time.strftime("%Y-%m-%d %H:%M:%S")
    
    user_name = user.username or user.first_name or "Unknown"

    values = [[date_str, category, amount, user_name, comment]]

    try:
        logger.info(f"Спроба запису до таблиці {SPREADSHEET_ID}")
        
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME,
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()
        
        logger.info(f"Запис успішний: {result}")
        
        # Зберігаємо інформацію про останню дію користувача (також з київським часом)
        kyiv_timestamp = utc_now + timedelta(hours=3)
        add_user_action(user.id, {
            'action': 'add',
            'date': date_str,
            'category': category,
            'amount': amount,
            'comment': comment,
            'row_range': result.get('updates', {}).get('updatedRange', ''),
            'timestamp': kyiv_timestamp  # Київський час для timestamp теж
        })
        
        success_message = (
            f"✅ Запис додано:\n"
            f"📂 Категорія: {category}\n"
            f"💰 Сума: {amount:.2f} грн\n"
            f"👤 Користувач: {user_name}\n"
            f"🕒 Час: {kyiv_time.strftime('%H:%M:%S')}"  # Показуємо час користувачу
        )
        if comment:
            success_message += f"\n💬 Коментар: {comment}"
        
        success_message += f"\n\n💡 Якщо помилились, використайте /undo для скасування"
            
        await safe_send_message(update, context, success_message)
        
    except Exception as e:
        logger.error(f"Детальна помилка при записі до Google Sheets: {e}")
        logger.error(f"Тип помилки: {type(e).__name__}")
        await safe_send_message(update, context, "❌ Виникла помилка при записі даних. Перевірте доступ до таблиці.")
        # === ОРИГІНАЛЬНІ КОМАНДИ БОТА (НЕЗМІНЕНІ) ===

async def stats_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за сьогодні"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "day")
    message = generate_stats_message(filtered_expenses, "сьогодні")
    await safe_send_message(update, context, message)

async def stats_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за тиждень"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "week")
    message = generate_stats_message(filtered_expenses, "поточний тиждень")
    await safe_send_message(update, context, message)

async def stats_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    message = generate_stats_message(filtered_expenses, "поточний місяць")
    await safe_send_message(update, context, message)

async def stats_prev_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за попередній місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "prev_month")
    message = generate_stats_message(filtered_expenses, "попередній місяць")
    await safe_send_message(update, context, message)

async def stats_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика за рік"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "year")
    message = generate_stats_message(filtered_expenses, "поточний рік")
    await safe_send_message(update, context, message)

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Особиста статистика користувача за місяць"""
    user = update.message.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month", user_name)
    message = generate_stats_message(filtered_expenses, "поточний місяць", user_name)
    await safe_send_message(update, context, message)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стара функція статистики - тепер перенаправляє на stats_month"""
    await stats_month(update, context)

async def top_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Топ категорій за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        await safe_send_message(update, context, "Немає витрат за поточний місяць.")
        return
    
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
    
    await safe_send_message(update, context, message)

async def undo_last_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасовує останню дію користувача"""
    user = update.message.from_user
    
    if user.id not in user_last_actions:
        await safe_send_message(update, context, "❌ Немає дій для скасування.")
        return
    
    last_action = user_last_actions[user.id]
    
    if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
        await safe_send_message(update, context, "❌ Час для скасування минув (максимум 10 хвилин).")
        return
    
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await safe_send_message(update, context, "❌ Таблиця порожня.")
            return
        
        user_name = user.username or user.first_name or "Unknown"
        row_to_delete = None
        
        for i, row in enumerate(values):
            if len(row) >= 4:
                if (row[0] == last_action['date'] and 
                    row[1] == last_action['category'] and 
                    float(row[2]) == last_action['amount'] and
                    row[3] == user_name):
                    row_to_delete = i + 1
                    break
        
        if row_to_delete is None:
            await safe_send_message(update, context, "❌ Запис не знайдено для скасування.")
            return
        
        requests = [{
            'deleteDimension': {
                'range': {
                    'sheetId': 0,
                    'dimension': 'ROWS',
                    'startIndex': row_to_delete - 1,
                    'endIndex': row_to_delete
                }
            }
        }]
        
        sheet.batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={'requests': requests}
        ).execute()
        
        del user_last_actions[user.id]
        
        await safe_send_message(update, context,
            f"✅ Запис скасовано:\n"
            f"📂 Категорія: {last_action['category']}\n"
            f"💰 Сума: {last_action['amount']:.2f} грн"
        )
        
    except Exception as e:
        logger.error(f"Помилка скасування: {e}")
        await safe_send_message(update, context, "❌ Помилка при скасуванні запису.")

async def mark_as_ignored(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Позначає останній запис як ігнорований для статистики"""
    user = update.message.from_user
    
    if user.id not in user_last_actions:
        await safe_send_message(update, context, "❌ Немає дій для позначення.")
        return
    
    last_action = user_last_actions[user.id]
    
    if datetime.datetime.now() - last_action['timestamp'] > timedelta(minutes=10):
        await safe_send_message(update, context, "❌ Час для позначення минув (максимум 10 хвилин).")
        return
    
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await safe_send_message(update, context, "❌ Таблиця порожня.")
            return
        
        user_name = user.username or user.first_name or "Unknown"
        row_to_update = None
        
        for i, row in enumerate(values):
            if len(row) >= 4:
                if (row[0] == last_action['date'] and 
                    row[1] == last_action['category'] and 
                    float(row[2]) == last_action['amount'] and
                    row[3] == user_name):
                    row_to_update = i + 1
                    break
        
        if row_to_update is None:
            await safe_send_message(update, context, "❌ Запис не знайдено для позначення.")
            return
        
        current_comment = last_action.get('comment', '')
        new_comment = f"[IGNORED] {current_comment}".strip()
        
        range_to_update = f"'Аркуш1'!E{row_to_update}"
        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=range_to_update,
            valueInputOption='USER_ENTERED',
            body={'values': [[new_comment]]}
        ).execute()
        
        del user_last_actions[user.id]
        
        await safe_send_message(update, context,
            f"🔕 Запис позначено як ігнорований:\n"
            f"📂 Категорія: {last_action['category']}\n"
            f"💰 Сума: {last_action['amount']:.2f} грн\n"
            f"💡 Він не буде враховуватись у статистиці"
        )
        
    except Exception as e:
        logger.error(f"Помилка позначення: {e}")
        await safe_send_message(update, context, "❌ Помилка при позначенні запису.")

async def show_recent_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує останні 5 записів користувача"""
    user = update.message.from_user
    user_name = user.username or user.first_name or "Unknown"
    
    try:
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            await safe_send_message(update, context, "❌ Немає записів.")
            return
        
        user_expenses = []
        for i, row in enumerate(values[1:], 2):
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
            await safe_send_message(update, context, "❌ У вас немає записів.")
            return
        
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
        
        await safe_send_message(update, context, message)
        
    except Exception as e:
        logger.error(f"Помилка отримання записів: {e}")
        await safe_send_message(update, context, "❌ Помилка при отриманні записів.")

async def compare_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Порівняння витрат між користувачами за місяць"""
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, "month")
    
    if not filtered_expenses:
        await safe_send_message(update, context, "Немає витрат за поточний місяць.")
        return
    
    users_stats = {}
    total_amount = 0
    
    for exp in filtered_expenses:
        user = exp['user']
        if user not in users_stats:
            users_stats[user] = {'total': 0, 'count': 0, 'categories': {}}
        
        users_stats[user]['total'] += exp['amount']
        users_stats[user]['count'] += 1
        total_amount += exp['amount']
        
        category = exp['category']
        if category not in users_stats[user]['categories']:
            users_stats[user]['categories'][category] = 0
        users_stats[user]['categories'][category] += exp['amount']
    
    message = "👫 Порівняння витрат за місяць:\n\n"
    message += f"💰 Загальний бюджет сім'ї: {total_amount:.2f} грн\n\n"
    
    sorted_users = sorted(users_stats.items(), key=lambda x: x[1]['total'], reverse=True)
    
    for i, (user, stats) in enumerate(sorted_users, 1):
        percentage = (stats['total'] / total_amount) * 100
        avg_expense = stats['total'] / stats['count']
        
        message += f"{i}. 👤 {user}:\n"
        message += f"   💰 {stats['total']:.2f} грн ({percentage:.1f}%)\n"
        message += f"   📝 {stats['count']} записів\n"
        message += f"   📊 Середня витрата: {avg_expense:.2f} грн\n"
        
        top_categories = sorted(stats['categories'].items(), key=lambda x: x[1], reverse=True)[:3]
        message += "   🏆 Топ категорії: "
        message += ", ".join([f"{cat} ({amt:.0f}₴)" for cat, amt in top_categories])
        message += "\n\n"
    
    await safe_send_message(update, context, message)

async def family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сімейний бюджет з детальною розбивкою"""
    expenses = get_all_expenses()
    
    week_expenses = filter_expenses_by_period(expenses, "week")
    week_total = sum(exp['amount'] for exp in week_expenses)
    
    month_expenses = filter_expenses_by_period(expenses, "month")
    month_total = sum(exp['amount'] for exp in month_expenses)
    
    if not month_expenses:
        await safe_send_message(update, context, "Немає витрат за поточний місяць.")
        return
    
    users_month = {}
    for exp in month_expenses:
        user = exp['user']
        users_month[user] = users_month.get(user, 0) + exp['amount']
    
    categories_month = {}
    for exp in month_expenses:
        category = exp['category']
        categories_month[category] = categories_month.get(category, 0) + exp['amount']
    
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
    
    await safe_send_message(update, context, message)

async def who_spent_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хто більше витратив за період"""
    period = "month"
    
    if context.args:
        period_arg = context.args[0].lower()
        if period_arg in ["today", "week", "month", "year"]:
            period = period_arg if period_arg != "today" else "day"
    
    expenses = get_all_expenses()
    filtered_expenses = filter_expenses_by_period(expenses, period)
    
    if not filtered_expenses:
        period_names = {"day": "сьогодні", "week": "тиждень", "month": "місяць", "year": "рік"}
        await safe_send_message(update, context, f"Немає витрат за {period_names.get(period, period)}.")
        return
    
    users = {}
    for exp in filtered_expenses:
        user = exp['user']
        users[user] = users.get(user, 0) + exp['amount']
    
    if len(users) < 2:
        await safe_send_message(update, context, "Потрібно мінімум 2 користувачі для порівняння.")
        return
    
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
    total = sum(users.values())
    
    period_names = {"day": "сьогодні", "week": "цього тижня", "month": "цього місяця", "year": "цього року"}
    period_name = period_names.get(period, period)
    
    message = f"🏆 Рейтинг витрат {period_name}:\n\n"
    
    for i, (user, amount) in enumerate(sorted_users, 1):
        percentage = (amount / total) * 100
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        message += f"{emoji} {user}: {amount:.2f} грн ({percentage:.1f}%)\n"
    
    if len(sorted_users) >= 2:
        difference = sorted_users[0][1] - sorted_users[1][1]
        message += f"\n💸 Різниця: {difference:.2f} грн"
        
        if difference > 0:
            message += f"\n💡 {sorted_users[0][0]} витратив більше на {difference:.2f} грн"
    
    await safe_send_message(update, context, message)

async def set_family_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановлення сімейного бюджету"""
    global family_budget_amount
    
    if not context.args:
        await safe_send_message(update, context,
            "💰 Встановіть сімейний бюджет:\n"
            "/budget 15000 - встановити бюджет 15000 грн на місяць\n"
            "/budget - подивитись поточний бюджет"
        )
        return
    
    try:
        budget_amount = float(context.args[0])
        family_budget_amount = budget_amount
        
        await safe_send_message(update, context,
            f"💰 Сімейний бюджет встановлено: {budget_amount:.2f} грн на місяць\n"
            f"💡 Використайте /budget_status для перевірки виконання бюджету"
        )
        
    except ValueError:
        await safe_send_message(update, context, "❌ Введіть коректну суму. Приклад: /budget 15000")

async def budget_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статус виконання сімейного бюджету"""
    global family_budget_amount
    
    if family_budget_amount == 0:
        await safe_send_message(update, context,
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
    
    progress_length = 10
    filled_length = int(progress_length * percentage / 100)
    bar = "█" * filled_length + "░" * (progress_length - filled_length)
    message += f"\n📊 Прогрес: {bar} {percentage:.1f}%"
    
    await safe_send_message(update, context, message)

# === ОБРОБКА ГОЛОСОВИХ ПОВІДОМЛЕНЬ ===

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    voice = update.message.voice
    
    if FFMPEG_PATH is None:
        await safe_send_message(update, context,
            "❌ Обробка голосових повідомлень недоступна.\n"
            "FFmpeg не встановлено. Використовуйте текстові повідомлення."
        )
        return
    
    if voice.duration > MAX_VOICE_DURATION:
        await safe_send_message(update, context,
            f"❌ Голосове повідомлення занадто довге. Максимальна тривалість: {MAX_VOICE_DURATION} секунд."
        )
        return
    
    async def send_processing_message():
        return await update.message.reply_text("🎤 Обробляю голосове повідомлення...")
    
    processing_message = await safe_bot_operation(send_processing_message)
    
    ogg_path = None
    wav_path = None
    
    try:
        file = await context.bot.get_file(voice.file_id)
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf_ogg:
            await file.download_to_drive(custom_path=tf_ogg.name)
            ogg_path = tf_ogg.name

        wav_path = ogg_path.replace(".ogg", ".wav")
        
        try:
            # Додаємо timeout для FFmpeg
            result = subprocess.run([
                FFMPEG_PATH, "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path, "-y"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
               timeout=FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            async def edit_message():
                return await processing_message.edit_text("❌ Перевищено час обробки аудіо.")
            await safe_bot_operation(edit_message)
            logger.error("FFmpeg timeout")
            return
        except subprocess.CalledProcessError as e:
            async def edit_message():
                return await processing_message.edit_text("❌ Помилка конвертації аудіо.")
            await safe_bot_operation(edit_message)
            logger.error(f"ffmpeg error: {e}")
            return

        with open(wav_path, "rb") as audio_file:
            content = audio_file.read()

        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=SPEECH_LANGUAGE,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=False
        )

        response = speech_client.recognize(config=config, audio=audio)
        
        if not response.results:
            async def edit_message():
                return await processing_message.edit_text("❌ Не вдалося розпізнати голосове повідомлення. Спробуйте говорити чіткіше.")
            await safe_bot_operation(edit_message)
            return
        
        recognized_text = response.results[0].alternatives[0].transcript
        confidence = response.results[0].alternatives[0].confidence
        
        logger.info(f"Розпізнано: '{recognized_text}' (впевненість: {confidence:.2f})")
        
        async def delete_message():
            return await processing_message.delete()
        await safe_bot_operation(delete_message)
        
        await safe_send_message(update, context, f"🎤 Розпізнано: \"{recognized_text}\"")
        
        await process_and_save(recognized_text, user, update, context)
        
    except Exception as e:
        logger.error(f"Google Speech-to-Text error: {e}")
        async def edit_message():
            return await processing_message.edit_text("❌ Помилка при розпізнаванні голосу. Спробуйте пізніше.")
        await safe_bot_operation(edit_message)
    finally:
        # Гарантоване видалення тимчасових файлів
        for path in [ogg_path, wav_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.debug(f"Видалено тимчасовий файл: {path}")
                except Exception as e:
                    logger.warning(f"Не вдалося видалити файл {path}: {e}")

# === ДОПОМІЖНІ ФУНКЦІЇ ===

async def test_sheets_access():
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
    
    if update and update.effective_message:
        try:
            await safe_send_message(update, context, 
                "❌ Виникла тимчасова помилка. Спробуйте ще раз через кілька секунд.")
        except Exception as e:
            logger.error(f"Не вдалося відправити повідомлення про помилку: {e}")

def add_handlers(app):
    """Додає всі обробники до додатку"""
    # Основні команди
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("today", stats_today))
    app.add_handler(CommandHandler("week", stats_week))
    app.add_handler(CommandHandler("month", stats_month))
    app.add_handler(CommandHandler("prevmonth", stats_prev_month))
    app.add_handler(CommandHandler("year", stats_year))
    app.add_handler(CommandHandler("mystats", my_stats))
    app.add_handler(CommandHandler("top", top_categories))
    
    # Команди управління записами
    app.add_handler(CommandHandler("undo", undo_last_action))
    app.add_handler(CommandHandler("ignore", mark_as_ignored))
    app.add_handler(CommandHandler("recent", show_recent_expenses))
    
    # Команди для пар
    app.add_handler(CommandHandler("compare", compare_users))
    app.add_handler(CommandHandler("family", family_budget))
    app.add_handler(CommandHandler("whospent", who_spent_more))
    app.add_handler(CommandHandler("budget", set_family_budget))
    app.add_handler(CommandHandler("budget_status", budget_status))
    
    # ОБРОБНИК CALLBACK ЗАПИТІВ
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Обробники повідомлень (включаючи кнопку "Меню")
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Додаємо обробник помилок
    app.add_error_handler(error_handler)

# === ПОКРАЩЕННЯ 1: Функція для безпечного polling ===

# === ДОДАЙТЕ ЦІ ФУНКЦІЇ ПЕРЕД async def main(): ===

async def safe_start_polling(app, max_retries=8):
    """Безпечний запуск polling з автоматичним відновленням після конфліктів"""
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logger.info(f"🔄 Спроба запуску polling #{retry_count + 1}")
            
            # КРИТИЧНО ВАЖЛИВО: перевіряємо що application та updater готові
            if not hasattr(app, 'updater') or not app.updater:
                logger.error("❌ Updater не створено! Викличте app.initialize() спочатку")
                raise RuntimeError("Application was not initialized via 'app.initialize()'!")
                
            if not hasattr(app.bot, '_request') or not app.bot._request:
                logger.error("❌ HTTP request не ініціалізовано! Викличте app.initialize() спочатку")
                raise RuntimeError("Application was not initialized via 'app.initialize()'!")
                
            logger.info("✅ Application готовий для запуску polling")
            
            await app.updater.start_polling(
                drop_pending_updates=True,
                bootstrap_retries=5,
                timeout=30,
                read_timeout=35,
                write_timeout=35,
                connect_timeout=20,
                allowed_updates=["message", "callback_query"],
                poll_interval=1.0
            )
            
            logger.info("✅ Polling запущено успішно")
            return True
            
        except Exception as e:
            # Перевіряємо чи це конфлікт
            error_msg = str(e).lower()
            if "conflict" in error_msg or "terminated by other getupdates" in error_msg:
                retry_count += 1
                wait_time = min(30 * retry_count, 180)  # Exponential backoff: 30, 60, 90, 120, 150, 180 сек
                
                logger.warning(f"⚠️ Конфлікт з Telegram API (спроба {retry_count}/{max_retries})")
                logger.warning(f"🕐 Чекаємо {wait_time} секунд перед наступною спробою...")
                
                if retry_count < max_retries:
                    # Додаткове очищення перед повторною спробою
                    try:
                        await app.bot.delete_webhook(drop_pending_updates=True)
                        logger.info("🧹 Додаткове очищення webhook перед повторною спробою")
                        await asyncio.sleep(5)  # Коротка пауза після очищення
                    except Exception as cleanup_error:
                        logger.warning(f"⚠️ Не вдалося виконати додаткове очищення: {cleanup_error}")
                    
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ Всі спроби вичерпано, не вдалося запустити polling")
                    raise
            else:
                logger.error(f"❌ Неочікувана помилка при запуску polling: {e}")
                raise
    
    return False

async def graceful_shutdown(app):
    """Коректне завершення роботи бота з очищенням ресурсів"""
    logger.info("🛑 Початок graceful shutdown...")
    
    try:
        # Зупиняємо updater
        if hasattr(app, 'updater') and app.updater.running:
            logger.info("🔄 Зупиняємо updater...")
            await app.updater.stop()
            logger.info("✅ Updater зупинено")
        
        # Зупиняємо application
        if hasattr(app, 'running') and app.running:
            logger.info("🔄 Зупиняємо application...")
            await app.stop()
            logger.info("✅ Application зупинено")
        
        # Завершуємо application
        logger.info("🔄 Завершуємо application...")
        await app.shutdown()
        logger.info("✅ Application завершено")
        
    except Exception as e:
        logger.error(f"❌ Помилка при graceful shutdown: {e}")
    
    logger.info("✅ Graceful shutdown завершено")

async def clear_webhook_and_pending_updates(bot):
    """Очищає webhook та pending updates для уникнення конфліктів"""
    try:
        logger.info("🧹 Очищення webhook та pending updates...")
        
        # Видаляємо webhook якщо він встановлений
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook очищено")
        
        # Додаткова пауза для стабільності
        await asyncio.sleep(3)
        
    except Exception as e:
        logger.warning(f"⚠️ Не вдалося очистити webhook: {e}")

async def main():
    """Основна функція запуску бота з покращеною обробкою конфліктів"""
    logger.info("🚀 Запуск FinDotBot з покращеною обробкою конфліктів...")
    
    # Налаштування обробників сигналів
    def signal_handler_improved(signum, frame):
        logger.info(f"🛑 Отримано сигнал {signum}. Graceful shutdown...")
        # Встановлюємо флаг для коректного завершення
        asyncio.create_task(cleanup_and_exit())
    
    async def cleanup_and_exit():
        try:
            if 'app' in locals():
                await graceful_shutdown(app)
        except Exception as e:
            logger.error(f"Помилка cleanup: {e}")
        finally:
            # Встановлюємо глобальний флаг для завершення
            logger.info("✅ Cleanup завершено, зупинка event loop")
    
    signal.signal(signal.SIGTERM, signal_handler_improved)
    signal.signal(signal.SIGINT, signal_handler_improved)
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logger.error(f"❌ Файл сервісного акаунту не знайдено: {SERVICE_ACCOUNT_FILE}")
        return
    
    # Тестуємо доступ до Google Sheets
    try:
        await test_sheets_access()
    except Exception as e:
        logger.error(f"❌ Не вдалося протестувати доступ до Google Sheets: {e}")
    
    # Створення Application з покращеними налаштуваннями
    app = create_application()
    
    try:
        # ПРАВИЛЬНА ПОСЛІДОВНІСТЬ ІНІЦІАЛІЗАЦІЇ для python-telegram-bot 20.x з retry логікою
        logger.info("🔄 Ініціалізація application...")
        
        # Retry логіка для ініціалізації
        init_retry_count = 0
        max_init_retries = 3
        
        while init_retry_count < max_init_retries:
            try:
                await app.initialize()
                logger.info(f"✅ Application ініціалізовано успішно (спроба {init_retry_count + 1})")
                break
            except Exception as init_error:
                init_retry_count += 1
                logger.warning(f"⚠️ Помилка ініціалізації (спроба {init_retry_count}/{max_init_retries}): {init_error}")
                
                if init_retry_count >= max_init_retries:
                    logger.error("❌ Не вдалося ініціалізувати application після всіх спроб")
                    raise
                    
                await asyncio.sleep(2)  # Пауза перед наступною спробою
        
        # Перевірка ініціалізації
        if not app.updater:
            logger.error("❌ Updater не створено!")
            return
            
        if not hasattr(app.bot, '_request') or not app.bot._request:
            logger.error("❌ HTTP request не ініціалізовано!")
            return
            
        logger.info("✅ Перевірка ініціалізації пройшла успішно")
        
        # Додавання обробників команд ПІСЛЯ ініціалізації
        add_handlers(app)
        
        await app.start()
        
        # Додаткова пауза для повної ініціалізації Application після start()
        logger.info("⏳ Очікуємо повної ініціалізації Application...")
        await asyncio.sleep(2)
        
        logger.info("✅ FinDotBot ініціалізовано та готовий до роботи...")
        if FFMPEG_PATH:
            logger.info("🎤 Голосові повідомлення увімкнені")
        else:
            logger.warning("⚠️ Голосові повідомлення вимкнені (FFmpeg не знайдено)")
        
        # ОЧИЩЕННЯ ПЕРЕД ЗАПУСКОМ
        await clear_webhook_and_pending_updates(app.bot)
        
        # БЕЗПЕЧНИЙ ЗАПУСК POLLING
        polling_started = await safe_start_polling(app)
        
        if polling_started:
            logger.info("🎯 Бот працює стабільно та очікує повідомлення...")
            
            # Основний цикл роботи з моніторингом
            error_count = 0
            max_errors = 10
            
            while True:
                try:
                    await asyncio.sleep(5)  # Перевірка кожні 5 секунд
                    
                    # Перевіряємо чи updater ще працює
                    if hasattr(app, 'updater') and not app.updater.running:
                        logger.warning("⚠️ Updater зупинився, спробуємо перезапустити...")
                        await safe_start_polling(app)
                    
                    # Скидаємо лічільник помилок при успішній роботі
                    error_count = 0
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "conflict" in error_msg or "terminated by other getupdates" in error_msg:
                        error_count += 1
                        logger.warning(f"⚠️ Конфлікт у головному циклі ({error_count}/{max_errors})")
                        
                        if error_count >= max_errors:
                            logger.error("❌ Забагато конфліктів, завершуємо роботу")
                            break
                        
                        await asyncio.sleep(30)  # Чекаємо перед наступною спробою
                    else:
                        error_count += 1
                        logger.error(f"❌ Помилка у головному циклі: {e} ({error_count}/{max_errors})")
                        
                        if error_count >= max_errors:
                            logger.error("❌ Забагато помилок, завершуємо роботу")
                            break
                        
                        await asyncio.sleep(10)
        else:
            logger.error("❌ Не вдалося запустити polling")
            
    except KeyboardInterrupt:
        logger.info("🛑 Отримано сигнал переривання")
    except Exception as e:
        logger.error(f"❌ Критична помилка: {e}")
    finally:
        # Завжди виконуємо graceful shutdown
        await graceful_shutdown(app)

if __name__ == '__main__':
    # Використовуємо run.py для запуску
    logger.warning("⚠️ Використовуйте run.py для запуску бота замість finedot_bot.py")
    asyncio.run(main())