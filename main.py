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
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PASS    = os.environ["GMAIL_APP_PASS"]
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


def get_voonix_code_from_gmail(timeout=90) -> str:
    print("📧 Читаем код из Gmail...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASS)
    mail.select("inbox")

    deadline = time.time() + timeout
    while time.time() < deadline:
        _, data = mail.search(None, 'FROM', '"no-reply@voonix.net"', 'UNSEEN')
        ids = data[0].split()
        if ids:
            _, msg_data = mail.fetch(ids[-1], "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct in ("text/plain", "text/html"):
                        body = part.get_payload(decode=True).decode(errors="ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            codes = re.findall(r'\b(\d{6})\b', body)
            if codes:
                print(f"✅ Код: {codes[0]}")
                mail.store(ids[-1], '+FLAGS', '\\Seen')
                mail.logout()
                return codes[0]

        print("⏳ Ждём письмо...")
        time.sleep(5)

    mail.logout()
    raise Exception("Код не получен за 90 секунд")


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

        # -- 2FA если нужен --
        body_text = await page.inner_text('body')
        if 'erification' in body_text:
            print("🔐 Нужен код верификации...")
            code = get_voonix_code_from_gmail()
            code_input = page.locator('input[type="text"]').first
            await code_input.fill(code)
            await page.click('input[type="submit"], button[type="submit"]')
            await page.wait_for_timeout(5000)
            print(f"✅ Код введён | URL: {page.url}")

        # -- Переходим на отчёт --
        print("⏳ Переходим на отчёт...")
        await page.evaluate(f"window.location.href = '/{report_params}'")
        await page.wait_for_timeout(10000)
        print(f"✅ Report | URL: {page.url}")

        btn_count = await page.locator('a.buttons-csv').count()
        if btn_count == 0:
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

    # Читаем CSV
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        print("⚠️  CSV пустой")
        return

    # Отделяем заголовок и строки данных (без итоговой строки "Site")
    header = rows[0]
    data_rows = [
        row for row in rows[1:]
        if any(cell.strip() for cell in row)      # не пустые
        and row[0].strip().lower() != "site"       # не итоговая строка
    ]

    existing = ws.get_all_values()

    # -- Защита от дублей --
    if existing:
        existing_dates = [r[0] for r in existing[1:] if r]
        if date_str in existing_dates:
            print(f"⚠️  {date_str} уже есть — пропускаем")
            return

    # -- Если таблица пустая — пишем заголовок --
    if not existing:
        ws.append_row(["Date"] + header)

    # -- Пишем строки данных --
    uploaded = 0
    for row in data_rows:
        ws.append_row([date_str] + row)
        uploaded += 1

    print(f"✅ {uploaded} строк за {date_str} → Google Sheets")


async def main():
    csv_path, date_str = await download_csv()
    upload_to_sheets(csv_path, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
