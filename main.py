import asyncio
import os
import json
from playwright.async_api import async_playwright

BASE_URL  = "https://gggroup.voonix.net"
LOGIN_URL = f"{BASE_URL}/"

USERNAME = os.environ["VOONIX_USER"]
PASSWORD = os.environ["VOONIX_PASS"]

SCREENSHOT_DIR = "/tmp/voonix/screenshots"


async def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    await page.screenshot(path=f"{SCREENSHOT_DIR}/{name}.png", full_page=True)
    print(f"📸 {name}.png")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(30000)

        # Перехватываем все запросы
        requests_log = []
        async def on_request(req):
            if req.method == "POST":
                try:
                    body = req.post_data
                except:
                    body = None
                requests_log.append({
                    "url": req.url,
                    "method": req.method,
                    "body": body,
                    "headers": dict(req.headers)
                })

        page.on("request", on_request)

        # Загружаем страницу логина
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await screenshot(page, "0_login")
        print(f"Login page title: {await page.title()}")

        # Заполняем и сабмитим
        await page.fill('input[name="username"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('input[type="submit"][value="Login"]')
        await page.wait_for_timeout(6000)
        await screenshot(page, "1_after_submit")

        print(f"\nAfter submit:")
        print(f"  URL: {page.url}")
        print(f"  Title: {await page.title()}")
        body_text = (await page.inner_text('body'))[:400]
        print(f"  Body: {body_text}")

        print(f"\nPOST requests intercepted:")
        for r in requests_log:
            print(f"  URL: {r['url']}")
            print(f"  Body: {r['body']}")
            print(f"  Headers: {dict(list(r['headers'].items())[:5])}")

        # Проверяем все поля формы
        form_fields = await page.evaluate("""
            () => Array.from(document.querySelectorAll('form input'))
                .map(i => ({name: i.name, type: i.type, value: i.value}))
        """)
        print(f"\nForm fields on page: {form_fields}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
