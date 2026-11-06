FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование приложения
COPY . .

# Порт
# EXPOSE 3239

# Запуск как модуль
CMD ["python", "-m", "app.main"]
