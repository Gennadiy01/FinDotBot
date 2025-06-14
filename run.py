# run.py - виправлено для Render
import asyncio
import logging
import sys
import os
from aiohttp import web
import signal

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

class BotRunner:
    def __init__(self):
        self.bot_task = None
        self.web_app = None
        self.runner = None
        self.site = None
        self.shutdown_event = asyncio.Event()

    async def health_handler(self, request):
        """Health check endpoint"""
        return web.Response(text="FinDotBot is running!", status=200)

    async def start_web_server(self):
        """Запуск веб-сервера для health check"""
        self.web_app = web.Application()
        self.web_app.router.add_get('/health', self.health_handler)
        self.web_app.router.add_get('/', self.health_handler)
        
        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()
        
        port = int(os.environ.get('PORT', 10000))
        self.site = web.TCPSite(self.runner, '0.0.0.0', port)
        await self.site.start()
        
        logger.info(f"Health check сервер запущено на порту {port}")

    async def start_bot(self):
        """Запуск Telegram бота"""
        try:
            from finedot_bot import main as bot_main
            await bot_main()
        except Exception as e:
            logger.error(f"Помилка запуску бота: {e}")
            raise

    async def run(self):
        """Головна функція запуску"""
        try:
            logger.info("🚀 Запуск FinDotBot з health check...")
            
            # Запускаємо веб-сервер
            await self.start_web_server()
            
            # Запускаємо бота в окремому таску
            self.bot_task = asyncio.create_task(self.start_bot())
            
            # Чекаємо завершення або сигнал
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=None)
            except asyncio.CancelledError:
                pass
            
        except Exception as e:
            logger.error(f"💥 Критична помилка: {e}")
            raise
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Очищення ресурсів"""
        logger.info("Зупинка сервісів...")
        
        # Зупиняємо бота
        if self.bot_task and not self.bot_task.done():
            self.bot_task.cancel()
            try:
                await asyncio.wait_for(self.bot_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        
        # Зупиняємо веб-сервер
        if self.runner:
            await self.runner.cleanup()
        
        logger.info("Всі сервіси зупинено")

    def signal_handler(self, signum, frame):
        """Обробник сигналів"""
        logger.info(f"Отримано сигнал {signum}. Зупиняємо...")
        self.shutdown_event.set()

def main():
    """Простий запуск без зайвих ускладнень"""
    try:
        # Перевіряємо конфігурацію
        try:
            from config import SERVICE_ACCOUNT_FILE, TOKEN
            
            if not os.path.exists(SERVICE_ACCOUNT_FILE):
                logger.warning(f"Service account файл не знайдено: {SERVICE_ACCOUNT_FILE}")
            
            if not TOKEN or TOKEN == 'ваш_telegram_bot_token':
                logger.warning("TOKEN може бути не встановлено")
                
        except ImportError as e:
            logger.error(f"Помилка імпорту config: {e}")
        
        # Створюємо і запускаємо бота
        runner = BotRunner()
        
        # Налаштовуємо обробники сигналів
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, runner.signal_handler)
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, runner.signal_handler)
        
        # Запускаємо event loop
        try:
            asyncio.run(runner.run())
        except KeyboardInterrupt:
            logger.info("🛑 Бот зупинено користувачем")
        except Exception as e:
            logger.error(f"💥 Фатальна помилка: {e}")
            sys.exit(1)
        
    except Exception as e:
        logger.error(f"💥 Помилка ініціалізації: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()