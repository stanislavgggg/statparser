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

SITE_ID        = "82"   # MAIL
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
                    if part.get_content_type() in ("text/plain", "text/html"):
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


def parse_table(rows: list[list[str]]) -> list[list[str]]:
    """Убирает пустые строки и итоговые строки из CSV"""
    result = []
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        first = row[0].strip().lower() if row else ""
        # Пропускаем итоговые строки (повтор заголовка)
        if first in ("site", "advertiser", "account", "username", "campaign", "login"):
            continue
        result.append(row)
    return result


async def navigate(page, params: str):
    """Навигация внутри браузера без потери сессии"""
    await page.evaluate(f"window.location.href = '/{params}'")
    await page.wait_for_timeout(6000)


async def download_level_csv(page, params: str, save_path: str) -> list[list[str]]:
    """Переходит на страницу и скачивает CSV, возвращает строки данных"""
    await navigate(page, params)

    btn = page.locator('a.buttons-csv')
    count = await btn.count()
    if count == 0:
        print(f"   ⚠️ Кнопка CSV не найдена для {params}")
        return []

    async with page.expect_download(timeout=30000) as dl:
        await page.evaluate("""
            () => {
                const btn = document.querySelector('a.buttons-csv');
                btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }
        """)
    download = await dl.value
    await download.save_as(save_path)

    with open(save_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    header = rows[0]
    data = parse_table(rows[1:])
    return header, data


async def get_advertiser_ids(page, date_str: str) -> list[tuple[str, str]]:
    """Возвращает список (adve_id, advertiser_name) со страницы уровня 1"""
    params = f"?p=siteearnings&start={date_str}&end={date_str}&site={SITE_ID}&ql=yesterday&&submit"
    await navigate(page, params)

    # Извлекаем ссылки на advertisers из таблицы
    links = await page.evaluate("""
        () => {
            const rows = document.querySelectorAll('table tbody tr');
            const result = [];
            rows.forEach(row => {
                const link = row.querySelector('a[href*="adve="]');
                if (link) {
                    const href = link.href;
                    const match = href.match(/adve=(\d+)/);
                    if (match) {
                        result.push({
                            id: match[1],
                            name: link.textContent.trim()
                        });
                    }
                }
            });
            return result;
        }
    """)
    print(f"🔍 Найдено advertisers: {len(links)}")
    return [(l['id'], l['name']) for l in links]


async def get_account_ids(page, date_str: str, adve_id: str) -> list[tuple[str, str]]:
    """Возвращает список (login_id, account_name) со страницы уровня 2"""
    params = f"?p=siteearnings&start={date_str}&end={date_str}&site={SITE_ID}&adve={adve_id}&ql=yesterday&&submit"
    await navigate(page, params)

    links = await page.evaluate("""
        () => {
            const rows = document.querySelectorAll('table tbody tr');
            const result = [];
            rows.forEach(row => {
                const link = row.querySelector('a[href*="login="]');
                if (link) {
                    const href = link.href;
                    const match = href.match(/login=(\d+)/);
                    if (match) {
                        result.push({
                            id: match[1],
                            name: link.textContent.trim()
                        });
                    }
                }
            });
            return result;
        }
    """)
    return [(l['id'], l['name']) for l in links]


# Единый заголовок для всех уровней
FINAL_HEADER = [
    "Date", "Advertiser", "Account", "Campaign",
    "Clicks", "Unique clicks", "Signups", "Active players",
    "Depositors", "Deposits", "Deposit value", "Bonus", "FTD",
    "C/SU %", "C/FTD %", "SU/FTD %", "Player value",
    "CPA", "NDC", "QNDC", "Turnover", "Net revenue",
    "REV income", "CPA income"
]


def normalize_row(date, advertiser, account, campaign, csv_header, csv_row):
    """Приводит строку CSV к единой схеме FINAL_HEADER"""
    # Маппинг: название колонки CSV -> значение
    row_map = {}
    for i, col in enumerate(csv_header):
        if i < len(csv_row):
            row_map[col.strip()] = csv_row[i].strip()

    result = [date, advertiser, account, campaign]
    for col in FINAL_HEADER[4:]:  # пропускаем первые 4 (Date, Advertiser, Account, Campaign)
        result.append(row_map.get(col, ""))
    return result


async def scrape_all(page, date_str: str) -> tuple[list, list[list]]:
    """Скачивает все 3 уровня и возвращает (header, все строки данных)"""
    all_rows = []

    # -- Уровень 1: Advertisers --
    print("📊 Уровень 1: Advertisers...")
    params_l1 = f"?p=siteearnings&start={date_str}&end={date_str}&site={SITE_ID}&ql=yesterday&&submit"
    path_l1 = f"{DOWNLOAD_DIR}/l1_{date_str}.csv"
    result = await download_level_csv(page, params_l1, path_l1)
    if result:
        h1, data1 = result
        print(f"   L1 ВСЕ заголовки: {h1}")
        for row in data1:
            advertiser = row[0] if row else ""
            all_rows.append(normalize_row(date_str, advertiser, "", "", h1, row[1:]))
        print(f"   ✅ {len(data1)} advertisers")

    # -- Уровень 2 & 3: для каждого advertiser --
    adve_list = await get_advertiser_ids(page, date_str)

    for adve_id, adve_name in adve_list:
        print(f"📊 Уровень 2: {adve_name} (adve={adve_id})...")
        params_l2 = f"?p=siteearnings&start={date_str}&end={date_str}&site={SITE_ID}&adve={adve_id}&ql=yesterday&&submit"
        path_l2 = f"{DOWNLOAD_DIR}/l2_{adve_id}_{date_str}.csv"
        result2 = await download_level_csv(page, params_l2, path_l2)
        if result2:
            h2, data2 = result2
            for row in data2:
                account = row[0] if row else ""
                all_rows.append(normalize_row(date_str, adve_name, account, "", h2, row[1:]))

        # Уровень 3
        account_list = await get_account_ids(page, date_str, adve_id)
        for login_id, account_name in account_list:
            print(f"   📊 Уровень 3: {account_name} (login={login_id})...")
            params_l3 = f"?p=siteearnings&start={date_str}&end={date_str}&site={SITE_ID}&adve={adve_id}&login={login_id}&ql=yesterday&&submit"
            path_l3 = f"{DOWNLOAD_DIR}/l3_{adve_id}_{login_id}_{date_str}.csv"
            result3 = await download_level_csv(page, params_l3, path_l3)
            if result3:
                h3, data3 = result3
                for row in data3:
                    campaign = row[0] if row else ""
                    all_rows.append(normalize_row(date_str, adve_name, account_name, campaign, h3, row[1:]))

    return FINAL_HEADER, all_rows


async def main_scrape() -> tuple[list, list[list], str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    date_str = get_yesterday()
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

        body_text = await page.inner_text('body')
        if 'erification' in body_text:
            print("🔐 Нужен код верификации...")
            code = get_voonix_code_from_gmail()
            await page.locator('input[type="text"]').first.fill(code)
            await page.click('input[type="submit"], button[type="submit"]')
            await page.wait_for_timeout(5000)
            print(f"✅ Код введён")

        print(f"✅ Logged in | URL: {page.url}")

        header, all_rows = await scrape_all(page, date_str)
        await browser.close()
        return header, all_rows, date_str


def upload_to_sheets(header: list, all_rows: list[list], date_str: str):
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

    existing = ws.get_all_values()

    # Защита от дублей
    if existing:
        existing_dates = [r[0] for r in existing[1:] if r]
        if date_str in existing_dates:
            print(f"⚠️  {date_str} уже есть — пропускаем")
            return

    # Собираем все строки для записи
    rows_to_write = []
    if not existing and header:
        rows_to_write.append(header)
    rows_to_write.extend(all_rows)

    # Пишем батчами по 500 строк (лимит Google Sheets API)
    batch_size = 500
    for i in range(0, len(rows_to_write), batch_size):
        batch = rows_to_write[i:i + batch_size]
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        print(f"✅ Записано строк {i+1}-{min(i+batch_size, len(rows_to_write))}")
        time.sleep(2)

    print(f"✅ Всего {len(all_rows)} строк → Google Sheets")


async def main():
    header, all_rows, date_str = await main_scrape()
    upload_to_sheets(header, all_rows, date_str)
    print("🎉 All done!")


if __name__ == "__main__":
    asyncio.run(main())
