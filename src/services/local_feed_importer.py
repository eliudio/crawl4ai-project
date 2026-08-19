"""
Runs one structured-bulk-feed importer in-process, with no Pub/Sub involved - the
local-dev equivalent of local_event_scraper.py, but for the separate pipeline
feed_importers.py registers (parkrun, future meetup/OSM importers, ...), never the
pattern-website one local_event_scraper.py drives. Production triggers this via
main.py's own /tasks/feed-import handler, from a scheduled Pub/Sub message (see
pubsub_client.publish_feed_import) rather than a per-organiser/per-event fan-out.

Usage:
    python -m services.local_feed_importer --source parkrun
    python -m services.local_feed_importer --source parkrun --param country="United Kingdom"
"""

import argparse

from services import feed_importers, parkrun_import  # noqa: F401 - importing registers "parkrun"
from services.db import init_db, session_scope


def run(source: str, params: dict | None = None) -> None:
    importer = feed_importers.get_importer(source)
    if importer is None:
        print(f"unknown feed import source {source!r}")
        return

    init_db()
    with session_scope() as session:
        summary = importer(session, params or {})
    print(f"{source}: {summary}")


def _parse_params(pairs: list[str]) -> dict:
    """--param key=value, repeatable - deliberately minimal (no type coercion beyond
    plain strings), since each importer already parses/validates its own params
    (e.g. parkrun_import.py's "country") out of whatever it's handed."""
    params = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        params[key] = value
    return params


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help='which registered importer to run, e.g. "parkrun"')
    parser.add_argument(
        "--param", action="append", default=[], metavar="key=value",
        help="importer-specific param, repeatable - forwarded to the importer as a dict",
    )
    args = parser.parse_args()
    run(args.source, _parse_params(args.param))
