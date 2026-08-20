from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow
from .enums import CrawlRunType, CrawlStatus

__all__ = ["CrawlRun"]


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
