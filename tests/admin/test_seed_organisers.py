"""
Unit tests for admin/seed_organisers.py's _handler_params_from_row - the CSV
row -> Organiser.handler_params mapping, tested directly against plain dicts
since seed_from_csv() itself needs a real DB (init_db() would try to create
Organiser's Postgres-only ARRAY-typed listing_urls column, which SQLite can't
build - same limitation test_export_events.py's own docstring already notes).
"""

from services.admin.seed_organisers import _handler_params_from_row, _registrator_from_row


def test_sitemap_url_alone_becomes_handler_params():
    row = {"sitemap_url": "https://example.com/sitemap.xml"}
    assert _handler_params_from_row(row) == {"sitemap_url": "https://example.com/sitemap.xml"}


def test_no_sitemap_url_and_no_handler_params_is_none():
    assert _handler_params_from_row({}) is None
    assert _handler_params_from_row({"sitemap_url": ""}) is None
    assert _handler_params_from_row({"handler_params": ""}) is None


def test_handler_params_json_column_alone():
    row = {"handler_params": '{"country_code": 1}'}
    assert _handler_params_from_row(row) == {"country_code": 1}


def test_sitemap_url_merges_with_handler_params_json_column():
    row = {"sitemap_url": "https://example.com/sitemap.xml", "handler_params": '{"country_code": 1}'}
    assert _handler_params_from_row(row) == {
        "country_code": 1,
        "sitemap_url": "https://example.com/sitemap.xml",
    }


def test_sitemap_url_wins_over_a_conflicting_key_in_handler_params_json():
    # Not expected in practice (nothing else should ever set this key), but confirms
    # the flat column is authoritative for its own key rather than silently losing to
    # whatever the JSON column happened to also contain.
    row = {"sitemap_url": "https://real.example/sitemap.xml", "handler_params": '{"sitemap_url": "https://stale.example/sitemap.xml"}'}
    assert _handler_params_from_row(row)["sitemap_url"] == "https://real.example/sitemap.xml"


# ---------------------------------------------------------------------------
# _registrator_from_row - see Organiser.registrator's own docstring.
# ---------------------------------------------------------------------------

def test_registrator_column_read_as_is():
    assert _registrator_from_row({"registrator": "bot"}) == "bot"
    assert _registrator_from_row({"registrator": "jane_doe"}) == "jane_doe"


def test_registrator_defaults_to_bot_when_missing_or_blank():
    assert _registrator_from_row({}) == "bot"
    assert _registrator_from_row({"registrator": ""}) == "bot"
