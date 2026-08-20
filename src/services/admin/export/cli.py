"""
Argparse entrypoint tying csv_export.py/html_export.py together.

Usage:
    python -m services.admin.export.cli --format csv
    python -m services.admin.export.cli --format html
    python -m services.admin.export.cli --format html --output out.html --output-by-type out2.html --output-invalid out3.html
    python -m services.admin.export.cli --organiser-id 3
"""

import argparse
from pathlib import Path

from services.admin.export.csv_export import export_csv
from services.admin.export.html_export import export_events_per_event_type, export_events_per_organiser, export_invalid_events

DEFAULT_OUTPUT = {
    "csv": Path("c:/temp/crawl4ai/events/events_export.csv"),
    "html": Path("c:/temp/crawl4ai/events/events_per_organiser.html"),
    "html_by_type": Path("c:/temp/crawl4ai/events/events_per_event_type.html"),
    "html_invalid": Path("c:/temp/crawl4ai/events/events_invalid.html"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["csv", "html"], default="html")
    parser.add_argument("--output", type=Path, default=None, help="output path (default: events_export.csv, or events_per_organiser.html when --format html)")
    parser.add_argument("--output-by-type", type=Path, default=None, help="only used with --format html: path for the sport/race-type-grouped export")
    parser.add_argument("--output-invalid", type=Path, default=None, help="only used with --format html: path for the INVALID-events debugging export")
    parser.add_argument("--organiser-id", type=int, default=None, help="only export events for this organiser id")
    args = parser.parse_args()

    if args.format == "csv":
        export_csv(args.output or DEFAULT_OUTPUT["csv"], organiser_id=args.organiser_id)
    else:
        export_events_per_organiser(args.output or DEFAULT_OUTPUT["html"], organiser_id=args.organiser_id)
        export_events_per_event_type(args.output_by_type or DEFAULT_OUTPUT["html_by_type"], organiser_id=args.organiser_id)
        export_invalid_events(args.output_invalid or DEFAULT_OUTPUT["html_invalid"], organiser_id=args.organiser_id)


if __name__ == "__main__":
    main()
