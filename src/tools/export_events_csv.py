"""
Dumps the `events` table (joined with its organiser) to a CSV file, for
ad-hoc inspection of what the pipeline has collected so far without needing
`psql` open.

Usage:
    python -m tools.export_events_csv
    python -m tools.export_events_csv --output out.csv
    python -m tools.export_events_csv --organiser-id 3
"""

import argparse
import csv
from pathlib import Path

from sqlalchemy import select

from services.db import session_scope
from services.models import Event, Organiser

DEFAULT_OUTPUT = Path(__file__).parent / "data" / "events_export.csv"

FIELDNAMES = [
    "id",
    "organiser_id",
    "organiser_name",
    "name",
    "sport",
    "date_text",
    "location",
    "start_location",
    "finish_location",
    "distance_text",
    "price_text",
    "age_restriction_text",
    "url",
    "summary",
    "first_seen_at",
    "last_seen_at",
    "last_crawled_at",
]


def export_events(output_path: Path = DEFAULT_OUTPUT, organiser_id: int | None = None) -> int:
    """Writes every event row to `output_path`, joined with its organiser's name. Returns the row count."""
    stmt = select(Event, Organiser.name).join(Organiser, Organiser.id == Event.organiser_id)
    if organiser_id is not None:
        stmt = stmt.where(Event.organiser_id == organiser_id)
    stmt = stmt.order_by(Event.id)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with session_scope() as session, output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for event, organiser_name in session.execute(stmt):
            writer.writerow(
                {
                    "id": event.id,
                    "organiser_id": event.organiser_id,
                    "organiser_name": organiser_name,
                    "name": event.name,
                    "sport": event.sport,
                    "date_text": event.date_text,
                    "location": event.location,
                    "start_location": event.start_location,
                    "finish_location": event.finish_location,
                    "distance_text": event.distance_text,
                    "price_text": event.price_text,
                    "age_restriction_text": event.age_restriction_text,
                    "url": event.url,
                    "summary": event.summary,
                    "first_seen_at": event.first_seen_at,
                    "last_seen_at": event.last_seen_at,
                    "last_crawled_at": event.last_crawled_at,
                }
            )
            count += 1

    print(f"wrote {count} event(s) to {output_path}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="CSV path to write (default: tools/data/events_export.csv)")
    parser.add_argument("--organiser-id", type=int, default=None, help="only export events for this organiser id")
    args = parser.parse_args()

    export_events(output_path=args.output, organiser_id=args.organiser_id)


if __name__ == "__main__":
    main()
