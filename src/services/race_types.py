"""
Standardises the (sport, distance) pairs coming out of an event's extracted
distances into one shared RaceType row per unique combination - "42.195 km",
"26.22 miles" and "Marathon" on three different organisers' own pages should
all resolve to the SAME row (label "running_marathon"), not three unrelated
free-text strings that never get compared to each other.

event_crawler.py calls get_or_create_race_type() once per EventDistance while
building event.distances, instead of each one inserting its own copy.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.models import RaceType, Sport

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Deterministic safety net on top of llm_extractor's distance_category prompt rules -
# an LLM can't be guaranteed 100% consistent run to run (confirmed in practice: 5K/10K
# came back as both "5k"/"10k" AND "5_k"/"10_k" for the identical distance), so this is
# the one chokepoint every distance passes through before becoming a label, and folds
# any such drift onto a single canonical spelling rather than leaving near-duplicate
# RaceType rows around.
_ROUND_KM_RE = re.compile(r"(\d+)_k")
_ROUND_KM_DISTANCES = {"5", "10", "15", "20"}  # well-known race distances - always bare "Nk", never "N_k"

# Different colloquial names for the exact same real-world distance (confirmed in
# practice: "Middle Distance Triathlon" on one page vs "Half Ironman" elsewhere) -
# fold onto the one canonical spelling from llm_extractor's named-distance list.
_CATEGORY_ALIASES = {
    "middle_distance_triathlon": "half_ironman",
    "70_3": "half_ironman",
    "long_distance_triathlon": "ironman",
    "full_distance_triathlon": "ironman",
    "140_6": "ironman",
    "standard_triathlon": "olympic_triathlon",
    # Junior/kids races (confirmed in practice: "Junior Race", "Kids Race - Year 3", etc,
    # each a separate distance_text but all the same category, not one per age group).
    "kids": "junior",
    "kids_race": "junior",
    "junior_race": "junior",
    "youth": "junior",
    "youth_race": "junior",
}


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_")


def _canonicalize_category(category: str) -> str:
    match = _ROUND_KM_RE.fullmatch(category)
    if match and match.group(1) in _ROUND_KM_DISTANCES:
        return f"{match.group(1)}k"
    return _CATEGORY_ALIASES.get(category, category)


def _coerce_sport(sport_text: str | None) -> Sport:
    """Event.sport is free text straight from the LLM (see llm_extractor.py) - map it onto
    RaceType's closed vocabulary, falling back to OTHER rather than failing outright on a
    wording mismatch (e.g. "athletics")."""
    if not sport_text:
        return Sport.OTHER
    try:
        return Sport(_slugify(sport_text))
    except ValueError:
        return Sport.OTHER


def get_or_create_race_type(
    session: Session, sport_text: str | None, distance_category: str | None
) -> RaceType | None:
    """
    Returns the shared RaceType row for (sport_text, distance_category), creating
    it the first time this exact combination is seen. Returns None when
    distance_category is missing - nothing meaningful to categorise without it,
    and EventDistance.race_type_id is nullable for exactly this case.
    """
    if not distance_category:
        return None

    sport = _coerce_sport(sport_text)
    category = _canonicalize_category(_slugify(distance_category))
    if not category:
        return None

    label = f"{sport.value}_{category}"

    existing = session.scalar(select(RaceType).where(RaceType.label == label))
    if existing:
        return existing

    race_type = RaceType(label=label, sport=sport, distance_category=category)
    session.add(race_type)
    session.flush()  # assign race_type.id so the caller can reference it immediately
    return race_type
