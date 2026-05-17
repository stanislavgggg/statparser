FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем только Chromium (меньше весит)
RUN playwright install chromium

# Копируем код
COPY main.py .

CMD ["python", "main.py"]
