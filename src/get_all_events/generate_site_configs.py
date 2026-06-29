"""
generate_site_configs.py
Generates SiteConfig using Grok + real page content from self-hosted Firecrawl (v2 compatible).

Now produces configs compatible with the enhanced multi-strategy process_site.
"""

from pathlib import Path
import json
import requests
from datetime import datetime
from typing import Optional

from openai import OpenAI

from site_config import SiteConfig
from grok.key import GROK_API_KEY


def generate_site_config(url: str) -> Optional[SiteConfig]:
    """
    Generate SiteConfig using Grok + markdown scraped from self-hosted Firecrawl.
    """
    print(f"{datetime.now():%H:%M:%S} - Generating config for: {url}")

    markdown = ""

    # Step 1: Scrape via direct HTTP POST (v2-compatible format)
    try:
        response = requests.post(
            "http://localhost:3002/v1/scrape",
            json={
                "url": url,
                "formats": ["markdown", "html"],   # ask for both — html helps Grok see exact attributes
                "onlyMainContent": True,
                "excludeTags": ["script", "style", "iframe", "noscript", "header", "footer", "nav"],
                "waitFor": 1500,
                "timeout": 90000
            },
            timeout=120
        )
        response.raise_for_status()
        data = response.json()

        if data.get("success") and "data" in data:
            if "markdown" in data["data"]:
                markdown = data["data"]["markdown"]
            elif "html" in data["data"]:
                markdown = data["data"]["html"][:15000]  # fallback
            print(f"{datetime.now():%H:%M:%S} - Scraped page successfully ({len(markdown)} chars)")
        else:
            print(f"{datetime.now():%H:%M:%S} - Scrape failed: {data.get('error', 'unknown')}")

    except requests.exceptions.HTTPError as e:
        print(f"{datetime.now():%H:%M:%S} - HTTP error {e.response.status_code}: {e.response.text[:500]}")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Scraping error: {type(e).__name__}: {e}")

    if not markdown:
        print(f"{datetime.now():%H:%M:%S} - No page content — falling back to URL-only mode")
        markdown = "No page content available. Infer structure from URL and common running event site patterns only."

    markdown = markdown[:12000]  # safety limit

    client = OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )

    system_prompt = f"""You are an expert web scraping configuration generator for event listing pages.

Your task: Analyze the provided page content (markdown + HTML hints) for the website {url} and output a precise JSON configuration that can be used by a Python scraper.

The scraper supports THREE strategies:
1. "single_page" — all events are visible immediately after page load (no clicking needed).
2. "load_more" — there is a "Load More", "Show More", or similar button that must be clicked repeatedly until it disappears.
3. "pagination" — there are numbered pages or a "Next" button/link to navigate to additional pages.

Rules:
- Output ONLY a single valid JSON object. Nothing else. No explanations, no markdown fences.
- Use null for any field you cannot confidently determine.
- Prefer CSS selectors over XPath when possible (more stable).
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
  "next_button_selector": string|null,   // CSS or XPath for Next / page link (only if load_strategy == "pagination")
  "max_load_clicks": number,             // safety limit, default 40
  "max_pages": number,                   // safety limit for pagination, default 200
  "enabled": true,
  "notes": string|null                   // optional human-readable notes, e.g. "Wix site; details on eventrac.co.uk"
}}

Examples of good output:

Example 1 — Load More site (zigzagrunning.co.uk style):
{{
  "name": "Zig Zag Running",
  "listing_url": "https://www.zigzagrunning.co.uk/",
  "base_url": "https://zigzagrunning.eventrac.co.uk",
  "event_link_selector": "a[href*='eventrac.co.uk/e/']",
  "link_pattern": null,
  "link_regex": null,
  "load_strategy": "load_more",
  "load_more_selector": "//button[contains(translate(text(), 'LOAD MORE', 'load more'), 'load more')]",
  "next_button_selector": null,
  "max_load_clicks": 35,
  "max_pages": 10,
  "enabled": true,
  "notes": "Wix site. Event detail links point to external eventrac.co.uk subdomain."
}}

Example 2 — Single page (phoenixrunning.co.uk style):
{{
  "name": "Phoenix Running",
  "listing_url": "https://www.phoenixrunning.co.uk/events",
  "base_url": "https://www.phoenixrunning.co.uk",
  "event_link_selector": "a[href*='/events/']",
  "link_pattern": "/events/",
  "link_regex": null,
  "load_strategy": "single_page",
  "load_more_selector": null,
  "next_button_selector": null,
  "max_load_clicks": 5,
  "max_pages": 5,
  "enabled": true,
  "notes": "All events visible on one page. ~130 events."
}}

Example 3 — Pagination (findarace.com style):
{{
  "name": "Find a Race",
  "listing_url": "https://findarace.com/events",
  "base_url": "https://findarace.com",
  "event_link_selector": "a[href*='/events/']",
  "link_pattern": "/events/",
  "link_regex": null,
  "load_strategy": "pagination",
  "load_more_selector": null,
  "next_button_selector": "a[href*='/p'], a.next, button[aria-label*='next']",
  "max_load_clicks": 5,
  "max_pages": 100,
  "enabled": true,
  "notes": "Numbered pagination /p2, /p3 etc. 40 events per page."
}}

Page content (markdown + HTML hints):
{markdown}
"""

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
            print(f"{datetime.now():%H:%M:%S} - Success: {cfg.name} | strategy={cfg.load_strategy}")
            return cfg
        else:
            print(f"{datetime.now():%H:%M:%S} - Incomplete config (missing base_url or link selector)")
            print(f"Raw data keys: {list(data.keys())}")
            return None

    except json.JSONDecodeError as e:
        print(f"{datetime.now():%H:%M:%S} - JSON decode error: {e}")
        print(f"Raw response (first 800 chars):\n{content[:800]}")
        return None
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Grok / parsing error: {type(e).__name__}: {e}")
        return None


def main():
    """Test / generate configs for the known sites."""
    urls = [
        "https://www.zigzagrunning.co.uk/",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        # "https://findarace.com/events",   # uncomment when ready
    ]

    for url in urls:
        config = generate_site_config(url)
        if config is None:
            print(f"{datetime.now():%H:%M:%S} - Skipping {url} (generation failed)\n")
            continue

        # Test the generated config (skip actual detail processing)
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
            test_only=False,   # set True for very quick first-page-only test
        )
        print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
