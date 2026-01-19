# all_events_scraper_crawl4ai_then_ollama.py
from crawl4ai import CacheMode
import asyncio
import time
import random
import json
import re

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
import ollama

import asyncio
from ollama import AsyncClient

# Your custom VPN rotator (keep your own implementation if needed)
# from crawl4ai_project.express_vpn import select_random_location


async def get_event_detail_urls_from_markdown(markdown_text: str) -> list[str]:
    """Simple regex + cleaning - usually good enough for findarace listing pages"""
    # Most common link pattern on findarace listing pages
    pattern = r'https://findarace\.com/events/[^"\')\s<>\[\]]+'
    urls = re.findall(pattern, markdown_text)

    # Clean and deduplicate
    cleaned = []
    for u in urls:
        u = u.strip().rstrip('/.,"\'')
        if '/events/' in u and len(u) > 35:
            cleaned.append(u)

    return list(set(cleaned))  # remove duplicates


#
# async def extract_event_details_with_ollama(markdown_text: str, model: str = "qwen2:7b") -> dict | None:
#     prompt = f"""... your prompt here ..."""  # (your existing prompt)
#
#     try:
#         client = AsyncClient()   # defaults to http://localhost:11434
#
#         response = await client.chat(
#             model=model,
#             messages=[{"role": "user", "content": prompt}],
#             options={
#                 "temperature": 0.0,
#                 "num_predict": 600,
#             }
#         )
#
#         content = response['message']['content'].strip()
#
#         # Your JSON cleaning logic here...
#         if "```json" in content:
#             content = content.split("```json")[1].split("```")[0].strip()
#         # etc.
#
#         return json.loads(content)
#
#     except Exception as e:
#         print(f"  Ollama extraction failed → {e}")
#         return None
async def extract_event_details_with_ollama(markdown_text: str, model: str = "qwen2:7b") -> dict | None:
    """
    Ask Ollama to extract structured event information from markdown
    """
    prompt = f"""You are an accurate event information extractor.
From the following markdown content of a race/event page, extract:

- date: the main event date (any reasonable format)
- time: start time or time window
- location: venue / town / address
- distances: available race distances (comma separated or as shown)

Rules:
- Use 'N/A' when information is missing or unclear
- Be concise
- Return ONLY valid JSON object, nothing else!

Markdown content:
{markdown_text[:12000]}  # safety limit
"""

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": 0.0,
                "num_predict": 600,
            }
        )

        content = response['message']['content'].strip()

        # Try to find JSON block (in case model adds extra text)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("{"):
            pass
        else:
            # last attempt - take first { ... }
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]

        return json.loads(content)

    except Exception as e:
        print(f"  Ollama extraction failed → {e}")
        return None


async def main():
    base_url = "https://findarace.com/events"
    max_pages = 80
    output_file = "all_findarace_events_2026.md"

    all_markdown = f"# All Events on findarace.com\n"
    all_markdown += f"Scraped on {time.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
    all_markdown += "> Note: Content crawled with Crawl4AI • Structured with Ollama\n\n"

    total_events = 0
    page = 1

    # You can change model here (make sure it's pulled: ollama pull qwen2:7b etc)
    #OLLAMA_MODEL = "qwen2:7b"
    OLLAMA_MODEL = "llama3.1:8b"
    #OLLAMA_MODEL = "llama3.2:3b"

    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        excluded_tags=["script", "style", "noscript", "iframe"],
    )

    browser_config = BrowserConfig(
        headless=True,
        verbose=True,
        java_script_enabled=True,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        while page <= max_pages:
            url = base_url if page == 1 else f"{base_url}/p{page}"
            print(f"\nPage {page:2d} → {url}")

            # select_random_location()  # ← uncomment if using VPN rotator

            result = await crawler.arun(
                url=url,
                config=crawler_config,
                bypass_cache=True
            )

            if not result.success or not result.markdown:
                print("  Failed to get markdown → skipping page")
                page += 1
                continue

            # ── Step 1: Extract detail page URLs from listing markdown ──
            detail_urls = await get_event_detail_urls_from_markdown(result.markdown)

            if not detail_urls:
                print("  No event links found → probably last page")
                break

            print(f"  Found {len(detail_urls)} event links")

            # ── Step 2: Process each event detail page ──
            for i, detail_url in enumerate(detail_urls, 1):
                print(f"  {i:3d}/{len(detail_urls)} → {detail_url}")

                # select_random_location()  # ← per event rotation if needed

                detail_result = await crawler.arun(
                    url=detail_url,
                    config=crawler_config,
                    bypass_cache=True
                )

                if not detail_result.success or not detail_result.markdown:
                    print("    → Failed to crawl event page")
                    continue

                # ── Step 3: Ask Ollama to structure the event data ──
                details = await extract_event_details_with_ollama(
                    detail_result.markdown,
                    model=OLLAMA_MODEL
                )

                if not details or all(v in (None, "", "N/A") for v in details.values()):
                    print("    → Empty/failed extraction → skipping")
                    continue

                total_events += 1
                all_markdown += f"## Event {total_events}\n"
                all_markdown += f"**Link**: {detail_url}\n\n"

                for key, value in details.items():
                    all_markdown += f"- **{key.title()}**: {value}\n"

                all_markdown += "\n---\n\n"

                await asyncio.sleep(random.uniform(1.4, 3.2))  # polite delay

            page += 1

    # Final save
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(all_markdown)

    print("\n" + "="*70)
    print(f"Finished scraping!")
    print(f"Processed {page-1} pages")
    print(f"Extracted usable details for {total_events} events")
    print(f"Saved to → {output_file}")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())