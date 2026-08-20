"""
Renders the `events` table as three self-contained HTML files, for browsing
what the pipeline has collected without needing `psql` open - see
csv_export.py for the flat-CSV equivalent, whose _fetch_rows is this
module's own query-building seam too (called via the `csv_export` module
object, not a bare imported name, so tests can monkeypatch
`csv_export._fetch_rows` and have it take effect here as well).

All three render an expand/collapse tree using plain <details>/<summary> (no
JS needed), just grouped/filtered differently:
- events_per_organiser.html: every VALID event, organiser -> its events ->
  each event's full detail, plus a Google Maps link and embedded map per
  event where a location is known.
- events_per_event_type.html: every VALID event, sport -> standardised race
  type (see race_types.py - "marathon", "10_k", "10_m", ...) -> the events
  offering that distance, for browsing "show me every marathon" rather than
  per-organiser. A distance with no resolved race type (see
  EventDistance.race_type_id) still shows up, grouped under "uncategorised".
- events_invalid.html: every INVALID event (see EventStatus - a crawled URL
  that turned out to be a redirect notice, dead page, etc. with no real event
  content), organiser -> events, same detail view as events_per_organiser.html
  - for debugging what the LLM flagged as invalid and why, without wading
  through every genuinely valid event to find them.
"""

import html
from datetime import datetime
from enum import Enum as PyEnum
from pathlib import Path
from urllib.parse import quote_plus

import markdown

from services.common.db import session_scope
from services.common.models import Event, EventDistance, EventLifecycle, EventStatus

from . import csv_export

__all__ = ["export_events_per_organiser", "export_invalid_events", "export_events_per_event_type"]

_UNCATEGORISED_SPORT = "uncategorised"
_UNCATEGORISED_LABEL = "(uncategorised)"

# Fields shown in the HTML event detail tree, in display order. Distances/occurrences are
# rendered separately (see _render_distances/_render_occurrences) since they're lists, not
# a single scalar value. Status/lifecycle_status are rendered separately too (see
# _render_event's INVALID/CANCELLED/POSTPONED badges and their own conditional detail
# rows) rather than appearing as plain rows here - lifecycle_text follows invalid_reason's
# own "only shown when there's actually something to say" pattern, not registration_text's
# "always shown" one, since SCHEDULED (the overwhelmingly common case) never has one.
DETAIL_FIELDS = [
    ("Organiser ID", "organiser_id"),
    ("Sport", "sport"),
    ("Date", "date_text"),
    ("Recurs", "occurrence"),
    ("Recurs on", "occurrence_weekdays"),
    ("Recurs at", "occurrence_time"),
    ("Recurring from", "occurrence_starts_on"),
    ("Recurring until", "occurrence_ends_on"),
    ("Location", "location"),
    ("Start location", "start_location"),
    ("Finish location", "finish_location"),
    ("Age restriction", "age_restriction_text"),
    ("Registration", "registration_status"),
    ("Registration detail", "registration_text"),
    ("Registration opens", "registration_opens_at"),
    ("Registration closes", "registration_closes_at"),
    ("Original summary", "summary"),
    ("Alternative summary", "summary_alt"),
    ("Summary of summary", "summary_short"),
    ("First seen", "first_seen_at"),
    ("Last seen", "last_seen_at"),
    ("Last crawled", "last_crawled_at"),
]


def _maps_link_url(location: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(location)}"


def _maps_embed_url(location: str) -> str:
    # Undocumented but widely-used no-API-key embed form - fine for a local/internal tool.
    return f"https://www.google.com/maps?q={quote_plus(location)}&output=embed"


def _render_map(event: Event) -> str:
    # Prefer the event's own geocoded coordinates (see geocoding_client.py, populated once
    # per crawl) when available - a real point rather than a text search Google has to
    # resolve itself, and the same coordinates a "near me" query would filter on, so what's
    # shown here is exactly what the database actually thinks this event's location is.
    if event.latitude is not None and event.longitude is not None:
        coords = f"{event.latitude},{event.longitude}"
        return f"""
        <p><a class="maps-link" href="https://www.google.com/maps/search/?api=1&query={coords}" target="_blank" rel="noopener">Open in Google Maps &#8599;</a></p>
        <iframe class="maps-embed" src="https://www.google.com/maps?q={coords}&output=embed" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
        """

    location = event.location or event.start_location or event.finish_location
    if not location:
        return '<p class="no-map">No location available.</p>'

    loc = html.escape(location, quote=True)
    return f"""
    <p><a class="maps-link" href="{_maps_link_url(location)}" target="_blank" rel="noopener">Open "{loc}" in Google Maps &#8599;</a></p>
    <iframe class="maps-embed" src="{_maps_embed_url(location)}" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
    """


def _render_distances(event: Event) -> str:
    if not event.distances:
        return '<p class="empty">No distances listed.</p>'

    rows = []
    for d in event.distances:
        price_html = html.escape(d.price_text) if d.price_text else '<span class="empty">&mdash;</span>'
        race_type_html = (
            f'<code>{html.escape(d.race_type.label)}</code>' if d.race_type else '<span class="empty">&mdash;</span>'
        )
        rows.append(
            f"<tr><td>{html.escape(d.distance_text)}</td><td>{price_html}</td><td>{race_type_html}</td></tr>"
        )
    return (
        '<table class="distances"><thead><tr><th>Distance</th><th>Price</th>'
        f'<th>Race type</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _render_occurrences(event: Event) -> str:
    """Same shape as _render_distances - empty for a one-off event (no individually-listed
    dates at all) or an unbounded recurrence (see "Recurs on"/"Recurs at" instead, rendered
    as plain DETAIL_FIELDS rows) - only populated for a bounded/enumerated one (see
    models.py's Occurrence docstring)."""
    if not event.occurrences:
        return '<p class="empty">No specific dates listed.</p>'

    rows = []
    for o in event.occurrences:
        time_html = html.escape(o.time_text) if o.time_text else '<span class="empty">&mdash;</span>'
        price_html = html.escape(o.price_text) if o.price_text else '<span class="empty">&mdash;</span>'
        rows.append(f"<tr><td>{html.escape(o.date_text or '')}</td><td>{time_html}</td><td>{price_html}</td></tr>")
    return (
        '<table class="distances"><thead><tr><th>Date</th><th>Time</th>'
        f'<th>Price</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _format_detail_value(value) -> str:
    """Shared by every DETAIL_FIELDS row below - str(value) alone renders a couple of real
    shapes awkwardly: an Enum's default str() is "Occurrence.WEEKLY", not its own value
    ("weekly"), and a list reads as Python's own repr rather than a plain line of text."""
    if isinstance(value, PyEnum):
        return value.value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _render_page_content(event: Event) -> str:
    """Nested, collapsed-by-default section rendering `raw_markdown` as real HTML (option 2: convert server-side, no client JS)."""
    if not event.raw_markdown:
        return ""

    rendered = markdown.markdown(event.raw_markdown, extensions=["extra", "sane_lists"])
    return f"""
          <details class="page-content">
            <summary>Page content</summary>
            <div class="markdown-body">{rendered}</div>
          </details>"""


def _render_event(event: Event, organiser_name: str | None = None) -> str:
    """
    Renders one event's full detail block - fields table, distances, map, raw
    page content. Shared by both HTML exports: events_per_organiser.html calls
    this with no organiser_name (it's already the enclosing group there);
    events_per_event_type.html passes it, since an event's organiser isn't
    otherwise implied by "grouped under this distance".
    """
    name = html.escape(event.name or f"(untitled event #{event.id})")
    badges = "".join(
        f'<span class="badge">{html.escape(v)}</span>'
        for v in (event.sport, event.date_text)
        if v
    )
    if event.status == EventStatus.INVALID:
        badges += '<span class="badge badge-invalid">INVALID</span>'
    if event.lifecycle_status == EventLifecycle.CANCELLED:
        badges += '<span class="badge badge-cancelled">CANCELLED</span>'
    elif event.lifecycle_status == EventLifecycle.POSTPONED:
        badges += '<span class="badge badge-postponed">POSTPONED</span>'

    rows = []
    if organiser_name is not None:
        rows.append(f"<tr><th>Organiser</th><td>{html.escape(organiser_name)}</td></tr>")
    if event.status == EventStatus.INVALID:
        reason_html = html.escape(event.invalid_reason) if event.invalid_reason else '<span class="empty">&mdash;</span>'
        rows.append(f'<tr><th>Invalid reason</th><td class="invalid-reason">{reason_html}</td></tr>')
    if event.lifecycle_status != EventLifecycle.SCHEDULED:
        lifecycle_html = html.escape(event.lifecycle_text) if event.lifecycle_text else '<span class="empty">&mdash;</span>'
        rows.append(f'<tr><th>Lifecycle detail</th><td class="lifecycle-detail">{lifecycle_html}</td></tr>')
    for label, attr in DETAIL_FIELDS:
        value = getattr(event, attr)
        value_html = html.escape(_format_detail_value(value)) if value else '<span class="empty">&mdash;</span>'
        rows.append(f"<tr><th>{html.escape(label)}</th><td>{value_html}</td></tr>")
    if event.url:
        rows.append(
            f'<tr><th>URL</th><td><a href="{html.escape(event.url, quote=True)}" target="_blank" rel="noopener">{html.escape(event.url)}</a></td></tr>'
        )

    return f"""
        <details class="event">
          <summary>{name}{badges}</summary>
          <div class="event-body">
            <table class="fields">{"".join(rows)}</table>
            <div class="distances-section">
              <h4>Distances</h4>
              {_render_distances(event)}
            </div>
            <div class="distances-section">
              <h4>Specific dates</h4>
              {_render_occurrences(event)}
            </div>
            <div class="map">{_render_map(event)}</div>
            {_render_page_content(event)}
          </div>
        </details>"""


def _render_organiser(organiser_id: int, organiser_name: str, events: list[Event]) -> str:
    name = html.escape(organiser_name)
    events_html = "".join(_render_event(e) for e in events)
    return f"""
      <details class="organiser">
        <summary>{name} <span class="org-id">(ID {organiser_id})</span> <span class="count">({len(events)} event{"s" if len(events) != 1 else ""})</span></summary>
        <div class="events">{events_html}</div>
      </details>"""


def _render_distance_group(label: str, entries: list[tuple[Event, str, EventDistance]]) -> str:
    # Reuses _render_event (same function events_per_organiser.html renders with) rather
    # than a second, parallel "what does an event look like" renderer - each entry's
    # distance itself isn't singled out here since _render_event's own distances table
    # already shows all of that event's distances, this one included.
    events_html = "".join(_render_event(event, organiser_name) for event, organiser_name, _distance in entries)
    return f"""
      <details class="distance">
        <summary><code>{html.escape(label)}</code> <span class="count">({len(entries)} event{"s" if len(entries) != 1 else ""})</span></summary>
        <div class="events">{events_html}</div>
      </details>"""


def _render_sport(sport_label: str, distances_by_label: dict[str, list]) -> str:
    total = sum(len(entries) for entries in distances_by_label.values())
    distances_html = "".join(
        _render_distance_group(label, entries) for label, entries in sorted(distances_by_label.items())
    )
    return f"""
      <details class="sport">
        <summary>{html.escape(sport_label)} <span class="count">({total} event{"s" if total != 1 else ""} across {len(distances_by_label)} distance{"s" if len(distances_by_label) != 1 else ""})</span></summary>
        <div class="distances-by-sport">{distances_html}</div>
      </details>"""


_CSS = """
  :root {
    color-scheme: light dark;
    --bg: #f7f7f9;
    --panel: #ffffff;
    --border: #dcdfe4;
    --text: #1c1f24;
    --muted: #6b7280;
    --accent: #2563eb;
  }
  * { box-sizing: border-box; }
  body {
    margin: 2rem auto;
    max-width: 960px;
    padding: 0 1.5rem 4rem;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.45;
  }
  h1 { margin-bottom: 0.1rem; }
  .meta { color: var(--muted); margin-top: 0; margin-bottom: 2rem; font-size: 0.9rem; }

  details { margin: 0.4rem 0; }
  summary {
    cursor: pointer;
    list-style: none;
    user-select: none;
  }
  summary::-webkit-details-marker { display: none; }
  summary::before {
    content: "\\25B8";
    display: inline-block;
    width: 1em;
    margin-right: 0.35em;
    color: var(--accent);
    transition: transform 0.1s ease;
  }
  details[open] > summary::before { transform: rotate(90deg); }

  details.organiser, details.sport {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
  }
  details.organiser > summary, details.sport > summary {
    font-size: 1.15rem;
    font-weight: 600;
  }
  .count { font-weight: 400; color: var(--muted); font-size: 0.9rem; }
  .org-id { font-weight: 400; color: var(--muted); font-size: 0.85rem; }
  .events, .distances-by-sport {
    margin-top: 0.6rem;
    padding-left: 1.4rem;
    border-left: 2px solid var(--border);
  }

  details.event, details.distance {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.8rem;
    margin: 0.5rem 0;
  }
  details.event > summary, details.distance > summary { font-weight: 500; }
  .badge {
    display: inline-block;
    margin-left: 0.5rem;
    padding: 0.05rem 0.5rem;
    border-radius: 999px;
    background: #e8edff;
    color: var(--accent);
    font-size: 0.78rem;
    font-weight: 500;
    vertical-align: middle;
  }
  .badge-invalid { background: #fde2e2; color: #c81e1e; }
  .invalid-reason { color: #c81e1e; }
  .badge-cancelled { background: #fde2e2; color: #c81e1e; }
  .badge-postponed { background: #fdf1d6; color: #96660a; }
  .lifecycle-detail { color: #c81e1e; }

  .event-body { margin-top: 0.6rem; }
  table.fields { width: 100%; border-collapse: collapse; margin-bottom: 0.8rem; }
  table.fields th, table.fields td {
    text-align: left;
    vertical-align: top;
    padding: 0.3rem 0.6rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
  }
  table.fields th { width: 9.5rem; color: var(--muted); font-weight: 500; }
  .empty { color: var(--muted); }

  .distances-section { margin-bottom: 0.8rem; }
  .distances-section h4 {
    margin: 0 0 0.3rem;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  table.distances { width: 100%; max-width: 34rem; border-collapse: collapse; }
  table.distances th, table.distances td {
    text-align: left;
    padding: 0.25rem 0.6rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
  }
  table.distances th { color: var(--muted); font-weight: 500; }
  table.distances code {
    background: var(--bg);
    border-radius: 3px;
    padding: 0.1em 0.35em;
    font-size: 0.88em;
  }

  .maps-link { color: var(--accent); text-decoration: none; font-size: 0.92rem; }
  .maps-link:hover { text-decoration: underline; }
  .maps-embed {
    width: 100%;
    height: 280px;
    border: 0;
    border-radius: 6px;
    margin-top: 0.3rem;
  }
  .no-map { color: var(--muted); font-size: 0.9rem; }

  details.page-content {
    margin-top: 0.8rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.8rem;
  }
  details.page-content > summary { font-size: 0.92rem; color: var(--muted); }
  .markdown-body {
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px solid var(--border);
    font-size: 0.92rem;
    max-width: 100%;
    overflow-x: auto;
  }
  .markdown-body h1, .markdown-body h2, .markdown-body h3,
  .markdown-body h4, .markdown-body h5, .markdown-body h6 {
    margin: 1em 0 0.4em;
    line-height: 1.3;
  }
  .markdown-body h1 { font-size: 1.3rem; }
  .markdown-body h2 { font-size: 1.15rem; }
  .markdown-body h3 { font-size: 1.05rem; }
  .markdown-body p { margin: 0.5em 0; }
  .markdown-body a { color: var(--accent); }
  .markdown-body img { max-width: 100%; border-radius: 4px; }
  .markdown-body ul, .markdown-body ol { padding-left: 1.4em; }
  .markdown-body blockquote {
    margin: 0.5em 0;
    padding: 0.2em 0.9em;
    border-left: 3px solid var(--border);
    color: var(--muted);
  }
  .markdown-body code {
    background: var(--bg);
    border-radius: 3px;
    padding: 0.1em 0.35em;
    font-size: 0.88em;
  }
  .markdown-body pre {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.7em 0.9em;
    overflow-x: auto;
  }
  .markdown-body pre code { background: none; padding: 0; }
  .markdown-body table { border-collapse: collapse; margin: 0.6em 0; }
  .markdown-body th, .markdown-body td {
    border: 1px solid var(--border);
    padding: 0.3em 0.6em;
  }
  .markdown-body hr { border: none; border-top: 1px solid var(--border); margin: 1em 0; }

  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16181d;
      --panel: #1e2127;
      --border: #33373f;
      --text: #e6e8eb;
      --muted: #9aa1ab;
    }
    .badge { background: #22314f; }
    .badge-invalid { background: #4a1f1f; color: #ff8a8a; }
    .invalid-reason { color: #ff8a8a; }
    .badge-cancelled { background: #4a1f1f; color: #ff8a8a; }
    .badge-postponed { background: #4a3a1f; color: #f0c674; }
    .lifecycle-detail { color: #ff8a8a; }
  }
"""

# Shared by all three HTML exports - a plain sibling file next to whichever html
# output_path is currently being written (see _write_css), rather than inlined into
# every <style> block. All three normally land in the same directory
# (cli.DEFAULT_OUTPUT), so this ends up written once and just re-read by the browser for
# each; --output/--output-by-type/--output-invalid pointing elsewhere still each get
# their own copy alongside them, since a <link> is only ever relative to its own file.
_CSS_FILENAME = "events_style.css"


def _write_css(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / _CSS_FILENAME).write_text(_CSS, encoding="utf-8")


def _export_organiser_tree(output_path: Path, organiser_id: int | None, status: EventStatus | None, title: str) -> int:
    """
    Shared by export_events_per_organiser (VALID events, the normal export) and
    export_invalid_events (INVALID events, for debugging what the LLM flagged and why)
    - same organiser -> events -> details tree either way, just filtered to a different
    status and titled differently. Returns the event count.
    """
    with session_scope() as session:
        rows = csv_export._fetch_rows(session, organiser_id, status=status)

        # Group in Python (rather than a GROUP BY query) - session_scope's connection is
        # closed by the time we render, and we need the full Event objects, not aggregates.
        grouped: dict[int, dict] = {}
        for event, organiser_name in rows:
            group = grouped.setdefault(event.organiser_id, {"name": organiser_name, "events": []})
            group["events"].append(event)

    _write_css(output_path.parent)

    total = sum(len(g["events"]) for g in grouped.values())
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    organisers_html = "".join(
        _render_organiser(organiser_id, g["name"], g["events"]) for organiser_id, g in grouped.items()
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{_CSS_FILENAME}">
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="meta">{total} event(s) across {len(grouped)} organiser(s) &middot; generated {generated_at}</p>
  {organisers_html}
</body>
</html>
"""
    output_path.write_text(doc, encoding="utf-8")
    print(f"wrote {total} event(s) across {len(grouped)} organiser(s) to {output_path}")
    return total


def export_events_per_organiser(output_path: Path, organiser_id: int | None = None) -> int:
    """Writes the organiser -> events -> details tree to a single HTML file (VALID events only). Returns the event count."""
    return _export_organiser_tree(output_path, organiser_id, EventStatus.VALID, "Events per organiser")


def export_invalid_events(output_path: Path, organiser_id: int | None = None) -> int:
    """
    Writes the organiser -> events -> details tree for INVALID events only (see
    EventStatus - a crawled URL that turned out to be a redirect notice, dead page, etc.
    with no real event content). Useful for debugging what the LLM flagged as invalid and
    why (each event's "Invalid reason" row/badge - see _render_event) without wading
    through every genuinely valid event to find them. Returns the event count.
    """
    return _export_organiser_tree(output_path, organiser_id, EventStatus.INVALID, "Invalid events")


def export_events_per_event_type(output_path: Path, organiser_id: int | None = None) -> int:
    """
    Writes the sport -> race type -> events tree to a single HTML file (see
    race_types.py for how each distance resolves to a standardised race type -
    "marathon", "10_k", "10_m", etc). A distance with no resolved race type
    still appears, grouped under "uncategorised" rather than silently dropped.
    Returns the number of (event, distance) entries written.
    """
    with session_scope() as session:
        rows = csv_export._fetch_rows(session, organiser_id)

        # sport label -> race type label -> [(event, organiser_name, distance), ...]
        grouped: dict[str, dict[str, list[tuple[Event, str, EventDistance]]]] = {}
        for event, organiser_name in rows:
            for d in event.distances:
                if d.race_type:
                    sport_label = d.race_type.sport.value
                    type_label = d.race_type.label
                else:
                    sport_label = _UNCATEGORISED_SPORT
                    type_label = _UNCATEGORISED_LABEL
                grouped.setdefault(sport_label, {}).setdefault(type_label, []).append((event, organiser_name, d))

    _write_css(output_path.parent)

    total = sum(len(entries) for distances in grouped.values() for entries in distances.values())
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    sports_html = "".join(_render_sport(sport, distances) for sport, distances in sorted(grouped.items()))

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Events per race type</title>
<link rel="stylesheet" href="{_CSS_FILENAME}">
</head>
<body>
  <h1>Events per race type</h1>
  <p class="meta">{total} distance entr{"y" if total == 1 else "ies"} across {len(grouped)} sport(s) &middot; generated {generated_at}</p>
  {sports_html}
</body>
</html>
"""
    output_path.write_text(doc, encoding="utf-8")
    print(f"wrote {total} distance entries across {len(grouped)} sport(s) to {output_path}")
    return total
