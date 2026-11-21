FROM python:3.11-slim

# Установка системных зависимостей (включая FFmpeg для конвертации аудио)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копирование зависимостей и установка
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY . .

# Создание директории для загрузок
RUN mkdir -p downloads

# Порт для Hugging Face Spaces / Render / Koyeb
ENV PORT=7860
EXPOSE 7860

# Запуск бота
CMD ["python", "bot.py"]
