import sqlite3
from typing import List, Dict, Any

from pydantic import BaseModel, Field


class RunningEvent(BaseModel):
    """Pydantic model for running events (findarace.com style data)"""

    date: str = Field(..., description="Date range as string")
    event_name: str = Field(..., alias="event_name")
    event_summary: str
    finish: str
    location: str
    md: str  # full markdown content
    start: str
    url: str

    class Config:
        populate_by_name = True


def create_database():
    """Create events table with event_name and url as unique constraints"""
    with sqlite3.connect('events.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS events
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           event_name
                           TEXT
                           UNIQUE
                           NOT
                           NULL,
                           date
                           TEXT
                           NOT
                           NULL,
                           event_summary
                           TEXT
                           NOT
                           NULL,
                           location
                           TEXT
                           NOT
                           NULL,
                           start
                           TEXT,
                           finish
                           TEXT,
                           url
                           TEXT
                           UNIQUE, -- secondary unique key
                           md
                           TEXT
                       )
                       ''')


def event_exists(event_name: str) -> bool:
    """Check if an event with the given name already exists"""
    with sqlite3.connect('events.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM events WHERE event_name = ?",
            (event_name,)
        )
        return cursor.fetchone() is not None


def event_with_url_exists(url: str) -> bool:
    """
    Check if an event with the given URL already exists.

    Returns:
        bool: True if any event with this exact URL exists, False otherwise
    """
    with sqlite3.connect('events.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM events WHERE url = ?",
            (url,)
        )
        return cursor.fetchone() is not None


def event_exists_by_name_or_url(event_name: str, url: str) -> bool:
    """
    Combined check: returns True if event exists by name OR by URL.
    This is the most practical check before insertion.
    """
    return event_exists(event_name) or event_with_url_exists(url)


def insert_or_skip_events(events: List[RunningEvent]):
    """
    Insert events only if neither the event_name nor the url already exists.
    """
    with sqlite3.connect('events.db') as conn:
        cursor = conn.cursor()

        for event in events:
            if event_exists_by_name_or_url(event.event_name, event.url):
                print(f"Skipped: '{event.event_name}' (already exists by name or URL)")
                continue

            try:
                cursor.execute('''
                               INSERT INTO events (event_name, date, event_summary, location,
                                                   start, finish, url, md)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                               ''', (
                                   event.event_name,
                                   event.date,
                                   event.event_summary,
                                   event.location,
                                   event.start,
                                   event.finish,
                                   event.url,
                                   event.md
                               ))
                print(f"Added: '{event.event_name}'")
            except sqlite3.IntegrityError:
                print(f"Insertion failed (likely duplicate name/url): '{event.event_name}'")

        # commit automatic at end of context


def event_from_dict(data: Dict[str, Any]) -> RunningEvent:
    """Convert raw dict to RunningEvent model"""
    return RunningEvent(**data)


# ──────────────────────────────────────────────────────────────────────────────
# Example usage
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    create_database()

    # Sample event
    sample_event = RunningEvent(
        date="Fri 8th March 2024 - Thu 31st December 2026",
        event_name="Run the Thames Bridges - 10K",
        event_summary="Self-guided 10K audio tour running across 11 Thames bridges...",
        finish="near Vauxhall Station",
        location="Tower Hill, London, United Kingdom",
        start="sundial outside Tower Hill Station",
        url="https://findarace.com/events/run-the-thames-bridges-10k",
        md="# Run the Thames Bridges - 10K\n\n(full content...)"
    )

    # Quick existence checks
    print("Checking existence...")
    print("By name:", event_exists("Run the Thames Bridges - 10K"))
    print("By URL: ", event_with_url_exists("https://findarace.com/events/run-the-thames-bridges-10k"))
    print("By name OR URL:", event_exists_by_name_or_url(
        "Run the Thames Bridges - 10K",
        "https://findarace.com/events/run-the-thames-bridges-10k"
    ))

    # Try to insert (will skip if already present)
    print("\nTrying to insert sample event:")
    insert_or_skip_events([sample_event])

    # Another event with same URL but different name → should be skipped
    duplicate_url_event = RunningEvent(
        date="Some date",
        event_name="Different Name Same URL",
        event_summary="Test duplicate url",
        finish="Somewhere",
        location="London",
        start="Start",
        url="https://findarace.com/events/run-the-thames-bridges-10k",  # same url!
        md="Test md"
    )

    print("\nTrying duplicate URL event:")
    insert_or_skip_events([duplicate_url_event])