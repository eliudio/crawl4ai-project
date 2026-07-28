"""
generate_site_configs.py
Generates SiteConfig using Grok + real page content from self-hosted Firecrawl (v2 compatible).

Selectors are grounded in the page's real HTML (real classes/hrefs), not just markdown
text, and every generated config is immediately validated against the live site via
Selenium before it's trusted or cached. A failed validation is fed back to Grok for a
single retry with the specific failure explained.
"""

from pathlib import Path
import json
import re
import requests
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from openai import OpenAI

from site_config import SiteConfig
from site_config_cache import load_cached_config, save_config_to_cache
from process_site import validate_site_config, count_visible_event_links
from grok.key import GROK_API_KEY

_JUNK_HREF_HINTS = (
    "facebook", "twitter", "instagram", "linkedin", "youtube", "mailto:", "tel:",
    "javascript:", "#", "/privacy", "/terms", "/cookie", "/login", "/signup",
)

_BUTTON_TEXT_PATTERN = re.compile(
    r"load\s*more|show\s*more|view\s*more|see\s*more|^\s*next\s*$|next\s*page",
    re.IGNORECASE,
)


def _scrape_listing_page(url: str) -> Tuple[str, str]:
    """Scrape the listing page via self-hosted Firecrawl. Returns (markdown, html)."""
    markdown, html = "", ""
    try:
        response = requests.post(
            "http://localhost:3002/v1/scrape",
            json={
                "url": url,
                "formats": ["markdown", "html"],
                "onlyMainContent": True,
                "excludeTags": ["script", "style", "iframe", "noscript"],
                "waitFor": 1500,
                "timeout": 90000
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()

        if data.get("success") and "data" in data:
            markdown = data["data"].get("markdown") or ""
            html = data["data"].get("html") or ""
            print(f"{datetime.now():%H:%M:%S} - Scraped page successfully "
                  f"({len(markdown)} md chars, {len(html)} html chars)")
        else:
            print(f"{datetime.now():%H:%M:%S} - Scrape failed: {data.get('error', 'unknown')}")

    except requests.exceptions.HTTPError as e:
        print(f"{datetime.now():%H:%M:%S} - HTTP error {e.response.status_code}: {e.response.text[:500]}")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Scraping error: {type(e).__name__}: {e}")

    # Keep markdown capped for prompt size, but don't truncate raw html here — the
    # relevant pagination/button markup can sit well past any fixed byte offset
    # (e.g. after a large header/hero section). _build_structural_hints parses the
    # full document and truncates only the compact summary it produces.
    return markdown[:12000], html


def _group_key(href: str) -> str:
    """Collapse a href to a coarse 'template' so links to similar pages cluster together."""
    segments = [s for s in urlparse(href).path.split("/") if s]
    return "/" + "/".join(segments[:2]) if segments else "/"


def _find_numbered_pagination_links(soup: BeautifulSoup) -> dict:
    """
    Find anchors whose visible text is a plain number (e.g. "2", "3", "4") and whose
    href shares a common template differing only by that number. These are the
    strongest possible signal of pagination — but their href often lands in the same
    path-prefix "group" as real event links (e.g. /events/p2 vs /events/some-race),
    and their link text ("2") never matches "next"/"load more" wording, so they'd
    otherwise go completely unnoticed and get mistaken for single_page.
    """
    templates: dict = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if not text.isdigit():
            continue
        href = a["href"]
        match = re.search(r"(\d+)(?!.*\d)", href)
        if not match:
            continue
        start, end = match.span(1)
        template = href[:start] + "{n}" + href[end:]
        templates.setdefault(template, []).append(a)

    # Require at least 2 distinct numbered links sharing a template — a single
    # numeric-text link is more likely an unrelated id than real pagination.
    return {t: anchors for t, anchors in templates.items() if len(anchors) >= 2}


def _build_structural_hints(html: str, max_chars: int = 4000) -> str:
    """
    Build a compact summary of the page's REAL anchor/button structure — actual
    classes and hrefs pulled from the DOM, not inferred from markdown prose — so
    Grok's selector guesses are grounded in what's actually there instead of guessed
    from URL conventions. This is what catches cases like numbered pagination links
    (e.g. <a href="/events/p2">2</a>) that a markdown-only view can't reveal.
    """
    if not html:
        return "No HTML available — infer conservatively."

    soup = BeautifulSoup(html, "html.parser")

    groups: dict = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or any(j in href.lower() for j in _JUNK_HREF_HINTS):
            continue
        groups.setdefault(_group_key(href), []).append(a)

    ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    lines = ["REAL ANCHOR LINK GROUPS (grouped by URL path prefix, largest groups first):"]
    for key, anchors in ranked[:8]:
        lines.append(f"\nGroup {key!r} — {len(anchors)} links found on page:")
        for a in anchors[:3]:
            classes = " ".join(a.get("class", []))
            text = a.get_text(strip=True)[:60]
            lines.append(f'  <a class="{classes}" href="{a["href"]}">{text}</a>')

    candidates = []
    seen_ids = set()
    # Page builders (Divi, Elementor, Wix, ...) very commonly render a "Load More"
    # control as a <div>/<span> with a click handler rather than a semantic
    # <button>/<a> — so those tags must be scanned too. The length cap keeps this
    # from matching a giant wrapping container whose full nested text happens to
    # contain the phrase somewhere deep inside.
    for tag in soup.find_all(["button", "a", "div", "span", "li"]):
        text = tag.get_text(strip=True)
        if len(text) > 60:
            continue
        aria = tag.get("aria-label", "")
        classes = " ".join(tag.get("class", []))
        if (_BUTTON_TEXT_PATTERN.search(text) or _BUTTON_TEXT_PATTERN.search(aria)
                or _BUTTON_TEXT_PATTERN.search(classes.replace("-", " ").replace("_", " "))):
            if id(tag) not in seen_ids:
                seen_ids.add(id(tag))
                candidates.append(tag)
    for tag in soup.find_all(True, rel="next"):
        if id(tag) not in seen_ids:
            seen_ids.add(id(tag))
            candidates.append(tag)

    if candidates:
        lines.append("\nCANDIDATE 'LOAD MORE' / 'NEXT PAGE' ELEMENTS (real HTML):")
        lines.append("(Note: these are NOT necessarily <button> or <a> tags — page builders like "
                      "Divi/Elementor/Wix often render a clickable 'Load More' as a plain <div> or <span> "
                      "with a class-based click handler. Build load_more_selector from the actual tag name "
                      "and class shown below, e.g. 'div.dmach-loadmore', not an assumed 'button' selector.)")
        for tag in candidates[:10]:
            classes = " ".join(tag.get("class", []))
            href = tag.get("href", "")
            aria = tag.get("aria-label", "")
            text = tag.get_text(strip=True)[:40]
            lines.append(f'  <{tag.name} class="{classes}" href="{href}" aria-label="{aria}">{text}</{tag.name}>')
    else:
        lines.append("\nNo obvious 'Load More' / 'Next' element found in the real HTML "
                      "(it may only appear as a <div>/<span> with unrelated text, or be added by JS after load).")

    pagination_groups = _find_numbered_pagination_links(soup)
    if pagination_groups:
        lines.append("\nCANDIDATE NUMBERED PAGE LINKS — STRONG EVIDENCE OF MULTI-PAGE PAGINATION:")
        lines.append("(Plain-numbered links like '2', '3', '4' sharing one href pattern. "
                      "This site has multiple pages even though no 'Load More'/'Next' text exists.)")
        for template, anchors in list(pagination_groups.items())[:5]:
            sample = anchors[0]
            classes = " ".join(sample.get("class", []))
            href_prefix = template.split("{n}")[0]
            path_prefix = urlparse(href_prefix).path or href_prefix
            suggested_xpath = (
                f"//a[contains(@href, '{path_prefix}') and "
                f"translate(normalize-space(text()), '0123456789', '') = '']"
            )
            lines.append(
                f'  href pattern "{template}" — {len(anchors)} page links found, '
                f'e.g. <a class="{classes}" href="{sample["href"]}">{sample.get_text(strip=True)}</a>'
            )
            lines.append(
                f"  SUGGESTED next_button_selector (use this verbatim or adapt it): {suggested_xpath}"
            )
        lines.append(
            "If this section is non-empty, you MUST set load_strategy='pagination' — do not choose "
            "single_page or load_more. IMPORTANT: a plain 'a[href*=\"...\"]' CSS substring selector is "
            "NOT safe here — an event's own slug can coincidentally start with the same letters as the "
            "page-link prefix (e.g. '/events/p' also matches an event slug like '/events/phoenix-run'). "
            "Use the SUGGESTED XPath above (or an equivalent that also requires the link TEXT to be "
            "purely numeric), since that's the only thing that reliably distinguishes a page-number "
            "link from a same-prefixed event link."
        )

    return "\n".join(lines)[:max_chars]


def _build_system_prompt(url: str, markdown: str, html_hints: str, failure_note: str = "") -> str:
    failure_block = ""
    if failure_note:
        failure_block = f"""
IMPORTANT — YOUR PREVIOUS ATTEMPT FAILED VALIDATION AGAINST THE LIVE PAGE:
{failure_note}

Fix this. Ground your new answer in the REAL ANCHOR LINK GROUPS / CANDIDATE ELEMENTS
below — do not repeat the same selector, and avoid broad substring matches that could
match unrelated links (e.g. 'a[href*="/p"]' can match "/partners" as well as "/p2").
"""

    return f"""You are an expert web scraping configuration generator for event listing pages.

Your task: Analyze the provided page content for {url} and output a precise JSON
configuration that can be used by a Python scraper.
{failure_block}
The scraper supports THREE strategies:
1. "single_page" — all events are visible immediately after page load (no clicking needed).
2. "load_more" — there is a "Load More", "Show More", or similar button that must be clicked repeatedly until it disappears.
3. "pagination" — there are numbered pages or a "Next" button/link to navigate to additional pages.

Rules:
- Output ONLY a single valid JSON object. Nothing else. No explanations, no markdown fences.
- Use null for any field you cannot confidently determine.
- Prefer CSS selectors over XPath when possible (more stable).
- Ground every selector in the REAL ANCHOR LINK GROUPS / CANDIDATE ELEMENTS section below —
  those are actual elements pulled from the live DOM (real classes, real hrefs). Do NOT
  guess a selector that isn't backed by something you can see there.
- Prefer narrow, specific selectors over broad substring matches. A selector like
  'a[href*="/p"]' is dangerous — it can match "/partners/foo" as well as "/events/p2".
  Prefer 'a[href*="/events/p"]' or similar once you've seen the real href in the hints.
- For buttons with text like "Load More", you may use a robust XPath with contains(text()).
- event_link_selector should be as specific as possible (e.g. 'a[href*="/e/"]' or '.event-listing a[href]').
- base_url must be the correct domain for building absolute event detail URLs (critical for sites where listing and detail pages are on different subdomains).

Required JSON structure (exact keys):
{{
  "name": string,                        // short organiser name, e.g. "Zig Zag Running"
  "listing_url": "{url}",
  "base_url": string,                    // correct base for urljoin (may differ from listing_url domain)
  "event_link_selector": string|null,    // best CSS selector for event detail <a> tags
  "link_pattern": string|null,           // fallback substring for href contains()
  "link_regex": string|null,             // precise regex for href (optional)
  "load_strategy": "single_page" | "load_more" | "pagination",
  "load_more_selector": string|null,     // CSS or XPath for Load More button (only if load_strategy == "load_more")
  "next_button_selector": string|null,   // CSS or XPath for Next / page-number link (only if load_strategy == "pagination")
  "max_load_clicks": number,             // safety limit, default 40
  "max_pages": number,                   // safety limit for pagination, default 200
  "enabled": true,
  "notes": string|null                   // optional human-readable notes, e.g. "Wix site; details on eventrac.co.uk"
}}

{html_hints}

Page content (markdown, for prose context only — selectors must come from the real HTML above):
{markdown}
"""


def _ask_grok_for_config(client: OpenAI, url: str, markdown: str, html_hints: str, failure_note: str) -> Optional[SiteConfig]:
    system_prompt = _build_system_prompt(url, markdown, html_hints, failure_note)
    user_prompt = "Return ONLY the JSON object now. Be precise and conservative with selectors."

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1400,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        print(f"{datetime.now():%H:%M:%S} - Grok response received ({len(content)} chars)")

        data = json.loads(content)

        cfg = SiteConfig(
            name=data.get("name", "Unnamed Site"),
            listing_url=data.get("listing_url", url),
            base_url=data.get("base_url", ""),
            event_link_selector=data.get("event_link_selector"),
            link_pattern=data.get("link_pattern"),
            link_regex=data.get("link_regex"),
            load_strategy=data.get("load_strategy", "single_page"),
            load_more_selector=data.get("load_more_selector"),
            next_button_selector=data.get("next_button_selector"),
            max_load_clicks=data.get("max_load_clicks", 40),
            max_pages=data.get("max_pages", 200),
            enabled=data.get("enabled", True),
            notes=data.get("notes"),
        )

        if cfg.base_url and (cfg.event_link_selector or cfg.link_pattern or cfg.link_regex):
            return cfg

        print(f"{datetime.now():%H:%M:%S} - Incomplete config (missing base_url or link selector)")
        print(f"Raw data keys: {list(data.keys())}")
        return None

    except json.JSONDecodeError as e:
        print(f"{datetime.now():%H:%M:%S} - JSON decode error: {e}")
        return None
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Grok / parsing error: {type(e).__name__}: {e}")
        return None


def generate_site_config(url: str, force_refresh: bool = False) -> Optional[SiteConfig]:
    """
    Generate a SiteConfig for the given listing URL. Uses a cached, previously
    validated config when available; otherwise scrapes the page, asks Grok for a
    config grounded in the real HTML, validates it live via Selenium, and retries
    once with the validation failure fed back if the first attempt doesn't hold up.
    """
    if not force_refresh:
        cached = load_cached_config(url)
        if cached is not None:
            print(f"{datetime.now():%H:%M:%S} - Using cached, validated config for {url} ({cached.name})")
            return cached

    print(f"{datetime.now():%H:%M:%S} - Generating config for: {url}")

    markdown, html = _scrape_listing_page(url)
    if not markdown:
        print(f"{datetime.now():%H:%M:%S} - No page content — falling back to URL-only mode")
        markdown = "No page content available. Infer structure from URL and common running event site patterns only."

    html_hints = _build_structural_hints(html)

    client = OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )

    failure_note = ""
    max_attempts = 2
    last_cfg: Optional[SiteConfig] = None

    for attempt in range(1, max_attempts + 1):
        cfg = _ask_grok_for_config(client, url, markdown, html_hints, failure_note)
        if cfg is None:
            failure_note = "Your previous response wasn't valid/complete JSON. Return ONLY the JSON object."
            continue
        last_cfg = cfg

        ok, message = validate_site_config(cfg)
        if ok:
            print(f"{datetime.now():%H:%M:%S} - Validated: {cfg.name} | strategy={cfg.load_strategy} "
                  f"(attempt {attempt}/{max_attempts})")
            save_config_to_cache(cfg)
            return cfg

        print(f"{datetime.now():%H:%M:%S} - Validation failed (attempt {attempt}/{max_attempts}): {message}")
        failure_note = (
            f"Your attempt produced load_strategy={cfg.load_strategy!r}, "
            f"event_link_selector={cfg.event_link_selector!r}, load_more_selector={cfg.load_more_selector!r}, "
            f"next_button_selector={cfg.next_button_selector!r}.\n"
            f"That failed validation on the live page: {message}"
        )

    # Couldn't validate a load_more/pagination strategy after retries. Rather than
    # discard the site entirely, fall back to single_page if the event link selector
    # itself is sound — better to capture page 1 than nothing, and this is common
    # enough (Grok gets the strategy wrong more often than the link selector).
    if last_cfg is not None and last_cfg.load_strategy != "single_page":
        visible = count_visible_event_links(
            last_cfg.listing_url, last_cfg.base_url,
            last_cfg.event_link_selector, last_cfg.link_pattern, last_cfg.link_regex,
        )
        if visible > 0:
            degraded_notes = (
                f"{last_cfg.notes or ''} [DEGRADED: {last_cfg.load_strategy} strategy could not be validated "
                f"after {max_attempts} attempts — falling back to single_page. Only the first page's "
                f"{visible} events will be captured.]".strip()
            )
            degraded = last_cfg._replace(
                load_strategy="single_page",
                load_more_selector=None,
                next_button_selector=None,
                notes=degraded_notes,
            )
            print(f"{datetime.now():%H:%M:%S} - Falling back to single_page for {url} "
                  f"({visible} events visible on first page)")
            save_config_to_cache(degraded)
            return degraded

    print(f"{datetime.now():%H:%M:%S} - Giving up on {url} after {max_attempts} attempts")
    return None


def main():
    """Test / generate configs for the known sites."""
    urls = [
        "https://www.zigzagrunning.co.uk/",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        "https://findarace.com/events",
    ]

    for url in urls:
        config = generate_site_config(url)
        if config is None:
            print(f"{datetime.now():%H:%M:%S} - Skipping {url} (generation failed)\n")
            continue

        from process_site import process_site
        process_site(
            site_name=config.name,
            listing_url=config.listing_url,
            base_url=config.base_url,
            event_link_selector=config.event_link_selector,
            link_pattern=config.link_pattern,
            link_regex=config.link_regex,
            load_strategy=config.load_strategy,
            load_more_selector=config.load_more_selector,
            next_button_selector=config.next_button_selector,
            max_load_clicks=config.max_load_clicks,
            max_pages=config.max_pages,
            skip_actual_processing=True,
            page_number=1,
            test_only=False,
        )
        print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
