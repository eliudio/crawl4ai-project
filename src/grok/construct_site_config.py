from datetime import datetime
from openai import OpenAI
import json
from typing import Dict, Any, List

from grok.key import GROK_API_KEY

# ──────────────────────────────────────────────
#  Define the schema Grok should follow
# ──────────────────────────────────────────────

SITE_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "site_name": {
            "type": "string",
            "description": "Clean, human-readable name of the site/organiser (e.g. 'Zig Zag Running')"
        },
        "listing_url": {
            "type": "string",
            "description": "The exact URL provided (input)"
        },
        "base_url": {
            "type": "string",
            "description": "Base domain used for urljoin when building detail URLs (may differ from listing domain)"
        },
        "link_pattern": {
            "type": "string",
            "description": "Substring that appears in every event detail href (case sensitive)"
        },
        "load_more_xpath": {
            "type": ["string", "null"],
            "description": "XPath to the 'Load More' button if present, else null. Use case-insensitive contains(text()) pattern if possible."
        },
        "link_regex": {
            "type": ["string", "null"],
            "description": "Python regex (re.IGNORECASE used) to precisely match event hrefs. Prefer this over pattern when possible. Null if broad pattern is sufficient."
        },
        "notes": {
            "type": "string",
            "description": "Optional short explanation of choices or warnings (e.g. 'detail pages on external domain', 'may need filter click')"
        }
    },
    "required": ["site_name", "listing_url", "base_url", "link_pattern"],
    "additionalProperties": False
}

def generate_site_configs(urls: List[str]) -> List[Dict[str, Any]]:
    """
    Ask Grok to generate SiteConfig-compatible dicts for one or more event listing URLs.
    Returns list of parsed configs (or empty list on failure).
    """
    client = OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",
    )

    # Build a numbered list of URLs for the prompt
    url_list_str = "\n".join(f"{i+1}. {url}" for i, url in enumerate(urls))

    system_prompt = (
        "You are an expert web scraper configuration generator for UK running/triathlon event calendars. "
        "Your task is to analyze typical event listing pages and produce SiteConfig objects "
        "that are compatible with the provided Selenium + BeautifulSoup scraping function.\n\n"

        "Key rules:\n"
        "- Return ONLY valid JSON – an array of objects matching the schema.\n"
        "- No explanations outside the JSON, no markdown, no extra text.\n"
        "- For each URL, infer:\n"
        "  - site_name: short descriptive name\n"
        "  - base_url: domain to use with urljoin (may be different if events are on external platform)\n"
        "  - link_pattern: reliable substring present in ALL event detail links\n"
        "  - link_regex: stricter regex when helpful (e.g. r'/e/[^/]+-\\d+$' for slug-id)\n"
        "  - load_more_xpath: XPath if 'Load More' or similar button exists – prefer case-insensitive contains(text())\n"
        "    Example good value: \"//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]\"\n"
        "    Use null if no load more / pagination\n"
        "- Common patterns you know:\n"
        "  - Many use /e/slug-12345 → link_pattern='e/', regex=r'/e/[^/]+-\\d+$'\n"
        "  - Some use /events/slug/ or /events/category/slug-year\n"
        "  - Load More buttons often have text 'Load More' (case varies)\n"
        "- If unsure about exact XPath, use the safe contains version above.\n"
        "- If detail pages are on external domain (e.g. eventrac.co.uk), set base_url accordingly.\n"
    )

    user_prompt = (
        f"Generate SiteConfig entries for these event listing URLs:\n\n"
        f"{url_list_str}\n\n"
        f"Each config must strictly follow this JSON schema:\n"
        f"{json.dumps(SITE_CONFIG_SCHEMA, indent=2)}\n\n"
        f"Return ONLY a JSON array of objects. Example structure:\n"
        f"[{{\n"
        f'  "site_name": "Example Site",\n'
        f'  "listing_url": "https://...",\n'
        f'  "base_url": "https://...",\n'
        f'  "link_pattern": "event/",\n'
        f'  "load_more_xpath": null,\n'
        f'  "link_regex": "/event/[^/]+$",\n'
        f'  "notes": "optional"\n'
        f"}}, ...]"
    )

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.15,               # low randomness – we want precision
            max_tokens=1200,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)

        # Expecting an array directly
        if isinstance(data, list):
            configs = data
        elif isinstance(data, dict) and "configs" in data:
            configs = data["configs"]
        else:
            print("Unexpected response structure:", data)
            return []

        # Basic validation / cleanup
        cleaned = []
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            # Ensure required fields
            if all(k in cfg for k in ["site_name", "listing_url", "base_url", "link_pattern"]):
                # Normalize load_more_xpath to None if empty string or missing
                if cfg.get("load_more_xpath") in ("", None, "null"):
                    cfg["load_more_xpath"] = None
                cleaned.append(cfg)

        print(f"{datetime.now():%H:%M:%S} - Generated {len(cleaned)} site config(s)")
        return cleaned

    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Grok config generation failed: {type(e).__name__}: {e}")
        if 'content' in locals():
            print("Raw response was:", content)
        return []

if __name__ == "__main__":
    urls_to_analyze = [
        "https://www.zigzagrunning.co.uk/",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
    ]

    generated_configs = generate_site_configs(urls_to_analyze)

    if generated_configs:
        print("\nGenerated SiteConfigs (ready to copy into your SITES list):\n")
        for cfg in generated_configs:
            print(json.dumps(cfg, indent=2))
            print("-" * 60)
