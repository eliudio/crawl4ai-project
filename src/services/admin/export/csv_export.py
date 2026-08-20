"""
Dumps the `events` table (joined with its organiser) to a flat CSV, for ad-hoc
inspection of what the pipeline has collected so far without needing `psql`
open. See html_export.py for the richer, browsable HTML equivalent - both
share this module's own _fetch_rows as their one query-building seam.
"""

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from services.common.db import session_scope
from services.common.models import Event, EventDistance, EventOccurrence, EventStatus, Organiser

__all__ = ["export_csv"]

CSV_FIELDNAMES = [
    "id",
    "organiser_id",
    "organiser_name",
    "name",
    "sport",
    "status",
    "invalid_reason",
    "date_text",
    "occurrence",
    "occurrence_weekdays",
    "occurrence_time",
    "occurrence_starts_on",
    "occurrence_ends_on",
    "occurrences",
    "location",
    "start_location",
    "finish_location",
    "latitude",
    "longitude",
    "distances",
    "age_restriction_text",
    "registration_status",
    "registration_text",
    "registration_opens_at",
    "registration_closes_at",
    "lifecycle_status",
    "lifecycle_text",
    "url",
    "summary",
    "summary_alt",
    "summary_short",
    "first_seen_at",
    "last_seen_at",
    "last_crawled_at",
]


def _fetch_rows(session, organiser_id: int | None = None, status: EventStatus | None = EventStatus.VALID):
    """Every event matching `status` (default VALID) joined with its organiser's name, grouped
    for display by organiser then event id. Defaulting to VALID means INVALID events (see
    EventStatus - redirect notices, dead pages, etc. with no real event content) are excluded
    from the normal exports without each one having to remember to filter them out itself;
    html_export.export_invalid_events passes status=EventStatus.INVALID instead, for exactly
    the opposite view - pass status=None for every status at once (not currently used by any
    export)."""
    stmt = (
        select(Event, Organiser.name)
        .join(Organiser, Organiser.id == Event.organiser_id)
        # Eager-load: callers render after `session_scope` has closed the session, so a lazy
        # load of event.distances (or distance.race_type)/event.occurrences at that point
        # would raise DetachedInstanceError.
        .options(
            selectinload(Event.distances).selectinload(EventDistance.race_type),
            selectinload(Event.occurrences),
        )
    )
    if status is not None:
        stmt = stmt.where(Event.status == status)
    if organiser_id is not None:
        stmt = stmt.where(Event.organiser_id == organiser_id)
    stmt = stmt.order_by(Organiser.name, Event.id)
    return list(session.execute(stmt))


def _format_distance(distance: EventDistance) -> str:
    label = f"{distance.distance_text} [{distance.race_type.label}]" if distance.race_type else distance.distance_text
    return f"{label}: {distance.price_text}" if distance.price_text else label


def _distances_summary(event: Event) -> str:
    """One-line "5k: £15; 10k: £20" summary, for the flat CSV format."""
    return "; ".join(_format_distance(d) for d in event.distances)


def _format_occurrence(occurrence: EventOccurrence) -> str:
    label = f"{occurrence.date_text} {occurrence.time_text}" if occurrence.time_text else occurrence.date_text
    return f"{label}: {occurrence.price_text}" if occurrence.price_text else label


def _occurrences_summary(event: Event) -> str:
    """One-line "18th Aug 2026 06:00 PM: £10.00; ..." summary, for the flat CSV format -
    same spirit as _distances_summary, empty for a one-off/unbounded-recurrence event
    with no individually-listed dates (see models.py's Occurrence docstring)."""
    return "; ".join(_format_occurrence(o) for o in event.occurrences)


def export_csv(output_path: Path, organiser_id: int | None = None) -> int:
    """Writes every event row to `output_path`. Returns the row count."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with session_scope() as session, output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for event, organiser_name in _fetch_rows(session, organiser_id):
            writer.writerow(
                {
                    "id": event.id,
                    "organiser_id": event.organiser_id,
                    "organiser_name": organiser_name,
                    "name": event.name,
                    "sport": event.sport,
                    "status": event.status.value if event.status else EventStatus.VALID.value,
                    "invalid_reason": event.invalid_reason,
                    "date_text": event.date_text,
                    "occurrence": event.occurrence.value if event.occurrence else None,
                    "occurrence_weekdays": ", ".join(event.occurrence_weekdays) if event.occurrence_weekdays else None,
                    "occurrence_time": event.occurrence_time,
                    "occurrence_starts_on": event.occurrence_starts_on,
                    "occurrence_ends_on": event.occurrence_ends_on,
                    "occurrences": _occurrences_summary(event),
                    "location": event.location,
                    "start_location": event.start_location,
                    "finish_location": event.finish_location,
                    "latitude": event.latitude,
                    "longitude": event.longitude,
                    "distances": _distances_summary(event),
                    "age_restriction_text": event.age_restriction_text,
                    "registration_status": event.registration_status.value if event.registration_status else None,
                    "registration_text": event.registration_text,
                    "registration_opens_at": event.registration_opens_at,
                    "registration_closes_at": event.registration_closes_at,
                    "lifecycle_status": event.lifecycle_status.value if event.lifecycle_status else None,
                    "lifecycle_text": event.lifecycle_text,
                    "url": event.url,
                    "summary": event.summary,
                    "summary_alt": event.summary_alt,
                    "summary_short": event.summary_short,
                    "first_seen_at": event.first_seen_at,
                    "last_seen_at": event.last_seen_at,
                    "last_crawled_at": event.last_crawled_at,
                }
            )
            count += 1

    print(f"wrote {count} event(s) to {output_path}")
    return count
