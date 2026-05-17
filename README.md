# Voonix → Google Sheets scraper

Ежедневно логинится на gggroup.voonix.net, скачивает CSV за вчера
и добавляет строки в Google Sheets.

---

## Быстрый старт

### 1. Google Cloud — Service Account

1. Открой https://console.cloud.google.com
2. Создай проект (или используй существующий)
3. Включи APIs:
   - Google Sheets API
   - Google Drive API
4. IAM & Admin → Service Accounts → Create Service Account
5. Скачай JSON ключ
6. Открой свою Google Таблицу → Share → добавь email сервис-аккаунта
   (выглядит как `xxx@xxx.iam.gserviceaccount.com`) с правом **Editor**

### 2. Переменные окружения Railway

| Variable           | Значение                                      |
|--------------------|-----------------------------------------------|
| `VOONIX_USER`      | Логин на voonix                               |
| `VOONIX_PASS`      | Пароль на voonix                              |
| `GOOGLE_SHEET_ID`  | ID таблицы из URL (между /d/ и /edit)         |
| `GOOGLE_CREDS_JSON`| Весь JSON файл сервис-аккаунта одной строкой  |

Как превратить JSON в одну строку:
```bash
cat service_account.json | tr -d '\n'
```

### 3. Deploy на Railway

```bash
# Установи Railway CLI если нет
npm install -g @railway/cli

# Логин
railway login

# Создай проект
railway init

# Задеплой
railway up
```

Или просто подключи GitHub репозиторий в Railway UI.

### 4. Расписание

В `railway.toml` уже настроен крон: `0 8 * * *` = каждый день в 08:00 UTC.

Если нужно другое время — измени строку `cronSchedule`.
Примеры:
- `0 7 * * *`  — 07:00 UTC (09:00 Москва / 08:00 Мадрид летом)
- `0 6 * * 1-5` — только по будням в 06:00 UTC

---

## Структура таблицы

| Date       | Site     | Clicks | Unique clicks | Signups | FTD | ... |
|------------|----------|--------|---------------|---------|-----|-----|
| 2026-05-17 | META     | 1098   | 0             | 520     | 69  | ... |
| 2026-05-17 | MAIL     | 1932   | 858           | 254     | 29  | ... |

Каждый запуск добавляет новые строки с датой запуска.
Заголовок создаётся автоматически при первом запуске.

---

## Локальный запуск (для теста)

```bash
pip install -r requirements.txt
playwright install chromium

export VOONIX_USER="твой_логин"
export VOONIX_PASS="твой_пароль"
export GOOGLE_SHEET_ID="1BxiM..."
export GOOGLE_CREDS_JSON='{"type":"service_account",...}'

python main.py
```
