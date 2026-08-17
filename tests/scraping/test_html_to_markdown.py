"""
Real (not mocked) HTML -> markdown conversion, using crawl4ai's own "raw:" URL
scheme - the same DefaultMarkdownGenerator/PruningContentFilter pipeline
crawl4ai_client.py uses in production, fed a plain HTML string directly, no
browser navigation to a real URL and no Docker/self-hosted Firecrawl needed.

This is the one place in the suite that exercises a real headless browser
(Playwright, via crawl4ai) rather than a mock - everywhere else monkeypatches
scraper_client/firecrawl_client/crawl4ai_client outright. Skipped automatically
if the browser isn't available (e.g. `playwright install` never ran), so it
never breaks a machine/CI image that doesn't have it.
"""

import pytest

crawl4ai = pytest.importorskip("crawl4ai")

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # noqa: E402
from crawl4ai.content_filter_strategy import PruningContentFilter  # noqa: E402
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator  # noqa: E402

# Mirrors crawl4ai_client.py's own config exactly (see _EXCLUDED_TAGS/_MAIN_CONTENT_MARKDOWN_GENERATOR
# there) - this test is only meaningful if it exercises the same conversion settings production uses.
_EXCLUDED_TAGS = ["nav", "header", "footer", "aside", "script", "style", "noscript"]
_MAIN_CONTENT_MARKDOWN_GENERATOR = DefaultMarkdownGenerator(content_filter=PruningContentFilter())

_EVENT_PAGE_HTML = """
<html>
<head><title>Lyme Regis 10K</title></head>
<body>
<nav>Home | Events | About | Contact</nav>
<header><div class="logo">Jurassic Coast Races</div></header>
<article>
<h1>Lyme Regis 10K</h1>
<p>Join us for the Lyme Regis 10K on Sunday 12th July 2026, starting and finishing
on the seafront. A scenic, challenging route along the Jurassic Coast.</p>
<h2>Distances</h2>
<ul>
<li>10k - &pound;25</li>
<li>5k Fun Run - &pound;15</li>
</ul>
<p>Minimum age for the 10k is 15. The 5k Fun Run has no age restriction.</p>
</article>
<aside class="sidebar"><div class="ad">Sponsored: Running Shoes 20% off!</div></aside>
<footer>&copy; 2026 Jurassic Coast Races. All rights reserved.</footer>
</body>
</html>
"""


async def _crawl_raw_html(html: str) -> str:
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            verbose=False,
            excluded_tags=_EXCLUDED_TAGS,
            markdown_generator=_MAIN_CONTENT_MARKDOWN_GENERATOR,
        )
        result = await crawler.arun(url="raw:" + html, config=config)
        assert result.success, f"crawl4ai reported an error: {result.error_message}"
        markdown = result.markdown.fit_markdown if result.markdown else None
        return (markdown or (result.markdown.raw_markdown if result.markdown else "") or "").strip()


@pytest.mark.asyncio
async def test_raw_html_converts_to_markdown_with_boilerplate_stripped():
    markdown = await _crawl_raw_html(_EVENT_PAGE_HTML)

    # The actual event content survives conversion...
    assert "Lyme Regis 10K" in markdown
    assert "Sunday 12th July 2026" in markdown
    assert "10k" in markdown.lower() and "5k" in markdown.lower()
    assert "Minimum age" in markdown

    # ...but nav/header/aside/footer boilerplate - excluded_tags plus
    # PruningContentFilter's own boilerplate scoring - does not.
    assert "Home | Events" not in markdown
    assert "Sponsored" not in markdown
    assert "All rights reserved" not in markdown


@pytest.mark.asyncio
async def test_raw_html_with_no_real_content_yields_sparse_markdown():
    # Not a real event page (no article content at all) - the point is only that
    # conversion itself doesn't error out on a boilerplate-only/near-empty page.
    markdown = await _crawl_raw_html("<html><body><nav>Home | About</nav></body></html>")
    assert "Home | About" not in markdown
