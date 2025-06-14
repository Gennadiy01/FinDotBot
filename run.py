# run.py - виправлена версія для Render
import asyncio
import logging
import sys
import os
import signal

# Додаємо поточну директорію до шляху
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from finedot_bot import main
from config import HEALTH_CHECK_PORT, LOG_LEVEL
from aiohttp import web

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper()),
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Глобальна змінна для веб-додатку
app = None
runner = None
site = None

async def health_handler(request):
    """Health check endpoint"""
    return web.json_response({
        "status": "healthy",
        "service": "FinDotBot",
        "timestamp": asyncio.get_event_loop().time()
    })

async def start_health_server():
    """Запуск health check сервера"""
    global app, runner, site
    
    try:
        app = web.Application()
        app.router.add_get('/health', health_handler)
        app.router.add_get('/', health_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', HEALTH_CHECK_PORT)
        await site.start()
        
        logger.info(f"Health check сервер запущено на порту {HEALTH_CHECK_PORT}")
        
    except Exception as e:
        logger.error(f"Помилка запуску health check сервера: {e}")

async def stop_health_server():
    """Зупинка health check сервера"""
    global app, runner, site
    
    try:
        if site:
            await site.stop()
        if runner:
            await runner.cleanup()
        logger.info("Health check сервер зупинено")
    except Exception as e:
        logger.error(f"Помилка зупинки health check сервера: {e}")

async def run_bot():
    """Основна функція запуску"""
    try:
        # Запускаємо health check сервер
        logger.info(f"Запуск health check сервера на порту {HEALTH_CHECK_PORT}")
        await start_health_server()
        
        # Запускаємо основного бота
        logger.info("Запуск FinDotBot...")
        await main()
        
    except KeyboardInterrupt:
        logger.info("Отримано сигнал переривання, зупинка бота...")
    except Exception as e:
        logger.error(f"Критична помилка: {e}")
        raise
    finally:
        await stop_health_server()
        logger.info("FinDotBot зупинено")

def signal_handler(signum, frame):
    """Обробник сигналів для graceful shutdown"""
    logger.info(f"Отримано сигнал {signum}, зупинка...")
    raise KeyboardInterrupt()

if __name__ == '__main__':
    # Встановлюємо обробники сигналів
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Програма перервана користувачем")
    except Exception as e:
        logger.error(f"Фатальна помилка: {e}")
        sys.exit(1)