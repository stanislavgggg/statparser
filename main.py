import asyncio
import os
import csv
import json
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

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
    report_params = f"?p=siteearnings&start={date_str}&end={date_str}&ql=yesterday&&submit"

    print(f"📅 Дата: {date_str}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(60000)

        # -- Логин --
        print("⏳ Логинимся...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await screenshot(page, "0_login_form")

        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await screenshot(page, "1_filled")

        await page.click('input[type="submit"][value="Login"]')
        await page.wait_for_timeout(8000)
        await screenshot(page, "2_after_login")

        url = page.url
        title = await page.title()
        body = (await page.inner_text('body'))[:600]
        cookies = await context.cookies()
        print(f"✅ After login:")
        print(f"   URL: {url}")
        print(f"   Title: {title}")
        print(f"   Body: {body}")
        print(f"   Cookies: {[(c['name'], c['value'][:30]) for c in cookies]}")

        # Если всё ещё на странице логина — проблема в логине
        if 'login' in title.lower() or 'login' in body.lower()[:100]:
            print("❌ Логин не прошёл — остались на странице логина!")
            raise Exception("Логин не прошёл")

        # -- Переход через window.location --
        print("⏳ Navigating to report...")
        await page.evaluate(f"window.location.href = '/{report_params}'")
        await page.wait_for_timeout(10000)
        await screenshot(page, "3_report")
        print(f"   URL: {page.url}")
        print(f"   Title: {await page.title()}")

        btn_count = await page.locator('a.buttons-csv').count()
        print(f"🔍 a.buttons-csv: {btn_count}")

        if btn_count == 0:
            raise Exception("Кнопка CSV не найдена")

        print("⏳ Downloading CSV...")
        async with page.expect_download(timeout=30000) as dl:
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('a.buttons-csv');
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                }
            """)
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
