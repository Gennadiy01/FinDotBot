# run.py
import asyncio
import logging
import sys
import os

# Додаємо поточну директорію до шляху
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from finedot_bot import main
from health_server import start_health_server_in_background
from config import HEALTH_CHECK_PORT, LOG_LEVEL

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper()),
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

async def run_bot():
    """Основна функція запуску"""
    try:
        # Запускаємо health check сервер у фоні
        logger.info(f"Запуск health check сервера на порту {HEALTH_CHECK_PORT}")
        start_health_server_in_background(HEALTH_CHECK_PORT)
        
        # Запускаємо основного бота
        logger.info("Запуск FinDotBot...")
        await main()
        
    except KeyboardInterrupt:
        logger.info("Отримано сигнал переривання, зупинка бота...")
    except Exception as e:
        logger.error(f"Критична помилка: {e}")
        raise
    finally:
        logger.info("FinDotBot зупинено")

if __name__ == '__main__':
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Програма перервана користувачем")
    except Exception as e:
        logger.error(f"Фатальна помилка: {e}")
        sys.exit(1)