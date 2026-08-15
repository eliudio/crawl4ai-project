"""
Chooses which scraper backend actually talks to a given site: self-hosted
crawl4ai by default - free beyond Cloud Run compute, no per-page SaaS fee -
falling back to Firecrawl's hosted API only when crawl4ai's own attempt
fails (anti-bot block, browser crash, etc.). Firecrawl's proxy rotation /
stealth handling is the one thing self-hosting can't replicate, so it's kept
as a paid escape hatch for whichever specific organisers actually need it,
rather than paying Firecrawl's per-page rate across all ~170 regardless of
whether they need it.

Same scrape(url, want_links, want_html, actions) contract as both backends -
listing_crawler.py and event_crawler.py call this instead of either backend
directly, so neither needs to know or care which one actually served a call.
"""

from datetime import datetime

from services import crawl4ai_client, firecrawl_client
from services.config import settings


def scrape(
    url: str, want_links: bool = False, want_html: bool = False, actions: list[dict] | None = None
) -> tuple[str, list[str], str, str]:
    if settings.scraper_backend == "firecrawl":
        return firecrawl_client.scrape(url, want_links=want_links, want_html=want_html, actions=actions)

    try:
        return crawl4ai_client.scrape(url, want_links=want_links, want_html=want_html, actions=actions)
    except Exception as e:
        print(
            f"{datetime.now():%H:%M:%S} - crawl4ai failed for {url} ({type(e).__name__}: {e}), "
            "falling back to Firecrawl"
        )
        return firecrawl_client.scrape(url, want_links=want_links, want_html=want_html, actions=actions)
