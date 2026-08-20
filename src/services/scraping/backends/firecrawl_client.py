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

from services.common.config import settings

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
) -> tuple[str, list[str], str, str]:
    """
    Fetch a page and return (markdown, links, html, final_url). `links` is []
    unless want_links=True; `html` is "" unless want_html=True (needed by the
    listing crawler to have the LLM pick out a "load more" button's CSS
    selector - markdown strips class/id attributes, so it's useless for
    that). `actions` (e.g. scroll/wait/click steps) run in Firecrawl's own
    browser before the page is captured - see listing_crawler.py's "load
    more"/"paginate" handling.

    markdown/links both come from Firecrawl's own conversion (formats=
    ["markdown", "links"]), not derived from raw html ourselves - Firecrawl's
    markdown generation does real main-content extraction (Readability-style
    boilerplate/widget removal), confirmed in practice to matter: converting
    html ourselves via a plain tag-stripper let a page's third-party reviews
    widget flood out its actual event description entirely. Trusting
    Firecrawl's own conversion here is deliberate, not an oversight.

    final_url is metadata.url - the page's actual URL after any actions ran
    (e.g. client-side navigation from a click updating the address bar via
    history.pushState), as opposed to metadata.source_url (the originally-
    requested URL, unchanged). Lets callers that click through same-URL
    client-rendered pages (listing_crawler's numbered-pager handling) detect
    whether/how the URL changed as a result, without needing a persistent
    browser session across calls.
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

    metadata = _unwrap(result, "metadata")
    final_url = getattr(metadata, "url", None) or url

    return markdown, list(links), html, final_url
