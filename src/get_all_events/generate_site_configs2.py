#!/usr/bin/env python3
"""
Generate SiteConfig from URL using Grok (xAI API)

Usage:
    python grok_siteconfig_from_url.py "https://sportivaevents.co.uk/events/"

Expected output: ready-to-paste SiteConfig(...) code
"""

import sys
import json
from typing import Optional

from openai import OpenAI

from get_all_events.process_site import get_event_detail_urls
from get_all_events.site_config import SiteConfig

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────

MODEL = "grok-3",

SYSTEM_PROMPT = """\
You are an expert at reverse-engineering event listing websites for a Selenium + BeautifulSoup scraper.

When given a URL, analyze the page structure (based on your knowledge or tools if needed) 
and return **ONLY** a valid JSON object with these exact keys:

{
  "name":           "Human readable site name",
  "listing_url":    "the same URL that was given",
  "base_url":       "root domain for urljoin",
  "link_pattern":   "broad substring that appears in detail page hrefs",
  "load_more_xpath": "XPath to 'Load More' button or null",
  "link_regex":     "precise regex pattern for detail URLs (preferred over link_pattern)",
  "enabled":        true or false (usually true)
}

Rules:
- Return **ONLY** the JSON — no explanation, no markdown, no code fences, no extra text
- Use null (not "null" string) for missing values
- Make link_regex as specific as possible (end anchors $, avoid matching pagination)
- If the site has no load more, use null for load_more_xpath
- Be conservative with regex — better too strict than matching wrong links
"""

from grok.key import GROK_API_KEY

def get_api_key():
    return GROK_API_KEY


def ask_grok_for_siteconfig(url: str, api_key: str) -> dict | None:
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )

    user_prompt = f"Now create SiteConfig(...) for {url}"

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.15,
            max_tokens=600,
        )

        raw = response.choices[0].message.content.strip()

        # Try to clean up common LLM wrappers
        if raw.startswith("```json"):
            raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
        elif raw.startswith("```"):
            raw = raw.split("```", 2)[1].strip()

        data = json.loads(raw)
        return data

    except json.JSONDecodeError as e:
        print("Could not parse JSON from Grok response:", e, file=sys.stderr)
        print("\nRaw response was:\n", raw, file=sys.stderr)
        return None
    except Exception as e:
        print("API call failed:", e, file=sys.stderr)
        return None


def dict_to_siteconfig_code(data: dict) -> SiteConfig:
    """Turn JSON dict into SiteConfig(...) string"""
    name          = data.get("name",          "Unknown Site")
    listing_url   = data.get("listing_url",   "")
    base_url      = data.get("base_url",      "")
    link_pattern  = data.get("link_pattern",  "")
    load_more     = data.get("load_more_xpath")
    link_regex    = data.get("link_regex")
    enabled       = data.get("enabled",       True)

    load_more_str = f'"{load_more}"' if load_more else "None"
    regex_str     = f'"{link_regex}"' if link_regex else "None"

    return SiteConfig(
        name="{name}",
        listing_url="{listing_url}",
        base_url="{base_url}",
        link_pattern="{link_pattern}",
        load_more_xpath={load_more_str},
        link_regex={regex_str},
        enabled={str(enabled).lower()},
    )



def list_sites(
    site_name: str,
    listing_url: str,
    base_url: str,
    link_pattern: str,
    load_more_xpath: Optional[str] = None,
    link_regex: Optional[str] = None,          # ← new
    page_number: int = 1,
    test_only: bool = False,
):
    print(f"\n{'=' * 70}")
    print(f"{datetime.now():%H:%M:%S} - Processing {site_name} (page {page_number})")
    print(f"{'=' * 70}\n")

    detail_urls = get_event_detail_urls(
        listing_url=listing_url,
        base_url=base_url,
        link_pattern=link_pattern,
        load_more_xpath=load_more_xpath,
        link_regex=link_regex,                 # ← passed through
        test_only=test_only,
    )

    if not detail_urls:
        print(f"{datetime.now():%H:%M:%S} - No events found for {site_name}")
        return

    print(f"{datetime.now():%H:%M:%S} - Processing {len(detail_urls)} events...")

    for i, detail_url in enumerate(detail_urls, 1):
        print(f"{datetime.now():%H:%M:%S} - [{i}/{len(detail_urls)}] {detail_url}")

def main():
    url = "https://www.phoenixrunning.co.uk/events"
    api_key = get_api_key()

    print(f"Querying Grok for: {url}\n", file=sys.stderr)

    data = ask_grok_for_siteconfig(url, api_key)
    if not data:
        print("Failed to get valid response from Grok.", file=sys.stderr)
        sys.exit(1)

    print("\n" + "="*70)
    print("# Recommended SiteConfig (generated by Grok)")
    print("# Generated for:", url)
    print("# Date:", datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("="*70 + "\n")

    config = dict_to_siteconfig_code(data)
    list_sites(
        site_name=config.name,
        listing_url=config.listing_url,
        base_url=config.base_url,
        link_pattern=config.link_pattern,
        load_more_xpath=config.load_more_xpath,
        link_regex=config.link_regex,
        page_number=1,
        test_only=False,
    )

if __name__ == "__main__":
    from datetime import datetime
    main()