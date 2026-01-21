import time
import re
from datetime import datetime
from typing import Optional, List

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from store_details import store_details
from events.events_manager import create_database, migrate_remove_unique_name


def get_event_detail_urls(
    listing_url: str,
    base_url: str,
    link_pattern: str,
    load_more_xpath: Optional[str] = None,
    test_only: bool = False,
    min_wait_after_click: float = 2.5,
) -> List[str]:
    """
    Unified function to scrape event detail URLs from a listing page.

    Supports:
    - Sites with "Load More" button (e.g. RunThrough)
    - Sites that load everything at once (e.g. Race for Life with ?size=n_1000_n)
    """
    print(f"{datetime.now():%H:%M:%S} - Starting scrape of: {listing_url}")

    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(listing_url)
        time.sleep(3.5)  # let initial JS settle

        # Wait until at least one event link appears
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, f'a[href*="{link_pattern}"]'))
            )
            print(f"{datetime.now():%H:%M:%S} - Event links detected")
        except Exception as e:
            print(f"{datetime.now():%H:%M:%S} - Timeout waiting for event links: {e}")

        if load_more_xpath:
            print(f"{datetime.now():%H:%M:%S} - Looking for 'Load More' buttons...")
            while True:
                try:
                    load_more = WebDriverWait(driver, 12).until(
                        EC.element_to_be_clickable((By.XPATH, load_more_xpath))
                    )
                    if test_only:
                        break
                    print(f"{datetime.now():%H:%M:%S} - Clicking Load More...")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more)
                    time.sleep(0.6)
                    load_more.click()
                    time.sleep(min_wait_after_click)
                except Exception as e:
                    print(f"{datetime.now():%H:%M:%S} - No more 'Load More' found or timeout: {e}")
                    break

        print(f"{datetime.now():%H:%M:%S} - Parsing final page source...")
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Find candidate links (case-insensitive)
        event_links = soup.find_all('a', href=lambda h: h and link_pattern in str(h).lower())

        # Build full URLs + filter junk
        urls = []
        seen = set()
        for link in event_links:
            href = link.get('href')
            if not href:
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen:
                continue
            # Basic filter — avoid nav, social, etc.
            if any(x in full_url.lower() for x in ['facebook', 'twitter', 'instagram', '#', 'login', 'donate', 'signup']):
                continue
            seen.add(full_url)
            urls.append(full_url)

        print(f"{datetime.now():%H:%M:%S} - Found {len(urls)} unique event URLs")
        return urls

    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Scrape failed: {type(e).__name__}: {e}")
        return []
    finally:
        driver.quit()


def process_site(
    site_name: str,
    listing_url: str,
    base_url: str,
    link_pattern: str,
    load_more_xpath: Optional[str],
    page_number: int = 1,
):
    print(f"\n{'=' * 60}")
    print(f"{datetime.now():%H:%M:%S} - Processing {site_name} (page {page_number})")
    print(f"{'=' * 60}\n")

    detail_urls = get_event_detail_urls(
        listing_url=listing_url,
        base_url=base_url,
        link_pattern=link_pattern,
        load_more_xpath=load_more_xpath,
        test_only=False,
    )

    if not detail_urls:
        print(f"{datetime.now():%H:%M:%S} - No events found for {site_name}")
        return

    print(f"{datetime.now():%H:%M:%S} - Processing {len(detail_urls)} events...")

    for i, detail_url in enumerate(detail_urls, 1):
        print(f"{datetime.now():%H:%M:%S} - [{i}/{len(detail_urls)}] {detail_url}")
        store_details(page_number, detail_url)


def main():
    #migrate_remove_unique_name()
    create_database()

    process_run_through = False
    process_race_for_life = False

    if process_run_through:
        process_site(
            site_name="RunThrough",
            listing_url="https://www.runthrough.co.uk/events-timeline",
            base_url="https://www.runthrough.co.uk",
            link_pattern="event/",
            load_more_xpath="//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]",
        )

    if process_race_for_life:
        process_site(
            site_name="Race for Life",
            listing_url="https://raceforlife.cancerresearchuk.org/find-an-event?size=n_1000_n",
            base_url="https://raceforlife.cancerresearchuk.org",
            link_pattern="find-an-event/",
            load_more_xpath=None,           # No load more needed — all events load at once
        )


if __name__ == "__main__":
    main()