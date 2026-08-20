"""
Test-wide fixtures.

scraper_client.scrape() (see services/scraping/backends/scraper_client.py) tries
crawl4ai_client first in production, only falling back to firecrawl_client on
failure. tests/scraping/test_scraping.py monkeypatches firecrawl_client.scrape with
canned, offline fixtures - that only takes effect if scraper_client actually
calls firecrawl_client. Forcing scraper_backend="firecrawl" here makes it do
exactly that (skipping crawl4ai entirely, per scraper_client's own routing),
restoring the tests' original no-real-network seam without changing anything
any test itself asserts or patches.
"""

import pytest
from protego import Protego

from services.common.config import settings
from services.scraping import robots


@pytest.fixture(autouse=True)
def _force_firecrawl_backend(monkeypatch):
    monkeypatch.setattr(settings, "scraper_backend", "firecrawl")


@pytest.fixture(autouse=True)
def _stub_robots_network(monkeypatch):
    """
    services.scraping.robots fetches robots.txt over the real network (is_allowed(),
    also called from sitemap_crawler.py; wait_for_crawl_delay(), called from
    scraper_client.scrape()/sitemap_crawler.py) - without this, every test
    anywhere in the suite that exercises a scrape/crawl/sitemap path would make
    a live HTTP call the first time it saw a given domain. Stubbed to "no
    robots.txt found" (the network-equivalent of a 404) so is_allowed() stays
    permissive and wait_for_crawl_delay() no-ops everywhere by default.
    tests/scraping/test_robots.py overrides this fixture by name (pytest's
    documented override-by-name mechanism) to exercise the real fetch/parse/throttle
    logic instead, with its own canned requests.get responses.
    """
    monkeypatch.setattr(robots, "_parser_for", lambda domain_root: Protego.parse(""))
