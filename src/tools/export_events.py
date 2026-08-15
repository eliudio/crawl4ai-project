"""
Dumps the `events` table (joined with its organiser) to CSV, or to a single
self-contained HTML file, for ad-hoc inspection of what the pipeline has
collected so far without needing `psql` open.

The HTML export renders an expand/collapse tree - organiser -> its events ->
each event's full detail - using plain <details>/<summary> (no JS needed),
plus a Google Maps link and embedded map per event where a location is known.

Usage:
    python -m tools.export_events --format csv
    python -m tools.export_events --format html
    python -m tools.export_events --format html --output out.html
    python -m tools.export_events --organiser-id 3
"""

import argparse
import csv
import html
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import markdown
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from services.db import session_scope
from services.models import Event, Organiser

DEFAULT_OUTPUT = {
    "csv": Path(__file__).parent / "data" / "events_export.csv",
    "html": Path(__file__).parent / "data" / "events_export.html",
}

CSV_FIELDNAMES = [
    "id",
    "organiser_id",
    "organiser_name",
    "name",
    "sport",
    "date_text",
    "location",
    "start_location",
    "finish_location",
    "distances",
    "age_restriction_text",
    "url",
    "summary",
    "first_seen_at",
    "last_seen_at",
    "last_crawled_at",
]

# Fields shown in the HTML event detail tree, in display order. Distances are rendered
# separately (see _render_distances) since they're a list, not a single scalar value.
DETAIL_FIELDS = [
    ("Sport", "sport"),
    ("Date", "date_text"),
    ("Location", "location"),
    ("Start location", "start_location"),
    ("Finish location", "finish_location"),
    ("Age restriction", "age_restriction_text"),
    ("Summary", "summary"),
    ("First seen", "first_seen_at"),
    ("Last seen", "last_seen_at"),
    ("Last crawled", "last_crawled_at"),
]


def _fetch_rows(session, organiser_id: int | None = None):
    """Every event joined with its organiser's name, grouped for display by organiser then event id."""
    stmt = (
        select(Event, Organiser.name)
        .join(Organiser, Organiser.id == Event.organiser_id)
        # Eager-load: export_html renders after `session_scope` has closed the session,
        # so a lazy load of event.distances at that point would raise DetachedInstanceError.
        .options(selectinload(Event.distances))
    )
    if organiser_id is not None:
        stmt = stmt.where(Event.organiser_id == organiser_id)
    stmt = stmt.order_by(Organiser.name, Event.id)
    return list(session.execute(stmt))


def _format_distance(distance) -> str:
    return f"{distance.distance_text}: {distance.price_text}" if distance.price_text else distance.distance_text


def _distances_summary(event: Event) -> str:
    """One-line "5k: £15; 10k: £20" summary, for the flat CSV format."""
    return "; ".join(_format_distance(d) for d in event.distances)


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
                    "date_text": event.date_text,
                    "location": event.location,
                    "start_location": event.start_location,
                    "finish_location": event.finish_location,
                    "distances": _distances_summary(event),
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


def _maps_link_url(location: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(location)}"


def _maps_embed_url(location: str) -> str:
    # Undocumented but widely-used no-API-key embed form - fine for a local/internal tool.
    return f"https://www.google.com/maps?q={quote_plus(location)}&output=embed"


def _render_map(event: Event) -> str:
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
        rows.append(f"<tr><td>{html.escape(d.distance_text)}</td><td>{price_html}</td></tr>")
    return f'<table class="distances"><thead><tr><th>Distance</th><th>Price</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'


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


def _render_event(event: Event) -> str:
    name = html.escape(event.name or f"(untitled event #{event.id})")
    badges = "".join(
        f'<span class="badge">{html.escape(v)}</span>'
        for v in (event.sport, event.date_text)
        if v
    )

    rows = []
    for label, attr in DETAIL_FIELDS:
        value = getattr(event, attr)
        value_html = html.escape(str(value)) if value else '<span class="empty">&mdash;</span>'
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
            <div class="map">{_render_map(event)}</div>
            {_render_page_content(event)}
          </div>
        </details>"""


def _render_organiser(organiser_name: str, events: list[Event]) -> str:
    name = html.escape(organiser_name)
    events_html = "".join(_render_event(e) for e in events)
    return f"""
      <details class="organiser" open>
        <summary>{name} <span class="count">({len(events)} event{"s" if len(events) != 1 else ""})</span></summary>
        <div class="events">{events_html}</div>
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

  details.organiser {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
  }
  details.organiser > summary {
    font-size: 1.15rem;
    font-weight: 600;
  }
  .count { font-weight: 400; color: var(--muted); font-size: 0.9rem; }
  .events { margin-top: 0.6rem; padding-left: 1.4rem; border-left: 2px solid var(--border); }

  details.event {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem 0.8rem;
    margin: 0.5rem 0;
  }
  details.event > summary { font-weight: 500; }
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
  table.distances { width: 100%; max-width: 26rem; border-collapse: collapse; }
  table.distances th, table.distances td {
    text-align: left;
    padding: 0.25rem 0.6rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.92rem;
  }
  table.distances th { color: var(--muted); font-weight: 500; }

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
  }
"""


def export_html(output_path: Path, organiser_id: int | None = None) -> int:
    """Writes the organiser -> events -> details tree to a single HTML file. Returns the event count."""
    with session_scope() as session:
        rows = _fetch_rows(session, organiser_id)

        # Group in Python (rather than a GROUP BY query) - session_scope's connection is
        # closed by the time we render, and we need the full Event objects, not aggregates.
        grouped: dict[int, dict] = {}
        for event, organiser_name in rows:
            group = grouped.setdefault(event.organiser_id, {"name": organiser_name, "events": []})
            group["events"].append(event)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = sum(len(g["events"]) for g in grouped.values())
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    organisers_html = "".join(_render_organiser(g["name"], g["events"]) for g in grouped.values())

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Events export</title>
<style>{_CSS}</style>
</head>
<body>
  <h1>Events export</h1>
  <p class="meta">{total} event(s) across {len(grouped)} organiser(s) &middot; generated {generated_at}</p>
  {organisers_html}
</body>
</html>
"""
    output_path.write_text(doc, encoding="utf-8")
    print(f"wrote {total} event(s) across {len(grouped)} organiser(s) to {output_path}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["csv", "html"], default="html")
    parser.add_argument("--output", type=Path, default=None, help="output path (default: tools/data/events_export.<format>)")
    parser.add_argument("--organiser-id", type=int, default=None, help="only export events for this organiser id")
    args = parser.parse_args()

    output_path = args.output or DEFAULT_OUTPUT[args.format]

    if args.format == "csv":
        export_csv(output_path, organiser_id=args.organiser_id)
    else:
        export_html(output_path, organiser_id=args.organiser_id)


if __name__ == "__main__":
    main()
