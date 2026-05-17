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
LOGIN_URL = "https://gggroup.voonix.net/"

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
        f"https://gggroup.voonix.net/?p=siteearnings"
        f"&start={date_str}&end={date_str}&ql=yesterday&&submit"
    )
    print(f"📅 Дата: {date_str}")
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
        await page.wait_for_timeout(3000)
        print(f"✅ Logged in | URL: {page.url}")

        # -- Переход на отчёт по URL --
        print("⏳ Переходим на отчёт...")
        await page.goto(report_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        await screenshot(page, "1_report_loaded")
        print(f"✅ URL: {page.url}")

        # -- Кликаем кнопку View чтобы загрузить данные --
        print("⏳ Кликаем View...")
        try:
            await page.click('input[value="View"], button:has-text("View")', timeout=5000)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(5000)
            await screenshot(page, "2_after_view")
            print("✅ View clicked")
        except Exception as e:
            print(f"   View не найден: {e}")

        # Дебаг
        rows = await page.locator('table tr').count()
        btns = await page.locator('a.buttons-csv').count()
        body_snippet = (await page.inner_text('body'))[:300]
        print(f"🔍 table tr: {rows} | a.buttons-csv: {btns}")
        print(f"📄 Body: {body_snippet}")

        if btns == 0:
            await screenshot(page, "3_no_btn")
            raise Exception("Кнопка CSV не найдена")

        # -- Скачиваем CSV --
        print("⏳ Скачиваем CSV...")
        async with page.expect_download(timeout=30000) as dl:
            await page.locator('a.buttons-csv').first.click()
        download = await dl.value
        await download.save_as(save_path)
        print(f"✅ CSV saved → {save_path}")

        await browser.close()
        return save_path, date_str


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
