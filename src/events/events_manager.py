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
    organiser: str

    class Config:
        populate_by_name = True


def create_database():
    """Create events table with event_name and url as unique constraints"""
    with sqlite3.connect('events.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name    TEXT UNIQUE NOT NULL,
                date          TEXT NOT NULL,
                event_summary TEXT NOT NULL,
                location      TEXT NOT NULL,
                start         TEXT,
                finish        TEXT,
                url           TEXT UNIQUE,          -- secondary unique key
                md            TEXT,
                organiser     TEXT
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
                                                   start, finish, url, md, organiser)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                               ''', (
                                   event.event_name,
                                   event.date,
                                   event.event_summary,
                                   event.location,
                                   event.start,
                                   event.finish,
                                   event.url,
                                   event.md,
                                   event.organiser,
                               ))
                print(f"Added: '{event.event_name}'")
            except sqlite3.IntegrityError:
                print(f"Insertion failed (likely duplicate name/url): '{event.event_name}'")
            except Exception as e:
                print(f"Exception: '{e}'")

        # commit automatic at end of context


def event_from_dict(data: Dict[str, Any]) -> RunningEvent | None:
    """Convert raw dict to RunningEvent model"""
    try:
        return RunningEvent(**data)
    except Exception as e:
        print(f"Exception: '{e}'")
        return None

