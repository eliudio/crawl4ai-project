from enum import Enum as PyEnum

__all__ = [
    "SourceType",
    "CrawlRunType",
    "CrawlStatus",
    "EventStatus",
    "Occurrence",
    "RegistrationStatus",
    "EventLifecycle",
    "Sport",
]


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


class EventStatus(PyEnum):
    """
    VALID: the page actually describes a specific event.
    INVALID: the crawled URL doesn't - e.g. it's just a redirect notice to
    another site (confirmed in practice: runthrough.co.uk/event/running-tours-
    copenhagen-marathon is literally just "We are redirecting you to
    runnerretreats.com"), a dead/error page, or otherwise has no real event
    content to extract. Distinct from the crawl failing outright (that's
    CrawlStatus.FAILED / a None return from crawl_event) - this is a *successful*
    crawl of a page that turns out not to be an event page at all, so it's worth
    keeping the row (rather than silently discarding it) with the reason why.
    """

    VALID = "valid"
    INVALID = "invalid"


class Occurrence(PyEnum):
    """
    How an event recurs. Two genuinely different storage mechanisms sit behind
    this, decided by whether the organiser's own page enumerates concrete dates
    at all - see Event's own occurrence_* columns and EventOccurrence below:

    - ONE_OFF / SPECIFIC_DATES: bounded - a finite, known set of dates (a
      single date, or several individually listed/ticketed ones - e.g.
      atwevents.co.uk's own per-session tickets, one row each). These live in
      EventOccurrence, one row per known date+time. A one-off event is simply
      a SPECIFIC_DATES-shaped event with exactly one row - ONE_OFF exists as
      its own value purely as a descriptive label for humans/UI, not a
      different storage path.
    - DAILY / WEEKLY / MONTHLY / YEARLY: unbounded - a standing rule with no
      enumerable dates at all (e.g. parkrun: "every Saturday, 9am", forever,
      no page ever lists individual future dates). Represented by Event's own
      occurrence_weekdays/occurrence_time/occurrence_starts_on/
      occurrence_ends_on directly - EventOccurrence stays empty for these,
      that's correct, not a gap.
    """

    ONE_OFF = "one_off"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    SPECIFIC_DATES = "specific_dates"


class RegistrationStatus(PyEnum):
    """
    Whether taking part in this event needs sign-up/entry/a ticket at all, and if so,
    whether that's currently open. Two independent facts folded into one enum rather than
    a bool-plus-status pair, since "is registration open" is meaningless for an event that
    never needed it in the first place - confirmed in practice: parkrun requires no
    sign-up whatsoever (NOT_REQUIRED), vs. zigzagrunning.co.uk's Two Hundred Miles
    Challenge, which states outright "Registration is Closed" (CLOSED) with no other
    detail - not every event states this at all (UNKNOWN, the safe default - never assume
    OPEN just because nothing was said).
    """

    NOT_REQUIRED = "not_required"
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class EventLifecycle(PyEnum):
    """
    Whether the event itself is still going ahead as planned - deliberately kept separate
    from RegistrationStatus above: "entries closed" and "event cancelled" are independent
    facts (an event can be sold out and still on, or cancelled after entries were already
    closed) and collapsing them would make it impossible to tell "closed because full" from
    "closed because called off" apart, or to express both at once. Also separate from
    EventStatus (valid/invalid): a cancelled event's page is still a perfectly valid,
    well-formed description of a (no-longer-happening) event, not a redirect/dead page.

    Defaults to SCHEDULED - unlike RegistrationStatus's UNKNOWN default, silence here really
    does mean "going ahead": organisers reliably announce a cancellation/postponement
    prominently when it happens, rather than the reverse (needing to affirmatively state
    "yes, still on" on every ordinary event page) - same reasoning as EventStatus defaulting
    to VALID / Occurrence defaulting to ONE_OFF.
    """

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"


class Sport(PyEnum):
    """
    Fixed, closed vocabulary for RaceType.sport - unlike Event.sport (free text,
    straight from the LLM, since forcing an exact enum match there risks losing an
    event entirely over a wording mismatch like "athletics"), this is deliberately
    strict: RaceType rows are a shared lookup table other events reference, so its
    own sport needs to be one of a small fixed set for that to mean anything.
    race_types.get_or_create_race_type() coerces Event.sport's free text into this
    enum (falling back to OTHER), rather than this being fed by the LLM directly.
    """

    RUNNING = "running"
    CYCLING = "cycling"
    SWIMMING = "swimming"
    TRIATHLON = "triathlon"
    MULTI_SPORT = "multi_sport"
    WALKING = "walking"
    OBSTACLE = "obstacle"
    OTHER = "other"
