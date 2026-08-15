"""
Maintenance script: for every organiser in the seed CSV, fetch its
robots.txt and record whatever `Sitemap:` entry it advertises, so the crawl
pipeline can read that sitemap directly (see sitemap_crawler.py) instead of
opening the listing page and clicking through load-more/pagination.

Deliberately simple - a robots.txt is a small static text file, so this is
a plain `requests.get`, no Firecrawl/browser needed. Reads the whole seed
CSV into memory, visits every organiser's robots.txt, and only rewrites the
CSV once at the end (not incrementally per row), so a run that's interrupted
partway through never leaves the file half-written.

Usage:
    python -m services.discover_sitemaps
"""

import csv
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

SEED_CSV = Path(__file__).parent / "data" / "organisers_seed.csv"
_TIMEOUT = 10
_DELAY_BETWEEN_REQUESTS = 0.3  # be polite - this hits ~170 different sites in one run
_RETRIES = 3  # some sites transiently 404/5xx robots.txt (e.g. a stale edge cache) - a couple retries clears most of these
_RETRY_BACKOFF = 2.0  # seconds, doubles each attempt


def robots_txt_url(homepage_url: str) -> str:
    """robots.txt always lives at the domain root, regardless of homepage_url's own path."""
    return urljoin(homepage_url, "/robots.txt")


def find_sitemap(robots_txt: str) -> str | None:
    """
    A robots.txt can advertise more than one `Sitemap:` line (e.g. one per
    language/section) - takes the first, since that's enough to get from
    "no sitemap known" to "one sitemap known" for sites that only have one,
    which is the common case seen in practice. Case-insensitive per the
    robots.txt spec (real files use both `Sitemap:` and `sitemap:`).
    """
    for line in robots_txt.splitlines():
        line = line.strip()
        if line.lower().startswith("sitemap:"):
            return line.split(":", 1)[1].strip()
    return None


def discover_sitemaps(csv_path: Path = SEED_CSV) -> None:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "sitemap_url" not in fieldnames:
        fieldnames.append("sitemap_url")

    found = 0
    for row in rows:
        homepage_url = row["homepage_url"]
        url = robots_txt_url(homepage_url)

        response = None
        error = None
        backoff = _RETRY_BACKOFF
        for attempt in range(1, _RETRIES + 1):
            try:
                response = requests.get(url, timeout=_TIMEOUT)
                response.raise_for_status()
                error = None
                break
            except requests.RequestException as e:
                error = e
                response = None
                if attempt < _RETRIES:
                    time.sleep(backoff)
                    backoff *= 2

        if error is not None:
            print(f"  {homepage_url}: robots.txt fetch failed after {_RETRIES} attempts ({type(error).__name__}: {error})")
            time.sleep(_DELAY_BETWEEN_REQUESTS)
            continue

        sitemap_url = find_sitemap(response.text)
        if sitemap_url:
            print(f"  {homepage_url}: sitemap -> {sitemap_url}")
            row["sitemap_url"] = sitemap_url
            found += 1
        else:
            print(f"  {homepage_url}: no Sitemap: entry in robots.txt")
            row.setdefault("sitemap_url", row.get("sitemap_url", ""))
        #time.sleep(_DELAY_BETWEEN_REQUESTS)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})

    print(f"rewrote {csv_path}: sitemap found for {found}/{len(rows)} organiser(s)")


if __name__ == "__main__":
    discover_sitemaps()
