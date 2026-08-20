"""
Unit tests for admin/discover_sitemaps.py: reads a robots.txt body for a
Sitemap: entry, and rewrites the seed CSV with whatever it found.
"""

import csv

import pytest
import requests

from services.admin import discover_sitemaps


def test_find_sitemap_extracts_first_entry_case_insensitively():
    robots_txt = "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n"
    assert discover_sitemaps.find_sitemap(robots_txt) == "https://example.com/sitemap.xml"


def test_find_sitemap_lowercase_directive():
    robots_txt = "user-agent: *\nsitemap: https://example.com/sitemap.xml\n"
    assert discover_sitemaps.find_sitemap(robots_txt) == "https://example.com/sitemap.xml"


def test_find_sitemap_none_when_absent():
    robots_txt = "User-agent: *\nDisallow: /private/\n"
    assert discover_sitemaps.find_sitemap(robots_txt) is None


def test_find_sitemap_takes_first_of_several():
    robots_txt = "Sitemap: https://example.com/one.xml\nSitemap: https://example.com/two.xml\n"
    assert discover_sitemaps.find_sitemap(robots_txt) == "https://example.com/one.xml"


@pytest.mark.parametrize("homepage_url", [
    "https://www.runthrough.co.uk/",
    "https://www.runthrough.co.uk",
    "https://www.runthrough.co.uk/events-timeline",
])
def test_robots_txt_url_always_domain_root(homepage_url):
    assert discover_sitemaps.robots_txt_url(homepage_url) == "https://www.runthrough.co.uk/robots.txt"


def test_discover_sitemaps_rewrites_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "organisers_seed.csv"
    csv_path.write_text(
        "name,listing_urls,homepage_url,discovered_via\n"
        'Has Sitemap,"[]",https://has-sitemap.example.com/,test\n'
        'No Sitemap,"[]",https://no-sitemap.example.com/,test\n'
        'Unreachable,"[]",https://unreachable.example.com/,test\n',
        encoding="utf-8",
    )

    robots_by_url = {
        "https://has-sitemap.example.com/robots.txt": "Sitemap: https://has-sitemap.example.com/sitemap.xml\n",
        "https://no-sitemap.example.com/robots.txt": "User-agent: *\nDisallow: /private/\n",
    }

    def fake_get(url, headers=None, timeout=None):
        if url not in robots_by_url:
            raise requests.ConnectionError(f"no fake response for {url}")
        response = requests.Response()
        response.status_code = 200
        response._content = robots_by_url[url].encode("utf-8")
        return response

    monkeypatch.setattr(discover_sitemaps.requests, "get", fake_get)
    monkeypatch.setattr(discover_sitemaps, "_DELAY_BETWEEN_REQUESTS", 0)

    discover_sitemaps.discover_sitemaps(csv_path)

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = {row["name"]: row for row in csv.DictReader(f)}

    assert rows["Has Sitemap"]["sitemap_url"] == "https://has-sitemap.example.com/sitemap.xml"
    assert rows["No Sitemap"]["sitemap_url"] == ""
    assert rows["Unreachable"]["sitemap_url"] == ""
    # Every other column survives the rewrite untouched.
    assert rows["Has Sitemap"]["homepage_url"] == "https://has-sitemap.example.com/"
