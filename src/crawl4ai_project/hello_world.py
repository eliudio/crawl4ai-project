import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
# Optional: for cleaner "fit" version (recommended for event listings)
from crawl4ai.content_filter_strategy import PruningContentFilter

async def main():
    base_url = "https://findarace.com/10k-runs/london"
    # Adjust range if needed — currently ~6 pages
    listing_urls = [base_url] + [f"{base_url}/p{i}" for i in range(2, 8)]

    # Optional: Pruning filter to remove noise (sidebars, footers, ads...)
    prune_filter = PruningContentFilter(
        threshold=0.48,          # 0.4–0.6 is a good range
        threshold_type="fixed",
        min_word_threshold=10    # ignore very short blocks
    )

    # Modern markdown generator setup
    md_generator = DefaultMarkdownGenerator(
        content_filter=prune_filter,  # remove if you want raw/full markdown only
        options={
            "ignore_links": True,     # ← clean output, no useless URLs
            "escape_html": False,     # ← keep some formatting if needed
            "body_width": 0,          # 0 = no wrapping (recommended)
            # You can experiment with these if code/tables look broken:
            # "mark_code": True,
            # "handle_code_in_pre": True
        }
    )

    all_markdown = "# London 10k Runs – All Events\n\n"

    async with AsyncWebCrawler(verbose=True) as crawler:
        results = await crawler.arun_many(
            urls=listing_urls,
            config=CrawlerRunConfig(
                markdown_generator=md_generator,
                word_count_threshold=30,      # global pre-filter (helps a lot)
                excluded_tags=["nav", "footer", "header", "script", "style"],
                exclude_external_links=True,
                cache_mode="BYPASS"           # or "DISABLED"
            ),
            bypass_cache=True,                # legacy flag, still works in some versions
            js=True,
            wait_for=4000                     # give JS time to load events
        )

        for i, result in enumerate(results, 1):
            if result.success and result.markdown:
                # Use .raw_markdown or .fit_markdown depending on filter
                content = result.markdown.fit_markdown or result.markdown.raw_markdown
                all_markdown += f"## Page {i}\n\n{content}\n\n---\n\n"
            else:
                print(f"Page {i} failed: {result.error_message}")

    # Save everything
    with open("london_10k_events.md", "w", encoding="utf-8") as f:
        f.write(all_markdown)

    print("All pages saved as london_10k_events.md – ready for AI querying!")

asyncio.run(main())