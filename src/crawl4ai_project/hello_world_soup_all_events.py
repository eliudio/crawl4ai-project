# all_events_scraper.py
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import time
import random
from urllib.parse import urljoin

from crawl4ai_project.express_vpn import select_random_location


def extract_event_details(detail_url: str) -> dict:
    """
    Attempt to extract key event information from the detail page.
    Uses keyword-based heuristic approach (may need tuning per event type).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(detail_url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
    except requests.RequestException as e:
        print(f"  Failed to fetch detail page {detail_url}: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove scripts, styles etc. to reduce noise
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    details = {
        "date": "N/A",
        "time": "N/A",
        "location": "N/A",
        "price": "N/A",
        "distances": "N/A"
    }

    # Collect text from likely containers
    candidates = soup.find_all(['h1', 'h2', 'h3', 'p', 'div', 'span', 'li', 'strong', 'dt', 'dd'])

    for elem in candidates:
        text = elem.get_text(strip=True).lower()
        raw = elem.get_text(strip=True)

        if any(k in text for k in ['date', 'when', 'starts']) and details['date'] == "N/A":
            details['date'] = raw
        elif any(k in text for k in ['start time', 'time', 'begins at']) and details['time'] == "N/A":
            details['time'] = raw
        elif any(k in text for k in ['location', 'venue', 'where', 'place', 'address']) and details['location'] == "N/A":
            details['location'] = raw
        elif any(k in text for k in ['price', 'entry fee', 'cost', '£']) and details['price'] == "N/A":
            details['price'] = raw
        elif any(k in text for k in ['distance', 'distances', 'race options', 'km', 'miles']) and details['distances'] == "N/A":
            details['distances'] = raw

    # Fallback: try to split date+time if combined
    if details['time'] == "N/A" and any(sep in details['date'] for sep in [' at ', ' from ', ' | ']):
        for sep in [' at ', ' from ', ' | ']:
            if sep in details['date']:
                parts = details['date'].split(sep, 1)
                details['date'] = parts[0].strip()
                details['time'] = parts[1].strip()
                break

    # Clean up
    for k in details:
        if details[k] != "N/A":
            details[k] = ' '.join(details[k].split())  # normalize whitespace

    return details


def get_event_detail_urls(page_url: str) -> list[str]:
    """Extract all event detail page URLs from a listing page"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(page_url, headers=headers, timeout=12)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Failed to fetch listing page {page_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Most common patterns for event links on findarace.com
    possible_selectors = [
        'a[href*="/events/"]',
        'a.card-link',
        'a.event-card',
        '.event-title a',
        'article a',
        'div.event a'
    ]

    links = set()
    for sel in possible_selectors:
        for a in soup.select(sel):
            href = a.get('href')
            if href and '/events/' in href:
                full_url = urljoin("https://findarace.com", href)
                links.add(full_url)

    return list(links)


def main():
    base_url = "https://findarace.com/events"
    max_pages = 80          # safety margin (currently ~43-45 pages in Jan 2026)

    output_file = "all_findarace_events_with_details.md"
    all_markdown = f"# All Events on findarace.com\n"
    all_markdown += f"Scraped on {time.strftime('%Y-%m-%d')}\n\n"
    all_markdown += "> Note: Detail extraction is heuristic-based and may miss some information\n\n"

    total_events = 0
    page = 1

    select_random_location()
    while page <= max_pages:
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}/p{page}"

        print(f"\nProcessing page {page:2d} → {url}")

        detail_urls = get_event_detail_urls(url)

        if not detail_urls:
            print("  No more events found → stopping")
            break

        print(f"  Found {len(detail_urls)} event links")

        for i, detail_url in enumerate(detail_urls, 1):
            print(f"  {i:3d}/{len(detail_urls)} → {detail_url}")
            details = extract_event_details(detail_url)

            if any(v != "N/A" for v in details.values()):  # only save if we got something
                total_events += 1
                all_markdown += f"## Event {total_events}\n"
                all_markdown += f"**Link**: {detail_url}\n\n"

                for key, value in details.items():
                    all_markdown += f"- **{key.title()}**: {value}\n"

                all_markdown += "\n---\n\n"

            # Be very polite – important when doing 1700+ requests
            #time.sleep(random.uniform(1.2, 2.8))

        # Between listing pages
        select_random_location()
        page += 1

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(all_markdown)

    print("\n" + "="*70)
    print(f"Finished!")
    print(f"Processed {page-1} pages")
    print(f"Extracted details for {total_events} events")
    print(f"Results saved to: {output_file}")
    print("="*70)


if __name__ == "__main__":
    main()

