import requests
from bs4 import BeautifulSoup
import time
from urllib.parse import urljoin

from events.events_manager import create_database
from store_details import store_details

def get_listing_events(listing_url: str) -> list:
    """
    Fetch a listing page and extract event detail URLs.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(listing_url, headers=headers, timeout=12)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
    except requests.RequestException as e:
        print(f"Failed to fetch {listing_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all links that look like event details (href contains "/events/")
    event_links = soup.find_all('a', href=lambda h: h and '/events/' in h)

    # Get unique full URLs
    base = "https://findarace.com"
    urls = list(set(urljoin(base, link['href']) for link in event_links if link.get('href')))

    return urls


def main():
    base_url = "https://findarace.com/events"
    max_pages = 90  # Adjust as needed

    for page in range(1, max_pages + 1):
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}/p{page}"

        print(f"Processing listing page {page}: {url}")

        detail_urls = get_listing_events(url)

        if not detail_urls:
            print(f"No events found on page {page} – stopping")
            break

        print(f"Found {len(detail_urls)} potential event URLs on page {page}")

        for detail_url in detail_urls:
            print(f"store details from: {detail_url}")
            store_details(detail_url)

        time.sleep(3)  # Delay between pages

    print(f"\nFinished!")


if __name__ == "__main__":
    create_database()
    main()