import os
import json

# Telegram Bot
TOKEN = os.getenv('TOKEN', 'ваш_telegram_bot_token')

# Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'ваш_google_sheets_id')
RANGE_NAME = os.getenv('RANGE_NAME', "'Аркуш1'!A:E")

# Google Service Account - може бути як файл, так і JSON рядок
SERVICE_ACCOUNT_FILE = 'service_account.json'
SERVICE_ACCOUNT_JSON = os.getenv('SERVICE_ACCOUNT_JSON')

# Якщо є змінна середовища з JSON, створюємо файл
if SERVICE_ACCOUNT_JSON:
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
        with open(SERVICE_ACCOUNT_FILE, 'w') as f:
            json.dump(service_account_info, f)
    except json.JSONDecodeError:
        print("Помилка при парсингу SERVICE_ACCOUNT_JSON")

# Speech Recognition
SPEECH_LANGUAGE = os.getenv('SPEECH_LANGUAGE', 'uk-UA')
MAX_VOICE_DURATION = int(os.getenv('MAX_VOICE_DURATION', '60'))

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# Health Check & Anti-Sleep
HEALTH_CHECK_PORT = int(os.getenv('PORT', '10000'))  # Зберігаємо вашу логіку з PORT
RENDER_URL = os.getenv('RENDER_URL', 'https://findotbot.onrender.com')
SELF_PING_INTERVAL = int(os.getenv('SELF_PING_INTERVAL', '600'))  # 10 хвилин
ENABLE_SELF_PING = os.getenv('ENABLE_SELF_PING', 'true').lower() == 'true'

# Telegram Connection Settings (нові параметри для стабільності)
TELEGRAM_POOL_SIZE = int(os.getenv('TELEGRAM_POOL_SIZE', '8'))
TELEGRAM_TIMEOUT = int(os.getenv('TELEGRAM_TIMEOUT', '20'))
TELEGRAM_READ_TIMEOUT = int(os.getenv('TELEGRAM_READ_TIMEOUT', '30'))