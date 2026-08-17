import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

URL = "https://www.threefortschallenge.org.uk/e/three-forts-challenge-8513"
_EXCLUDED_TAGS = ["nav", "header", "footer", "aside", "script", "style", "noscript"]
_MAIN_CONTENT_MARKDOWN_GENERATOR = DefaultMarkdownGenerator(content_filter=PruningContentFilter())


async def main():
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            verbose=False,
            excluded_tags=_EXCLUDED_TAGS,
            markdown_generator=_MAIN_CONTENT_MARKDOWN_GENERATOR,
        )
        result = await crawler.arun(url=URL, config=config)
        print("success:", result.success)
        if not result.success:
            print("error:", result.error_message)
            return

        html = result.html or ""
        with open("_3forts_raw.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("raw html length:", len(html))

        fit_md = result.markdown.fit_markdown if result.markdown else None
        raw_md = result.markdown.raw_markdown if result.markdown else None
        with open("_3forts_fit.md", "w", encoding="utf-8") as f:
            f.write(fit_md or "")
        with open("_3forts_raw.md", "w", encoding="utf-8") as f:
            f.write(raw_md or "")
        print("fit_markdown length:", len(fit_md or ""))
        print("raw_markdown length:", len(raw_md or ""))


asyncio.run(main())
