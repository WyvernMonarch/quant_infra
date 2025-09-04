import asyncio
import os
import random
from typing import Set
from urllib.parse import quote
from playwright.async_api import async_playwright, Error as PlaywrightError

# ------ Конфигурация ------
SEARCH_QUERY = "BFgdzMkTPdKKJeTipv2njtDEwhKxkgFueJQfJGt1jups"
OUTPUT_FILE = f"accounts_{SEARCH_QUERY.split()[0].replace('$', '')}.txt"

MAX_ACCOUNTS_TO_FIND = 500  # Цель: найти 500 уникальных аккаунтов
HEADLESS_MODE = True
MAX_STALE_SCROLLS = 5  # Остановиться после 5 прокруток без новых твитов

# --- НОВОЕ: Функции для загрузки и сохранения ---

def load_existing_accounts(filepath: str) -> Set[str]:
    """Загружает аккаунты из файла, если он существует."""
    if not os.path.exists(filepath):
        return set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            accounts = {line.strip() for line in f if line.strip()}
            print(f"Загружено {len(accounts)} существующих аккаунтов из {filepath}")
            return accounts
    except Exception as e:
        print(f"Ошибка при чтении файла {filepath}: {e}")
        return set()

def save_accounts_to_file(filepath: str, accounts: Set[str]):
    """Сохраняет множество аккаунтов в файл, перезаписывая его."""
    # open(..., "w") автоматически создает файл, если его нет.
    with open(filepath, "w", encoding="utf-8") as f:
        for account in sorted(list(accounts)):
            f.write(account + "\n")

# ---------------------------------------------------

class TwitterSearch:
    # ... (код класса остается без изменений) ...
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        print("Запуск браузера...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(storage_state="auth_state.json")
        self.page = await self.context.new_page()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print("Закрытие браузера.")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def find_accounts(self, query: str, max_accounts: int, initial_accounts: Set[str]) -> Set[str]:
        encoded_query = quote(query)
        url = f"https://x.com/search?q={encoded_query}&src=typed_query"
        
        print(f"Переход на страницу поиска: {url}")
        await self.page.goto(url, timeout=60000, wait_until="networkidle")

        # --- ИЗМЕНЕНИЕ: Используем переданное множество ---
        found_accounts = initial_accounts
        processed_tweet_urls = set()
        stale_scroll_attempts = 0

        while len(found_accounts) < max_accounts and stale_scroll_attempts < MAX_STALE_SCROLLS:
            try:
                await self.page.wait_for_selector("article[data-testid='tweet']", timeout=15000)
            except PlaywrightError:
                print("Твиты на странице не найдены или не загрузились вовремя. Завершаем.")
                break

            new_content_found_this_iteration = False
            articles = await self.page.locator("article[data-testid='tweet']").all()

            for article in articles:
                try:
                    time_element = article.locator("time").first
                    tweet_url = await time_element.evaluate("el => el.closest('a')?.href")

                    if not tweet_url or tweet_url in processed_tweet_urls:
                        continue
                    
                    processed_tweet_urls.add(tweet_url)
                    new_content_found_this_iteration = True

                    author_link = article.locator("div[data-testid='User-Name'] a[role='link']").first
                    href = await author_link.get_attribute("href")
                    if href:
                        author_username = href.split('/')[-1]
                        if author_username and len(author_username) > 2:
                            found_accounts.add(author_username)
                
                except Exception:
                    pass
            
            if new_content_found_this_iteration:
                stale_scroll_attempts = 0
                # --- НОВОЕ: Сохраняем результат после успешной итерации ---
                save_accounts_to_file(OUTPUT_FILE, found_accounts)
            else:
                stale_scroll_attempts += 1
                print(f"Прокрутка не дала новых твитов. Попытка {stale_scroll_attempts}/{MAX_STALE_SCROLLS}...")

            # --- ИЗМЕНЕНИЕ: Более понятный лог ---
            print(f"Цель: {len(found_accounts)}/{max_accounts} аккаунтов | Всего обработано твитов: {len(processed_tweet_urls)}")
            
            await self.page.mouse.wheel(0, 5000)
            await asyncio.sleep(random.uniform(2, 4))
        
        if stale_scroll_attempts >= MAX_STALE_SCROLLS:
            print(f"\nОстановка: достигнут лимит бесполезных прокруток ({MAX_STALE_SCROLLS}).")

        return found_accounts

async def main():
    print(f"Начинаем поиск аккаунтов по запросу: '{SEARCH_QUERY}'")
    
    # --- НОВОЕ: Загружаем существующие аккаунты перед запуском ---
    existing_accounts = load_existing_accounts(OUTPUT_FILE)

    async with TwitterSearch(headless=HEADLESS_MODE) as searcher:
        # Передаем существующие аккаунты в парсер
        final_accounts = await searcher.find_accounts(SEARCH_QUERY, MAX_ACCOUNTS_TO_FIND, existing_accounts)

    # Финальное сохранение на всякий случай
    save_accounts_to_file(OUTPUT_FILE, final_accounts)
    print(f"\nРабота завершена. Найдено и сохранено {len(final_accounts)} уникальных аккаунтов в файл: {OUTPUT_FILE}")

if __name__ == "__main__":
    asyncio.run(main())