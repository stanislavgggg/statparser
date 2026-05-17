import asyncio
import os
import csv
import json
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
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

DOWNLOAD_DIR   = "/tmp/voonix"
SCREENSHOT_DIR = "/tmp/voonix/screenshots"


def get_yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


async def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    await page.screenshot(path=f"{SCREENSHOT_DIR}/{name}.png", full_page=True)
    print(f"📸 {name}.png")


async def download_csv() -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    date_str = get_yesterday()
    save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")
    report_url = (
        f"{BASE_URL}/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit"
    )
    print(f"📅 Дата: {date_str}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(60000)

        # -- Логин --
        print("⏳ Логинимся...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('input[type="submit"][value="Login"]')

        # Ждём появления элемента главной страницы после логина
        # Из HTML видно: после логина есть div id="menu"
        await page.wait_for_selector('#menu', timeout=30000)
        await page.wait_for_timeout(1000)
        print(f"✅ Logged in | URL: {page.url}")
        await screenshot(page, "1_logged_in")

        # -- Переходим на отчёт в том же окне --
        print("⏳ Переходим на отчёт...")
        await page.goto(report_url, wait_until="domcontentloaded")

        # Ждём появления div#sitestats — это контейнер с таблицей
        print("⏳ Ждём таблицу (#sitestats)...")
        await page.wait_for_selector('#sitestats', timeout=30000)

        # Ждём появления кнопки CSV которую рендерит DataTables
        print("⏳ Ждём кнопку CSV (.buttons-csv)...")
        await page.wait_for_selector('a.buttons-csv', timeout=30000)
        await page.wait_for_timeout(500)

        rows_count = await page.locator('table tr').count()
        print(f"✅ Таблица загружена | tr: {rows_count}")
        await screenshot(page, "2_report")

        # -- Скачиваем CSV --
        print("⏳ Скачиваем CSV...")
        async with page.expect_download(timeout=30000) as dl:
            await page.locator('a.buttons-csv').first.click()
        download = await dl.value
        await download.save_as(save_path)
        print(f"✅ CSV saved → {save_path}")

        await browser.close()
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


async def main():
    csv_path, date_str = await download_csv()
    upload_to_sheets(csv_path, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
