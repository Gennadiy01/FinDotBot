
# 🚨 Діагностика та виправлення проблем FinDotBot на Render Free Plan

## 🔍 Виявлені критичні проблеми

### 1. **Memory Leaks** ⚠️
- `user_last_actions = {}` — необмежене зростання
- `ConnectionMonitor` — накопичення статистики без очищення
- Тимчасові файли можуть не видалятись при помилках

### 2. **Блокуючі операції** ⏱️
- `subprocess.run()` без timeout блокує event loop
- Голосові повідомлення можуть "підвісити" бот
- Google API виклики без timeout

### 3. **Resource Management** 💾
- `TELEGRAM_POOL_SIZE=8` — забагато для free plan
- Відсутність cleanup для Google API клієнтів
- Нескінченні цикли без exit умов

---

## 🛠️ Негайні виправлення (КРИТИЧНІ)

### 1. Виправити обробку тимчасових файлів

**finedot_bot.py:1776** — Додати правильне cleanup:
```python
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... валідація ...
    
    ogg_path = None
    wav_path = None
    
    try:
        # Створення файлів
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf_ogg:
            await file.download_to_drive(custom_path=tf_ogg.name)
            ogg_path = tf_ogg.name
        
        wav_path = ogg_path.replace(".ogg", ".wav")
        
        # FFmpeg з timeout
        try:
            await asyncio.create_subprocess_exec(
                FFMPEG_PATH, "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path, "-y",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                timeout=30  # 30 секунд timeout
            )
        except asyncio.TimeoutError:
            await safe_send_message(update, context, "❌ Перевищено час обробки аудіо")
            return
        
        # ... решта логіки ...
        
    finally:
        # Гарантоване видалення файлів
        for path in [ogg_path, wav_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception as e:
                    logger.warning(f"Не вдалося видалити файл {path}: {e}")
```

### 2. Додати очищення memory leaks

**finedot_bot.py:44** — Обмежити розмір `user_last_actions`:
```python
import collections

# Замість простого dict
user_last_actions = collections.OrderedDict()
MAX_USER_ACTIONS = 100  # Максимум 100 записів

def add_user_action(user_id, action):
    if len(user_last_actions) >= MAX_USER_ACTIONS:
        # Видаляємо найстарший запис
        user_last_actions.popitem(last=False)
    
    user_last_actions[user_id] = action
```

### 3. Оптимізувати конфігурацію для free plan

**config.py:38** — Зменшити споживання ресурсів:
```python
# Оптимізовані налаштування для Render Free Plan
TELEGRAM_POOL_SIZE = int(os.getenv('TELEGRAM_POOL_SIZE', '4'))  # Зменшено з 8
TELEGRAM_TIMEOUT = int(os.getenv('TELEGRAM_TIMEOUT', '15'))     # Зменшено з 20
TELEGRAM_READ_TIMEOUT = int(os.getenv('TELEGRAM_READ_TIMEOUT', '20'))  # Зменшено з 30

# Додаткові налаштування оптимізації
MAX_VOICE_DURATION = int(os.getenv('MAX_VOICE_DURATION', '30'))  # Зменшено з 60
MAX_CONCURRENT_VOICE_PROCESSING = int(os.getenv('MAX_CONCURRENT_VOICE', '2'))
MEMORY_CLEANUP_INTERVAL = int(os.getenv('MEMORY_CLEANUP_INTERVAL', '300'))  # 5 хвилин

# Subprocess timeouts
FFMPEG_TIMEOUT = int(os.getenv('FFMPEG_TIMEOUT', '30'))
GOOGLE_API_TIMEOUT = int(os.getenv('GOOGLE_API_TIMEOUT', '10'))
```

---

## 🔧 Покращення для довгострокової стабільності

### 1. Додати моніторинг пам'яті

**finedot_bot.py** — Додати після імпортів:
```python
import psutil
import gc

async def memory_monitor():
    """Моніторинг використання пам'яті"""
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        logger.info(f"💾 Використання пам'яті: {memory_mb:.1f} MB")
        
        # Якщо пам'ять більше 400MB (для free plan 512MB)
        if memory_mb > 400:
            logger.warning("⚠️ Високе використання пам'яті! Запускається cleanup...")
            
            # Очищуємо старі записи
            if len(user_last_actions) > 50:
                for _ in range(len(user_last_actions) // 2):
                    user_last_actions.popitem(last=False)
            
            # Запускаємо garbage collection
            gc.collect()
            
            new_memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            logger.info(f"💾 Пам'ять після cleanup: {new_memory_mb:.1f} MB")
            
    except Exception as e:
        logger.error(f"Помилка моніторингу пам'яті: {e}")
```

### 2. Покращити health check

**health_server.py** — Додати детальну перевірку:
```python
import psutil

async def health_handler(self, request):
    """Розширений health check з метриками"""
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        cpu_percent = process.cpu_percent()
        
        # Статус здоров'я
        health_status = "healthy"
        if memory_mb > 450:  # Якщо близько до ліміту
            health_status = "warning"
        if memory_mb > 480:  # Критичний рівень
            health_status = "critical"
            
        return web.json_response({
            "status": health_status,
            "service": "FinDotBot",
            "timestamp": asyncio.get_event_loop().time(),
            "metrics": {
                "memory_mb": round(memory_mb, 2),
                "cpu_percent": round(cpu_percent, 2),
                "user_actions_count": len(user_last_actions) if 'user_last_actions' in globals() else 0
            }
        })
        
    except Exception as e:
        return web.json_response({
            "status": "error",
            "error": str(e)
        }, status=500)
```

### 3. Оптимізувати keep-alive

**keepalive.py** — Додати інтелектуальний ping:
```python
async def keep_render_awake():
    """Розумний keep-alive для Render"""
    service_url = os.getenv('RENDER_SERVICE_URL')
    
    if not service_url:
        logger.info("RENDER_SERVICE_URL не встановлено, keep-alive відключено")
        return
    
    logger.info("Keep-alive для Render активовано")
    consecutive_errors = 0
    
    while True:
        try:
            # Збільшуємо інтервал при помилках
            sleep_time = 600 if consecutive_errors < 3 else 900  # 10 або 15 хвилин
            await asyncio.sleep(sleep_time)
            
            timeout = aiohttp.ClientTimeout(total=15)  # Зменшений timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{service_url}/health") as response:
                    if response.status == 200:
                        data = await response.json()
                        memory = data.get('metrics', {}).get('memory_mb', 0)
                        
                        if memory > 400:
                            logger.warning(f"⚠️ Високе використання пам'яті: {memory} MB")
                        else:
                            logger.info(f"✅ Keep-alive OK, пам'ять: {memory} MB")
                            
                        consecutive_errors = 0
                    else:
                        logger.warning(f"Keep-alive статус: {response.status}")
                        consecutive_errors += 1
                        
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Keep-alive помилка #{consecutive_errors}: {e}")
            
            # При багатьох помилках збільшуємо паузу
            if consecutive_errors >= 5:
                await asyncio.sleep(1800)  # 30 хвилин при критичних помилках
            else:
                await asyncio.sleep(120)   # 2 хвилини при звичайних помилках
```

---

## 🎯 Швидкі дії для стабілізації

### **Крок 1: Оновити змінні середовища в Render**

В Dashboard Render → Environment Variables:
```bash
TELEGRAM_POOL_SIZE=4
MAX_VOICE_DURATION=30
FFMPEG_TIMEOUT=30
GOOGLE_API_TIMEOUT=10
LOG_LEVEL=WARNING
```

### **Крок 2: Оновити UptimeRobot**

Збільшити інтервал перевірок:
- **Інтервал**: 10 хвилин замість 5
- **Timeout**: 30 секунд
- **URL**: `https://your-app.onrender.com/health`

### **Крок 3: Додати обмеження в код**

Найкритичніше — обмежити `user_last_actions`:
```python
# В топі finedot_bot.py
from collections import OrderedDict

user_last_actions = OrderedDict()
MAX_USER_ACTIONS = 50  # Обмеження

def cleanup_old_actions():
    while len(user_last_actions) > MAX_USER_ACTIONS:
        user_last_actions.popitem(last=False)
```

---

## 📊 Моніторинг після змін

### Що відстежувати в Render Logs:

1. **Memory warnings**: `Високе використання пам'яті`
2. **Cleanup events**: `Запускається cleanup`
3. **Health check metrics**: `memory_mb`, `cpu_percent`
4. **Voice processing**: Timeout помилки
5. **Keep-alive**: Consecutive errors

### Індикатори проблем:

❌ **Memory > 450MB** — небезпечно близько до ліміту  
❌ **Consecutive errors > 3** — проблеми з підключенням  
❌ **Voice timeout errors** — FFmpeg блокує систему  
❌ **Google API timeouts** — проблеми з зовнішніми сервісами  

---

## 🚀 План впровадження змін

### **Фаза 1 (Терміново - 1 день):**
1. ✅ Додати обмеження `user_last_actions`
2. ✅ Оновити конфігурацію (pool size, timeouts)
3. ✅ Додати proper cleanup для тимчасових файлів

### **Фаза 2 (1-2 дні):**
4. ✅ Впровадити memory monitoring
5. ✅ Покращити health check з метриками
6. ✅ Оптимізувати keep-alive логіку

### **Фаза 3 (Довгостроково):**
7. ✅ Додати Docker multi-stage build для зменшення образу
8. ✅ Реалізувати graceful degradation
9. ✅ Розглянути міграцію з Google Sheets на легшу БД

---

## ⚡ Екстрені заходи

Якщо бот "падає" прямо зараз:

1. **Перезапустити сервіс в Render**
2. **Встановити `LOG_LEVEL=ERROR`** для зменшення I/O
3. **Тимчасово відключити голосові повідомлення**: `MAX_VOICE_DURATION=0`
4. **Збільшити UptimeRobot інтервал до 15 хвилин**

Ці заходи дадуть негайне покращення стабільності на безкоштовному плані Render.

---

*Документ створено для діагностики проблем FinDotBot на Render Free Plan*