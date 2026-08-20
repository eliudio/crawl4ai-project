"""
Runs the pattern-website pipeline (Organiser homepage -> listing page -> event
page, see pattern_site/listing_crawler.py/event_crawler.py) in-process, with no
Pub/Sub involved - for local development against a local/dev Postgres before
anything is deployed to Cloud Run. Production uses server/main.py's HTTP handlers,
triggered by Pub/Sub. Named for the one pipeline it drives, not "the local runner"
generically - see local_feed_importer.py for the separate structured-bulk-feed
pipeline's own local-dev equivalent (parkrun, ...), which this module has nothing
to do with.

Usage:
    python -m services.local.local_event_scraper --limit 3
    python -m services.local.local_event_scraper --organiser-id 42
    python -m services.local.local_event_scraper --mode dry-run  # discover event URLs but don't crawl/store them, just print
    python -m services.local.local_event_scraper --mode sanity-check  # crawl only 1 event per organiser - a quick smoke test across all of them
    python -m services.local.local_event_scraper --check-mode url-check  # skip re-crawl of any URL already stored, changed or not
    python -m services.local.local_event_scraper --organiser-id 42 --force-refresh  # re-crawl every event for organiser
                                                                                # 42, replacing existing rows, adding
                                                                                # missing ones - e.g. after fixing a bug
                                                                                # in extraction that needs a full redo
"""

import argparse

import requests
from sqlalchemy import select

from services.admin import seed_from_csv
from services.common import init_db, session_scope
from services.common.config import settings
from services.common.models import Organiser, SourceType
from services.pattern_site import crawl_event, crawl_listing


def run(
    limit: int | None = None,
    organiser_id: int | None = None,
    mode: str = "normal",
    check_mode: str = "hash-check",
    scraper_backend: str | None = None,
    force_refresh: bool = False,
) -> None:
    """
    mode "normal" (default): crawl every new event URL found for each organiser.
    mode "dry-run": discover new event URLs but don't crawl/store them, just print
    them - for previewing what a real run would touch.
    mode "sanity-check": crawl only the first new event URL found per organiser -
    confirms the pipeline works end-to-end (listing discovery + one real event
    crawl/extraction/store) for every organiser without paying to crawl every
    single one of its events. The opposite trade-off from --limit: fewer events
    per organiser instead of fewer organisers.

    force_refresh: re-crawl every event URL currently found for each organiser
    (not just ones missing from the database) and always re-extract, even if a
    URL is already stored and its content hasn't changed - replacing that row
    in place, same as a normal re-extraction would, rather than skipping it.
    A URL with no existing row is still added as new. For picking up a fix to
    the extraction pipeline itself across events that were already crawled
    (see e.g. the Three Forts Challenge distance-stripping bug) - the point is
    to force every one of them through extraction again, not wait for their
    content to change. Overrides check_mode entirely (always uses "force"
    internally, see event_crawler.crawl_event) - normally combined with
    --organiser-id, since refreshing every organiser at once re-runs the LLM
    over every event in the database.
    """
    # See services/scraper_client.py: "crawl4ai" (self-hosted, no per-page cost) is the
    # default; "firecrawl" always uses Firecrawl's hosted API instead. None leaves
    # whatever's already configured via SCRAPER_BACKEND/.env untouched.
    if scraper_backend is not None:
        settings.scraper_backend = scraper_backend

    effective_check_mode = "force" if force_refresh else check_mode

    init_db()
    seed_from_csv()

    with session_scope() as session:
        # source_type == ORGANISER: matches main.py's own eligibility check for this
        # same pipeline (see models.py's SourceType docstring) - a PLATFORM row (e.g.
        # parkrun's own umbrella Organiser, see feed_importers.get_or_create_organiser)
        # exists only for FK/provenance purposes and must never reach crawl_listing()
        # here either, same as it never should in production.
        query = select(Organiser).where(Organiser.active.is_(True), Organiser.source_type == SourceType.ORGANISER)
        if organiser_id is not None:
            query = query.where(Organiser.id == organiser_id)
        if limit is not None:
            query = query.limit(limit)
        organisers = list(session.scalars(query))

    # See listing_crawler.crawl_listing's own docstring for why these two matter at
    # all: every currently-registered handler only ever discovers a URL list, letting
    # the code below (the dry-run print loop / the urls_to_crawl = urls[:1] slice)
    # apply the same "preview only"/"just one" effect generically. Kept as part of the
    # contract anyway - a handler that writes real event data inline, before this
    # function ever gets a list back to slice, would need to be told directly instead
    # (the old "parkrun" handler was exactly that shape - see git history/
    # feed_importers.py, the separate pipeline that replaced it).
    dry_run = mode == "dry-run"
    event_limit = 1 if mode == "sanity-check" else None

    for organiser in organisers:
        # Captured as a plain string *before* the risky block below, not read from the
        # ORM object inside except - see that clause's own comment for why.
        organiser_name = organiser.name
        try:
            with session_scope() as session:
                organiser = session.get(Organiser, organiser.id)
                urls = crawl_listing(
                    session, organiser, force=force_refresh, dry_run=dry_run, event_limit=event_limit
                )
                label = "event URL(s) to refresh" if force_refresh else "new event URL(s)"
                print(f"{organiser.name}: {len(urls)} {label}")
        except requests.exceptions.ConnectionError:
            raise  # can't reach Firecrawl at all - stop the run, see crawl_event
        except Exception as e:
            # Same reasoning as the per-event except below (the run-frimley-2022 incident):
            # one organiser's listing being unreachable (dead domain/DNS failure, site down,
            # ...) must cost only this organiser, not the whole overnight batch run - confirmed
            # in practice: limelightsportsgroup.com's DNS no longer resolving at all (crawl4ai
            # AND Firecrawl both exhausted their retries) propagated all the way out of run()
            # uncaught, since this listing-discovery step - unlike the per-event loop just
            # below - had no try/except of its own.
            #
            # organiser_name (not organiser.name): session_scope's own except clause (see
            # db.py) rolls back on any exception - and Session.rollback() *expires* every
            # object loaded in that session (regardless of expire_on_commit=False, which
            # only governs commit) - so by the time control reaches here, the `organiser`
            # re-fetched inside the `with` block above is both expired and detached (its
            # session is already closed). Touching organiser.name here would try to
            # lazily refresh that expired attribute against a session that no longer
            # exists, raising DetachedInstanceError - confirmed in practice, the first
            # time this exact except clause actually fired for real.
            print(f"  ERROR: {organiser_name}: listing crawl failed: {type(e).__name__}: {e}")
            continue

        if mode == "dry-run":
            for url in urls:
                print(f"  [dry-run] {url}")
            continue

        urls_to_crawl = urls
        if mode == "sanity-check":
            urls_to_crawl = urls[:1]
            if len(urls) > 1:
                print(f"  [sanity-check] crawling 1 of {len(urls)} event URL(s)")

        for url in urls_to_crawl:
            try:
                with session_scope() as session:
                    event = crawl_event(session, organiser.id, url, check_mode=effective_check_mode)
                    print(f"  {'ok' if event else 'FAILED'}: {url}")
            except requests.exceptions.ConnectionError:
                raise  # can't reach Firecrawl at all - stop the run, see crawl_event
            except Exception as e:
                # Belt-and-braces on top of crawl_event's own rollback/except: whatever
                # slips through here (including session_scope's closing commit itself
                # failing) should cost this one event, not the rest of the overnight
                # run - see the run-frimley-2022 StringDataRightTruncation incident,
                # where an uncontained error here killed everything after it.
                print(f"  ERROR: {url}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--organiser-id", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=["normal", "dry-run", "sanity-check"],
        default="normal",
        help="normal (default): crawl every new event URL found for each organiser. "
        "dry-run: discover new event URLs but don't crawl/store them, just print. "
        "sanity-check: crawl only the first new event URL per organiser - a quick "
        "smoke test across every organiser rather than a full crawl.",
    )
    parser.add_argument(
        "--check-mode",
        choices=["hash-check", "url-check"],
        default="hash-check",
        help="hash-check (default): always fetch, skip re-extraction if content is unchanged. "
        "url-check: skip entirely (no fetch) if the URL is already stored.",
    )
    parser.add_argument(
        "--scraper-backend",
        choices=["crawl4ai", "firecrawl"],
        default="crawl4ai",
        help="crawl4ai (default): self-hosted, no per-page cost, falls back to Firecrawl "
        "automatically on failure. firecrawl: always use Firecrawl's hosted API.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="re-crawl every event URL for each organiser, not just new ones, and always "
        "re-extract even if content is unchanged - replaces existing rows, adds missing "
        "ones. Overrides --check-mode. Normally combined with --organiser-id, e.g. "
        "--organiser-id 42 --force-refresh.",
    )
    args = parser.parse_args()
    run(
        limit=args.limit,
        organiser_id=args.organiser_id,
        mode=args.mode,
        check_mode=args.check_mode,
        scraper_backend=args.scraper_backend,
        force_refresh=args.force_refresh,
    )
