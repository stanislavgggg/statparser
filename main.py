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
        print("⏳ Открываем страницу логина...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await screenshot(page, "0_login_page")

        # Дебаг: что на странице логина
        title = await page.title()
        url = page.url
        body = await page.inner_text('body')
        print(f"📄 Login page title: {title} | URL: {url}")
        print(f"📄 Body text (first 500): {body[:500]}")

        print("⏳ Заполняем форму...")
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await screenshot(page, "1_filled")

        print("⏳ Кликаем Login...")
        await page.click('input[type="submit"][value="Login"]')
        await page.wait_for_timeout(5000)
        await screenshot(page, "2_after_login")

        title2 = await page.title()
        url2 = page.url
        body2 = await page.inner_text('body')
        print(f"📄 After login title: {title2} | URL: {url2}")
        print(f"📄 Body text (first 800): {body2[:800]}")

        # Считаем ссылки
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a'))
                .map(a => a.href + ' | ' + a.textContent.trim())
                .filter(x => x.trim() !== ' | ')
                .slice(0, 30)
        """)
        print(f"🔍 Ссылки после логина ({len(links)}):")
        for l in links:
            print(f"   {l[:150]}")

        await browser.close()
        raise Exception("ДИАГНОСТИКА — смотри логи выше")


async def main():
    await download_csv()


if __name__ == "__main__":
    asyncio.run(main())
