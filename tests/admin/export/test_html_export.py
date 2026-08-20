"""
Unit tests for admin/export/html_export.py.

No real database anywhere here: csv_export._fetch_rows (html_export's own
query-building seam - see that module's docstring for why it's addressed via
the `csv_export` module object, not a bare imported name) and
html_export.session_scope are monkeypatched with an in-memory object graph
built directly from the ORM classes - same "monkeypatch at the real seam"
style as test_scraping.py, rather than standing up a real/in-memory-SQLite
database (which won't work here anyway: Organiser uses Postgres' ARRAY column
type, which SQLite can't build).

The rendering/formatting helpers (_render_map, _render_event, etc.) are
tested directly against hand-built model instances, no monkeypatching needed.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from services.admin.export import csv_export, html_export
from services.common.models import (
    Event,
    EventDistance,
    EventLifecycle,
    EventOccurrence,
    EventStatus,
    Occurrence,
    RaceType,
    Sport,
)

# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------

def test_maps_link_url_encodes_location():
    url = html_export._maps_link_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "The+Avenue%2C+Southampton" in url


def test_maps_embed_url_encodes_location():
    url = html_export._maps_embed_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps?q=")
    assert url.endswith("&output=embed")


# ---------------------------------------------------------------------------
# _render_map
# ---------------------------------------------------------------------------

def test_render_map_no_location():
    event = Event(location=None, start_location=None, finish_location=None)
    assert "No location available" in html_export._render_map(event)


def test_render_map_prefers_location_over_start_finish():
    event = Event(location="Hyde Park", start_location="Gate A", finish_location="Gate B")
    rendered = html_export._render_map(event)
    assert "Hyde Park" in rendered
    assert "maps-embed" in rendered


def test_render_map_falls_back_to_start_location():
    event = Event(location=None, start_location="Start Line", finish_location=None)
    assert "Start Line" in html_export._render_map(event)


def test_render_map_prefers_stored_coordinates_over_text_location():
    # See events/geocoding_client.py - a real geocoded point is preferred over a text
    # search Google would otherwise have to resolve itself, and is exactly what a
    # "near me" query would filter on, so the map should show precisely that, when it
    # exists.
    event = Event(location="Hyde Park", latitude=51.5073, longitude=-0.1657)
    rendered = html_export._render_map(event)
    assert "51.5073,-0.1657" in rendered
    assert "Hyde Park" not in rendered  # coordinates win outright, not a fallback hint


def test_render_map_falls_back_to_text_when_not_yet_geocoded():
    event = Event(location="Hyde Park", latitude=None, longitude=None)
    rendered = html_export._render_map(event)
    assert "Hyde Park" in rendered


# ---------------------------------------------------------------------------
# _render_distances
# ---------------------------------------------------------------------------

def test_render_distances_empty():
    assert "No distances listed" in html_export._render_distances(Event(distances=[]))


def test_render_distances_shows_race_type_label_when_present():
    event = Event(distances=[
        EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k")),
    ])
    rendered = html_export._render_distances(event)
    assert "<code>running_5k</code>" in rendered
    assert "£15" in rendered


def test_render_distances_shows_placeholder_when_no_race_type():
    event = Event(distances=[EventDistance(distance_text="Fun Run", price_text=None, race_type=None)])
    rendered = html_export._render_distances(event)
    assert "Fun Run" in rendered
    assert rendered.count('<span class="empty">&mdash;</span>') == 2  # price AND race type both missing


# ---------------------------------------------------------------------------
# _render_occurrences - same shape as _render_distances, for the "specific
# dates" (bounded/enumerated recurrence) case - see common/models's Occurrence.
# ---------------------------------------------------------------------------

def test_render_occurrences_empty():
    assert "No specific dates listed" in html_export._render_occurrences(Event(occurrences=[]))


def test_render_occurrences_shows_date_time_and_price():
    event = Event(occurrences=[
        EventOccurrence(
            starts_at=datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc),
            date_text="18th Aug 2026", time_text="06:00 PM", price_text="£10.00",
        ),
    ])
    rendered = html_export._render_occurrences(event)
    assert "18th Aug 2026" in rendered
    assert "06:00 PM" in rendered
    assert "£10.00" in rendered


def test_render_occurrences_shows_placeholder_when_no_time_or_price():
    event = Event(occurrences=[
        EventOccurrence(starts_at=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc), date_text="20th Aug 2026"),
    ])
    rendered = html_export._render_occurrences(event)
    assert "20th Aug 2026" in rendered
    assert rendered.count('<span class="empty">&mdash;</span>') == 2  # time AND price both missing


# ---------------------------------------------------------------------------
# _format_detail_value - the couple of DETAIL_FIELDS value shapes plain
# str(value) renders awkwardly.
# ---------------------------------------------------------------------------

def test_format_detail_value_uses_enum_value_not_default_str():
    assert html_export._format_detail_value(Occurrence.WEEKLY) == "weekly"


def test_format_detail_value_joins_a_list_with_commas():
    assert html_export._format_detail_value(["sat", "sun"]) == "sat, sun"


def test_format_detail_value_passes_through_plain_values():
    assert html_export._format_detail_value("plain string") == "plain string"


# ---------------------------------------------------------------------------
# _render_page_content
# ---------------------------------------------------------------------------

def test_render_page_content_empty_when_no_markdown():
    assert html_export._render_page_content(Event(raw_markdown=None)) == ""


def test_render_page_content_renders_markdown_to_html():
    event = Event(raw_markdown="# Heading\n\n**bold text**")
    rendered = html_export._render_page_content(event)
    assert "<h1>Heading</h1>" in rendered
    assert "<strong>bold text</strong>" in rendered
    assert 'class="page-content"' in rendered


# ---------------------------------------------------------------------------
# _render_event - escaping is the important thing here, since event fields
# come straight from arbitrary crawled pages.
# ---------------------------------------------------------------------------

def test_render_event_escapes_untrusted_name():
    event = Event(id=1, name="<script>alert(1)</script>", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = html_export._render_event(event)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_event_untitled_fallback():
    event = Event(id=42, name=None, sport=None, date_text=None, distances=[], raw_markdown=None)
    assert "(untitled event #42)" in html_export._render_event(event)


def test_render_event_omits_organiser_row_by_default():
    # organiser_name not given (e.g. _render_organiser's per-organiser tree, where the
    # name is already the enclosing group) - the *name* row shouldn't render, but the
    # organiser_id row (see below) is unconditional, so check the exact <th> not a bare
    # "Organiser" substring - that would also match "Organiser ID".
    event = Event(id=1, organiser_id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    assert "<th>Organiser</th>" not in html_export._render_event(event)


def test_render_event_includes_organiser_row_when_given():
    event = Event(id=1, organiser_id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    rendered = html_export._render_event(event, organiser_name="Acme Runners")
    assert "<th>Organiser</th><td>Acme Runners</td>" in rendered


def test_render_event_always_shows_organiser_id():
    # Unlike the organiser *name* row, organiser_id is always shown - it's the one
    # identifier that's stable and correlates a rendered event back to a --organiser-id
    # filter or a DB row, regardless of which export (or grouping) is rendering it.
    event = Event(id=1, organiser_id=42, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    rendered = html_export._render_event(event)
    assert "<th>Organiser ID</th><td>42</td>" in rendered


def test_render_event_includes_url_link_when_present():
    event = Event(id=1, name="Test", sport=None, date_text=None, distances=[], raw_markdown=None, url="https://example.com/event")
    rendered = html_export._render_event(event)
    assert 'href="https://example.com/event"' in rendered


def test_render_event_valid_has_no_invalid_badge_or_reason_row():
    event = Event(id=1, name="Real Event", sport="running", date_text=None, distances=[], raw_markdown=None, status=EventStatus.VALID)
    rendered = html_export._render_event(event)
    assert "badge-invalid" not in rendered
    assert "Invalid reason" not in rendered


def test_render_event_invalid_shows_badge_and_reason():
    # The reported case: a page that's just a redirect notice, e.g.
    # runthrough.co.uk/event/running-tours-copenhagen-marathon.
    event = Event(
        id=1, name="No event details available", sport="other", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.INVALID, invalid_reason="Page is just a redirect notice to an external site, no event details shown",
    )
    rendered = html_export._render_event(event)
    assert '<span class="badge badge-invalid">INVALID</span>' in rendered
    assert "Page is just a redirect notice to an external site, no event details shown" in rendered


def test_render_event_invalid_with_no_reason_shows_placeholder():
    event = Event(id=1, name="X", sport=None, date_text=None, distances=[], raw_markdown=None, status=EventStatus.INVALID, invalid_reason=None)
    rendered = html_export._render_event(event)
    assert '<tr><th>Invalid reason</th><td class="invalid-reason"><span class="empty">&mdash;</span></td></tr>' in rendered


def test_render_event_scheduled_has_no_lifecycle_badge_or_row():
    event = Event(
        id=1, name="Real Event", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.SCHEDULED,
    )
    rendered = html_export._render_event(event)
    assert "badge-cancelled" not in rendered
    assert "badge-postponed" not in rendered
    assert "Lifecycle detail" not in rendered


def test_render_event_cancelled_shows_badge_and_detail():
    event = Event(
        id=1, name="Storm-hit 10k", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.CANCELLED,
        lifecycle_text="Cancelled due to adverse weather",
    )
    rendered = html_export._render_event(event)
    assert '<span class="badge badge-cancelled">CANCELLED</span>' in rendered
    assert "Cancelled due to adverse weather" in rendered


def test_render_event_postponed_shows_badge_and_detail():
    event = Event(
        id=1, name="Some 10k", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.POSTPONED,
        lifecycle_text="Postponed to 12 September 2026",
    )
    rendered = html_export._render_event(event)
    assert '<span class="badge badge-postponed">POSTPONED</span>' in rendered
    assert "Postponed to 12 September 2026" in rendered


# ---------------------------------------------------------------------------
# _render_organiser / _render_sport - pluralisation and nesting
# ---------------------------------------------------------------------------

def test_render_organiser_singular_count():
    event = Event(id=1, name="Solo Event", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = html_export._render_organiser(1, "Acme Runners", [event])
    assert "(1 event)" in rendered
    assert "Solo Event" in rendered


def test_render_organiser_plural_count():
    events = [
        Event(id=1, name="Event A", sport=None, date_text=None, distances=[], raw_markdown=None),
        Event(id=2, name="Event B", sport=None, date_text=None, distances=[], raw_markdown=None),
    ]
    rendered = html_export._render_organiser(1, "Acme Runners", events)
    assert "(2 events)" in rendered


def test_render_organiser_header_shows_organiser_id():
    event = Event(id=1, name="Solo Event", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = html_export._render_organiser(7, "Acme Runners", [event])
    assert '<span class="org-id">(ID 7)</span>' in rendered


def test_render_sport_counts_events_and_distances_across_groups():
    event = Event(id=1, name="Acme 5K", sport="running", date_text=None, distances=[], raw_markdown=None)
    distances_by_label = {
        "running_5k": [(event, "Acme Runners", EventDistance(distance_text="5K"))],
        "running_10k": [(event, "Acme Runners", EventDistance(distance_text="10K"))],
    }
    rendered = html_export._render_sport("running", distances_by_label)
    assert "(2 events across 2 distances)" in rendered
    assert "running_5k" in rendered
    assert "running_10k" in rendered


# ---------------------------------------------------------------------------
# Top-level exports - csv_export._fetch_rows and html_export.session_scope
# monkeypatched, so no real database is ever touched.
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_rows():
    """Two organisers, three events, mirroring what _fetch_rows would return."""
    event1 = Event(
        id=1, organiser_id=1, url="https://acme.example/5k", name="Acme 5K", sport="running",
        status=EventStatus.VALID, date_text="Sunday", location="Acme Park", raw_markdown=None,
        distances=[EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k"))],
    )
    event2 = Event(
        id=2, organiser_id=1, url="https://acme.example/10k", name="Acme 10K", sport="running",
        status=EventStatus.VALID, date_text="Sunday", location="Acme Park", raw_markdown=None,
        distances=[
            EventDistance(distance_text="10K", price_text="£20", race_type=RaceType(label="running_10k", sport=Sport.RUNNING, distance_category="10k")),
            EventDistance(distance_text="Fun Run", price_text=None, race_type=None),
        ],
    )
    event3 = Event(
        id=3, organiser_id=2, url="https://beta.example/tri", name="Beta Triathlon", sport="triathlon",
        status=EventStatus.VALID, date_text="Saturday", location="Beta Lake", raw_markdown=None,
        distances=[EventDistance(distance_text="Sprint Triathlon", price_text="£50", race_type=RaceType(label="triathlon_sprint_triathlon", sport=Sport.TRIATHLON, distance_category="sprint_triathlon"))],
    )
    return [
        (event1, "Acme Runners"),
        (event2, "Acme Runners"),
        (event3, "Beta Multisport"),
    ]


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch, sample_rows):
    """Every test in this module gets the same canned rows - no real DB/session ever touched."""

    @contextmanager
    def fake_session_scope():
        yield None  # never touched: _fetch_rows below ignores it entirely

    monkeypatch.setattr(html_export, "session_scope", fake_session_scope)
    # html_export calls csv_export._fetch_rows via the module object (see
    # html_export.py's own docstring), so that's the seam to patch here too -
    # patching html_export._fetch_rows would have no effect, it isn't a name
    # html_export ever binds.
    monkeypatch.setattr(csv_export, "_fetch_rows", lambda session, organiser_id=None, status=None: sample_rows)


def test_export_events_per_organiser_requests_valid_status(monkeypatch, tmp_path, sample_rows):
    captured = {}

    def fake_fetch_rows(session, organiser_id=None, status=None):
        captured["status"] = status
        return sample_rows

    monkeypatch.setattr(csv_export, "_fetch_rows", fake_fetch_rows)
    html_export.export_events_per_organiser(tmp_path / "valid.html")

    assert captured["status"] == EventStatus.VALID


def test_export_invalid_events_requests_invalid_status_and_renders_reason(monkeypatch, tmp_path):
    captured = {}
    invalid_event = Event(
        id=5, organiser_id=1, name="No event details available", sport="other",
        status=EventStatus.INVALID, invalid_reason="Page is just a redirect notice to an external site, no event details shown",
        date_text=None, location=None, raw_markdown=None, distances=[], url="https://acme.example/redirect",
    )

    def fake_fetch_rows(session, organiser_id=None, status=None):
        captured["status"] = status
        return [(invalid_event, "Acme Runners")] if status == EventStatus.INVALID else []

    monkeypatch.setattr(csv_export, "_fetch_rows", fake_fetch_rows)

    total = html_export.export_invalid_events(tmp_path / "invalid.html")

    assert captured["status"] == EventStatus.INVALID
    assert total == 1
    html_text = (tmp_path / "invalid.html").read_text(encoding="utf-8")
    assert "<title>Invalid events</title>" in html_text
    assert "<h1>Invalid events</h1>" in html_text
    assert "No event details available" in html_text
    assert "Page is just a redirect notice to an external site, no event details shown" in html_text
    assert '<span class="badge badge-invalid">INVALID</span>' in html_text


def test_export_invalid_events_empty_when_none_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(csv_export, "_fetch_rows", lambda session, organiser_id=None, status=None: [])

    total = html_export.export_invalid_events(tmp_path / "invalid.html")

    assert total == 0
    html_text = (tmp_path / "invalid.html").read_text(encoding="utf-8")
    assert "0 event(s) across 0 organiser(s)" in html_text


def test_export_events_per_organiser_groups_by_organiser(tmp_path):
    output_path = tmp_path / "per_organiser.html"
    total = html_export.export_events_per_organiser(output_path)

    assert total == 3
    html_text = output_path.read_text(encoding="utf-8")
    assert "Acme Runners" in html_text
    assert "Beta Multisport" in html_text
    assert "Acme 5K" in html_text
    assert "Beta Triathlon" in html_text
    assert "(2 events)" in html_text  # Acme Runners' count
    assert "(1 event)" in html_text  # Beta Multisport's count
    # organiser_id (see sample_rows: event1/event2 -> organiser 1, event3 -> organiser 2)
    # threaded through from the grouped dict's key, not just each event's own row.
    assert '<span class="org-id">(ID 1)</span>' in html_text
    assert '<span class="org-id">(ID 2)</span> <span class="count">(1 event)</span>' in html_text
    assert "<th>Organiser ID</th><td>1</td>" in html_text
    assert "<th>Organiser ID</th><td>2</td>" in html_text


def test_export_events_per_event_type_groups_by_sport_and_distance(tmp_path):
    output_path = tmp_path / "per_type.html"
    total = html_export.export_events_per_event_type(output_path)

    # 4 distance entries total: 5k, 10k, the uncategorised fun run, sprint triathlon.
    assert total == 4
    html_text = output_path.read_text(encoding="utf-8")
    assert "running_5k" in html_text
    assert "running_10k" in html_text
    assert "triathlon_sprint_triathlon" in html_text
    assert html_export._UNCATEGORISED_LABEL in html_text
    # Full event detail (not just a name) should appear under each distance node.
    assert "Acme Park" in html_text
    assert "Acme Runners" in html_text  # organiser shown here, unlike the per-organiser export


# ---------------------------------------------------------------------------
# CSS: a shared sibling file next to whichever HTML output is written, not
# inlined into a <style> block in every export.
# ---------------------------------------------------------------------------

def test_html_export_writes_css_as_sibling_file_not_inline(tmp_path):
    output_path = tmp_path / "per_organiser.html"
    html_export.export_events_per_organiser(output_path)

    html_text = output_path.read_text(encoding="utf-8")
    assert "<style>" not in html_text
    assert f'<link rel="stylesheet" href="{html_export._CSS_FILENAME}">' in html_text

    css_path = tmp_path / html_export._CSS_FILENAME
    assert css_path.exists()
    assert "details" in css_path.read_text(encoding="utf-8")


def test_css_file_written_alongside_each_distinct_output_directory(tmp_path):
    organiser_dir = tmp_path / "organiser"
    by_type_dir = tmp_path / "by_type"

    html_export.export_events_per_organiser(organiser_dir / "per_organiser.html")
    html_export.export_events_per_event_type(by_type_dir / "per_type.html")

    assert (organiser_dir / html_export._CSS_FILENAME).exists()
    assert (by_type_dir / html_export._CSS_FILENAME).exists()


def test_export_events_per_organiser_respects_organiser_id_filter(monkeypatch, tmp_path, sample_rows):
    # _fetch_rows is normally responsible for filtering by organiser_id - confirm the
    # export function actually threads organiser_id through to it rather than ignoring it.
    captured = {}

    def fake_fetch_rows(session, organiser_id=None, status=None):
        captured["organiser_id"] = organiser_id
        return [row for row in sample_rows if row[0].organiser_id == organiser_id] if organiser_id else sample_rows

    monkeypatch.setattr(csv_export, "_fetch_rows", fake_fetch_rows)

    total = html_export.export_events_per_organiser(tmp_path / "filtered.html", organiser_id=2)

    assert captured["organiser_id"] == 2
    assert total == 1
