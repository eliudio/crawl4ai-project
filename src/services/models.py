"""
ORM models for the events pipeline (Cloud SQL / Postgres).

Table design notes:
- `Organiser.source_type` is the enforcement point for the "never store
  aggregator/platform data" rule: only rows with source_type == "organiser"
  are ever enqueued for event crawling (see listing_crawler.py). Aggregators
  are only ever a *source of organiser URLs* (phase 2), never a source of
  event rows.
- `Event.content_hash` lets a re-crawl skip re-extraction (and therefore the
  LLM call) when a page hasn't changed since last time.
"""

from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceType(PyEnum):
    ORGANISER = "organiser"
    AGGREGATOR = "aggregator"
    PLATFORM = "platform"


class CrawlRunType(PyEnum):
    LISTING = "listing"
    EVENT = "event"


class CrawlStatus(PyEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Organiser(Base):
    __tablename__ = "organisers"
    __table_args__ = (UniqueConstraint("homepage_url", name="uq_organiser_homepage_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    homepage_url: Mapped[str] = mapped_column(String(1024))
    # Several organisers split listings across multiple sub-pages (e.g. by
    # category), so this is a list rather than a single URL. Empty/absent
    # until discover_listing_urls() (see llm_extractor.py) has run once.
    listing_urls: Mapped[list[str]] = mapped_column(ARRAY(String(1024)), default=list)

    # A "Sitemap:" entry read from this organiser's robots.txt (see
    # discover_sitemaps.py) - when present, listing_crawler.py prefers
    # reading this directly (sitemap_crawler.py) over opening listing_urls
    # and clicking through load-more/pagination, since it's a direct,
    # complete list of the site's URLs with no browser/LLM interaction
    # needed to obtain it. Null until discover_sitemaps.py has found one.
    sitemap_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Only "organiser" rows are ever fed to the event-crawl queue. Rows
    # discovered on aggregator/platform sites are recorded for provenance
    # but structurally excluded from event crawling.
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type"), default=SourceType.ORGANISER
    )
    discovered_via: Mapped[str | None] = mapped_column(String(255), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    events: Mapped[list["Event"]] = relationship(back_populates="organiser")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("url", name="uq_event_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organiser_id: Mapped[int] = mapped_column(ForeignKey("organisers.id"))
    url: Mapped[str] = mapped_column(String(1024))

    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sport: Mapped[str | None] = mapped_column(String(64), nullable=True)  # running, cycling, ...
    date_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    start_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    finish_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    distance_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    age_restriction_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organiser: Mapped["Organiser"] = relationship(back_populates="events")


class CrawlRun(Base):
    """Audit log of every listing-crawl / event-crawl attempt, success or not."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[CrawlRunType] = mapped_column(Enum(CrawlRunType, name="crawl_run_type"))
    target_url: Mapped[str] = mapped_column(String(1024))
    organiser_id: Mapped[int | None] = mapped_column(ForeignKey("organisers.id"), nullable=True)
    status: Mapped[CrawlStatus] = mapped_column(Enum(CrawlStatus, name="crawl_status"))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
