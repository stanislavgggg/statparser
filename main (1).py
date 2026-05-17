import asyncio
import os
import csv
import json
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from datetime import datetime

# ---------------------------------------------------------------------------
# Config — все значения берутся из env vars Railway
# ---------------------------------------------------------------------------
LOGIN_URL   = "https://gggroup.voonix.net/"
REPORT_URL  = "https://gggroup.voonix.net/?p=siteearnings"

USERNAME          = os.environ["VOONIX_USER"]
PASSWORD          = os.environ["VOONIX_PASS"]
SHEET_ID          = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]   # весь JSON одной строкой

DOWNLOAD_DIR = "/tmp/voonix"


# ---------------------------------------------------------------------------
# 1. Скачать CSV с Voonix
# ---------------------------------------------------------------------------
async def download_csv() -> str:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # -- Логин --
        print("⏳ Открываем страницу логина...")
        await page.goto(LOGIN_URL)
        await page.wait_for_load_state("networkidle")

        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        print("✅ Logged in")

        # -- Переход на отчёт --
        await page.goto(REPORT_URL)
        await page.wait_for_load_state("networkidle")
        print("✅ Report page loaded")

        # -- Клик Yesterday --
        await page.click('a:text("Yesterday")')
        await page.wait_for_load_state("networkidle")
        print("✅ Yesterday selected")

        # -- Скачать CSV --
        date_str  = datetime.now().strftime("%Y-%m-%d")
        save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")

        async with page.expect_download() as dl:
            await page.click('a:text("CSV")')

        download = await dl.value
        await download.save_as(save_path)
        print(f"✅ CSV saved → {save_path}")

        await browser.close()
        return save_path


# ---------------------------------------------------------------------------
# 2. Загрузить CSV в Google Sheets
# ---------------------------------------------------------------------------
def upload_to_sheets(csv_path: str):
    # Авторизация через service account
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
    ws = sh.sheet1   # первый лист

    # Читаем CSV
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        print("⚠️  CSV пустой, пропускаем")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")

    # Если таблица пустая — пишем заголовок
    existing = ws.get_all_values()
    if not existing:
        header = ["Date"] + rows[0]
        ws.append_row(header)
        data_rows = rows[1:]
    else:
        data_rows = rows[1:]   # заголовок уже есть — пропускаем

    # Записываем строки данных
    uploaded = 0
    for row in data_rows:
        if any(cell.strip() for cell in row):   # пропускаем пустые строки
            ws.append_row([date_str] + row)
            uploaded += 1

    print(f"✅ {uploaded} rows → Google Sheets")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    csv_path = await download_csv()
    upload_to_sheets(csv_path)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
