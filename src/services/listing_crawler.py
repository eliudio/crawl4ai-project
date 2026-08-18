"""
Stage 1 of the pipeline: given an organiser, fetch its listing/homepage and
find event detail URLs on it.

No hand-maintained per-site CSS selector config (unlike the old prototype's
SiteConfig approach, which generated + validated per-site selectors for three
loading strategies via Selenium). Instead:

- crawl_listing() dispatches to whichever handler Organiser.handler names (see
  discovery_handlers.py's registry) - every organiser has exactly one, defaulting
  to "default", this module's own default_handler. That handler prefers reading a
  sitemap (a Sitemap: entry read from this organiser's robots.txt by
  discover_sitemaps.py, passed in via handler_params["sitemap_url"]) over
  everything else below, when one is known: sitemap_crawler.py reads it directly -
  a static XML file, no browser/clicking/per-page LLM confirmation needed - and
  _crawl_from_sitemap only falls through to the mechanisms below when no sitemap
  is known, or the known one couldn't be resolved into anything.
- Failing that, links are first narrowed down heuristically by domain + junk
  patterns (filter_candidate_links), then the survivors are confirmed as
  actual event detail pages by the LLM (llm_extractor.analyze_listing_page) -
  the heuristic pass alone can't tell an event link apart from e.g. /about or
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

from bs4 import BeautifulSoup
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from services import discovery_handlers, event_crawler, llm_extractor, parkrun_feed, robots, scraper_client, sitemap_crawler
from services.link_filters import filter_candidate_links
from services.models import CrawlRun, CrawlRunType, CrawlStatus, Event, Organiser

# Safety caps, not tuning knobs - just there to bound a runaway pagination
# loop or a "load more" page that never stops offering more.
_MAX_LISTING_PAGES = 50
_LOAD_MORE_MAX_ROUNDS = 50
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


def _validate_selector(html: str, selector: str | None) -> str | None:
    """
    An LLM-picked CSS selector is wrong in two costly ways, and this catches
    both before it's ever used for a real click: matching zero elements
    (e.g. built from an attribute like Angular's `classname` that only
    appears in server-rendered markup, not the live DOM the scraper's browser
    actually clicks against - every click then hangs and burns 3 retries
    with exponential backoff on something that can never succeed) or
    matching more than one (a generic class shared with unrelated buttons
    elsewhere on the page - the click lands on whichever matches first in
    the DOM, not the one meant, silently doing nothing useful).

    Checked against this same round's `html` (what the LLM was shown) -
    not a full guarantee, since a selector unique here can still fail to
    resolve post-hydration, but it's a real filter for both known failure
    modes at zero extra cost.
    """
    if not selector:
        return None
    try:
        count = len(BeautifulSoup(html, "html.parser").select(selector))
    except Exception:
        count = 0
    if count != 1:
        print(f"DEBUG selector {selector!r} matches {count} element(s), not 1 - discarding")
        return None
    return selector


_APPEND_TEXT_KEYWORDS = ("load more", "show more", "view more")
_NEXT_TEXT_KEYWORDS = ("next", "next page")


def _element_text_signals(el) -> set[str]:
    signals = {el.get_text(strip=True).lower()}
    for attr in ("aria-label", "title"):
        value = el.get(attr)
        if value:
            signals.add(value.strip().lower())
    return signals


def _piece(node) -> str:
    node_id = node.get("id")
    if node_id:
        return f"#{node_id}"
    classes = node.get("class") or []
    return node.name + "".join(f".{c}" for c in classes) if classes else node.name


def _find_click_selector(html: str, keywords: tuple[str, ...]) -> str | None:
    """
    Deterministic alternative to asking the LLM to invent a CSS selector
    (see _validate_selector's docstring for why that's unreliable): find
    the actual clickable element whose own visible text - or aria-label/
    title, for icon-only controls - says one of `keywords`, then walk up
    its ancestors combining tag+class/id until the resulting selector
    uniquely matches just that one element. Grounding the selector in the
    element's own real text sidesteps both of an LLM-guessed selector's
    failure modes: a hallucinated/framework-internal attribute that
    doesn't survive to the live DOM, and a reusable class shared with
    unrelated buttons elsewhere on the page - "Load more"/"Next" text is
    reliably unique even when the classes around it aren't (confirmed in
    practice: runthrough.co.uk's real Load More button shares its only
    classes with 44 unrelated per-event "Book now" buttons, but its own
    text is the only "load more" on the page).

    Returns None if there isn't exactly one text match, or no ancestor
    chain resolves to a unique selector within a few levels - callers
    should fall back to the LLM's own guess (still run through
    _validate_selector) in that case.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        el for el in soup.find_all(["button", "a"])
        if any(kw in signal for signal in _element_text_signals(el) for kw in keywords)
    ]
    if len(candidates) != 1:
        return None

    pieces: list[str] = []
    node = candidates[0]
    for _ in range(6):
        pieces.insert(0, _piece(node))
        selector = " ".join(pieces)
        if len(soup.select(selector)) == 1:
            return selector
        node = node.parent
        if node is None or getattr(node, "name", None) in (None, "html", "[document]"):
            break
    return None


def _resolve_click_selector(html: str, interaction_type: str, llm_selector: str | None) -> str | None:
    """
    Single entry point _analyze_page uses to get a click selector for
    either "append" or "paginate": always prefer _find_click_selector's
    deterministic, text-grounded result over the LLM's guess, falling back
    to the (still uniqueness-checked) LLM guess only when no exactly-one
    text match exists to derive one from.
    """
    if interaction_type == "append":
        keywords = _APPEND_TEXT_KEYWORDS
    elif interaction_type == "paginate":
        keywords = _NEXT_TEXT_KEYWORDS
    else:
        return None
    return _find_click_selector(html, keywords) or _validate_selector(html, llm_selector)


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
    clicks), using scraper_client.scrape's normal formats=["markdown"] path -
    the active backend's own conversion, with real main-content extraction,
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
            page_markdown, page_links, _html, _final_url = scraper_client.scrape(
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


def _discover_listing_urls(session: Session, organiser: Organiser) -> list[str]:
    """
    Inspect the homepage with AI to find where event listings live, and
    persist the result on the organiser so this only has to run once. Covers
    all three cases: events on the homepage itself, a single dedicated
    listing page, or several (e.g. per-category) listing pages.
    """
    if not robots.is_allowed(organiser.homepage_url, registrator=organiser.registrator):
        print(f"ROBOTS-SKIP: {organiser.homepage_url} (listing discovery)")
        return []

    markdown, links, _html, _url = scraper_client.scrape(organiser.homepage_url, want_links=True)
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
        markdown, links, html, _url = scraper_client.scrape(page_url, want_links=True, want_html=True)
        probe = llm_extractor.detect_load_more(page_url, html)
        interaction_type = probe["interaction_type"]
        # Fixed at round 0 and never replaced: each later round reloads the page from
        # scratch and replays `round_num` clicks against it. Re-probing the post-click
        # HTML each round can report a different selector (some "load more" plugins add
        # an "active"/"loading" class to the button once clicked), but that selector
        # only exists on the already-clicked DOM, not on the next round's fresh load -
        # using it there clicks nothing and silently regresses to the unclicked page.
        load_more_selector = _resolve_click_selector(html, interaction_type, probe["load_more_selector"])
        print(f"DEBUG round 0: len(links)={len(links)} interaction_type={interaction_type!r} selector={load_more_selector!r}")
        for url in links:
            print(f"DEBUG round 0 link: {url!r}")

        if interaction_type == "paginate" and not load_more_selector:
            # No real "next" element to click reliably - "paginate" has no
            # scroll fallback the way "append" does below, so there's
            # nothing safe left to do but treat round 0 as everything
            # reachable, same as interaction_type == "none".
            print("DEBUG paginate: no valid selector, falling back to round 0's content only")
            interaction_type = "none"

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
                round_markdown, round_links, round_html, _url = scraper_client.scrape(
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
    session: Session, organiser: Organiser, listing_url: str, seen: set[str], force: bool = False
) -> list[str]:
    """
    Crawl one listing URL, following numbered pagination (a distinct
    next_page_url each time) up to _MAX_LISTING_PAGES. Each page visited gets
    its own CrawlRun audit row. Returns the new event URLs found across all
    pages of this listing - or, when force=True, every confirmed event URL
    regardless of whether it's already stored (still deduped against `seen`
    within this run, just not against the database) - see crawl_listing.
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

        if not robots.is_allowed(page_url, registrator=organiser.registrator):
            # See event_crawler.py's own ROBOTS-SKIP print - same grep-able marker,
            # so a skipped listing page isn't silently indistinguishable from one
            # that genuinely failed to scrape/analyze.
            print(f"ROBOTS-SKIP: {page_url} (listing)")
            run.status = CrawlStatus.SKIPPED
            run.detail = "disallowed by robots.txt"
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            break

        try:
            event_links, candidates, next_page_url = _analyze_page(page_url, organiser.homepage_url)

            existing_urls = set() if force else set(
                session.scalars(select(Event.url).where(Event.url.in_(event_links))).all()
            )
            page_new = [u for u in event_links if u not in existing_urls and u not in seen]
            seen.update(page_new)
            new_urls.extend(page_new)

            run.status = CrawlStatus.SUCCESS
            detail_suffix = f"{len(page_new)} to refresh (force)" if force else f"{len(page_new)} new"
            run.detail = f"{len(candidates)} candidate links, {len(event_links)} confirmed events, {detail_suffix}"
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


def _filter_new_urls(session: Session, event_urls: list[str], force: bool) -> list[str]:
    """Shared by every handler below that gets back a whole feed's worth of URLs in one
    go (sitemap, parkrun) - force=True skips the database lookup entirely and returns
    every URL as-is, matching local_runner.py's --force-refresh contract exactly."""
    if force:
        return event_urls
    existing_urls = set(session.scalars(select(Event.url).where(Event.url.in_(event_urls))).all())
    return [u for u in event_urls if u not in existing_urls]


def _crawl_from_sitemap(session: Session, organiser: Organiser, sitemap_url: str, force: bool = False) -> list[str] | None:
    """
    A direct, complete list of the site's URLs read straight from a static XML file
    (see sitemap_crawler.py) - no browser, no clicking through load-more/pagination, no
    per-page LLM confirmation needed. Only reached at all when handler_params has a
    sitemap_url (see default_handler) - itself only ever populated once
    discover_sitemaps.py has found a Sitemap: entry in this organiser's robots.txt.

    Returns None (not []) when the sitemap couldn't be resolved into anything -
    default_handler falls back to the normal page-crawling mechanism in that case,
    rather than treating a sitemap that turned out to be unusable as "this organiser
    has zero events". force=True returns every URL in the sitemap, not just ones
    missing from the database - see crawl_listing.
    """
    event_urls = sitemap_crawler.get_event_urls(sitemap_url, organiser.homepage_url, registrator=organiser.registrator)
    if event_urls is None:
        print(f"DEBUG sitemap {sitemap_url!r} unusable, falling back to listing_urls")
        return None

    run = CrawlRun(
        run_type=CrawlRunType.LISTING,
        target_url=sitemap_url,
        organiser_id=organiser.id,
        status=CrawlStatus.SUCCESS,
        started_at=datetime.now(timezone.utc),
    )
    new_urls = _filter_new_urls(session, event_urls, force)
    run.detail = f"{len(event_urls)} urls from sitemap, {len(new_urls)} new" + (
        " (force refresh: returning all)" if force else ""
    )
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    return new_urls


def default_handler(
    session: Session, organiser: Organiser, params: dict, force: bool = False,
    dry_run: bool = False, event_limit: int | None = None,
) -> list[str]:
    """
    The handler every organiser gets unless Organiser.handler names something else -
    registered under "default" below, looked up the same way as any other handler (see
    discovery_handlers.py) rather than being crawl_listing()'s own hardcoded fallback.

    Prefers reading params["sitemap_url"] (see _crawl_from_sitemap) when present - only
    falls through to listing_urls/clicking-through when none is known, or the known one
    couldn't be resolved into anything.

    dry_run/event_limit: unused - see discovery_handlers.DiscoveryHandler's own
    docstring for why they exist at all. This handler only ever discovers a URL list,
    never writes real event data itself, so the caller's own slicing/skip-crawling of
    whatever it returns already gives the same effect.
    """
    sitemap_url = params.get("sitemap_url")
    if sitemap_url:
        event_urls = _crawl_from_sitemap(session, organiser, sitemap_url, force=force)
        if event_urls is not None:
            return event_urls

    listing_urls = organiser.listing_urls or []
    if not listing_urls:
        listing_urls = _discover_listing_urls(session, organiser)
        if not listing_urls:
            return []

    new_urls: list[str] = []
    seen: set[str] = set()

    for listing_url in listing_urls:
        new_urls.extend(_crawl_one_listing_url(session, organiser, listing_url, seen, force=force))

    return new_urls


def _parkrun_handler(
    session: Session, organiser: Organiser, params: dict, force: bool = False,
    dry_run: bool = False, event_limit: int | None = None,
) -> list[str]:
    """
    Registered under "parkrun" below - events.json is a direct, structured, worldwide
    feed of every location (see parkrun_feed.py), cheaper and more reliable than
    guessing from an HTML page, so parkrun's own homepage is never opened for listing
    discovery at all.

    Deliberately does NOT fall back to default_handler when the feed is unusable -
    unlike a sitemap, parkrun's homepage isn't a listing page in any sense
    default_handler's LLM-guessing could make sense of; falling back to it would just
    burn an LLM call for nothing. Returns [] in that case instead, same as any other
    organiser whose listing genuinely has nothing crawlable this run.

    handler_params["registrator"]: an optional override of organiser.registrator,
    applied here before anything else in this function - including before the
    robots.txt-respecting-or-not decision that registrator itself controls (see
    robots.is_allowed's own docstring and README.md's "registrator" section for why
    this override exists and the real-person-authorisation it stands in for). Persisted
    onto the organiser row itself (not just used locally for this one call) so
    event_crawler.py's later per-event crawls of the URLs this run discovers see the
    same, already-resolved registrator too, without each of those separate calls having
    to know about this override mechanism themselves.

    A non-"bot" registrator (the override above being active) also changes what this
    function does with each URL, not just whether robots.txt applies to fetching the
    feed itself: instead of returning URLs for the normal per-event crawl_event scrape,
    it registers every event directly here from the feed's own data (see
    event_crawler.register_event_from_fields/parkrun_feed.get_events) - which is exactly
    why dry_run/event_limit matter here and nowhere else in this file: every other
    handler only ever discovers a URL list and lets the caller (local_runner.py) decide
    how many of those to actually crawl/whether to crawl them at all for --mode sanity-
    check/dry-run, but THIS branch does its real DB writes right here, before ever
    returning anything for that caller-side slicing to act on - confirmed in practice as
    a real gap, not hypothetical: a plain --mode sanity-check run against parkrun with a
    registrator override active registered its *entire* feed (1,417 events) in one go,
    since there was nothing left in the returned list for the caller's own `urls[:1]` to
    limit. event_limit slices the feature list before any registration happens at all;
    dry_run skips register_event_from_fields entirely and returns the (limit-sliced) URLs
    instead, so local_runner.py's own existing, unmodified "for url in urls: print(...)"
    dry-run preview keeps working unchanged for this branch too - same contract every
    other handler already satisfies, just reached a different way here.

    Confirmed in practice why the registrator branch exists at all, not just an
    optimisation: parkrun's own event pages return HTTP 403 for an unattended request
    (its own anti-bot protection), so the normal scrape-and-extract path reliably
    produces INVALID events for parkrun regardless of registrator - the "bot" path still
    goes through it anyway (robots.txt being satisfied doesn't mean the site's own server
    will actually serve the page), since only the registrator-override path is meant to
    represent "we already have a direct, obtained reason to treat this source specially."
    """
    override = params.get("registrator")
    if override and organiser.registrator != override:
        organiser.registrator = override
        session.add(organiser)
    # A falsy registrator (None/"") - e.g. a not-yet-flushed Organiser ORM instance,
    # whose Python-side default= isn't applied until insert - must fail safe as "bot",
    # same reasoning/same gotcha as robots.is_allowed's own falsy-registrator handling;
    # never let it be mistaken for "the override is active".
    registrator = organiser.registrator or "bot"
    country_code = params.get("country_code", parkrun_feed.UK_COUNTRY_CODE)

    if registrator != "bot":
        events = parkrun_feed.get_events(country_code=country_code, registrator=registrator)
        if events is None:
            print("DEBUG parkrun feed unusable this run")
            return []
        if event_limit is not None:
            events = events[:event_limit]

        if dry_run:
            # No DB writes at all here - not even a CrawlRun - same "preview only,
            # touch nothing" guarantee every other handler already gives dry-run
            # for free (they never write real event data during listing at all);
            # this is the one branch that otherwise would.
            print(f"{organiser.name}: [dry-run] {len(events)} event(s) would be registered directly from parkrun feed (registrator={registrator!r})")
            return [event_url for event_url, _fields in events]

        run = CrawlRun(
            run_type=CrawlRunType.LISTING,
            target_url=parkrun_feed.EVENTS_JSON_URL,
            organiser_id=organiser.id,
            status=CrawlStatus.SUCCESS,
            started_at=datetime.now(timezone.utc),
        )
        for event_url, fields in events:
            event_crawler.register_event_from_fields(session, organiser.id, event_url, fields, registrator)
        run.detail = f"{len(events)} event(s) registered directly from parkrun feed (registrator={registrator!r})"
        run.finished_at = datetime.now(timezone.utc)
        session.add(run)
        # Printed directly here (not just left in run.detail/the crawl_runs table) -
        # this is the one figure that actually matters, since the caller's own generic
        # "N new event URL(s)" print (see local_runner.py) is always 0 for this branch
        # by design (see this function's own docstring) and would otherwise read as
        # "nothing happened" when N events were in fact just registered.
        print(f"{organiser.name}: {run.detail}")
        # Already fully processed above - nothing left for the normal per-event
        # crawl_event step the caller would otherwise run on whatever this returns.
        return []

    event_urls = parkrun_feed.get_event_urls(country_code=country_code, registrator=registrator)
    if event_urls is None:
        print("DEBUG parkrun feed unusable this run")
        return []

    run = CrawlRun(
        run_type=CrawlRunType.LISTING,
        target_url=parkrun_feed.EVENTS_JSON_URL,
        organiser_id=organiser.id,
        status=CrawlStatus.SUCCESS,
        started_at=datetime.now(timezone.utc),
    )
    new_urls = _filter_new_urls(session, event_urls, force)
    run.detail = f"{len(event_urls)} urls from parkrun feed, {len(new_urls)} new" + (
        " (force refresh: returning all)" if force else ""
    )
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    return new_urls


discovery_handlers.register_handler("default", default_handler)
discovery_handlers.register_handler("parkrun", _parkrun_handler)


def crawl_listing(
    session: Session, organiser: Organiser, force: bool = False,
    dry_run: bool = False, event_limit: int | None = None,
) -> list[str]:
    """
    Crawl one organiser's listing page(s), by dispatching to whichever handler
    Organiser.handler names (see discovery_handlers.py) - every organiser has exactly
    one, defaulting to "default" (this module's own historical sitemap-then-LLM-
    guessed behaviour, now just one named handler among others, e.g. "parkrun").

    Returns the event URLs that are new (not already stored) so the caller can decide
    how to hand them off (Pub/Sub in production, direct in-process call for local
    runs) - or, when force=True, every event URL currently found for this organiser
    regardless of whether it's already stored, for a manual full refresh (see
    local_runner.py's --force-refresh): the caller is expected to re-crawl each one
    with event_crawler.crawl_event(check_mode="force") so an already-stored URL gets
    replaced in place rather than skipped.

    dry_run/event_limit: forwarded to whichever handler runs, as-is - see
    discovery_handlers.DiscoveryHandler's own docstring for why they exist (only
    meaningful for a handler that writes real event data inline, within this call,
    like _parkrun_handler's registrator-override path - local_runner.py's own --mode
    sanity-check/dry-run already gets the same effect on every other handler for free,
    by slicing/skipping whatever this function returns).
    """
    handler = discovery_handlers.get_handler(organiser.handler)
    if handler is None:
        print(
            f"WARNING: organiser {organiser.id} ({organiser.name}) has unknown "
            f"handler {organiser.handler!r} - falling back to 'default'"
        )
        handler = discovery_handlers.get_handler("default")
    return handler(session, organiser, organiser.handler_params or {}, force, dry_run, event_limit)
