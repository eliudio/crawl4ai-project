from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "utcnow"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass
