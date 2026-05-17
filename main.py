import os
import csv
import json
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL  = "https://gggroup.voonix.net"
LOGIN_URL = f"{BASE_URL}/"

USERNAME          = os.environ["VOONIX_USER"]
PASSWORD          = os.environ["VOONIX_PASS"]
SHEET_ID          = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

DOWNLOAD_DIR = "/tmp/voonix"


def get_yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 1. Скачать CSV через requests (сессия сохраняется автоматически)
# ---------------------------------------------------------------------------
def download_csv() -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    date_str = get_yesterday()
    save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")

    print(f"📅 Дата: {date_str}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": BASE_URL,
    })

    # -- Логин --
    print("⏳ Логинимся...")
    login_data = {
        "username": USERNAME,
        "password": PASSWORD,
    }
    resp = session.post(LOGIN_URL, data=login_data, allow_redirects=True)
    print(f"✅ Login response: {resp.status_code} | URL: {resp.url}")
    print(f"🍪 Cookies: {dict(session.cookies)}")
    print(f"📄 Body snippet: {resp.text[:300]}")

    # -- Запрос отчёта --
    print("⏳ Запрашиваем отчёт...")
    report_url = (
        f"{BASE_URL}/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit"
    )
    resp2 = session.get(report_url)
    print(f"✅ Report response: {resp2.status_code} | URL: {resp2.url}")
    print(f"📄 Body snippet: {resp2.text[:300]}")

    # Проверяем что не редиректнуло на логин
    if "Login" in resp2.text[:100] or resp2.url != report_url:
        raise Exception(f"Сессия не сохранилась — редирект на логин. URL: {resp2.url}")

    # -- Скачать CSV --
    # DataTables генерирует CSV на клиенте через JS — через requests не получим кнопку
    # Но попробуем найти прямой export endpoint
    print("⏳ Пробуем скачать CSV напрямую...")
    csv_url = (
        f"{BASE_URL}/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit&export=csv"
    )
    resp3 = session.get(csv_url)
    print(f"✅ CSV response: {resp3.status_code} | Content-Type: {resp3.headers.get('content-type')}")
    print(f"📄 CSV snippet: {resp3.text[:200]}")

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(resp3.text)

    print(f"✅ Saved → {save_path}")
    return save_path, date_str


# ---------------------------------------------------------------------------
# 2. Загрузить в Google Sheets
# ---------------------------------------------------------------------------
def upload_to_sheets(csv_path: str, date_str: str):
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.sheet1

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        print("⚠️  CSV пустой")
        return

    existing = ws.get_all_values()
    if existing:
        existing_dates = [row[0] for row in existing[1:] if row]
        if date_str in existing_dates:
            print(f"⚠️  {date_str} уже есть — пропускаем")
            return

    if not existing:
        ws.append_row(["Date"] + rows[0])
        data_rows = rows[1:]
    else:
        data_rows = rows[1:]

    uploaded = 0
    for row in data_rows:
        if any(cell.strip() for cell in row):
            ws.append_row([date_str] + row)
            uploaded += 1

    print(f"✅ {uploaded} rows → Google Sheets")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    csv_path, date_str = download_csv()
    upload_to_sheets(csv_path, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    main()
