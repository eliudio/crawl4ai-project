"""
Admin web interface - an on-the-fly, browsable view of the events database,
replacing the old admin/export static CSV/HTML scripts (see ARCHITECTURE.md's
"Admin interface" section for why). Every route below fetches via queries.py
and hands the rows straight to render.py; there's no CSV export any more -
the HTML tree view already covered the one use case anyone actually had for
these, and generating it live means there's no "did anyone remember to rerun
the export" staleness to worry about either.

Run locally with: uvicorn services.admin.web.app:app --reload --port 8001

Internal-only. Every page here is a raw view over the full database,
including internal debugging fields like invalid_reason, so this is never
meant to be public: the primary control is restricting who can invoke the
Cloud Run service itself (IAM-authenticated invocation - see
INSTALLATION.md), with an optional HTTP Basic gate below (ADMIN_BASIC_AUTH_USER/
ADMIN_BASIC_AUTH_PASSWORD) as a second layer - not a replacement for that.
Auth is skipped entirely when those aren't set (plain local dev).
"""

import secrets

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from services.common import session_scope
from services.common.config import settings
from services.common.models import EventStatus

from . import queries, render

app = FastAPI(title="Events admin")
_basic_auth = HTTPBasic(auto_error=False)


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic_auth)) -> None:
    """Optional HTTP Basic gate - a no-op unless both ADMIN_BASIC_AUTH_USER and
    ADMIN_BASIC_AUTH_PASSWORD are set (see config.py). secrets.compare_digest, not `==`,
    to avoid leaking timing info about how much of the guess was correct."""
    expected_user, expected_password = settings.admin_basic_auth_user, settings.admin_basic_auth_password
    if not expected_user or not expected_password:
        return
    ok = credentials is not None and secrets.compare_digest(
        credentials.username, expected_user
    ) and secrets.compare_digest(credentials.password, expected_password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/healthz")
def healthz():
    # Unauthenticated: this is Cloud Run's own liveness probe, not a maintainer page.
    return {"status": "ok"}


# Every maintainer-facing page goes through _require_auth - a router (rather than
# app-level `dependencies=`) so /healthz above stays exempt.
router = APIRouter(dependencies=[Depends(_require_auth)])


@router.get("/", response_class=HTMLResponse)
def index():
    with session_scope() as session:
        organisers = queries.fetch_organisers(session)
    return render.render_index(organisers)


@router.get("/events", response_class=HTMLResponse)
def events(organiser_id: int | None = None, q: str | None = None):
    """Organiser -> events -> details tree, VALID events only - the on-the-fly equivalent
    of the old export_events_per_organiser HTML export."""
    with session_scope() as session:
        rows = queries.fetch_events(session, organiser_id=organiser_id, status=EventStatus.VALID, search=q)
    return render.render_organiser_tree(
        rows, title="Events per organiser", active_path="/events", organiser_id=organiser_id, search=q
    )


@router.get("/events/invalid", response_class=HTMLResponse)
def invalid_events(organiser_id: int | None = None, q: str | None = None):
    """Same tree as /events, but INVALID events only - for debugging what the LLM flagged
    as invalid and why (each event's "Invalid reason" row/badge), without wading through
    every genuinely valid event to find them. The on-the-fly equivalent of the old
    export_invalid_events HTML export."""
    with session_scope() as session:
        rows = queries.fetch_events(session, organiser_id=organiser_id, status=EventStatus.INVALID, search=q)
    return render.render_organiser_tree(
        rows, title="Invalid events", active_path="/events/invalid", organiser_id=organiser_id, search=q
    )


@router.get("/events/by-type", response_class=HTMLResponse)
def events_by_type(organiser_id: int | None = None):
    """Sport -> race type -> events tree, VALID events only - the on-the-fly equivalent of
    the old export_events_per_event_type HTML export."""
    with session_scope() as session:
        rows = queries.fetch_events(session, organiser_id=organiser_id, status=EventStatus.VALID)
    return render.render_event_type_tree(rows, organiser_id=organiser_id)


app.include_router(router)
