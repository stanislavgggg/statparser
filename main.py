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
        await page.wait_for_selector('#menu', timeout=30000)
        await page.wait_for_timeout(1000)
        print(f"✅ Logged in | URL: {page.url}")

        # -- Переходим на отчёт --
        print("⏳ Переходим на отчёт...")
        await page.goto(report_url, wait_until="domcontentloaded")
        await page.wait_for_selector('#sitestats', timeout=30000)
        await page.wait_for_timeout(2000)
        await screenshot(page, "1_report")
        print(f"✅ Report loaded")

        # -- Дебаг: логируем всё что есть в зоне Download --
        download_area = await page.evaluate("""
            () => {
                // Ищем всё вокруг слова Download
                const all = document.querySelectorAll('a, input[type=button], input[type=submit], button');
                return Array.from(all).map(el => ({
                    tag: el.tagName,
                    type: el.type || '',
                    value: el.value || '',
                    text: el.textContent.trim(),
                    href: el.href || '',
                    class: el.className,
                    name: el.name || ''
                })).filter(el => 
                    el.text.includes('CSV') || el.text.includes('Excel') || 
                    el.value.includes('CSV') || el.href.includes('csv') ||
                    el.class.includes('csv') || el.name.includes('csv')
                );
            }
        """)
        print(f"🔍 CSV-related элементы: {download_area}")

        # Пробуем все варианты Download CSV
        selectors = [
            'input[value="CSV"]',
            'a:has-text("CSV")',
            'button:has-text("CSV")',
            'input[name*="csv"]',
            'a[href*="csv"]',
        ]
        downloaded = False
        for sel in selectors:
            try:
                count = await page.locator(sel).count()
                print(f"   {sel} → count: {count}")
                if count > 0:
                    async with page.expect_download(timeout=15000) as dl:
                        await page.locator(sel).first.click()
                    download = await dl.value
                    await download.save_as(save_path)
                    downloaded = True
                    print(f"✅ Downloaded via: {sel}")
                    break
            except Exception as e:
                print(f"   ⚠️ {sel}: {e}")

        if not downloaded:
            await screenshot(page, "2_failed")
            raise Exception("Не удалось скачать CSV")

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
