"""
Unit tests for the sitemap-as-preferred-source mechanism:

- discover_sitemaps.py: reads a robots.txt body for a Sitemap: entry, and
  rewrites the seed CSV with whatever it found.
- llm_extractor.select_events_sitemap: picks which sub-sitemap of a sitemap
  index sounds like it lists individual events, by index (see
  analyze_listing_page's own index-based fix for why: never echo full URLs
  back).
- sitemap_crawler.get_event_urls: reads either shape a robots.txt-advertised
  sitemap can be (a direct url-sitemap, or a sitemap index needing the above
  resolved first) - no Firecrawl, no browser, plain requests + stdlib XML.

No real network/LLM calls anywhere here - requests.get and llm_extractor's
_run_llm boundary are monkeypatched with canned responses.
"""

import csv
import gzip

import pytest
import requests

from services import discover_sitemaps, llm_extractor, sitemap_crawler

# ---------------------------------------------------------------------------
# discover_sitemaps.py
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# llm_extractor.select_events_sitemap
# ---------------------------------------------------------------------------

def test_select_events_sitemap_resolves_index(monkeypatch):
    sitemap_urls = [
        "https://www.runthrough.co.uk/sitemaps/events.xml",
        "https://www.runthrough.co.uk/sitemaps/event-categories.xml",
        "https://www.runthrough.co.uk/sitemaps/pages.xml",
    ]
    monkeypatch.setattr(llm_extractor, "_run_llm", lambda *a, **k: {"events_sitemap_index": 0})

    assert llm_extractor.select_events_sitemap(sitemap_urls) == sitemap_urls[0]


def test_select_events_sitemap_none_when_model_says_null(monkeypatch):
    monkeypatch.setattr(llm_extractor, "_run_llm", lambda *a, **k: {"events_sitemap_index": None})
    assert llm_extractor.select_events_sitemap(["https://example.com/a.xml"]) is None


def test_select_events_sitemap_none_for_out_of_range_index(monkeypatch):
    monkeypatch.setattr(llm_extractor, "_run_llm", lambda *a, **k: {"events_sitemap_index": 5})
    assert llm_extractor.select_events_sitemap(["https://example.com/a.xml"]) is None


def test_select_events_sitemap_empty_input_skips_llm_call(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("should not call the LLM for an empty candidate list")

    monkeypatch.setattr(llm_extractor, "_run_llm", fail_if_called)
    assert llm_extractor.select_events_sitemap([]) is None


# ---------------------------------------------------------------------------
# sitemap_crawler.get_event_urls
# ---------------------------------------------------------------------------

_URLSET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://raceforlife.cancerresearchuk.org/event/hyde-park-5k</loc></url>
<url><loc>https://raceforlife.cancerresearchuk.org/event/heaton-park-10k</loc></url>
<url><loc>https://raceforlife.cancerresearchuk.org/about</loc></url>
<url><loc>https://www.facebook.com/raceforlife</loc></url>
</urlset>
"""

_SITEMAPINDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.runthrough.co.uk/sitemaps/events.xml</loc><lastmod>2026-08-07</lastmod></sitemap>
<sitemap><loc>https://www.runthrough.co.uk/sitemaps/event-categories.xml</loc><lastmod>2026-08-07</lastmod></sitemap>
<sitemap><loc>https://www.runthrough.co.uk/sitemaps/pages.xml</loc><lastmod>2026-08-07</lastmod></sitemap>
</sitemapindex>
"""

_EVENTS_SUBSITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.runthrough.co.uk/event/newark-half-marathon</loc></url>
<url><loc>https://www.runthrough.co.uk/event/frome-running-festival</loc></url>
</urlset>
"""


def _fake_get(xml_by_url: dict[str, str]):
    def fake_get(url, headers=None, timeout=None):
        if url not in xml_by_url:
            raise requests.ConnectionError(f"no fake response for {url}")
        response = requests.Response()
        response.status_code = 200
        response._content = xml_by_url[url].encode("utf-8")
        return response
    return fake_get


def test_get_event_urls_case1_urlset_used_directly(monkeypatch):
    monkeypatch.setattr(
        sitemap_crawler.requests, "get",
        _fake_get({"https://raceforlife.cancerresearchuk.org/sitemap.xml": _URLSET_XML}),
    )

    urls = sitemap_crawler.get_event_urls(
        "https://raceforlife.cancerresearchuk.org/sitemap.xml",
        "https://raceforlife.cancerresearchuk.org/",
    )

    # Case 1 is used as-is (no per-URL LLM confirmation) - only the cheap
    # domain/junk filter runs, so an off-site link is dropped but a same-site
    # non-event page (here, /about) is NOT - "just retrieve all urls".
    assert urls == [
        "https://raceforlife.cancerresearchuk.org/event/hyde-park-5k",
        "https://raceforlife.cancerresearchuk.org/event/heaton-park-10k",
        "https://raceforlife.cancerresearchuk.org/about",
    ]


def test_get_event_urls_case2_sitemapindex_resolves_via_llm(monkeypatch):
    monkeypatch.setattr(sitemap_crawler.requests, "get", _fake_get({
        "https://www.runthrough.co.uk/sitemap.xml": _SITEMAPINDEX_XML,
        "https://www.runthrough.co.uk/sitemaps/events.xml": _EVENTS_SUBSITEMAP_XML,
    }))
    # events.xml is index 0 in _SITEMAPINDEX_XML - end-to-end through the
    # real select_events_sitemap logic, only the LLM boundary is faked.
    monkeypatch.setattr(llm_extractor, "_run_llm", lambda *a, **k: {"events_sitemap_index": 0})

    urls = sitemap_crawler.get_event_urls(
        "https://www.runthrough.co.uk/sitemap.xml", "https://www.runthrough.co.uk/",
    )

    assert urls == [
        "https://www.runthrough.co.uk/event/newark-half-marathon",
        "https://www.runthrough.co.uk/event/frome-running-festival",
    ]


def test_get_event_urls_none_when_no_events_sitemap_identified(monkeypatch):
    monkeypatch.setattr(sitemap_crawler.requests, "get", _fake_get({
        "https://www.runthrough.co.uk/sitemap.xml": _SITEMAPINDEX_XML,
    }))
    monkeypatch.setattr(llm_extractor, "_run_llm", lambda *a, **k: {"events_sitemap_index": None})

    assert sitemap_crawler.get_event_urls(
        "https://www.runthrough.co.uk/sitemap.xml", "https://www.runthrough.co.uk/",
    ) is None


def test_get_event_urls_none_when_disallowed_by_robots(monkeypatch, capsys):
    # A robots.txt-advertised sitemap is usually fine to fetch (that's how it got
    # advertised), but not guaranteed - _fetch_xml must still check, and must not
    # touch the network for the sitemap itself when it doesn't.
    monkeypatch.setattr(sitemap_crawler.robots, "is_allowed", lambda url, registrator="bot": False)
    monkeypatch.setattr(
        sitemap_crawler.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch a robots-disallowed sitemap")),
    )

    assert sitemap_crawler.get_event_urls(
        "https://example.com/sitemap.xml", "https://example.com/",
    ) is None
    # Same grep-able marker as event_crawler.py/listing_crawler.py's own skips -
    # otherwise this reads just like any other "failed to fetch" in the log.
    assert "ROBOTS-SKIP: https://example.com/sitemap.xml (sitemap)" in capsys.readouterr().out


def test_registrator_forwarded_to_robots_is_allowed(monkeypatch):
    captured = {}

    def fake_is_allowed(url, registrator="bot"):
        captured["registrator"] = registrator
        return True

    monkeypatch.setattr(sitemap_crawler.robots, "is_allowed", fake_is_allowed)
    # get_event_urls's own try/except around _fetch_xml swallows this - only the
    # registrator forwarding above matters to this test, not what happens after.
    monkeypatch.setattr(sitemap_crawler.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stop here")))

    sitemap_crawler.get_event_urls("https://example.com/sitemap.xml", "https://example.com/", registrator="jane_doe")

    assert captured["registrator"] == "jane_doe"


def test_get_event_urls_none_on_fetch_failure(monkeypatch):
    def raise_connection_error(url, headers=None, timeout=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(sitemap_crawler.requests, "get", raise_connection_error)

    assert sitemap_crawler.get_event_urls(
        "https://example.com/sitemap.xml", "https://example.com/",
    ) is None


_GZIPPED_URLSET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://jurassiccoast10k.co.uk/event/lyme-regis-10k</loc></url>
<url><loc>https://jurassiccoast10k.co.uk/event/west-bay-10k</loc></url>
</urlset>
"""


def test_get_event_urls_gzip_compressed_sitemap_is_decompressed(monkeypatch):
    # Confirmed in practice (jurassiccoast10k.co.uk/sitemap.xml.gz): served as raw
    # gzip bytes with no Content-Encoding: gzip header, so requests/urllib3 never
    # auto-decompresses it - response.content really is still gzip-compressed here.
    def fake_get(url, headers=None, timeout=None):
        assert url == "https://jurassiccoast10k.co.uk/sitemap.xml.gz"
        response = requests.Response()
        response.status_code = 200
        response._content = gzip.compress(_GZIPPED_URLSET_XML.encode("utf-8"))
        return response

    monkeypatch.setattr(sitemap_crawler.requests, "get", fake_get)

    urls = sitemap_crawler.get_event_urls(
        "https://jurassiccoast10k.co.uk/sitemap.xml.gz",
        "https://jurassiccoast10k.co.uk/",
    )

    assert urls == [
        "https://jurassiccoast10k.co.uk/event/lyme-regis-10k",
        "https://jurassiccoast10k.co.uk/event/west-bay-10k",
    ]


def test_get_event_urls_none_on_unexpected_root_element(monkeypatch):
    monkeypatch.setattr(sitemap_crawler.requests, "get", _fake_get({
        "https://example.com/sitemap.xml": "<html><body>not a sitemap</body></html>",
    }))

    assert sitemap_crawler.get_event_urls(
        "https://example.com/sitemap.xml", "https://example.com/",
    ) is None
