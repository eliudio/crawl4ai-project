from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, utcnow
from .enums import Sport

__all__ = ["RaceType"]


class RaceType(Base):
    """
    Standardised (sport, distance) category shared across every event that offers
    it, e.g. "42.195 km", "26.22 miles" and "Marathon" on three different
    organisers' pages should all resolve to the SAME row here (label
    "running_marathon") rather than three unrelated free-text strings - see
    events/race_types.get_or_create_race_type(), which every EventDistance is
    resolved through instead of each one inserting its own copy.

    label is the canonical `{sport}_{distance_category}` slug (e.g.
    "running_marathon", "running_10_k", "running_10_m") and is what get_or_create
    looks rows up by; sport/distance_category are kept as their own columns too so
    they're queryable without parsing the label back apart.
    """

    __tablename__ = "race_types"
    __table_args__ = (UniqueConstraint("label", name="uq_race_type_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    sport: Mapped[Sport] = mapped_column(Enum(Sport, name="race_sport"))
    # e.g. "marathon", "half_marathon", "10k", "10_k", "10_m" - see
    # llm/event_extraction.py's distance_category field for exactly what values this
    # can take and why.
    distance_category: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    distances: Mapped[list["EventDistance"]] = relationship(back_populates="race_type")
