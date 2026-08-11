"""
Stage 1 of the pipeline: given an organiser, fetch its listing/homepage and
find event detail URLs on it.

No hand-maintained per-site CSS selector config (unlike the old prototype's
SiteConfig approach, which generated + validated per-site selectors for three
loading strategies via Selenium). Instead:

- Links are first narrowed down heuristically by domain + junk patterns
  (filter_candidate_links), then the survivors are confirmed as actual event
  detail pages by the LLM (llm_extractor.analyze_listing_page) - the
  heuristic pass alone can't tell an event link apart from e.g. /about or
  /results on the same domain.
- The rest of a listing's events are reached one of three genuinely
  different ways, detected via llm_extractor.detect_load_more and handled by
  three separate code paths rather than one shared mechanism (conflating
  them is what caused real bugs - see git history):
    1. A distinct next-page URL (real numbered pagination with an actual
       href) - followed by looping `_crawl_one_listing_url` with each
       next_page_url in turn, capped at _MAX_LISTING_PAGES.
    2. A same-URL "load more"/infinite-scroll affordance that APPENDS more
       items below what's already shown - handled by _analyze_page's
       round-loop, clicking (or scrolling) and re-checking until pressing
       further surfaces nothing new.
    3. A same-URL numbered/"Next" pager with no real href that REPLACES the
       currently shown items each press (as opposed to appending) - handled
       by _crawl_paginated_same_url, which extracts and unions each page's
       own confirmed events separately, since only reading the final press's
       snapshot (fine for case 2) would silently drop every earlier page.

That's the trade that makes this scale to hundreds of organisers without a
bespoke config *maintained by us* per site - the selector is derived fresh
from the live page each crawl rather than hand-written and stored; phase 2
(aggregator-driven discovery) can layer smarter per-site heuristics on top
later if needed.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from services import firecrawl_client, llm_extractor, robots
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event, Organiser

_JUNK_SUBSTRINGS = [
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com",
    "mailto:", "tel:", "javascript:",
    "/privacy", "/terms", "/cookie", "/login", "/signin", "/signup",
    "/account", "/cart", "/checkout", "/basket",
]

# Safety caps, not tuning knobs - just there to bound a runaway pagination
# loop or a "load more" page that never stops offering more.
_MAX_LISTING_PAGES = 20
_LOAD_MORE_MAX_ROUNDS = 4
_LOAD_MORE_SCROLL_STEP_INCREMENT = 6
# AJAX-backed "load more" plugins (e.g. Divi Machine) fire a real
# wp-admin/admin-ajax.php request and re-render on response - 1500ms was
# measured too short (0 new links) on a live site where the round trip +
# render reliably needs ~3s; 4000ms leaves margin without hurting much on
# sites that resolve faster. Also used for case 3's click-replay (see
# _crawl_paginated_same_url) - tried giving intermediate clicks a shorter
# wait there to cut cumulative time on deep pagers, but that broke content
# correctness outright (a real site stopped advancing past its 5th page
# instead of reaching its 16th) - a uniform generous wait is what's actually
# proven to work, so this is the one wait used everywhere clicks replay.
_LOAD_MORE_WAIT_MS = 4000


def _scroll_actions(steps: int) -> list[dict]:
    actions: list[dict] = []
    for _ in range(steps):
        actions.append({"type": "scroll", "direction": "down"})
        actions.append({"type": "wait", "milliseconds": _LOAD_MORE_WAIT_MS})
    return actions


def _click_actions(selector: str, presses: int) -> list[dict]:
    """
    Click a "load more"/"next"-style element `presses` times in one fresh
    page load (there's no persistent browser session across separate
    scrape() calls, so reaching "pressed 3 times" means clicking 3 times in
    a row here, not resuming a previous click).
    """
    actions: list[dict] = []
    for _ in range(presses):
        actions.append({"type": "click", "selector": selector})
        actions.append({"type": "wait", "milliseconds": _LOAD_MORE_WAIT_MS})
    return actions


def _crawl_paginated_same_url(
    page_url: str, homepage_url: str, selector: str, first_markdown: str, first_links: list[str]
) -> tuple[list[str], list[str]]:
    """
    Case 3 (see module docstring): a numbered/"Next" pager that swaps this
    same URL's visible items via JavaScript with no real href per page,
    unlike case 2's "Load more" which grows the same page. Because each
    press REPLACES rather than adds, each page's own confirmed events have
    to be extracted and unioned in separately - only reading the last
    press's snapshot (as case 2's round-loop does) would silently drop every
    earlier page.

    Replays clicks from a fresh page load each round (page N needs N-1
    clicks), using firecrawl_client.scrape's normal formats=["markdown"]
    path - Firecrawl's own conversion, with real main-content extraction,
    not a raw-html workaround.

    An earlier version of this tried to detect a URL query-param pattern
    (some "Search UI"/Elastic-style pagers sync page state into the URL even
    without a real href) and switch to fetching pages directly once found,
    to avoid a growing click count. Dropped: in practice Firecrawl's
    reported post-click URL was not just occasionally stale but sometimes
    outright wrong (a single click reporting a URL for a page several
    clicks ahead) - not a signal worth building on, even with a
    confirmation check guarding against acting on it. Plain click-replay,
    while it doesn't scale indefinitely (very deep pagers can hit a request
    duration limit - seen in practice around 16 replayed clicks), is what's
    actually proven correct.

    Returns (event_urls, all_candidates_seen_across_every_page).
    """
    candidates = filter_candidate_links(first_links, homepage_url)
    analysis = llm_extractor.analyze_listing_page(page_url, first_markdown, candidates)
    confirmed: set[str] = set(analysis["event_urls"])
    all_candidates: set[str] = set(candidates)
    print(f"DEBUG paginate page 1: {len(candidates)} candidates, {len(confirmed)} confirmed")

    for page_num in range(2, _MAX_LISTING_PAGES + 1):
        presses = page_num - 1
        actions = _click_actions(selector, presses=presses)
        print(f"DEBUG paginate page {page_num}: clicking {selector!r} x{presses}")
        try:
            page_markdown, page_links, _html, _final_url = firecrawl_client.scrape(
                page_url, want_links=True, actions=actions
            )
        except Exception as e:
            print(f"DEBUG paginate page {page_num}: scrape failed ({e!r}), stopping")
            break

        page_candidates = filter_candidate_links(page_links, homepage_url)
        all_candidates |= set(page_candidates)
        if not page_candidates:
            print(f"DEBUG paginate page {page_num}: no candidates, stopping")
            break

        page_analysis = llm_extractor.analyze_listing_page(page_url, page_markdown, page_candidates)
        page_confirmed = set(page_analysis["event_urls"])
        new = page_confirmed - confirmed
        print(f"DEBUG paginate page {page_num}: {len(page_candidates)} candidates, {len(page_confirmed)} confirmed, {len(new)} new")
        if not new:
            print(f"DEBUG paginate page {page_num}: nothing new, stopping")
            break
        confirmed |= new

    return sorted(confirmed), sorted(all_candidates)


def _strip_www(netloc: str) -> str:
    return netloc[4:] if netloc.startswith("www.") else netloc


def _core_label(netloc: str) -> str:
    """First label of a (www-stripped) domain - approximates an organiser's
    own brand name (e.g. 'zigzagrunning' from 'zigzagrunning.co.uk') without
    needing a public-suffix list."""
    return netloc.split(".")[0]


def _same_site(url: str, homepage_netloc: str) -> bool:
    netloc = _strip_www(urlparse(url).netloc.lower())
    homepage_netloc = _strip_www(homepage_netloc.lower())
    if netloc == homepage_netloc or netloc.endswith("." + homepage_netloc):
        return True
    # Some organisers' actual event pages live on a third-party ticketing
    # platform under a branded subdomain (e.g. zigzagrunning.eventrac.co.uk
    # for homepage zigzagrunning.co.uk) rather than the organiser's own
    # domain - a domain/subdomain match alone misses these entirely. Treat it
    # as the same site if the candidate's leftmost label is the organiser's
    # own brand name, regardless of what domain it's a subdomain of.
    return _core_label(netloc) == _core_label(homepage_netloc)


def filter_candidate_links(links: list[str], homepage_url: str) -> list[str]:
    homepage_netloc = urlparse(homepage_url).netloc
    seen: set[str] = set()
    candidates: list[str] = []
    for url in links:
        url = url.strip()
        if not url or url in seen:
            continue
        lower = url.lower()
        if any(junk in lower for junk in _JUNK_SUBSTRINGS):
            continue
        if not _same_site(url, homepage_netloc):
            continue
        seen.add(url)
        candidates.append(url)
    return candidates


def _discover_listing_urls(session: Session, organiser: Organiser) -> list[str]:
    """
    Inspect the homepage with AI to find where event listings live, and
    persist the result on the organiser so this only has to run once. Covers
    all three cases: events on the homepage itself, a single dedicated
    listing page, or several (e.g. per-category) listing pages.
    """
    markdown, links, _html, _url = firecrawl_client.scrape(organiser.homepage_url, want_links=True)
    candidates = filter_candidate_links(links, organiser.homepage_url)
    discovered = llm_extractor.discover_listing_urls(organiser.homepage_url, markdown, candidates)

    organiser.listing_urls = discovered
    session.add(organiser)
    return discovered


def _analyze_page(page_url: str, homepage_url: str) -> tuple[list[str], list[str], str | None]:
    """
    Scrape one listing page and analyze it. Returns (event_urls, all_candidates,
    next_page_url).

    llm_extractor.detect_load_more classifies which of case 2 or case 3 (see
    module docstring) applies, if either - "append" (Load More/infinite
    scroll, grows the same page) or "paginate" (numbered/"Next" pager, no
    real href, replaces the same page's items). "paginate" is delegated
    entirely to _crawl_paginated_same_url, which does its own per-page
    analysis and union (necessary since replacing content means only the
    final press's snapshot would otherwise survive). "append" and "none"
    share the rest of this function: repeatedly press (click if there's a
    selector; scroll for genuine infinite scroll) and re-probe until the
    affordance is gone, then run llm_extractor.analyze_listing_page exactly
    once on the now fully-loaded page. Each scrape() call is a fresh page
    load though (no persistent browser session across calls), so "press
    further" means re-scraping with progressively more clicks/scroll, not
    resuming a previous one.

    The probe alone isn't trustworthy as a stop condition for "append": some
    plugins (e.g. Divi Machine's "Load more") leave "load more is enabled for
    this archive" markers in the DOM that don't reflect whether there's
    currently anything left to load, so the LLM can keep reporting
    interaction_type="append" forever even once every event is already
    showing. The actual stop condition is therefore whether pressing again
    surfaced any link not already seen - if not, the page has genuinely
    stopped changing and we treat it as exhausted regardless of what the
    probe says. _LOAD_MORE_MAX_ROUNDS is a secondary safety valve on top of
    that, not a tuning knob.
    """
    try:
        markdown, links, html, _url = firecrawl_client.scrape(page_url, want_links=True, want_html=True)
        probe = llm_extractor.detect_load_more(page_url, html)
        interaction_type = probe["interaction_type"]
        # Fixed at round 0 and never replaced: each later round reloads the page from
        # scratch and replays `round_num` clicks against it. Re-probing the post-click
        # HTML each round can report a different selector (some "load more" plugins add
        # an "active"/"loading" class to the button once clicked), but that selector
        # only exists on the already-clicked DOM, not on the next round's fresh load -
        # using it there clicks nothing and silently regresses to the unclicked page.
        load_more_selector = probe["load_more_selector"]
        print(f"DEBUG round 0: len(links)={len(links)} interaction_type={interaction_type!r} selector={load_more_selector!r}")
        for url in links:
            print(f"DEBUG round 0 link: {url!r}")

        if interaction_type == "paginate" and load_more_selector:
            event_urls, candidates = _crawl_paginated_same_url(page_url, homepage_url, load_more_selector, markdown, links)
            print(f"before return: len(links)={len(event_urls)} len(candidates)={len(candidates)} (paginate)")
            # A JS-driven same-URL pager has no real href to a further page -
            # every reachable page has already been walked above.
            return event_urls, candidates, None

        seen_links = set(links)
        round_num = 0
        while interaction_type == "append" and round_num < _LOAD_MORE_MAX_ROUNDS:
            round_num += 1
            if load_more_selector:
                actions = _click_actions(load_more_selector, presses=round_num)
                print(f"DEBUG round {round_num}: clicking {load_more_selector!r} x{round_num}")
            else:
                steps = _LOAD_MORE_SCROLL_STEP_INCREMENT * (round_num + 1)
                actions = _scroll_actions(steps)
                print(f"DEBUG round {round_num}: scrolling x{steps}")

            try:
                round_markdown, round_links, round_html, _url = firecrawl_client.scrape(
                    page_url, want_links=True, want_html=True, actions=actions
                )
            except Exception as e:
                # A later round replays *more* clicks than the last successful one
                # (round_num clicks, from a fresh page load each time) - if the
                # button/container is gone from the DOM once nothing's left to load
                # (common: plugins hide it when exhausted), that extra click can
                # itself throw (seen in practice as Firecrawl's own
                # InternalServerError). That's a sign we're past the end, not a
                # real failure - the previous round's links are already good, so
                # fall back to them instead of discarding a page we already loaded
                # correctly.
                print(f"DEBUG round {round_num}: scrape failed ({e!r}), stopping and keeping previous round's {len(links)} links")
                break

            new_links = set(round_links) - seen_links
            print(f"DEBUG round {round_num}: len(links)={len(round_links)} new_links={len(new_links)}")
            for url in round_links:
                print(f"DEBUG round {round_num} link: {'NEW ' if url in new_links else '    '}{url!r}")

            if not new_links:
                print(f"DEBUG round {round_num}: nothing new, breaking (keeping previous round's {len(links)} links)")
                break  # pressing further surfaced nothing new - exhausted, whatever the probe claims

            # Only now commit this round's (strictly better) result - a regressed round
            # must never overwrite the better page we already have.
            markdown, links, html = round_markdown, round_links, round_html
            seen_links |= new_links

            probe = llm_extractor.detect_load_more(page_url, html)
            interaction_type = probe["interaction_type"]
            print(f"DEBUG round {round_num}: probe after press -> interaction_type={interaction_type!r} selector={probe['load_more_selector']!r} (selector not used - keeping round 0's)")

        # Only now, on the fully-loaded (or round-capped) page, actually read events.
        print(f"before filter_candidate_links: len(links)={len(links)}")
        candidates = filter_candidate_links(links, homepage_url)
        print(f"before analyze_listing_page: len(links)={len(links)} len(candidates)={len(candidates)}")
        analysis = llm_extractor.analyze_listing_page(page_url, markdown, candidates)

        missing = set(candidates) - set(analysis["event_urls"])
        if missing:
            print(f"DEBUG {page_url}: {len(candidates)} candidates, {len(analysis['event_urls'])} confirmed, {len(missing)} missing")
            for url in missing:
                slug = url.rstrip("/").split("/")[-1]
                print(f"DEBUG   missing={url!r} slug_in_markdown={slug in markdown}")

        links = analysis["event_urls"]
        print(f"before return: len(links)={len(links)} len(candidates)={len(candidates)}")
        return links, candidates, analysis["next_page_url"]

    except Exception as e:
        print(f"exception _analyze_page: {e}")
        raise e


def _crawl_one_listing_url(
    session: Session, organiser: Organiser, listing_url: str, seen: set[str]
) -> list[str]:
    """
    Crawl one listing URL, following numbered pagination (a distinct
    next_page_url each time) up to _MAX_LISTING_PAGES. Each page visited gets
    its own CrawlRun audit row. Returns the new event URLs found across all
    pages of this listing.
    """
    new_urls: list[str] = []
    visited: set[str] = set()
    page_url: str | None = listing_url

    for _ in range(_MAX_LISTING_PAGES):
        if page_url is None or page_url in visited:
            break
        visited.add(page_url)

        run = CrawlRun(
            run_type=CrawlRunType.LISTING,
            target_url=page_url,
            organiser_id=organiser.id,
            status=CrawlStatus.FAILED,
            started_at=datetime.now(timezone.utc),
        )

        if not robots.is_allowed(page_url):
            run.status = CrawlStatus.SKIPPED
            run.detail = "disallowed by robots.txt"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            break

        try:
            event_links, candidates, next_page_url = _analyze_page(page_url, organiser.homepage_url)

            existing_urls = set(
                session.scalars(select(Event.url).where(Event.url.in_(event_links))).all()
            )
            page_new = [u for u in event_links if u not in existing_urls and u not in seen]
            seen.update(page_new)
            new_urls.extend(page_new)

            run.status = CrawlStatus.SUCCESS
            run.detail = f"{len(candidates)} candidate links, {len(event_links)} confirmed events, {len(page_new)} new"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)

            page_url = next_page_url
        except requests.exceptions.ConnectionError:
            raise  # can't reach Firecrawl at all - stop the run rather than retrying every remaining listing
        except Exception as e:
            run.detail = f"{type(e).__name__}: {e}"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            break

    return new_urls


def crawl_listing(session: Session, organiser: Organiser) -> list[str]:
    """
    Crawl one organiser's listing page(s). Returns the event URLs that are
    new (not already stored) so the caller can decide how to hand them off
    (Pub/Sub in production, direct in-process call for local runs).
    """
    listing_urls = organiser.listing_urls or []
    if not listing_urls:
        listing_urls = _discover_listing_urls(session, organiser)
        if not listing_urls:
            return []

    new_urls: list[str] = []
    seen: set[str] = set()

    for listing_url in listing_urls:
        new_urls.extend(_crawl_one_listing_url(session, organiser, listing_url, seen))

    return new_urls
