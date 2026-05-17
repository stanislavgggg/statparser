import asyncio
import os
import csv
import json
import requests
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


# ---------------------------------------------------------------------------
# 1. Логин через requests → получаем куки → передаём в Playwright
# ---------------------------------------------------------------------------
async def download_csv() -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    date_str = get_yesterday()
    save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")
    report_url = (
        f"{BASE_URL}/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit"
    )
    print(f"📅 Дата: {date_str}")

    # -- Логин через requests --
    print("⏳ Логинимся через requests...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    resp = session.post(LOGIN_URL, data={"username": USERNAME, "password": PASSWORD})
    cookies = dict(session.cookies)
    print(f"✅ Login: {resp.status_code} | Cookies: {cookies}")

    if not cookies:
        raise Exception("Логин не прошёл — куки пустые")

    # -- Передаём куки в Playwright --
    print("⏳ Открываем браузер с куками сессии...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
        )

        # Добавляем куки из requests сессии в Playwright контекст
        playwright_cookies = [
            {
                "name": name,
                "value": value,
                "domain": "gggroup.voonix.net",
                "path": "/",
            }
            for name, value in cookies.items()
        ]
        await context.add_cookies(playwright_cookies)
        print(f"🍪 Передано куки в Playwright: {[c['name'] for c in playwright_cookies]}")

        page = await context.new_page()
        page.set_default_timeout(60000)

        # -- Переходим на отчёт (уже с куками) --
        print("⏳ Открываем отчёт...")
        await page.goto(report_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(5000)
        await screenshot(page, "1_report")

        title = await page.title()
        url_now = page.url
        print(f"📄 Title: {title} | URL: {url_now}")

        # Проверяем что не на странице логина
        body = await page.inner_text('body')
        print(f"📄 Body (200): {body[:200]}")

        # Ждём кнопку CSV
        btn_count = await page.locator('a.buttons-csv').count()
        rows_count = await page.locator('table tr').count()
        print(f"🔍 a.buttons-csv: {btn_count} | table tr: {rows_count}")

        if btn_count == 0:
            await screenshot(page, "2_no_btn")
            raise Exception("Кнопка CSV не найдена после передачи куки")

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    csv_path, date_str = await download_csv()
    upload_to_sheets(csv_path, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
