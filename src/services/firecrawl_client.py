"""
Thin wrapper around Firecrawl's hosted API.

Deliberately points at Firecrawl's cloud endpoint (no api_url override) rather
than a self-hosted instance: the hosted service is what takes over the job
ExpressVPN used to do (proxy rotation / not looking like a bot), so workers
running as many parallel Cloud Run instances don't need any VPN/proxy
infrastructure of their own.
"""

from datetime import datetime

from firecrawl import FirecrawlApp
from tenacity import retry, stop_after_attempt, wait_exponential

from services.config import settings

_EXCLUDE_TAGS = [
    "nav", "header", "footer", "aside",
    "script", "style", "noscript",
    ".sidebar", ".promo", ".advert", ".advertisement",
    ".related-events", ".related", ".upcoming-events",
    ".social-share", ".newsletter", ".cookie-notice",
]

_app: FirecrawlApp | None = None


def _client() -> FirecrawlApp:
    global _app
    if _app is None:
        kwargs = {"api_key": settings.firecrawl_api_key}
        if settings.firecrawl_api_url:
            kwargs["api_url"] = settings.firecrawl_api_url
        _app = FirecrawlApp(**kwargs)
    return _app


def _unwrap(result, key: str):
    """Firecrawl SDK versions have returned plain dicts and typed Document objects; handle both."""
    if hasattr(result, key):
        return getattr(result, key)
    if isinstance(result, dict):
        if key in result:
            return result[key]
        data = result.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def scrape(
    url: str, want_links: bool = False, want_html: bool = False, actions: list[dict] | None = None
) -> tuple[str, list[str], str]:
    """
    Fetch a page and return (markdown, links, html). `links` is [] unless
    want_links=True; `html` is "" unless want_html=True (needed by the listing
    crawler to have the LLM pick out a "load more" button's CSS selector -
    markdown strips class/id attributes, so it's useless for that). `actions`
    (e.g. scroll/wait/click steps) run in Firecrawl's own browser before the
    page is captured - see listing_crawler.py's "load more" handling.
    """
    print(f"{datetime.now():%H:%M:%S} - firecrawl scrape: {url}")
    formats = ["markdown"]
    if want_links:
        formats.append("links")
    if want_html:
        formats.append("html")

    kwargs = {}
    if actions:
        kwargs["actions"] = actions

    result = _client().scrape(
        url=url,
        formats=formats,
        only_main_content=False if want_links else True,
        exclude_tags=_EXCLUDE_TAGS,
        wait_for=5000,
        **kwargs,
    )

    error = _unwrap(result, "error")
    if error:
        raise RuntimeError(f"Firecrawl reported an error for {url}: {error}")

    markdown = (_unwrap(result, "markdown") or "").strip()
    links = _unwrap(result, "links") or []
    html = (_unwrap(result, "html") or "").strip() if want_html else ""
    return markdown, list(links), html


# Firecrawl rejects any request with more than 50 total actions outright (a
# hard validation error, not a runtime failure), and a plain click isn't
# reliably registered without a wait before the next action (tested: without
# it, repeated clicks silently no-op and every snapshot comes back identical
# to the first page). That fixes the cost at 3 actions/page (click, wait,
# scrape), capping a single chained request at 16 clicks - see
# listing_crawler.py's numbered-pager ("paginate") handling.
_MAX_PAGINATE_CLICKS = 16
_PAGINATE_CLICK_WAIT_MS = 3000


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def scrape_paginated(url: str, click_selector: str, num_clicks: int) -> list[dict[str, str]]:
    """
    Load `url`, then click `click_selector` `num_clicks` times in sequence,
    capturing a full HTML snapshot after each click - all within one browser
    session/request, unlike scrape()'s actions (which only capture the final
    state, so reaching press N there means replaying N clicks from scratch on
    a fresh page load each time - fine for a couple of presses, but doesn't
    scale to a numbered pager with many pages).

    Returns one {"url": ..., "html": ...} dict per click, in order (page 2,
    3, ... first, since page 1 is whatever the caller already has from its
    own initial scrape()). Snapshots only ever include html, not
    markdown/links - that's what Firecrawl's "scrape" action captures,
    independent of this call's own `formats`.
    """
    num_clicks = min(num_clicks, _MAX_PAGINATE_CLICKS)
    actions: list[dict] = []
    for _ in range(num_clicks):
        actions.append({"type": "click", "selector": click_selector})
        actions.append({"type": "wait", "milliseconds": _PAGINATE_CLICK_WAIT_MS})
        actions.append({"type": "scrape"})

    print(f"{datetime.now():%H:%M:%S} - firecrawl scrape_paginated: {url} ({num_clicks} click(s))")
    result = _client().scrape(
        url=url,
        formats=["markdown"],
        only_main_content=False,
        wait_for=5000,
        actions=actions,
    )

    error = _unwrap(result, "error")
    if error:
        raise RuntimeError(f"Firecrawl reported an error for {url}: {error}")

    action_data = _unwrap(result, "actions") or {}
    return list(action_data.get("scrapes") or [])
