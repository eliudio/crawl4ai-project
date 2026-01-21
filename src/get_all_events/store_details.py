# Run this first if needed:
# pip install --upgrade firecrawl-py

from firecrawl import FirecrawlApp

from openai import OpenAI
import json
from typing import Dict, Any

from events.events_manager import event_from_dict, insert_or_skip_events, event_with_url_exists
from datetime import datetime
from grok.get_details_with_grok import get_details_with_grok

app = FirecrawlApp(api_url="http://localhost:3002")  # self-hosted, no key needed


def retrieve_md(url: str) -> str:
    """
    Scrape a Race for Life event page and return clean markdown of the main content.
    Handles both old dict-style and new Document-style responses.

    Example: 'https://raceforlife.cancerresearchuk.org/find-an-event/coopers-field-bute-park-pretty-muddy-kids-2026-05-02-1000'
    """
    print(f"{datetime.now():%H:%M:%S} - retrieve_md: {url}")
    try:
        result = app.scrape(
            url=url,
            formats=["markdown"],
            only_main_content=True,
            exclude_tags=[
                "nav", "header", "footer", "aside",
                "script", "style", "noscript",
                ".sidebar", ".promo", ".advert", ".advertisement",
                ".related-events", ".related", ".upcoming-events",
                ".social-share", ".newsletter", ".cookie-notice",
                ".event-map-wrapper",           # often contains map — exclude if not needed
                ".donation-block",              # fundraising/donation promos
                ".support-section",             # "We're with you every step" etc.
                ".fundraising-support",         # get fundraising help blocks
                ".sale-banner",                 # January sale / promo banners
                ".choose-event",                # "Choose the right event" sections
                ".other-ways-to-support",       # alternative support CTAs
            ],
            wait_for=5000,  # ms — slightly longer for charity sites with possible lazy elements
        )

        # ── Handle different possible return shapes ──
        if hasattr(result, 'markdown'):
            # Modern SDK / Document style (most common now)
            markdown = result.markdown
        elif isinstance(result, dict) and 'data' in result and 'markdown' in result['data']:
            # Older / some cloud responses
            markdown = result['data']['markdown']
        elif isinstance(result, dict) and 'markdown' in result:
            markdown = result['markdown']
        else:
            print(f"{datetime.now():%H:%M:%S} - Unexpected result format for {url}: {type(result)}")
            return ""

        # Optional safety: check if there's error-like info
        if hasattr(result, 'error') and result.error:
            print(f"{datetime.now():%H:%M:%S} - Scrape reported error for {url}: {result.error}")
            return ""

        markdown = (markdown or "").strip()

        # Light cleanup of common empty lines / artifacts
        lines = [line for line in markdown.splitlines() if line.strip()]
        markdown = "\n".join(lines)

        return markdown

    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Error scraping {url}: {type(e).__name__}: {e}")
        return ""

def store_details(page: int, url: str) -> None:
    if not event_with_url_exists(url):
        print(f"{datetime.now():%H:%M:%S} - processing: {url} from page {page}")
        md = retrieve_md(url)
        details = get_details_with_grok(url, md)
        if details is not None:
            print(f"{datetime.now():%H:%M:%S} - results: {json.dumps(details, indent=2)}")
            event_from_json = event_from_dict(details)
            if event_from_json is not None:
                insert_or_skip_events([event_from_json])
    else:
        print(f"raceforlife.cancerresearchuk.org {datetime.now():%H:%M:%S} - skipping: {url} already exists")
