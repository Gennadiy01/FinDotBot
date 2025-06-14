# run.py - остаточно спрощений без health check
import asyncio
import logging
import sys
import os

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

async def main():
    """Простий запуск бота без додаткових сервісів"""
    try:
        logger.info("🚀 Запуск FinDotBot...")
        
        # Просто імпорт та запуск бота
        from finedot_bot import main as bot_main
        await bot_main()
        
    except Exception as e:
        logger.error(f"💥 Критична помилка: {e}")
        raise

if __name__ == '__main__':
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
        
        # Запуск бота
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот зупинено користувачем")
    except Exception as e:
        logger.error(f"💥 Фатальна помилка: {e}")
        sys.exit(1)