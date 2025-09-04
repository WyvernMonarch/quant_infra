import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Set
from playwright.async_api import async_playwright, Locator, Page, TimeoutError as PlaywrightTimeoutError
import random

# ------ 1. Улучшенная конфигурация ------
ACCOUNTS_FILE_PATH = "accounts_URANUS.txt"  # <--- Файл, созданный первым скриптом
DATA_DIR = Path("data_uranus")
LOG_DIR = Path("logs_uranus")

# Параметры парсинга
CONFIG = {
    "headless": True,
    "max_new_tweets": 60,          # Максимум НОВЫХ твитов для сбора за один запуск
    "parse_mode": "incremental",    # 'incremental' (только новые) или 'full' (все до лимита)
    "since_date": "2024-01-01",     # Игнорировать твиты старше этой даты (в режиме 'full')
    "max_stale_scrolls": 5,         # Остановиться после 5 прокруток без новых твитов
    "save_every_n_tweets": 50,      # Сохранять прогресс каждые 50 найденных твитов
}
# -------------------------------------------

# Создание директорий
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# --- Функции логирования и загрузки аккаунтов (без изменений) ---
def log(message: str, *, level: str = "INFO", console: bool = True):
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"[{timestamp}] [{level}] {message}"
    log_file = LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")
    if console:
        print(log_line)

def load_accounts(path: str) -> List[str]:
    if not os.path.exists(path):
        log(f"Файл аккаунтов не найден: {path}", level="ERROR")
        return []
    with open(path, "r", encoding="utf-8") as f:
        accounts = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    log(f"Загружено аккаунтов: {len(accounts)} из {path}")
    return accounts

def parse_iso_datetime(ts: str) -> datetime:
    try:
        if ts.endswith("Z"): ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
# -------------------------------------------------------------

async def get_tweet_data(article: Locator) -> Optional[Dict]:
    """Извлекает все данные из одного элемента <article>."""
    try:
        time_tag = article.locator('time').first
        timestamp = await time_tag.get_attribute("datetime")
        tweet_url_path = await time_tag.evaluate("el => el.closest('a')?.href")
        if not timestamp or not tweet_url_path: return None
        
        tweet_url = f"https://x.com{tweet_url_path}"

        text = await article.locator('div[data-testid="tweetText"]').first.inner_text(timeout=5000)

        stats_group = article.locator('div[role="group"][aria-label*="replies"]')
        stats_aria = await stats_group.first.get_attribute('aria-label', timeout=5000) if await stats_group.count() > 0 else ""
        
        replies = int(re.search(r'([\d,]+)\s+repl', stats_aria, re.IGNORECASE).group(1).replace(',', '')) if 'repl' in stats_aria else 0
        reposts = int(re.search(r'([\d,]+)\s+repost', stats_aria, re.IGNORECASE).group(1).replace(',', '')) if 'repost' in stats_aria else 0
        likes = int(re.search(r'([\d,]+)\s+like', stats_aria, re.IGNORECASE).group(1).replace(',', '')) if 'like' in stats_aria else 0
        
        views = 0
        views_locator = article.locator('a[aria-label*="views"]')
        if await views_locator.count() > 0:
            views_text = await views_locator.first.get_attribute("aria-label")
            views_match = re.search(r'([\d,]+)', views_text)
            if views_match: views = int(views_match.group(1).replace(',', ''))

        return {
            "parse_timestamp": datetime.now(timezone.utc).isoformat(), "text": text.strip(),
            "timestamp": timestamp, "url": tweet_url, "replies": replies, "reposts": reposts,
            "likes": likes, "views": views,
        }
    except Exception:
        return None

def save_user_data(username: str, tweets: List[Dict]):
    """Сохраняет данные пользователя в JSON-файл."""
    filepath = DATA_DIR / f"{username}.json"
    sorted_tweets = sorted(tweets, key=lambda x: x['timestamp'], reverse=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"username": username, "tweets_count": len(sorted_tweets), "tweets": sorted_tweets}, f, ensure_ascii=False, indent=2)

async def fetch_user_tweets(username: str, cfg: dict):
    """Основная функция парсинга профиля пользователя."""
    log(f"Начинаем парсинг @{username} с режимом '{cfg['parse_mode']}'")
    
    filepath = DATA_DIR / f"{username}.json"
    existing_tweets = []
    if filepath.exists() and cfg['parse_mode'] == 'incremental':
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing_tweets = json.load(f).get("tweets", [])
            log(f"Найдено {len(existing_tweets)} существующих твитов для @{username}")
        except json.JSONDecodeError:
            log(f"Файл {filepath} поврежден.", level="WARNING")

    latest_ts = parse_iso_datetime(max(t['timestamp'] for t in existing_tweets)) if existing_tweets else None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg['headless'])
        context = await browser.new_context(storage_state="auth_state.json")
        page = await context.new_page()

        try:
            await page.goto(f"https://x.com/{username}", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_selector('article[role="article"]', timeout=20000)
            log(f"Страница @{username} успешно загружена.")
        except PlaywrightTimeoutError:
            log(f"Таймаут при загрузке страницы @{username} или не найдены твиты.", level="ERROR")
            await browser.close()
            return
        
        newly_found_tweets = []
        processed_urls = {t['url'] for t in existing_tweets}
        stale_scrolls = 0
        
        while len(newly_found_tweets) < cfg['max_new_tweets'] and stale_scrolls < cfg['max_stale_scrolls']:
            new_content_this_iteration = False
            articles = await page.locator('article[role="article"]').all()
            
            for article in articles:
                tweet_data = await get_tweet_data(article)
                if not tweet_data or tweet_data['url'] in processed_urls:
                    continue
                
                processed_urls.add(tweet_data['url'])
                new_content_this_iteration = True
                
                tweet_dt = parse_iso_datetime(tweet_data['timestamp'])
                if latest_ts and tweet_dt <= latest_ts and cfg['parse_mode'] == 'incremental':
                    stale_scrolls = cfg['max_stale_scrolls'] # Принудительная остановка
                    break
                
                newly_found_tweets.append(tweet_data)
                
                # --- НОВОЕ: Периодическое сохранение ---
                if len(newly_found_tweets) % cfg['save_every_n_tweets'] == 0:
                    log(f"@{username}: Сохраняем промежуточный результат ({len(newly_found_tweets)} новых твитов)...")
                    save_user_data(username, existing_tweets + newly_found_tweets)
                
                if len(newly_found_tweets) >= cfg['max_new_tweets']: break
            
            log(f"@{username}: Найдено новых твитов: {len(newly_found_tweets)}/{cfg['max_new_tweets']}")
            
            if new_content_this_iteration:
                stale_scrolls = 0
            else:
                stale_scrolls += 1
                log(f"@{username}: Прокрутка не дала результатов. Попытка {stale_scrolls}/{cfg['max_stale_scrolls']}")

            if stale_scrolls >= cfg['max_stale_scrolls']: break
            
            await page.mouse.wheel(0, 4000)
            await asyncio.sleep(random.uniform(2.0, 4.0))

        await browser.close()

    if newly_found_tweets:
        log(f"@{username}: Найдено {len(newly_found_tweets)} новых твитов. Сохранение итогового результата...")
        save_user_data(username, existing_tweets + newly_found_tweets)
        log(f"✅ @{username}: Готово. Всего твитов в файле: {len(existing_tweets) + len(newly_found_tweets)}.", console=True)
    else:
        log(f"Для @{username} новых твитов не найдено.", console=True)

async def main():
    log(f"Загружен конфиг: {CONFIG}")
    
    accounts = load_accounts(ACCOUNTS_FILE_PATH)
    random.shuffle(accounts)

    for account in accounts:
        await fetch_user_tweets(account, CONFIG)
        log(f"--- Перерыв перед следующим аккаунтом ---")
        await asyncio.sleep(random.uniform(5, 10))

if __name__ == "__main__":
    asyncio.run(main())