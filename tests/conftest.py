"""
Test-wide fixtures.

scraper_client.scrape() (see services/scraper_client.py) tries crawl4ai_client
first in production, only falling back to firecrawl_client on failure.
tests/scraping/test_scraping.py monkeypatches firecrawl_client.scrape with
canned, offline fixtures - that only takes effect if scraper_client actually
calls firecrawl_client. Forcing scraper_backend="firecrawl" here makes it do
exactly that (skipping crawl4ai entirely, per scraper_client's own routing),
restoring the tests' original no-real-network seam without changing anything
any test itself asserts or patches.
"""

import pytest

from services.config import settings


@pytest.fixture(autouse=True)
def _force_firecrawl_backend(monkeypatch):
    monkeypatch.setattr(settings, "scraper_backend", "firecrawl")
