import asyncio
import json
from crawl4ai import AsyncWebCrawler
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

async def main():
    url = "https://findarace.com/events/dorney-triathlon"

    # Schema tuned for this site's event details block
    # Uses broad baseSelector + text-contains selectors (Crawl4AI supports :contains pseudo)
    schema = {
        "name": "Triathlon Event Details",
        "baseSelector": "body",  # or ".event-details-container", "main" if you inspect & find better
        "fields": [
            {
                "name": "event_name",
                "selector": "h1",
                "type": "text"
            },
            {
                "name": "organizer",
                "selector": "p:contains('From ')",
                "type": "text"
            },
            {
                "name": "date",
                "selector": "p:contains('Date')",
                "type": "text"
            },
            {
                "name": "time",
                "selector": "p:contains('Time')",
                "type": "text"
            },
            {
                "name": "location_summary",
                "selector": "p:contains('Location')",
                "type": "text"
            },
            {
                "name": "price_range",
                "selector": "p:contains('Price')",
                "type": "text"
            },
            {
                "name": "number_of_races",
                "selector": "p:contains('Races')",
                "type": "text"
            },
            {
                "name": "distances",
                "selector": "p:contains('Distances')",
                "type": "text"
            },
            {
                "name": "detailed_location",
                "selector": "div:contains('Dorney Lake'), div.pt-8.mb-8.text-lg, div:contains('Windsor, SL4')",
                "type": "text"
            }
        ]
    }

    extraction_strategy = JsonCssExtractionStrategy(schema=schema, verbose=True)

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(
            url=url,
            extraction_strategy=extraction_strategy,
            bypass_cache=True,
            word_count_threshold=5,           # Filter tiny junk
            excluded_tags=['nav', 'footer', 'script', 'style', 'header'],
        )

        print("=== DEBUG ===")
        print("Success:", result.success)
        print("Extracted raw:", result.extracted_content)

        if result.success and result.extracted_content:
            try:
                # Usually returns a list with 1 dict (single event)
                data_list = json.loads(result.extracted_content)
                data = data_list[0] if data_list else {}

                markdown = f"""
# {data.get('event_name', 'N/A')}

**Organizer:** {data.get('organizer', 'N/A').replace('From ', '').strip()}  
**Date:** {data.get('date', 'N/A').replace('Date ', '').strip()}  
**Time:** {data.get('time', 'N/A').replace('Time ', '').strip()}  
**Location:** {data.get('location_summary', 'N/A').replace('Location ', '').strip()}  
**Price:** {data.get('price_range', 'N/A').replace('Price ', '').strip()}  
**Races:** {data.get('number_of_races', 'N/A').replace('Races ', '').strip()}  
**Distances:** {data.get('distances', 'N/A').replace('Distances ', '').strip()}  

**Detailed Venue/Address:**  
{data.get('detailed_location', 'N/A')}
"""
                print("\n=== CLEAN MARKDOWN ===\n")
                print(markdown.strip())
            except Exception as e:
                print("JSON parse error:", str(e))
                print("Raw:", result.extracted_content)
        else:
            print("Extraction failed. Check verbose logs above for clues.")

if __name__ == "__main__":
    asyncio.run(main())