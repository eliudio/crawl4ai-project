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
def scrape(url: str, want_links: bool = False) -> tuple[str, list[str]]:
    """Fetch a page and return (markdown, links). `links` is [] unless want_links=True."""
    print(f"{datetime.now():%H:%M:%S} - firecrawl scrape: {url}")
    formats = ["markdown", "links"] if want_links else ["markdown"]

    result = _client().scrape(
        url=url,
        formats=formats,
        only_main_content=False if want_links else True,
        exclude_tags=_EXCLUDE_TAGS,
        wait_for=5000,
    )

    error = _unwrap(result, "error")
    if error:
        raise RuntimeError(f"Firecrawl reported an error for {url}: {error}")

    markdown = (_unwrap(result, "markdown") or "").strip()
    links = _unwrap(result, "links") or []
    return markdown, list(links)
