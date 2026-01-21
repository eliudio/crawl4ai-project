# Run this first if needed:
# pip install --upgrade firecrawl-py

from datetime import datetime
from firecrawl import FirecrawlApp

from openai import OpenAI
import json
from typing import Dict, Any

from events.events_manager import event_from_dict, insert_or_skip_events, event_with_url_exists
from grok.get_details_with_grok import get_details_with_grok


def retrieve_md(url:str) -> str:

    #app = FirecrawlApp(api_key="fc-17bcb15e30384f1bbba50b552887aa63")
    app = FirecrawlApp(api_url="http://localhost:3002")  # No api_key required for self-hosted

    # Scrape with minimal fluff
    result = app.scrape(
        url=url,
        formats=["markdown"],
        only_main_content=True,              # ← removes nav, footer, ads, sidebars, etc.
        exclude_tags=[
            "nav", "header", "footer", "aside",
            "script", "style", ".sidebar", ".promo",
            ".related-events", ".advertisement"
        ],
        wait_for=2000                        # wait 2s for dynamic JS content
    )

    markdown = result.markdown.strip()

    return markdown

def store_details(page: int, url:str)-> None:
    if not event_with_url_exists(url):
        print(f"{datetime.now():%H:%M:%S} - processing: {url} from page {page}")
        md = retrieve_md(url)
        details = get_details_with_grok(url, md)
        if details is not None:
            print(f"{datetime.now():%H:%M:%S} - details: {details}")
            event_from_json = event_from_dict(details)
            if event_from_json is not None:
                insert_or_skip_events([event_from_json])
    else:
        print(f"{datetime.now():%H:%M:%S} - skipping: {url} already exists")
