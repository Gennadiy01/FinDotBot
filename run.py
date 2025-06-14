# run.py
import logging
import sys
import os
from aiohttp import web
import asyncio
import threading
import config

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

async def health_handler(request):
    """Health check endpoint для Render"""
    return web.json_response({
        "status": "healthy", 
        "service": "FinDotBot",
        "port": config.HEALTH_CHECK_PORT
    })

async def start_health_server():
    """Запуск health check сервера"""
    app = web.Application()
    app.router.add_get('/health', health_handler)
    app.router.add_get('/', health_handler)
    
    # Використовуємо PORT з config.py
    port = config.HEALTH_CHECK_PORT
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Health check сервер запущено на порту {port}")
    return runner, site

def main():
    """Простий синхронний запуск"""
    logger.info("🚀 Запуск FinDotBot для Render...")
    
    # Перевіряємо наявність service account файлу
    if not os.path.exists(config.SERVICE_ACCOUNT_FILE):
        logger.error(f"Service account файл не знайдено: {config.SERVICE_ACCOUNT_FILE}")
        if not config.SERVICE_ACCOUNT_JSON:
            logger.error("SERVICE_ACCOUNT_JSON змінна також не встановлена")
            return
        logger.info("SERVICE_ACCOUNT_JSON знайдено, файл має бути створений")
    
    async def run_all():
        # Запуск health check сервера
        runner, site = await start_health_server()
        
        try:
            # Запуск основного бота
            from finedot_bot import main as bot_main
            bot_main()  # Синхронний виклик
        except Exception as e:
            logger.error(f"Помилка запуску бота: {e}")
            raise
        finally:
            await runner.cleanup()
    
    # Запуск через новий event loop
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logger.info("🛑 Бот зупинено")
    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}")
        raise

if __name__ == '__main__':
    main()