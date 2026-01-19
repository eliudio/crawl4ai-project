# all_events_scraper_crawl4ai_ollama_fixed.py
import asyncio
import time
import random
import json
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, LLMConfig, LLMExtractionStrategy

# Your custom VPN rotator (keep your own implementation)
from crawl4ai_project.express_vpn import select_random_location


async def main():
    base_url = "https://findarace.com/events"
    max_pages = 80  # Safety margin (~43-45 pages in Jan 2026)
    output_file = "all_findarace_events_with_details.md"

    all_markdown = f"# All Events on findarace.com\n"
    all_markdown += f"Scraped on {time.strftime('%Y-%m-%d')}\n\n"
    all_markdown += "> Note: Details extracted using Crawl4AI + Ollama (LLM-based)\n\n"

    total_events = 0
    page = 1

    # Change this to your preferred Ollama model (must be pulled and running)
    ollama_model = "ollama/qwen2:7b"  # Examples: ollama/llama3.1, ollama/qwen2:7b, ollama/llama3.2:3b
    ollama_api_base = "http://localhost:11434/v1"  # usually not needed, but can help with some setups

    # ── Schema for extracting list of detail URLs from listing pages ──
    links_schema = {
        "name": "EventLinks",
        "description": "List of unique event detail page URLs",
        "parameters": {
            "type": "array",
            "items": {"type": "string"}
        }
    }

    # ── Schema for extracting event details ──
    details_schema = {
        "name": "EventDetails",
        "description": "Key information about a running/cycling event",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Event date (any format)"},
                "time": {"type": "string", "description": "Start time or time range"},
                "location": {"type": "string", "description": "Venue, town, or full address"},
                "distances": {"type": "string", "description": "Available race distances (e.g. 5K, 10K, Half Marathon)"}
            },
            "required": ["date", "time", "location", "distances"]
        }
    }

    async with AsyncWebCrawler(verbose=True) as crawler:
        while page <= max_pages:
            if page == 1:
                url = base_url
            else:
                url = f"{base_url}/p{page}"

            print(f"\nProcessing page {page:2d} → {url}")
            #select_random_location()  # Rotate VPN if needed

            # 1. Get listing page → extract event detail URLs using LLM
            result = await crawler.arun(
                url=url,

                extraction_strategy=LLMExtractionStrategy(
                    llm_config=LLMConfig(
                        provider=ollama_model,
                        base_url=ollama_api_base,          # uncomment only if you have connection issues
                        temperature=0.0,                   # optional: lower = more deterministic
                        max_tokens=3000,
                    ),
                    schema=links_schema,
                    instruction=(
                        "Extract **all unique event detail page URLs** visible on this listing page. "
                        "These are links can be recognised as starting with https://findarace.com/events/"
                        "Return only the list of URLs, nothing else."
                    )
                ),
                bypass_cache=True
            )

            # EVEN BETTER, SIMPLER: re.findall(r'\(https://findarace\.com/events/[^)]+\)', markdown)
            # FOR NOW, WE DO THE ABOVE, TO UNDERSTAND HOW IT WORKS. BECAUSE AT SOME POINT, WE MIGHT NEEED LLM
            # BUT HERE, PROBABLY OVERHEAD

            try:
                if isinstance(result.extracted_content, str):
                    detail_urls = json.loads(result.extracted_content)
                else:
                    detail_urls = result.extracted_content

                if not isinstance(detail_urls, list) or not detail_urls:
                    print("  No more events found → stopping pagination")
                    break

                # Optional: clean up possible malformed URLs
                detail_urls = [
                    urljoin("https://findarace.com", u.strip())
                    for u in detail_urls
                    if isinstance(u, str) and len(u.strip()) > 10
                ]
                detail_urls = list(set(detail_urls))  # remove possible duplicates

            except Exception as e:
                print(f"  Failed to parse listing page URLs: {e}")
                page += 1
                continue

            print(f"  Found {len(detail_urls)} event links")

            # 2. Process each detail page
            for i, detail_url in enumerate(detail_urls, 1):
                print(f"  {i:3d}/{len(detail_urls)} → {detail_url}")
                #select_random_location()  # Rotate per event if desired

                detail_result = await crawler.arun(
                    url=detail_url,
                    extraction_strategy=LLMExtractionStrategy(
                        llm_config=LLMConfig(
                            provider=ollama_model,
                            # api_base=ollama_api_base,
                        ),
                        schema=details_schema,
                        instruction=(
                            "Extract the following from the event detail page:\n"
                            "- date (when the event takes place)\n"
                            "- time (start time or time window)\n"
                            "- location (venue, town, address)\n"
                            "- distances (available race lengths)\n"
                            "Use 'N/A' when information is missing or unclear."
                        )
                    ),
                    bypass_cache=True
                )

                try:
                    if isinstance(detail_result.extracted_content, str):
                        details = json.loads(detail_result.extracted_content)
                    else:
                        details = detail_result.extracted_content

                    if not isinstance(details, dict):
                        raise ValueError("Not a dict")

                    # Skip if almost nothing was extracted
                    if all(v in ("N/A", "", None) for v in details.values()):
                        print("    → Mostly empty extraction, skipping")
                        continue

                except Exception as e:
                    print(f"    Failed to parse details: {e}")
                    continue

                total_events += 1
                all_markdown += f"## Event {total_events}\n"
                all_markdown += f"**Link**: {detail_url}\n\n"

                for key, value in details.items():
                    all_markdown += f"- **{key.title()}**: {value}\n"

                all_markdown += "\n---\n\n"

                # Be polite to the server
                await asyncio.sleep(random.uniform(1.3, 3.1))

            page += 1

        # Final save (also saved progressively if you want to add inside loop)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(all_markdown)

        print("\n" + "=" * 70)
        print(f"Finished!")
        print(f"Processed {page - 1} pages")
        print(f"Extracted usable details for {total_events} events")
        print(f"Results saved to: {output_file}")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())