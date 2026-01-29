# generate_site_configs.py
# Generates SiteConfig using Grok + real page content from self-hosted Firecrawl (v2 compatible)

import json
import requests
from datetime import datetime
from typing import Optional

from openai import OpenAI

# Import your SiteConfig NamedTuple
from site_config import SiteConfig

from grok.key import GROK_API_KEY


def generate_site_config(url: str) -> Optional[SiteConfig]:
    """
    Generate SiteConfig using Grok + markdown scraped from self-hosted Firecrawl (v2 API).
    """
    print(f"{datetime.now():%H:%M:%S} - Generating config for: {url}")

    markdown = ""

    # Step 1: Scrape via direct HTTP POST (v2-compatible format)
    try:
        response = requests.post(
            "http://localhost:3002/v1/scrape",
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "excludeTags": ["script", "style", "iframe", "noscript", "header", "footer"],
                "waitFor": 0,  # no extra wait
                "timeout": 60000  # 60s timeout in ms
            },
            timeout=90
        )

        response.raise_for_status()

        data = response.json()
        if data.get("success") and "data" in data and "markdown" in data["data"]:
            markdown = data["data"]["markdown"]
            print(f"{datetime.now():%H:%M:%S} - Scraped page successfully ({len(markdown)} chars markdown)")
        else:
            print(f"{datetime.now():%H:%M:%S} - Scrape failed: {data.get('error', 'unknown response')}")

    except requests.exceptions.HTTPError as e:
        print(f"{datetime.now():%H:%M:%S} - HTTP error {e.response.status_code}: {e.response.text[:400]}")
    except requests.exceptions.RequestException as e:
        print(f"{datetime.now():%H:%M:%S} - HTTP request failed: {e}")
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Scraping error: {type(e).__name__}: {e}")

    if not markdown:
        print(f"{datetime.now():%H:%M:%S} - No page content — falling back to URL-only mode")
        markdown = "No page content available. Infer from URL and common patterns only."

    # Limit to prevent token overflow
    markdown = markdown[:10000]

    client = OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )

    system_prompt = (
        "You are a precise scraper configuration generator for event listing pages.\n"
        "Analyze the markdown content and return ONLY a valid JSON object.\n\n"

        "Rules:\n"
        "- Output ONLY JSON { ... } – nothing else\n"
        "- No explanations, no markdown, no code fences\n"
        "- Use null for fields that cannot be determined\n"
        "- enabled is always true\n\n"

        "Fields:\n"
        "{\n"
        '  "name": string,               // short organiser name\n'
        '  "listing_url": string,         // input URL\n'
        '  "base_url": string,            // correct domain for detail URLs (critical! detect external domains)\n'
        '  "link_pattern": string,        // common substring in event <a href>\n'
        '  "load_more_xpath": string|null, // XPath if "Load More" button exists\n'
        '  "link_regex": string|null,     // precise regex preferred\n'
        '  "enabled": true\n'
        "}\n\n"

        "Key instructions:\n"
        "- Examine actual <a href=\"...\"> examples in markdown\n"
        "- If hrefs use different domain → set base_url to THAT domain\n"
        "- Look for patterns like /e/slug-12345, /events/slug/, /races/id-...\n"
        "- Detect buttons like 'Load More', 'More Events', 'Show All'\n"
        "- If no useful content → use URL-based best guess\n\n"

        f"Page markdown:\n{markdown}"
    )

    user_prompt = "Return the JSON object now."

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.05,
            max_tokens=1200,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        print(f"{datetime.now():%H:%M:%S} - Grok response:\n{content}\n")

        data = json.loads(content)

        cfg = SiteConfig(
            name=data.get("name", "Unnamed"),
            listing_url=data.get("listing_url", url),
            base_url=data.get("base_url", ""),
            link_pattern=data.get("link_pattern", ""),
            load_more_xpath=data.get("load_more_xpath"),
            link_regex=data.get("link_regex"),
            enabled=data.get("enabled", True)
        )

        if cfg.base_url and cfg.link_pattern:
            print(f"{datetime.now():%H:%M:%S} - Success: {cfg.name}")
            return cfg
        else:
            print(f"{datetime.now():%H:%M:%S} - Incomplete config")
            return None

    except json.JSONDecodeError as e:
        print(f"{datetime.now():%H:%M:%S} - JSON error: {e}")
        print(f"Raw: {content[:500]}...")
        return None
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Grok error: {type(e).__name__}: {e}")
        return None


def main():
    urls = [
        "https://www.zigzagrunning.co.uk/",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
    ]

    configs = []
    for url in urls:
        cfg = generate_site_config(url)
        if cfg:
            configs.append(cfg)

    print(f"\nGenerated {len(configs)} configs:\n")
    for cfg in configs:
        print(cfg)
        print("-" * 80)


if __name__ == "__main__":
    main()