# keepalive.py - анти-засипання для Render
import asyncio
import aiohttp
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

async def keep_render_awake():
    """Простий keep-alive для Render"""
    service_url = os.getenv('RENDER_SERVICE_URL')
    
    if not service_url:
        logger.info("RENDER_SERVICE_URL не встановлено, keep-alive відключено")
        return
    
    logger.info("Keep-alive для Render активовано")
    
    while True:
        try:
            await asyncio.sleep(600)  # Чекаємо 10 хвилин
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{service_url}/health", timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"Keep-alive пінг: {datetime.now().strftime('%H:%M:%S')}")
                    else:
                        logger.warning(f"Keep-alive статус: {response.status}")
                        
        except Exception as e:
            logger.error(f"Keep-alive помилка: {e}")
            await asyncio.sleep(60)  # При помилці чекаємо менше