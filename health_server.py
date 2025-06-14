# health_server.py
from aiohttp import web
import asyncio
import threading
import logging

logger = logging.getLogger(__name__)

class HealthCheckServer:
    def __init__(self, port=10000):
        self.port = port
        self.app = None
        self.runner = None
        self.site = None
        
    async def health_handler(self, request):
        """Health check endpoint"""
        return web.json_response({
            "status": "healthy",
            "service": "FinDotBot",
            "timestamp": asyncio.get_event_loop().time()
        })
    
    async def start_server(self):
        """Запуск health check сервера"""
        try:
            self.app = web.Application()
            self.app.router.add_get('/health', self.health_handler)
            self.app.router.add_get('/', self.health_handler)  # Root також відповідає
            
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            
            self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await self.site.start()
            
            logger.info(f"Health check сервер запущено на порту {self.port}")
            
        except Exception as e:
            logger.error(f"Помилка запуску health check сервера: {e}")
    
    async def stop_server(self):
        """Зупинка сервера"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

# Функція для інтеграції з основним ботом
def start_health_server_in_background(port=10000):
    """Запуск health check сервера в окремому потоці"""
    def run_server():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        health_server = HealthCheckServer(port)
        
        try:
            loop.run_until_complete(health_server.start_server())
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("Health check сервер зупинено")
        finally:
            loop.run_until_complete(health_server.stop_server())
            loop.close()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    return thread