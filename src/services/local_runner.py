"""
Runs the full pipeline in-process, with no Pub/Sub involved — for local
development against a local/dev Postgres before anything is deployed to
Cloud Run. Production uses main.py's HTTP handlers, triggered by Pub/Sub.

Usage:
    python -m services.local_runner --limit 3
    python -m services.local_runner --organiser-id 42
    python -m services.local_runner --limit 3 --dry-run  # discover event URLs but don't crawl/store them, just print
    python -m services.local_runner --check-mode url-check  # skip re-crawl of any URL already stored, changed or not
"""

import argparse

from sqlalchemy import select

from services import event_crawler, listing_crawler
from services.config import settings
from services.db import init_db, session_scope
from services.models import Organiser
from tools.seed_organisers import seed_from_csv


def run(
    limit: int | None = None,
    organiser_id: int | None = None,
    dry_run: bool = False,
    check_mode: str = "hash-check",
    scraper_backend: str | None = None,
) -> None:
    # See services/scraper_client.py: "crawl4ai" (self-hosted, no per-page cost) is the
    # default; "firecrawl" always uses Firecrawl's hosted API instead. None leaves
    # whatever's already configured via SCRAPER_BACKEND/.env untouched.
    if scraper_backend is not None:
        settings.scraper_backend = scraper_backend

    init_db()
    seed_from_csv()

    with session_scope() as session:
        query = select(Organiser).where(Organiser.active.is_(True))
        if organiser_id is not None:
            query = query.where(Organiser.id == organiser_id)
        if limit is not None:
            query = query.limit(limit)
        organisers = list(session.scalars(query))

    for organiser in organisers:
        with session_scope() as session:
            organiser = session.get(Organiser, organiser.id)
            new_urls = listing_crawler.crawl_listing(session, organiser)
            print(f"{organiser.name}: {len(new_urls)} new event URL(s)")

        for url in new_urls:
            if dry_run:
                print(f"  [dry-run] {url}")
                continue
            with session_scope() as session:
                event = event_crawler.crawl_event(session, organiser.id, url, check_mode=check_mode)
                print(f"  {'ok' if event else 'FAILED'}: {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--organiser-id", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print each discovered event URL instead of crawling and storing it.",
    )
    parser.add_argument(
        "--check-mode",
        choices=["hash-check", "url-check"],
        default="hash-check",
        help="hash-check (default): always fetch, skip re-extraction if content is unchanged. "
        "url-check: skip entirely (no fetch) if the URL is already stored.",
    )
    parser.add_argument(
        "--scraper-backend",
        choices=["crawl4ai", "firecrawl"],
        default="crawl4ai",
        help="crawl4ai (default): self-hosted, no per-page cost, falls back to Firecrawl "
        "automatically on failure. firecrawl: always use Firecrawl's hosted API.",
    )
    args = parser.parse_args()
    run(
        limit=args.limit,
        organiser_id=args.organiser_id,
        dry_run=args.dry_run,
        check_mode=args.check_mode,
        scraper_backend=args.scraper_backend,
    )
