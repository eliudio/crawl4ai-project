"""
Runs the full pipeline in-process, with no Pub/Sub involved — for local
development against a local/dev Postgres before anything is deployed to
Cloud Run. Production uses main.py's HTTP handlers, triggered by Pub/Sub.

Usage:
    python -m services.local_runner --limit 3
    python -m services.local_runner --organiser-id 42
    python -m services.local_runner --limit 3 --dry-run  # fetch/extract event details but don't store them, just print
"""

import argparse

from sqlalchemy import select

from services import event_crawler, listing_crawler
from services.db import init_db, session_scope
from services.models import Organiser
from services.seed_organisers import seed_from_csv


def run(limit: int | None = None, organiser_id: int | None = None, dry_run: bool = False) -> None:
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
                event_crawler.preview_event(url)
                continue
            with session_scope() as session:
                event = event_crawler.crawl_event(session, organiser.id, url)
                print(f"  {'ok' if event else 'FAILED'}: {url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--organiser-id", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and extract each event's details but skip storing them; print the extracted fields and continue.",
    )
    args = parser.parse_args()
    run(limit=args.limit, organiser_id=args.organiser_id, dry_run=args.dry_run)
