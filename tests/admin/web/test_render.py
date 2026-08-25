"""
Unit tests for admin/web/render.py.

Every function under test is pure (rows/organisers in, an HTML string out) -
no database, no monkeypatching, just hand-built model instances. This is a
step simpler than the old admin/export/test_html_export.py it replaces:
render.py doesn't call session_scope or queries.py itself any more (see
render.py's own module docstring) - that's app.py's job now - so there's no
seam left here that needs faking.
"""

from datetime import datetime, timezone

import pytest

from services.admin.web import render
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
    url = render._maps_link_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "The+Avenue%2C+Southampton" in url


def test_maps_embed_url_encodes_location():
    url = render._maps_embed_url("The Avenue, Southampton")
    assert url.startswith("https://www.google.com/maps?q=")
    assert url.endswith("&output=embed")


# ---------------------------------------------------------------------------
# _render_map
# ---------------------------------------------------------------------------

def test_render_map_no_location():
    event = Event(location=None, start_location=None, finish_location=None)
    assert "No location available" in render._render_map(event)


def test_render_map_prefers_location_over_start_finish():
    event = Event(location="Hyde Park", start_location="Gate A", finish_location="Gate B")
    rendered = render._render_map(event)
    assert "Hyde Park" in rendered
    assert "maps-embed" in rendered


def test_render_map_falls_back_to_start_location():
    event = Event(location=None, start_location="Start Line", finish_location=None)
    assert "Start Line" in render._render_map(event)


def test_render_map_prefers_stored_coordinates_over_text_location():
    event = Event(location="Hyde Park", latitude=51.5073, longitude=-0.1657)
    rendered = render._render_map(event)
    assert "51.5073,-0.1657" in rendered
    assert "Hyde Park" not in rendered  # coordinates win outright, not a fallback hint


def test_render_map_falls_back_to_text_when_not_yet_geocoded():
    event = Event(location="Hyde Park", latitude=None, longitude=None)
    rendered = render._render_map(event)
    assert "Hyde Park" in rendered


# ---------------------------------------------------------------------------
# _render_distances
# ---------------------------------------------------------------------------

def test_render_distances_empty():
    assert "No distances listed" in render._render_distances(Event(distances=[]))


def test_render_distances_shows_race_type_label_when_present():
    event = Event(distances=[
        EventDistance(distance_text="5K", price_text="£15", race_type=RaceType(label="running_5k", sport=Sport.RUNNING, distance_category="5k")),
    ])
    rendered = render._render_distances(event)
    assert "<code>running_5k</code>" in rendered
    assert "£15" in rendered


def test_render_distances_shows_placeholder_when_no_race_type():
    event = Event(distances=[EventDistance(distance_text="Fun Run", price_text=None, race_type=None)])
    rendered = render._render_distances(event)
    assert "Fun Run" in rendered
    assert rendered.count('<span class="empty">&mdash;</span>') == 2  # price AND race type both missing


# ---------------------------------------------------------------------------
# _render_occurrences
# ---------------------------------------------------------------------------

def test_render_occurrences_empty():
    assert "No specific dates listed" in render._render_occurrences(Event(occurrences=[]))


def test_render_occurrences_shows_date_time_and_price():
    event = Event(occurrences=[
        EventOccurrence(
            starts_at=datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc),
            date_text="18th Aug 2026", time_text="06:00 PM", price_text="£10.00",
        ),
    ])
    rendered = render._render_occurrences(event)
    assert "18th Aug 2026" in rendered
    assert "06:00 PM" in rendered
    assert "£10.00" in rendered


def test_render_occurrences_shows_placeholder_when_no_time_or_price():
    event = Event(occurrences=[
        EventOccurrence(starts_at=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc), date_text="20th Aug 2026"),
    ])
    rendered = render._render_occurrences(event)
    assert "20th Aug 2026" in rendered
    assert rendered.count('<span class="empty">&mdash;</span>') == 2  # time AND price both missing


# ---------------------------------------------------------------------------
# _format_detail_value
# ---------------------------------------------------------------------------

def test_format_detail_value_uses_enum_value_not_default_str():
    assert render._format_detail_value(Occurrence.WEEKLY) == "weekly"


def test_format_detail_value_joins_a_list_with_commas():
    assert render._format_detail_value(["sat", "sun"]) == "sat, sun"


def test_format_detail_value_passes_through_plain_values():
    assert render._format_detail_value("plain string") == "plain string"


# ---------------------------------------------------------------------------
# _render_page_content
# ---------------------------------------------------------------------------

def test_render_page_content_empty_when_no_markdown():
    assert render._render_page_content(Event(raw_markdown=None)) == ""


def test_render_page_content_renders_markdown_to_html():
    event = Event(raw_markdown="# Heading\n\n**bold text**")
    rendered = render._render_page_content(event)
    assert "<h1>Heading</h1>" in rendered
    assert "<strong>bold text</strong>" in rendered
    assert 'class="page-content"' in rendered


# ---------------------------------------------------------------------------
# _render_event - escaping is the important thing here, since event fields
# come straight from arbitrary crawled pages.
# ---------------------------------------------------------------------------

def test_render_event_escapes_untrusted_name():
    event = Event(id=1, name="<script>alert(1)</script>", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = render._render_event(event)
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_render_event_untitled_fallback():
    event = Event(id=42, name=None, sport=None, date_text=None, distances=[], raw_markdown=None)
    assert "(untitled event #42)" in render._render_event(event)


def test_render_event_omits_organiser_row_by_default():
    event = Event(id=1, organiser_id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    assert "<th>Organiser</th>" not in render._render_event(event)


def test_render_event_includes_organiser_row_when_given():
    event = Event(id=1, organiser_id=1, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    rendered = render._render_event(event, organiser_name="Acme Runners")
    assert "<th>Organiser</th><td>Acme Runners</td>" in rendered


def test_render_event_always_shows_organiser_id():
    event = Event(id=1, organiser_id=42, name="Test Event", sport="running", date_text=None, distances=[], raw_markdown=None)
    rendered = render._render_event(event)
    assert "<th>Organiser ID</th><td>42</td>" in rendered


def test_render_event_includes_url_link_when_present():
    event = Event(id=1, name="Test", sport=None, date_text=None, distances=[], raw_markdown=None, url="https://example.com/event")
    rendered = render._render_event(event)
    assert 'href="https://example.com/event"' in rendered


def test_render_event_valid_has_no_invalid_badge_or_reason_row():
    event = Event(id=1, name="Real Event", sport="running", date_text=None, distances=[], raw_markdown=None, status=EventStatus.VALID)
    rendered = render._render_event(event)
    assert "badge-invalid" not in rendered
    assert "Invalid reason" not in rendered


def test_render_event_invalid_shows_badge_and_reason():
    event = Event(
        id=1, name="No event details available", sport="other", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.INVALID, invalid_reason="Page is just a redirect notice to an external site, no event details shown",
    )
    rendered = render._render_event(event)
    assert '<span class="badge badge-invalid">INVALID</span>' in rendered
    assert "Page is just a redirect notice to an external site, no event details shown" in rendered


def test_render_event_invalid_with_no_reason_shows_placeholder():
    event = Event(id=1, name="X", sport=None, date_text=None, distances=[], raw_markdown=None, status=EventStatus.INVALID, invalid_reason=None)
    rendered = render._render_event(event)
    assert '<tr><th>Invalid reason</th><td class="invalid-reason"><span class="empty">&mdash;</span></td></tr>' in rendered


def test_render_event_scheduled_has_no_lifecycle_badge_or_row():
    event = Event(
        id=1, name="Real Event", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.SCHEDULED,
    )
    rendered = render._render_event(event)
    assert "badge-cancelled" not in rendered
    assert "badge-postponed" not in rendered
    assert "Lifecycle detail" not in rendered


def test_render_event_cancelled_shows_badge_and_detail():
    event = Event(
        id=1, name="Storm-hit 10k", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.CANCELLED,
        lifecycle_text="Cancelled due to adverse weather",
    )
    rendered = render._render_event(event)
    assert '<span class="badge badge-cancelled">CANCELLED</span>' in rendered
    assert "Cancelled due to adverse weather" in rendered


def test_render_event_postponed_shows_badge_and_detail():
    event = Event(
        id=1, name="Some 10k", sport="running", date_text=None, distances=[], raw_markdown=None,
        status=EventStatus.VALID, lifecycle_status=EventLifecycle.POSTPONED,
        lifecycle_text="Postponed to 12 September 2026",
    )
    rendered = render._render_event(event)
    assert '<span class="badge badge-postponed">POSTPONED</span>' in rendered
    assert "Postponed to 12 September 2026" in rendered


# ---------------------------------------------------------------------------
# _render_organiser / _render_sport - pluralisation and nesting
# ---------------------------------------------------------------------------

def test_render_organiser_singular_count():
    event = Event(id=1, name="Solo Event", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = render._render_organiser(1, "Acme Runners", [event])
    assert "(1 event)" in rendered
    assert "Solo Event" in rendered


def test_render_organiser_plural_count():
    events = [
        Event(id=1, name="Event A", sport=None, date_text=None, distances=[], raw_markdown=None),
        Event(id=2, name="Event B", sport=None, date_text=None, distances=[], raw_markdown=None),
    ]
    rendered = render._render_organiser(1, "Acme Runners", events)
    assert "(2 events)" in rendered


def test_render_organiser_header_shows_organiser_id():
    event = Event(id=1, name="Solo Event", sport=None, date_text=None, distances=[], raw_markdown=None)
    rendered = render._render_organiser(7, "Acme Runners", [event])
    assert '<span class="org-id">(ID 7)</span>' in rendered


def test_render_sport_counts_events_and_distances_across_groups():
    event = Event(id=1, name="Acme 5K", sport="running", date_text=None, distances=[], raw_markdown=None)
    distances_by_label = {
        "running_5k": [(event, "Acme Runners", EventDistance(distance_text="5K"))],
        "running_10k": [(event, "Acme Runners", EventDistance(distance_text="10K"))],
    }
    rendered = render._render_sport("running", distances_by_label)
    assert "(2 events across 2 distances)" in rendered
    assert "running_5k" in rendered
    assert "running_10k" in rendered


# ---------------------------------------------------------------------------
# Page-level renders - render_index / render_organiser_tree / render_event_type_tree
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_rows():
    """Two organisers, three events."""
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


def test_render_organiser_tree_groups_by_organiser(sample_rows):
    html_text = render.render_organiser_tree(sample_rows, title="Events per organiser", active_path="/events")

    assert "<title>Events per organiser</title>" in html_text
    assert "Acme Runners" in html_text
    assert "Beta Multisport" in html_text
    assert "Acme 5K" in html_text
    assert "Beta Triathlon" in html_text
    assert "(2 events)" in html_text  # Acme Runners' count
    assert "(1 event)" in html_text  # Beta Multisport's count
    assert '<span class="org-id">(ID 1)</span>' in html_text
    assert '<span class="org-id">(ID 2)</span> <span class="count">(1 event)</span>' in html_text
    assert "2 event(s) across" not in html_text  # sanity: not accidentally the wrong total
    assert "3 event(s) across 2 organiser(s)" in html_text


def test_render_organiser_tree_empty(sample_rows):
    html_text = render.render_organiser_tree([], title="Invalid events", active_path="/events/invalid")
    assert "0 event(s) across 0 organiser(s)" in html_text
    assert "<h1>Invalid events</h1>" in html_text


def test_render_organiser_tree_invalid_shows_reason():
    invalid_event = Event(
        id=5, organiser_id=1, name="No event details available", sport="other",
        status=EventStatus.INVALID, invalid_reason="Page is just a redirect notice to an external site, no event details shown",
        date_text=None, location=None, raw_markdown=None, distances=[], url="https://acme.example/redirect",
    )
    html_text = render.render_organiser_tree(
        [(invalid_event, "Acme Runners")], title="Invalid events", active_path="/events/invalid"
    )
    assert "No event details available" in html_text
    assert "Page is just a redirect notice to an external site, no event details shown" in html_text
    assert '<span class="badge badge-invalid">INVALID</span>' in html_text


def test_render_organiser_tree_includes_filter_form_with_current_values():
    html_text = render.render_organiser_tree([], title="Events per organiser", active_path="/events", organiser_id=7, search="marathon")
    assert 'value="7"' in html_text
    assert 'value="marathon"' in html_text
    assert 'class="clear-filters"' in html_text  # shown because a filter is active


def test_render_organiser_tree_no_clear_link_when_unfiltered():
    html_text = render.render_organiser_tree([], title="Events per organiser", active_path="/events")
    assert 'class="clear-filters"' not in html_text


def test_render_event_type_tree_groups_by_sport_and_distance(sample_rows):
    html_text = render.render_event_type_tree(sample_rows)

    # 4 distance entries total: 5k, 10k, the uncategorised fun run, sprint triathlon.
    assert "4 distance entries across" in html_text
    assert "running_5k" in html_text
    assert "running_10k" in html_text
    assert "triathlon_sprint_triathlon" in html_text
    assert render._UNCATEGORISED_LABEL in html_text
    assert "Acme Park" in html_text
    assert "Acme Runners" in html_text  # organiser shown here, unlike the per-organiser tree


def test_render_index_lists_organisers_with_counts():
    html_text = render.render_index([(1, "Acme Runners", 2), (2, "Beta Multisport", 1)])
    assert "Acme Runners" in html_text
    assert "Beta Multisport" in html_text
    assert 'href="/events?organiser_id=1"' in html_text
    assert "2 organiser(s), 3 valid event(s) total" in html_text


def test_render_nav_marks_active_page():
    html_text = render.render_index([])
    assert '<a href="/" class="active">Organisers</a>' in html_text
    assert '<a href="/events">Events</a>' in html_text
