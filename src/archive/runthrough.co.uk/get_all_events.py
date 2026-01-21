import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from store_details import store_details
from events.events_manager import create_database


def get_all_events(listing_url: str, *, test_only: bool = False) -> list:
    """
    Load the events timeline page, click 'Load More' until exhausted, then extract all event detail URLs.
    """
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')  # Run in headless mode (no browser window)
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(listing_url)

        # Strategy: Continuously click 'Load More' until the button is no longer present or clickable
        while True:
            try:
                print(f"{datetime.now():%H:%M:%S} - load more")
                load_more = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]"))
                )
                if test_only:
                    break
                load_more.click()
                time.sleep(2)  # Wait for new content to load; adjust if needed
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} - exception {e}")
                # Button not found or timeout - assume all events are loaded
                break

        # Parse the fully loaded page source
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Find all links that look like event details (href contains "/event/")
        event_links = soup.find_all('a', href=lambda h: h and '/event/' in h)

        # Get unique full URLs
        base = "https://www.runthrough.co.uk"
        urls = list(set(urljoin(base, link['href']) for link in event_links if link.get('href')))

        return urls

    finally:
        driver.quit()


def main():
    url = "https://www.runthrough.co.uk/events-timeline"
    print(f"{datetime.now():%H:%M:%S} - processing: {url}")

    detail_urls = get_all_events(url, test_only = False)

    if not detail_urls:
        print("{datetime.now():%H:%M:%S} - no events found")
        return

    print(f"{datetime.now():%H:%M:%S} - found {len(detail_urls)} event URLs")

    for detail_url in detail_urls:
        print(f"{datetime.now():%H:%M:%S} - verifying: {detail_url}")
        store_details(1, detail_url)

if __name__ == "__main__":

    create_database()
    main()