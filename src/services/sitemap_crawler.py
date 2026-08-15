"""
Reads an organiser's sitemap (discovered from robots.txt - see
discover_sitemaps.py) as the preferred, direct source of its event detail
page URLs, instead of opening the listing page and clicking through
load-more/pagination (listing_crawler.py's mechanism, still used as the
fallback when no sitemap is known).

Plain requests + stdlib XML parsing throughout - a sitemap is just a static
XML file, no browser/JS rendering (Firecrawl) needed to read one.

Handles both shapes a robots.txt `Sitemap:` entry can point to:
- A "url-sitemap" (<urlset>): already the list of individual page URLs -
  used as-is (after the same domain/junk filtering listing_crawler.py uses
  everywhere else in the pipeline).
- A "sitemap index" (<sitemapindex>): a list of OTHER sitemaps (e.g. one
  for events, one for categories, one for blog posts) - an LLM
  (llm_extractor.select_events_sitemap) picks which one sounds like it
  lists individual events, judged only from each sub-sitemap's own URL,
  then that one is fetched and used as the url-sitemap above.
"""

from xml.etree import ElementTree

import requests

from services import llm_extractor
from services.link_filters import filter_candidate_links

_TIMEOUT = 15


def _strip_ns(tag: str) -> str:
    """ElementTree prefixes every tag with its namespace as '{uri}tag' -
    stripping it lets callers match by plain tag name regardless of
    whether a given sitemap even bothers declaring the sitemaps.org xmlns
    (real-world ones aren't all consistent about it)."""
    return tag.rsplit("}", 1)[-1]


def _fetch_xml(url: str) -> ElementTree.Element:
    response = requests.get(url, timeout=_TIMEOUT)
    response.raise_for_status()
    return ElementTree.fromstring(response.content)


def _child_locs(root: ElementTree.Element, wrapper_tag: str) -> list[str]:
    """
    Direct children of `root` named `wrapper_tag`, each contributing its own
    <loc> child's text - covers both <sitemapindex><sitemap><loc> and
    <urlset><url><loc>, the only two shapes a sitemap.org XML file comes in.
    """
    urls = []
    for child in root:
        if _strip_ns(child.tag) != wrapper_tag:
            continue
        for grandchild in child:
            if _strip_ns(grandchild.tag) == "loc" and grandchild.text:
                urls.append(grandchild.text.strip())
    return urls


def get_event_urls(sitemap_url: str, homepage_url: str) -> list[str] | None:
    """
    Returns the event detail page URLs found via `sitemap_url`, resolving
    one level of sitemap-index indirection if needed (a sitemap index
    pointing at ANOTHER sitemap index is not handled - not seen in
    practice, and listing_crawler.py's own load-more/pagination fallback
    exists for whatever a sitemap can't resolve).

    Returns None on any fetch/parse failure, an unrecognised root element,
    or a sitemap index with no events-like sub-sitemap found - callers
    should fall back to listing_crawler's normal page-crawling mechanism in
    that case, not treat it as "this organiser genuinely has zero events".
    An empty list, by contrast, means the sitemap was read successfully but
    genuinely contained no links under the organiser's own domain.
    """
    try:
        root = _fetch_xml(sitemap_url)
    except Exception as e:
        print(f"sitemap_crawler: failed to fetch {sitemap_url}: {type(e).__name__}: {e}")
        return None

    tag = _strip_ns(root.tag)
    if tag == "sitemapindex":
        sub_sitemaps = _child_locs(root, "sitemap")
        events_sitemap = llm_extractor.select_events_sitemap(sub_sitemaps)
        if not events_sitemap:
            print(f"sitemap_crawler: no events-like sub-sitemap among {len(sub_sitemaps)} in {sitemap_url}")
            return None
        try:
            root = _fetch_xml(events_sitemap)
        except Exception as e:
            print(f"sitemap_crawler: failed to fetch {events_sitemap}: {type(e).__name__}: {e}")
            return None
        tag = _strip_ns(root.tag)

    if tag != "urlset":
        print(f"sitemap_crawler: unexpected root element <{tag}> in sitemap, giving up")
        return None

    urls = _child_locs(root, "url")
    return filter_candidate_links(urls, homepage_url)
