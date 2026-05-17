import asyncio
import os
import csv
import json
import imaplib
import email
import re
import time
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from datetime import datetime, timedelta

BASE_URL  = "https://gggroup.voonix.net"
LOGIN_URL = f"{BASE_URL}/"

USERNAME          = os.environ["VOONIX_USER"]
PASSWORD          = os.environ["VOONIX_PASS"]
GMAIL_USER        = os.environ["GMAIL_USER"]       # stanislav@gggroup.media
GMAIL_APP_PASS    = os.environ["GMAIL_APP_PASS"]   # mhvjjfwsoqqgjnzc
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


def get_voonix_code_from_gmail(timeout=60) -> str:
    """Читает последний код верификации от Voonix из Gmail через IMAP"""
    print("📧 Подключаемся к Gmail IMAP...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASS)
    mail.select("inbox")

    deadline = time.time() + timeout
    while time.time() < deadline:
        # Ищем письма от Voonix за последние 5 минут
        _, data = mail.search(None, 'FROM', '"no-reply@voonix.net"', 'UNSEEN')
        ids = data[0].split()

        if ids:
            # Берём последнее письмо
            _, msg_data = mail.fetch(ids[-1], "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            # Извлекаем текст
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode()
                        break
                    elif part.get_content_type() == "text/html":
                        body = part.get_payload(decode=True).decode()
            else:
                body = msg.get_payload(decode=True).decode()

            # Ищем 6-значный код
            codes = re.findall(r'\b(\d{6})\b', body)
            if codes:
                code = codes[0]
                print(f"✅ Код найден: {code}")
                mail.store(ids[-1], '+FLAGS', '\\Seen')
                mail.logout()
                return code

        print("⏳ Код ещё не пришёл, ждём 5 сек...")
        time.sleep(5)

    mail.logout()
    raise Exception("Код верификации не получен за 60 секунд")


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
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('input[type="submit"][value="Login"]')
        await page.wait_for_timeout(5000)
        await screenshot(page, "1_after_login")
        print(f"   URL: {page.url} | Title: {await page.title()}")

        # -- Проверяем нужен ли код верификации --
        body_text = await page.inner_text('body')
        if 'Verification' in body_text or 'verification' in body_text or 'code' in body_text.lower():
            print("🔐 Нужен код верификации — читаем из Gmail...")
            code = get_voonix_code_from_gmail(timeout=90)

            # Вводим код
            await page.fill('input[type="text"], input[name="code"], input[placeholder*="code"], input[placeholder*="Code"]', code)
            await screenshot(page, "2_code_entered")
            await page.click('input[type="submit"], button[type="submit"]')
            await page.wait_for_timeout(5000)
            await screenshot(page, "3_after_code")
            print(f"✅ Код введён | URL: {page.url}")

        # -- Переходим на отчёт --
        print("⏳ Переходим на отчёт...")
        await page.evaluate(f"window.location.href = '/{report_params}'")
        await page.wait_for_timeout(10000)
        await screenshot(page, "4_report")
        print(f"✅ Report | URL: {page.url} | Title: {await page.title()}")

        btn_count = await page.locator('a.buttons-csv').count()
        rows_count = await page.locator('table tr').count()
        print(f"🔍 a.buttons-csv: {btn_count} | rows: {rows_count}")

        if btn_count == 0:
            body = await page.inner_text('body')
            print(f"Body: {body[:300]}")
            raise Exception("Кнопка CSV не найдена")

        # -- Скачиваем CSV --
        print("⏳ Скачиваем CSV...")
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
