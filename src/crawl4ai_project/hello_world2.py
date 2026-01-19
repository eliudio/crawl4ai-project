# scraper.py
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import time
from urllib.parse import urljoin

def get_cleaned_markdown(url: str) -> str:
    """
    Fetch page → clean HTML → convert to markdown
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        resp.encoding = 'utf-8'
    except requests.RequestException as e:
        print(f"Failed to fetch {url}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove common noise elements
    for selector in [
        "nav", "footer", "header", "script", "style", "noscript",
        "iframe", ".advert", ".ads", ".cookie-banner", ".modal",
        "[role='banner']", "[role='navigation']"
    ]:
        for el in soup.select(selector):
            el.decompose()

    # Try to find the main events container
    # (most likely candidates based on common patterns & current site behavior)
    main_candidates = [
        soup.select_one("main"),
        soup.select_one(".results-list"),
        soup.select_one(".race-list"),
        soup.select_one(".event-list"),
        soup.select_one("div[class*='results']"),
        soup.select_one("div[class*='list']"),
        soup.body
    ]

    main = next((c for c in main_candidates if c), None)

    if not main:
        print(f"No meaningful content container found on {url}")
        return ""

    # Convert to markdown - ATX headings, preserve some structure
    markdown_content = md(
        str(main),
        heading_style="ATX",
        autolink=True,
        strip=["script", "style", "iframe"],
        body_width=0  # no hard wrapping
    )

    return markdown_content.strip()


def main():
    base_url = "https://findarace.com/10k-runs/london"
    # From recent data → ~227 events → ~6 pages (40 per page approx)
    # We go to 8 just to be safe
    max_pages = 8

    all_markdown = "# London 10k Runs Events – Scraped January 2026\n\n"
    all_markdown += f"Total expected events: ~227 (based on site info)\n\n"

    page_count = 0

    for page in range(1, max_pages + 1):
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}/p{page}"

        print(f"Scraping page {page}/{max_pages}: {url}")

        page_md = get_cleaned_markdown(url)

        if page_md and len(page_md) > 200:  # rough filter for empty/junk pages
            page_count += 1
            all_markdown += f"## Page {page}\n\n"
            all_markdown += f"Source: {url}\n\n"
            all_markdown += page_md
            all_markdown += "\n\n---\n\n"
        else:
            print(f"→ Page {page} returned little/no useful content → stopping")
            break

        time.sleep(2.8)  # polite delay – be nice to the server

    output_file = "london_10k_events_soup.md"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(all_markdown)

    print(f"\nFinished!")
    print(f"Processed {page_count} pages")
    print(f"Output saved to: {output_file}")
    print("You can now open the file and use AI (Grok/Claude/etc.) to extract structured events from it.")


if __name__ == "__main__":
    main()