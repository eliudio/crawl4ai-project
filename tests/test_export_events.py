"""
Unit tests for tools/export_events.py.

No real database anywhere here: `_fetch_rows` (the one function that actually
queries) and `session_scope` are monkeypatched with an in-memory object graph
built directly from the ORM classes (Event/EventDistance/RaceType/Organiser
work fine as plain Python objects without a session - see models.py) - same
"monkeypatch at the real seam" style as test_scraping.py, rather than standing
up a real/in-memory-SQLite database (which won't work here anyway: Organiser
uses Postgres' ARRAY column type, which SQLite can't build).

The rendering/formatting helpers (_format_distance, _render_map, etc.) are
tested directly against hand-built model instances, no monkeypatching needed.
"""

import csv
from contextlib import contextmanager

import pytest

from services.models import Event, EventDistance, EventStatus, Organiser, RaceType, Sport
from tools import export_events

# Captured before any test's autouse fixture monkeypatches export_events._fetch_rows (see
# _no_real_db below) - the two tests that need the REAL query-building logic call this
# directly instead of the (by-then-patched) module attribute.
_real_fetch_rows = export_events._fetch_rows


# ---------------------------------------------------------------------------
# Pure formatting helpers
# ---------------------------------------------------------------------------

def test_format_distance_with_price_and_race_type():
    d = EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k"))
    assert export_events._format_distance(d) == "5K [running_5k]: £15"


def test_format_distance_no_price():
    d = EventDistance(distance_text="Fun Run", price_text=None, race_type=None)
    assert export_events._format_distance(d) == "Fun Run"


def test_format_distance_no_race_type_but_has_price():
    d = EventDistance(distance_text="10K", price_text="£20", race_type=None)
    assert export_events._format_distance(d) == "10K: £20"


def test_distances_summary_joins_multiple_with_semicolon():
    event = Event(distances=[
        EventDistance(distance_text="5K", price_text="£15", race_type=None),
        EventDistance(distance_text="10K", price_text="£20", race_type=None),
    ])
    assert export_events._distances_summary(event) == "5K: £15; 10K: £20"


def test_distances_summary_empty_when_no_distances():
    assert export_events._distances_summary(Event(distances=[])) == ""


def test_maps_link_url_encodes_location():
    url = export_events._maps_link_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "The+Avenue%2C+Southampton" in url


def test_maps_embed_url_encodes_location():
    url = export_events._maps_embed_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps?q=")
    assert url.endswith("&output=embed")


# ---------------------------------------------------------------------------
# _render_map
# ---------------------------------------------------------------------------

def test_render_map_no_location():
    event = Event(location=None, start_location=None, finish_location=None)
    assert "No location available" in export_events._render_map(event)


def test_render_map_prefers_location_over_start_finish():
    event = Event(location="Hyde Park", start_location="Gate A", finish_location="Gate B")
    rendered = export_events._render_map(event)
    assert "Hyde Park" in rendered
    assert "maps-embed" in rendered


def test_render_map_falls_back_to_start_location():
    event = Event(location=None, start_location="Start Line", finish_location=None)
    assert "Start Line" in export_events._render_map(event)


# ---------------------------------------------------------------------------
# _render_distances
# ---------------------------------------------------------------------------

def test_render_distances_empty():
    assert "No distances listed" in export_events._render_distances(Event(distances=[]))


def test_render_distances_shows_race_type_label_when_present():
    event = Event(distances=[
        EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k")),
    ])
    rendered = export_events._render_distances(event)
    assert "<code>running_5k</code>" in rendered
    assert "£15" in rendered


def test_render_distances_shows_placeholder_when_no_race_type():
    event = Event(distances=[EventDistance(distance_text="Fun Run", price_text=None, race_type=None)])
    rendered = export_events._render_distances(event)
    assert "Fun Run" in rendered
    assert rendered.count('<span class="empty">&mdash;</span>') == 2  # price AND race type both missing


# ---------------------------------------------------------------------------
# _render_page_content
# ---------------------------------------------------------------------------

def test_render_page_content_empty_when_no_markdown():
    assert export_events._render_page_content(Event(raw_markdown=None)) == ""


def test_render_page_content_renders_markdown_to_html():
    event = Event(raw_markdown="# Heading\n\n**bold text**")
    rendered = export_events._render_page_content(event)
    assert "<h1>Heading</h1>" in rendered
    assert "<strong>bold text</strong>" in rendered
    assert 'class="page-content"' in rendered


# ---------------------------------------------------------------------------
# _render_event - escaping is the important thing here, since event fields
# come straight from arbitrary crawled pages.
# ---------------------------------------------------------------------------

def test_render_event_escapes_untrusted_name():
    event = Event(id=1, name="<script>alert(1)</script>", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = export_events._render_event(event)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_event_untitled_fallback():
    event = Event(id=42, name=None, sport=None, date_text=None, distances=[], raw_markdown=None)
    assert "(untitled event #42)" in export_events._render_event(event)


def test_render_event_omits_organiser_row_by_default():
    event = Event(id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    assert "Organiser" not in export_events._render_event(event)


def test_render_event_includes_organiser_row_when_given():
    event = Event(id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    rendered = export_events._render_event(event, organiser_name="Acme Runners")
    assert "<th>Organiser</th><td>Acme Runners</td>" in rendered


def test_render_event_includes_url_link_when_present():
    event = Event(id=1, name="Test", sport=None, date_text=None, distances=[], raw_markdown=None, url="https://example.com/event")
    rendered = export_events._render_event(event)
    assert 'href="https://example.com/event"' in rendered


def test_render_event_valid_has_no_invalid_badge_or_reason_row():
    event = Event(id=1, name="Real Event", sport="running", date_text=None, distances=[], raw_markdown=None, status=EventStatus.VALID)
    rendered = export_events._render_event(event)
    assert "badge-invalid" not in rendered
    assert "Invalid reason" not in rendered


def test_render_event_invalid_shows_badge_and_reason():
    # The reported case: a page that's just a redirect notice, e.g.
    # runthrough.co.uk/event/running-tours-copenhagen-marathon.
    event = Event(
        id=1, name="No event details available", sport="other", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.INVALID, invalid_reason="Page is just a redirect notice to an external site, no event details shown",
    )
    rendered = export_events._render_event(event)
    assert '<span class="badge badge-invalid">INVALID</span>' in rendered
    assert "Page is just a redirect notice to an external site, no event details shown" in rendered


def test_render_event_invalid_with_no_reason_shows_placeholder():
    event = Event(id=1, name="X", sport=None, date_text=None, distances=[], raw_markdown=None, status=EventStatus.INVALID, invalid_reason=None)
    rendered = export_events._render_event(event)
    assert '<tr><th>Invalid reason</th><td class="invalid-reason"><span class="empty">&mdash;</span></td></tr>' in rendered


# ---------------------------------------------------------------------------
# _render_organiser / _render_sport - pluralisation and nesting
# ---------------------------------------------------------------------------

def test_render_organiser_singular_count():
    event = Event(id=1, name="Solo Event", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = export_events._render_organiser("Acme Runners", [event])
    assert "(1 event)" in rendered
    assert "Solo Event" in rendered


def test_render_organiser_plural_count():
    events = [
        Event(id=1, name="Event A", sport=None, date_text=None, distances=[], raw_markdown=None),
        Event(id=2, name="Event B", sport=None, date_text=None, distances=[], raw_markdown=None),
    ]
    rendered = export_events._render_organiser("Acme Runners", events)
    assert "(2 events)" in rendered


def test_render_sport_counts_events_and_distances_across_groups():
    event = Event(id=1, name="Acme 5K", sport="running", date_text=None, distances=[], raw_markdown=None)
    distances_by_label = {
        "running_5k": [(event, "Acme Runners", EventDistance(distance_text="5K"))],
        "running_10k": [(event, "Acme Runners", EventDistance(distance_text="10K"))],
    }
    rendered = export_events._render_sport("running", distances_by_label)
    assert "(2 events across 2 distances)" in rendered
    assert "running_5k" in rendered
    assert "running_10k" in rendered


# ---------------------------------------------------------------------------
# _fetch_rows - checks the actual query it builds, without needing a real
# database: a fake Session.execute just captures the statement instead of
# running it, then we inspect its compiled SQL text directly.
# ---------------------------------------------------------------------------

class _CapturingSession:
    def __init__(self):
        self.captured_statement = None

    def execute(self, stmt):
        self.captured_statement = stmt
        return []


def test_fetch_rows_excludes_invalid_events():
    # No export format should have to remember to filter these out itself - see
    # the reported case, e.g. runthrough.co.uk's redirect-only "Running Tours" events.
    session = _CapturingSession()
    _real_fetch_rows(session)
    compiled = str(session.captured_statement)
    assert "events.status = " in compiled


def test_fetch_rows_still_filters_by_organiser_id_when_given():
    session = _CapturingSession()
    _real_fetch_rows(session, organiser_id=7)
    compiled = str(session.captured_statement)
    assert "events.organiser_id = " in compiled
    assert "events.status = " in compiled  # both filters present, not one replacing the other


def test_fetch_rows_can_request_invalid_status_instead():
    session = _CapturingSession()
    _real_fetch_rows(session, status=EventStatus.INVALID)
    # Render with literal_binds so the actual bound value (not just a ":status_1"
    # placeholder) shows up in the compiled text - confirms it's really INVALID, not
    # still defaulting to VALID.
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "'INVALID'" in compiled


def test_fetch_rows_status_none_omits_the_filter_entirely():
    session = _CapturingSession()
    _real_fetch_rows(session, status=None)
    compiled = str(session.captured_statement)
    # events.status still appears as a plain selected column either way - the thing
    # that must be absent is a WHERE clause filtering on it at all.
    assert "WHERE" not in compiled
    assert "events.status = " not in compiled


# ---------------------------------------------------------------------------
# Top-level exports - _fetch_rows and session_scope monkeypatched, so no real
# database is ever touched.
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

    monkeypatch.setattr(export_events, "session_scope", fake_session_scope)
    monkeypatch.setattr(export_events, "_fetch_rows", lambda session, organiser_id=None, status=None: sample_rows)


def test_export_csv_writes_header_and_rows(tmp_path):
    output_path = tmp_path / "events.csv"
    count = export_events.export_csv(output_path)

    assert count == 3
    with output_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 3
    assert rows[0]["name"] == "Acme 5K"
    assert rows[0]["organiser_name"] == "Acme Runners"
    assert rows[0]["distances"] == "5K [running_5k]: £15"
    # event2 has one categorised and one uncategorised distance - both show up.
    assert rows[1]["distances"] == "10K [running_10k]: £20; Fun Run"
    assert rows[0]["status"] == "valid"
    assert rows[0]["invalid_reason"] == ""


def test_export_csv_includes_invalid_event_status_and_reason(monkeypatch, tmp_path):
    invalid_event = Event(
        id=99, organiser_id=1, url="https://acme.example/redirect", name="No event details available",
        sport="other", status=EventStatus.INVALID,
        invalid_reason="Page is just a redirect notice to an external site, no event details shown",
        date_text=None, location=None, raw_markdown=None, distances=[],
    )
    monkeypatch.setattr(export_events, "_fetch_rows", lambda session, organiser_id=None, status=None: [(invalid_event, "Acme Runners")])

    export_events.export_csv(tmp_path / "invalid.csv")

    with (tmp_path / "invalid.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["status"] == "invalid"
    assert rows[0]["invalid_reason"] == "Page is just a redirect notice to an external site, no event details shown"


# ---------------------------------------------------------------------------
# export_invalid_events / status routing through _export_organiser_tree - each
# export must request the status it's actually meant to show, not just whatever
# _fetch_rows happens to default to.
# ---------------------------------------------------------------------------

def test_export_events_per_organiser_requests_valid_status(monkeypatch, tmp_path, sample_rows):
    captured = {}

    def fake_fetch_rows(session, organiser_id=None, status=None):
        captured["status"] = status
        return sample_rows

    monkeypatch.setattr(export_events, "_fetch_rows", fake_fetch_rows)
    export_events.export_events_per_organiser(tmp_path / "valid.html")

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

    monkeypatch.setattr(export_events, "_fetch_rows", fake_fetch_rows)

    total = export_events.export_invalid_events(tmp_path / "invalid.html")

    assert captured["status"] == EventStatus.INVALID
    assert total == 1
    html_text = (tmp_path / "invalid.html").read_text(encoding="utf-8")
    assert "<title>Invalid events</title>" in html_text
    assert "<h1>Invalid events</h1>" in html_text
    assert "No event details available" in html_text
    assert "Page is just a redirect notice to an external site, no event details shown" in html_text
    assert '<span class="badge badge-invalid">INVALID</span>' in html_text


def test_export_invalid_events_empty_when_none_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(export_events, "_fetch_rows", lambda session, organiser_id=None, status=None: [])

    total = export_events.export_invalid_events(tmp_path / "invalid.html")

    assert total == 0
    html_text = (tmp_path / "invalid.html").read_text(encoding="utf-8")
    assert "0 event(s) across 0 organiser(s)" in html_text


def test_export_events_per_organiser_groups_by_organiser(tmp_path):
    output_path = tmp_path / "per_organiser.html"
    total = export_events.export_events_per_organiser(output_path)

    assert total == 3
    html_text = output_path.read_text(encoding="utf-8")
    assert "Acme Runners" in html_text
    assert "Beta Multisport" in html_text
    assert "Acme 5K" in html_text
    assert "Beta Triathlon" in html_text
    assert "(2 events)" in html_text  # Acme Runners' count
    assert "(1 event)" in html_text  # Beta Multisport's count


def test_export_events_per_event_type_groups_by_sport_and_distance(tmp_path):
    output_path = tmp_path / "per_type.html"
    total = export_events.export_events_per_event_type(output_path)

    # 4 distance entries total: 5k, 10k, the uncategorised fun run, sprint triathlon.
    assert total == 4
    html_text = output_path.read_text(encoding="utf-8")
    assert "running_5k" in html_text
    assert "running_10k" in html_text
    assert "triathlon_sprint_triathlon" in html_text
    assert export_events._UNCATEGORISED_LABEL in html_text
    # Full event detail (not just a name) should appear under each distance node.
    assert "Acme Park" in html_text
    assert "Acme Runners" in html_text  # organiser shown here, unlike the per-organiser export


# ---------------------------------------------------------------------------
# CSS: a shared sibling file next to whichever HTML output is written, not
# inlined into a <style> block in every export.
# ---------------------------------------------------------------------------

def test_html_export_writes_css_as_sibling_file_not_inline(tmp_path):
    output_path = tmp_path / "per_organiser.html"
    export_events.export_events_per_organiser(output_path)

    html_text = output_path.read_text(encoding="utf-8")
    assert "<style>" not in html_text
    assert f'<link rel="stylesheet" href="{export_events._CSS_FILENAME}">' in html_text

    css_path = tmp_path / export_events._CSS_FILENAME
    assert css_path.exists()
    assert "details" in css_path.read_text(encoding="utf-8")


def test_css_file_written_alongside_each_distinct_output_directory(tmp_path):
    organiser_dir = tmp_path / "organiser"
    by_type_dir = tmp_path / "by_type"

    export_events.export_events_per_organiser(organiser_dir / "per_organiser.html")
    export_events.export_events_per_event_type(by_type_dir / "per_type.html")

    assert (organiser_dir / export_events._CSS_FILENAME).exists()
    assert (by_type_dir / export_events._CSS_FILENAME).exists()


def test_export_events_per_organiser_respects_organiser_id_filter(monkeypatch, tmp_path, sample_rows):
    # _fetch_rows is normally responsible for filtering by organiser_id - confirm the
    # export function actually threads organiser_id through to it rather than ignoring it.
    captured = {}

    def fake_fetch_rows(session, organiser_id=None, status=None):
        captured["organiser_id"] = organiser_id
        return [row for row in sample_rows if row[0].organiser_id == organiser_id] if organiser_id else sample_rows

    monkeypatch.setattr(export_events, "_fetch_rows", fake_fetch_rows)

    total = export_events.export_events_per_organiser(tmp_path / "filtered.html", organiser_id=2)

    assert captured["organiser_id"] == 2
    assert total == 1
