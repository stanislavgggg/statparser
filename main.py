import asyncio
import os
import csv
import json
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Config — все значения берутся из env vars Railway
# ---------------------------------------------------------------------------
LOGIN_URL = "https://gggroup.voonix.net/"

USERNAME          = os.environ["VOONIX_USER"]
PASSWORD          = os.environ["VOONIX_PASS"]
SHEET_ID          = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

DOWNLOAD_DIR   = "/tmp/voonix"
SCREENSHOT_DIR = "/tmp/voonix/screenshots"


def get_report_url() -> tuple[str, str]:
    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    url = (
        f"https://gggroup.voonix.net/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit"
    )
    return url, date_str


async def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    await page.screenshot(path=f"{SCREENSHOT_DIR}/{name}.png", full_page=True)
    print(f"📸 {name}.png")


# ---------------------------------------------------------------------------
# 1. Скачать CSV с Voonix
# ---------------------------------------------------------------------------
async def download_csv() -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    report_url, date_str = get_report_url()
    save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")

    print(f"📅 Дата отчёта: {date_str}")
    print(f"🔗 URL: {report_url}")

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
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await screenshot(page, "1_after_login")
        print(f"✅ Logged in | URL: {page.url}")

        # -- Переход на отчёт напрямую --
        print("⏳ Переходим на отчёт...")
        await page.goto(report_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        await screenshot(page, "2_report_page")
        print(f"✅ Report loaded | URL: {page.url}")

        # -- Скачать CSV --
        print("⏳ Скачиваем CSV...")
        downloaded = False
        for sel in ['a:has-text("CSV")', 'text=CSV', 'a[href*="csv"]']:
            try:
                async with page.expect_download(timeout=30000) as dl:
                    await page.click(sel, timeout=8000)
                download = await dl.value
                await download.save_as(save_path)
                downloaded = True
                print(f"✅ CSV downloaded via: {sel}")
                break
            except Exception as e:
                print(f"   ⚠️ {sel} — {e}")

        if not downloaded:
            await screenshot(page, "3_csv_failed")
            raise Exception("Не удалось найти кнопку CSV")

        print(f"✅ CSV saved → {save_path}")
        await browser.close()
        return save_path, date_str


# ---------------------------------------------------------------------------
# 2. Загрузить CSV в Google Sheets (с защитой от дублей)
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
        print("⚠️  CSV пустой, пропускаем")
        return

    existing = ws.get_all_values()

    # -- Защита от дублей --
    # Если в таблице уже есть строки с этой датой — пропускаем
    if existing:
        existing_dates = [row[0] for row in existing[1:] if row]  # первая колонка = Date
        if date_str in existing_dates:
            print(f"⚠️  Данные за {date_str} уже есть в таблице — пропускаем")
            return

    # Если таблица пустая — пишем заголовок
    if not existing:
        header = ["Date"] + rows[0]
        ws.append_row(header)
        data_rows = rows[1:]
    else:
        data_rows = rows[1:]  # заголовок уже есть

    # Записываем строки данных
    uploaded = 0
    for row in data_rows:
        if any(cell.strip() for cell in row):
            ws.append_row([date_str] + row)
            uploaded += 1

    print(f"✅ {uploaded} rows за {date_str} → Google Sheets")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    csv_path, date_str = await download_csv()
    upload_to_sheets(csv_path, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
