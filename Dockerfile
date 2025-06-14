# Використовуємо офіційний Python образ
FROM python:3.9-slim

# Встановлюємо системні залежності включно з FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Встановлюємо робочу директорію
WORKDIR /app

# Копіюємо файли залежностей
COPY requirements.txt .

# Встановлюємо Python залежності
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо код проекту
COPY . .

# Створюємо директорію для тимчасових файлів
RUN mkdir -p /app/temp

# Експортуємо порт
EXPOSE 10000

# Команда запуску
CMD ["python", "run.py"]