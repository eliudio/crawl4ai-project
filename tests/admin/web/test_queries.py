"""
Unit tests for admin/web/queries.py - checks the actual query each function
builds, without needing a real database: a fake Session.execute just captures
the statement instead of running it, then we inspect its compiled SQL text
directly. Same style as the old admin/export/test_csv_export.py's
_fetch_rows tests, which this replaces.
"""

from services.admin.web import queries
from services.common.models import EventStatus


class _CapturingSession:
    def __init__(self):
        self.captured_statement = None

    def execute(self, stmt):
        self.captured_statement = stmt
        return []


# ---------------------------------------------------------------------------
# fetch_events
# ---------------------------------------------------------------------------

def test_fetch_events_defaults_to_valid_status():
    # No view should have to remember to filter these out itself - see the reported
    # case, e.g. runthrough.co.uk's redirect-only "Running Tours" events.
    session = _CapturingSession()
    queries.fetch_events(session)
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "events.status = 'VALID'" in compiled


def test_fetch_events_still_filters_by_organiser_id_when_given():
    session = _CapturingSession()
    queries.fetch_events(session, organiser_id=7)
    compiled = str(session.captured_statement)
    assert "events.organiser_id = " in compiled
    assert "events.status = " in compiled  # both filters present, not one replacing the other


def test_fetch_events_can_request_invalid_status_instead():
    session = _CapturingSession()
    queries.fetch_events(session, status=EventStatus.INVALID)
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "'INVALID'" in compiled


def test_fetch_events_status_none_omits_the_filter_entirely():
    session = _CapturingSession()
    queries.fetch_events(session, status=None)
    compiled = str(session.captured_statement)
    assert "WHERE" not in compiled
    assert "events.status = " not in compiled


def test_fetch_events_search_filters_name_organiser_and_location():
    # .compile() with no dialect given renders ilike() as the generic lower(...) LIKE
    # lower(...) form (there's no dialect-neutral ILIKE keyword) - Postgres itself gets a
    # real ILIKE at execution time via its own dialect, this just confirms all three
    # columns are covered and the search term made it into the bound literal.
    session = _CapturingSession()
    queries.fetch_events(session, search="marathon")
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "lower(events.name) LIKE lower('%marathon%')" in compiled
    assert "lower(organisers.name) LIKE lower('%marathon%')" in compiled
    assert "lower(events.location) LIKE lower('%marathon%')" in compiled


def test_fetch_events_no_search_omits_ilike_filter():
    session = _CapturingSession()
    queries.fetch_events(session, search=None)
    compiled = str(session.captured_statement)
    assert "LIKE" not in compiled


# ---------------------------------------------------------------------------
# fetch_organisers
# ---------------------------------------------------------------------------

def test_fetch_organisers_counts_only_valid_events():
    session = _CapturingSession()
    queries.fetch_organisers(session)
    compiled = str(session.captured_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "count(events.id)" in compiled
    assert "'VALID'" in compiled


def test_fetch_organisers_outerjoins_so_zero_event_organisers_are_included():
    session = _CapturingSession()
    queries.fetch_organisers(session)
    compiled = str(session.captured_statement)
    assert "LEFT OUTER JOIN events" in compiled
