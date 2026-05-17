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


# ---------------------------------------------------------------------------
# 1. Скачать CSV с Voonix
# ---------------------------------------------------------------------------
async def download_csv() -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    date_str = get_yesterday()
    save_path = os.path.join(DOWNLOAD_DIR, f"voonix_{date_str}.csv")

    print(f"📅 Дата отчёта: {date_str}")

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
        await screenshot(page, "1_after_login")
        print(f"✅ Logged in | URL: {page.url}")

        # -- JS клик по ссылке Site earnings (игнорирует видимость) --
        print("⏳ JS-клик по Site earnings...")
        clicked = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (a.href && a.href.includes('p=siteearnings')) {
                        a.click();
                        return 'clicked: ' + a.href;
                    }
                }
                // Логируем все ссылки для диагностики
                return 'not found. links: ' + Array.from(links).map(a => a.href + '|' + a.textContent.trim()).slice(0,20).join(' || ');
            }
        """)
        print(f"🔍 JS click result: {clicked}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(5000)
        await screenshot(page, "2_site_earnings")
        print(f"✅ After JS click | URL: {page.url}")

        # -- JS клик по Yesterday --
        print("⏳ JS-клик по Yesterday...")
        clicked2 = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (a.textContent.trim() === 'Yesterday') {
                        a.click();
                        return 'clicked Yesterday';
                    }
                }
                return 'Yesterday not found. links: ' + Array.from(links).map(a => a.textContent.trim()).filter(t=>t).slice(0,30).join(' | ');
            }
        """)
        print(f"🔍 Yesterday click: {clicked2}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(5000)
        await screenshot(page, "3_after_yesterday")
        print(f"✅ After Yesterday | URL: {page.url}")

        # Проверяем таблицу
        rows_count = await page.locator('table tr').count()
        btn_count = await page.locator('a.buttons-csv').count()
        print(f"🔍 Строк в таблице: {rows_count} | a.buttons-csv: {btn_count}")

        if btn_count == 0:
            # Дополнительный дебаг
            all_a = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a'))
                    .map(a => a.className + ' :: ' + a.textContent.trim())
                    .filter(x => x.trim() !== ' :: ')
                    .slice(0, 40)
            """)
            print("🔍 Все ссылки:")
            for a in all_a:
                print(f"   {a[:120]}")
            await screenshot(page, "4_no_csv_btn")
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

    if existing:
        existing_dates = [row[0] for row in existing[1:] if row]
        if date_str in existing_dates:
            print(f"⚠️  Данные за {date_str} уже есть — пропускаем")
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
