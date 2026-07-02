import asyncio
import time
import os
import re
import random
from urllib.parse import urlencode, quote_plus
from database import upsert_social

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'screenshots')
BLOCKED_DOMAINS = {
    'wikipedia.org', 'wikimedia.org', 'tripadvisor.com', 'google.com',
    'youtube.com', 'facebook.com', 'shopee.tw', 'pchome.com.tw',
    'momo.com', 'amazon.', 'booking.com', 'agoda.com',
}
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def _is_blocked(url: str) -> bool:
    return any(d in url for d in BLOCKED_DOMAINS)


async def scrape_attraction(name: str):
    """Google 搜尋景點相關部落格文章，用 Playwright 截圖並存入 SQLite。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print('[scraper] playwright 未安裝，跳過爬蟲')
        return

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    queries = [
        f'{name} 旅遊 推薦 部落格',
        f'{name} 打卡 景點 心得',
        f'{name} 遊記 好玩',
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1280, 'height': 800},
        )

        collected_urls: list[tuple[str, str]] = []

        for q in queries:
            if len(collected_urls) >= 5:
                break
            try:
                page = await context.new_page()
                search_url = f'https://www.google.com/search?{urlencode({"q": q, "num": 10})}'
                await page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
                await asyncio.sleep(random.uniform(1.5, 3))

                links = await page.eval_on_selector_all(
                    'a[href]',
                    'els => els.map(e => ({href: e.href, text: e.innerText.trim()}))'
                )
                for link in links:
                    href = link.get('href', '')
                    text = link.get('text', '')
                    if not href.startswith('http'):
                        continue
                    if _is_blocked(href):
                        continue
                    if len(collected_urls) >= 5:
                        break
                    if href not in [u for u, _ in collected_urls]:
                        collected_urls.append((href, text[:80]))
                await page.close()
            except Exception as e:
                print(f'[scraper] 搜尋失敗 {q}: {e}')

        # 對每個 URL 截圖
        for url, title in collected_urls:
            await asyncio.sleep(random.uniform(2, 5))
            try:
                page = await context.new_page()
                await page.goto(url, wait_until='networkidle', timeout=20000)
                await asyncio.sleep(1.5)

                safe_name = re.sub(r'[^\w]', '_', name)[:30]
                safe_url = re.sub(r'[^\w]', '_', url)[:40]
                fname = f'{safe_name}_{safe_url}_{int(time.time())}.jpg'
                fpath = os.path.join(SCREENSHOT_DIR, fname)
                await page.screenshot(path=fpath, type='jpeg', quality=75, full_page=False)

                real_title = await page.title() or title
                upsert_social(name, url, real_title, fpath, time.time())
                print(f'[scraper] 截圖完成: {name} → {url}')
                await page.close()
            except Exception as e:
                print(f'[scraper] 截圖失敗 {url}: {e}')

        await browser.close()


async def scrape_loop(attraction_names: list[str], interval_hours: float = 6):
    """每 interval_hours 小時爬一輪所有景點。"""
    while True:
        for name in attraction_names:
            await scrape_attraction(name)
            await asyncio.sleep(5)
        await asyncio.sleep(interval_hours * 3600)
