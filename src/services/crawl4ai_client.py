"""
Self-hosted equivalent of firecrawl_client.py - same job (fetch a page,
convert to markdown, optionally click through a "load more"/pager), but
running our own headless Chromium (via crawl4ai/Playwright) instead of
paying Firecrawl per page. See scraper_client.py for how the two are
combined: this is the free default, Firecrawl stays available as a paid
fallback for whichever specific site actually needs real anti-bot handling.

Exposes the exact same scrape(url, want_links, want_html, actions) -> (markdown,
links, html, final_url) contract as firecrawl_client.scrape, so callers never
need to know which backend actually served a given call.
"""

import asyncio
import json
import threading
from datetime import datetime
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from tenacity import retry, stop_after_attempt, wait_exponential

from services.config import settings

_EXCLUDED_TAGS = ["nav", "header", "footer", "aside", "script", "style", "noscript"]

# Identifies honestly as settings.user_agent (the same identity robots.py's own
# is_allowed()/wait_for_crawl_delay() evaluate a site's rules against) rather than
# whatever real-Chrome UA crawl4ai defaults to - unlike firecrawl_client.py (which
# deliberately leaves Firecrawl's own stealth/anti-bot handling alone), this is a
# plain self-hosted browser with no anti-bot pretense to preserve.
_BROWSER_CONFIG = BrowserConfig(headless=True, verbose=False, user_agent=settings.user_agent)
# Mirrors firecrawl_client's only_main_content=True: PruningContentFilter strips
# boilerplate (nav/widgets/ads) the way Firecrawl's Readability-style extraction
# does - only applied for event detail pages (want_links=False), same as Firecrawl.
_MAIN_CONTENT_MARKDOWN_GENERATOR = DefaultMarkdownGenerator(content_filter=PruningContentFilter())

_BASE_PAGE_TIMEOUT_MS = 60_000


class _PersistentCrawler:
    """
    One real headless browser process, kept alive for this worker's lifetime,
    driven from a dedicated background thread running its own asyncio event
    loop. The rest of this codebase is plain sync code calling scrape()
    directly (no event loop of its own to hook into), and crawl4ai is
    async-only - bridging via a fresh asyncio.run() per call would work, but
    also relaunch/reconnect overhead on every single page; a persistent
    loop + browser amortizes that across every scrape() call in this process.
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._crawler: AsyncWebCrawler | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def _ensure_started(self) -> AsyncWebCrawler:
        if self._crawler is not None:
            return self._crawler
        with self._lock:
            if self._crawler is not None:
                return self._crawler

            threading.Thread(target=self._run_loop, daemon=True, name="crawl4ai-loop").start()
            self._ready.wait()

            crawler = AsyncWebCrawler(config=_BROWSER_CONFIG)
            asyncio.run_coroutine_threadsafe(crawler.start(), self._loop).result()
            self._crawler = crawler
            return crawler

    def arun(self, url: str, config: CrawlerRunConfig):
        crawler = self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(crawler.arun(url=url, config=config), self._loop)
        return future.result()


_persistent = _PersistentCrawler()


def _actions_to_js(actions: list[dict]) -> str:
    """
    Translates the {"type": "click"/"scroll"/"wait", ...} action list
    listing_crawler.py builds (shaped for Firecrawl's actions= API) into one
    JS async script crawl4ai runs in-page via `js_code` - same ordered
    click/wait/scroll sequence, same timing, different engine underneath.
    One script (not a list of separate js_code snippets) so the waits stay
    *between* clicks rather than all clicks firing back-to-back before crawl4ai
    ever pauses - listing_crawler.py's own comments note this ordering has
    caused real bugs before when handled carelessly.
    """
    steps = []
    for action in actions:
        kind = action.get("type")
        if kind == "click":
            selector_js = json.dumps(action["selector"])
            steps.append(f"{{ const el = document.querySelector({selector_js}); if (el) el.click(); }}")
        elif kind == "scroll":
            steps.append("window.scrollTo(0, document.body.scrollHeight);")
        elif kind == "wait":
            ms = int(action.get("milliseconds", 0))
            steps.append(f"await new Promise(r => setTimeout(r, {ms}));")
        # Unrecognized action types are skipped rather than raising - same
        # "do nothing extra" fallback as if this action had never been added.
    body = "\n  ".join(steps)
    return f"(async () => {{\n  {body}\n}})();"


def _extract_links(result, base_url: str) -> list[str]:
    """crawl4ai returns {"internal": [{"href": ...}], "external": [...]}, not a flat url list - flatten + resolve relative hrefs."""
    links_by_kind = result.links or {}
    hrefs = [
        item.get("href")
        for kind in ("internal", "external")
        for item in links_by_kind.get(kind, [])
    ]
    return [urljoin(base_url, href) for href in hrefs if href]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def scrape(
    url: str, want_links: bool = False, want_html: bool = False, actions: list[dict] | None = None
) -> tuple[str, list[str], str, str]:
    """Same contract as firecrawl_client.scrape - see its docstring for what each parameter means."""
    print(f"{datetime.now():%H:%M:%S} - crawl4ai scrape: {url}")

    config_kwargs: dict = {
        "cache_mode": CacheMode.BYPASS,
        "excluded_tags": _EXCLUDED_TAGS,
        "verbose": False,
        "page_timeout": _BASE_PAGE_TIMEOUT_MS,
    }
    if not want_links:
        config_kwargs["markdown_generator"] = _MAIN_CONTENT_MARKDOWN_GENERATOR
    if actions:
        config_kwargs["js_code"] = _actions_to_js(actions)
        # Actions carry their own explicit waits (see _actions_to_js) - extend the
        # page timeout to cover them, or a long "load more" click chain times out
        # before its own waits even finish.
        total_wait_ms = sum(a.get("milliseconds", 0) for a in actions if a.get("type") == "wait")
        config_kwargs["page_timeout"] = _BASE_PAGE_TIMEOUT_MS + total_wait_ms

    config = CrawlerRunConfig(**config_kwargs)
    result = _persistent.arun(url, config)

    if not result.success:
        raise RuntimeError(f"crawl4ai reported an error for {url}: {result.error_message}")

    if want_links:
        markdown = (result.markdown.raw_markdown if result.markdown else "") or ""
    else:
        fit = result.markdown.fit_markdown if result.markdown else None
        markdown = fit or (result.markdown.raw_markdown if result.markdown else "") or ""
    markdown = markdown.strip()

    links = _extract_links(result, url) if want_links else []
    html = (result.html or "").strip() if want_html else ""
    final_url = result.redirected_url or result.url or url

    return markdown, links, html, final_url
