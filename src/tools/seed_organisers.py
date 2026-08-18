"""
Phase 1 organiser seeding: load the manually-curated organiser list into the
`organisers` table, since phase 1 does not yet have automated aggregator
discovery (that's phase 2 — see README.md).

Usage:
    python -m services.seed_organisers                  # seed only
    python -m services.seed_organisers --publish         # seed + enqueue listing-crawl for each
"""

import argparse
import csv
import json
from pathlib import Path

from sqlalchemy import select

from services import pubsub_client
from services.db import init_db, session_scope
from services.models import Organiser, SourceType

SEED_CSV = Path(__file__).parent / "data" / "organisers_seed.csv"


def _handler_params_from_row(row: dict) -> dict | None:
    """
    Builds Organiser.handler_params from a CSV row: merges the CSV's own generic
    handler_params column (any handler-specific config, as a JSON string - e.g.
    "parkrun"'s own country_code) with its separate, flat sitemap_url column.

    sitemap_url is kept as its own flat CSV column rather than nested inside the
    handler_params JSON string, specifically so discover_sitemaps.py - a plain
    csv.DictWriter script that only ever does `row["sitemap_url"] = ...` - never has
    to parse/merge JSON just to set one string; it's merged in here instead, at the
    one place that already has to build handler_params from everything else anyway.
    The "default" handler reads it back out as params["sitemap_url"].

    Returns None (not {}) when there's nothing at all, so a freshly seeded organiser
    with no sitemap and no other handler_params gets a clean NULL column rather than
    a pointless empty dict.
    """
    params = json.loads(row.get("handler_params") or "{}")
    sitemap_url = row.get("sitemap_url")
    if sitemap_url:
        params["sitemap_url"] = sitemap_url
    return params or None


def seed_from_csv(csv_path: Path = SEED_CSV) -> list[int]:
    """Insert any organiser rows not already present (matched by homepage_url). Returns all organiser ids."""
    init_db()
    ids: list[int] = []

    with session_scope() as session, csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sitemap_url = row.get("sitemap_url") or None
            existing = session.scalar(
                select(Organiser).where(Organiser.homepage_url == row["homepage_url"])
            )
            if existing:
                # discover_sitemaps.py runs independently of seeding (it only
                # rewrites the CSV) and organisers already in the DB are
                # never re-inserted below, so a sitemap found after the
                # first seed would otherwise never reach an existing row -
                # sync just this one field rather than overwriting the whole
                # handler_params dict (which may carry other, unrelated config).
                if sitemap_url and (existing.handler_params or {}).get("sitemap_url") != sitemap_url:
                    existing.handler_params = {**(existing.handler_params or {}), "sitemap_url": sitemap_url}
                    session.add(existing)
                ids.append(existing.id)
                continue

            organiser = Organiser(
                name=row["name"],
                homepage_url=row["homepage_url"],
                listing_urls=json.loads(row["listing_urls"]),
                source_type=SourceType.ORGANISER,
                discovered_via=row["discovered_via"],
                handler=row.get("handler") or "default",
                handler_params=_handler_params_from_row(row),
            )
            session.add(organiser)
            session.flush()  # assign organiser.id
            ids.append(organiser.id)

    print(f"seeded/confirmed {len(ids)} organiser(s) from {csv_path}")
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="also enqueue a listing-crawl for each organiser")
    args = parser.parse_args()

    ids = seed_from_csv()

    if args.publish:
        for organiser_id in ids:
            pubsub_client.publish_listing_crawl(organiser_id)
        print(f"enqueued listing-crawl for {len(ids)} organiser(s)")


if __name__ == "__main__":
    main()
