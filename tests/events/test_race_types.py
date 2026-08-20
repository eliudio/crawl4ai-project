"""
Unit tests for race_types.get_or_create_race_type - the standardisation layer
that turns an event's free-text (sport, distance) pair into a shared RaceType
row (see models.py's RaceType/Sport, and event_crawler.py's usage).

Runs against a throwaway in-memory SQLite database rather than the real
Postgres dev DB - only the race_types table is created (Base.metadata also
covers Organiser's Postgres-only ARRAY column, which SQLite can't build), and
get_or_create_race_type only ever touches that one table anyway. No real
network/DB dependency, same "instant and free" spirit as test_scraping.py.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.common.models import RaceType, Sport
from services.events.race_types import get_or_create_race_type


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    RaceType.metadata.create_all(engine, tables=[RaceType.__table__])
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Label format - the exact convention: {sport}_{distance_category}
# ---------------------------------------------------------------------------

def test_label_format_named_distance(session):
    race_type = get_or_create_race_type(session, "running", "marathon")
    assert race_type.label == "running_marathon"
    assert race_type.sport == Sport.RUNNING
    assert race_type.distance_category == "marathon"


def test_label_format_generic_km(session):
    # 12k isn't one of the folded round distances (5/10/15/20) - stays in the
    # underscore numeric form, unlike the test right below this one.
    assert get_or_create_race_type(session, "running", "12_k").label == "running_12_k"


def test_label_format_generic_miles(session):
    assert get_or_create_race_type(session, "running", "10_m").label == "running_10_m"


# ---------------------------------------------------------------------------
# get-or-create: identical (sport, distance) must always resolve to the same
# row, not a new one each call - this is the whole point of the table.
# ---------------------------------------------------------------------------

def test_same_combination_returns_same_row(session):
    first = get_or_create_race_type(session, "running", "marathon")
    second = get_or_create_race_type(session, "running", "marathon")
    assert first.id == second.id
    assert session.query(RaceType).count() == 1


def test_case_and_whitespace_noise_still_dedupes(session):
    first = get_or_create_race_type(session, "running", "marathon")
    second = get_or_create_race_type(session, "Running", " Marathon ")
    assert first.id == second.id


def test_different_distances_create_separate_rows(session):
    a = get_or_create_race_type(session, "running", "5k")
    b = get_or_create_race_type(session, "running", "10k")
    assert a.id != b.id
    assert session.query(RaceType).count() == 2


def test_different_sports_same_distance_create_separate_rows(session):
    a = get_or_create_race_type(session, "running", "12_k")
    b = get_or_create_race_type(session, "cycling", "12_k")
    assert a.id != b.id
    assert a.label == "running_12_k"
    assert b.label == "cycling_12_k"


# ---------------------------------------------------------------------------
# _canonicalize_category: the round-km bug (5k/5_k, 10k/10_k) and the
# triathlon-synonym bug (half_ironman/middle_distance_triathlon) confirmed in
# practice - both must fold onto the one canonical row, never two.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wording", ["5k", "5_k"])
def test_5k_variants_fold_onto_one_row(session, wording):
    canonical = get_or_create_race_type(session, "running", "5k")
    variant = get_or_create_race_type(session, "running", wording)
    assert variant.id == canonical.id
    assert variant.distance_category == "5k"


@pytest.mark.parametrize("wording", ["10k", "10_k"])
def test_10k_variants_fold_onto_one_row(session, wording):
    canonical = get_or_create_race_type(session, "running", "10k")
    variant = get_or_create_race_type(session, "running", wording)
    assert variant.id == canonical.id
    assert variant.distance_category == "10k"


def test_odd_km_distance_keeps_underscore_form(session):
    # Only 5/10/15/20 are folded to the bare form - a one-off distance like 12k
    # must NOT collapse into "12" + something else, and must keep its own row.
    race_type = get_or_create_race_type(session, "running", "12_k")
    assert race_type.distance_category == "12_k"


@pytest.mark.parametrize("wording", ["half_ironman", "middle_distance_triathlon", "70_3"])
def test_half_ironman_synonyms_fold_onto_one_row(session, wording):
    canonical = get_or_create_race_type(session, "triathlon", "half_ironman")
    variant = get_or_create_race_type(session, "triathlon", wording)
    assert variant.id == canonical.id
    assert variant.distance_category == "half_ironman"


@pytest.mark.parametrize("wording", ["ironman", "long_distance_triathlon", "full_distance_triathlon", "140_6"])
def test_ironman_synonyms_fold_onto_one_row(session, wording):
    canonical = get_or_create_race_type(session, "triathlon", "ironman")
    variant = get_or_create_race_type(session, "triathlon", wording)
    assert variant.id == canonical.id


def test_standard_triathlon_folds_onto_olympic(session):
    canonical = get_or_create_race_type(session, "triathlon", "olympic_triathlon")
    variant = get_or_create_race_type(session, "triathlon", "standard_triathlon")
    assert variant.id == canonical.id
    assert variant.distance_category == "olympic_triathlon"


@pytest.mark.parametrize("wording", ["junior", "kids", "kids_race", "junior_race", "youth", "youth_race"])
def test_junior_synonyms_fold_onto_one_row(session, wording):
    # Confirmed in practice: "Junior Race" and every "Kids Race - Year N" age-group
    # variant on the same page must all resolve to one row, not one per age group.
    canonical = get_or_create_race_type(session, "running", "junior")
    variant = get_or_create_race_type(session, "running", wording)
    assert variant.id == canonical.id
    assert variant.distance_category == "junior"


def test_junior_race_type_exists_for_every_sport_independently(session):
    # Also confirmed in practice: a junior race turned up under both running and
    # cycling on the same organiser - each sport needs its own "junior" row.
    running_junior = get_or_create_race_type(session, "running", "junior")
    cycling_junior = get_or_create_race_type(session, "cycling", "junior")
    assert running_junior.id != cycling_junior.id
    assert running_junior.label == "running_junior"
    assert cycling_junior.label == "cycling_junior"


# ---------------------------------------------------------------------------
# Fallbacks: missing/unrecognised sport, missing distance.
# ---------------------------------------------------------------------------

def test_unrecognised_sport_falls_back_to_other(session):
    race_type = get_or_create_race_type(session, "athletics", "marathon")
    assert race_type.sport == Sport.OTHER
    assert race_type.label == "other_marathon"


def test_missing_sport_falls_back_to_other(session):
    race_type = get_or_create_race_type(session, None, "marathon")
    assert race_type.sport == Sport.OTHER


def test_missing_distance_category_returns_none(session):
    assert get_or_create_race_type(session, "running", None) is None
    assert get_or_create_race_type(session, "running", "") is None
    assert session.query(RaceType).count() == 0
