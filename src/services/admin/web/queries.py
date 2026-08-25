"""
Query-building for the admin web interface - the one seam every route in
app.py goes through to reach the database, same role csv_export._fetch_rows
played for the old file-based exports (see ARCHITECTURE.md's "Admin
interface" section for why those were replaced with this on-the-fly view).

Kept separate from render.py so the rendering functions stay pure (rows in,
HTML string out) and testable without a database at all - see
tests/admin/web/test_render.py.
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from services.common.models import Event, EventDistance, EventStatus, Organiser

__all__ = ["fetch_events", "fetch_organisers"]


def fetch_events(
    session,
    *,
    organiser_id: int | None = None,
    status: EventStatus | None = EventStatus.VALID,
    search: str | None = None,
):
    """Every event matching the given filters, joined with its organiser's name - the same
    shape csv_export._fetch_rows used to return. Defaulting to VALID means INVALID events
    (see EventStatus - redirect notices, dead pages, etc. with no real event content) are
    excluded from the normal /events view without every caller having to remember to filter
    them out itself; pass status=EventStatus.INVALID for the /events/invalid view instead, or
    status=None for every status at once.

    `search` is a free-text filter (case-insensitive substring) across the event name, its
    organiser's name, and its location - new here, not something the old static exports had:
    those were fully pre-rendered pages you'd Ctrl-F in a browser, this one's regenerated
    on-the-fly per request, so a server-side filter is what replaces that.
    """
    stmt = (
        select(Event, Organiser.name)
        .join(Organiser, Organiser.id == Event.organiser_id)
        # Eager-load: this module never keeps a session open past its own request (see
        # app.py's session_scope usage), so a lazy load of event.distances (or
        # distance.race_type)/event.occurrences after that would raise DetachedInstanceError.
        .options(
            selectinload(Event.distances).selectinload(EventDistance.race_type),
            selectinload(Event.occurrences),
        )
    )
    if status is not None:
        stmt = stmt.where(Event.status == status)
    if organiser_id is not None:
        stmt = stmt.where(Event.organiser_id == organiser_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Event.name.ilike(like), Organiser.name.ilike(like), Event.location.ilike(like)))
    stmt = stmt.order_by(Organiser.name, Event.id)
    return list(session.execute(stmt))


def fetch_organisers(session):
    """Every organiser, with its VALID event count, for the landing page's organiser index -
    a grouped count query rather than loading every Event just to len() them. Includes
    organisers with zero events (outerjoin), so a newly-seeded organiser that hasn't been
    crawled yet still shows up."""
    stmt = (
        select(Organiser.id, Organiser.name, func.count(Event.id))
        .outerjoin(Event, (Event.organiser_id == Organiser.id) & (Event.status == EventStatus.VALID))
        .group_by(Organiser.id, Organiser.name)
        .order_by(Organiser.name)
    )
    return list(session.execute(stmt))
