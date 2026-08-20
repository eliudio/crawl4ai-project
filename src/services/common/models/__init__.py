"""
ORM models for the events pipeline (Cloud SQL / Postgres), split by aggregate:
base.py (Base/utcnow), enums.py (every PyEnum column type), organiser.py,
event.py (Event + its two child tables), race_type.py, crawl_run.py.

Every other module imports models through this package (`from
services.common.models import Event, ...`), never reaching into a submodule
directly - that's what lets the split above move without touching any caller.
Cross-references between Organiser/Event/RaceType/EventDistance/EventOccurrence
resolve via SQLAlchemy's own string-based Mapped[...] forward refs (e.g.
Mapped["Event"]), not Python imports between these submodules - they're
matched up by class name against Base's shared registry the first time any
mapper is configured (SQLAlchemy does this lazily, on first real use), which
is why importing all of them here - regardless of order - is enough to make
every relationship() resolve correctly.
"""

from .base import Base, utcnow
from .crawl_run import CrawlRun
from .enums import (
    CrawlRunType,
    CrawlStatus,
    EventLifecycle,
    EventStatus,
    Occurrence,
    RegistrationStatus,
    SourceType,
    Sport,
)
from .event import Event, EventDistance, EventOccurrence
from .organiser import Organiser
from .race_type import RaceType

__all__ = [
    "Base",
    "utcnow",
    "SourceType",
    "CrawlRunType",
    "CrawlStatus",
    "EventStatus",
    "Occurrence",
    "RegistrationStatus",
    "EventLifecycle",
    "Sport",
    "Organiser",
    "Event",
    "EventDistance",
    "EventOccurrence",
    "RaceType",
    "CrawlRun",
]
