"""
A small, generic registry (the "factory") mapping an Organiser.handler name to
the callable that actually discovers that organiser's event URLs - see
listing_crawler.py's crawl_listing(), the one place that looks a handler up
by name and calls it.

Deliberately knows nothing about what handlers exist or how any of them
work - this module has zero dependency on listing_crawler.py/parkrun_feed.py.
listing_crawler.py registers its own handlers ("default", "parkrun") at its
own module level instead of this module importing them: the other way round
would be circular (listing_crawler.py already has to import this module to
call get_handler() inside crawl_listing() itself).
"""

from typing import Callable

from sqlalchemy.orm import Session

from services.models import Organiser

# (session, organiser, handler_params, force, dry_run, event_limit) -> event URLs.
# Every registered handler is fully responsible for resolving to a concrete list -
# never returns None the way the lower-level sitemap_crawler.get_event_urls()/
# parkrun_feed.get_event_urls() still do (those keep their own "couldn't resolve
# this way" contract for internal composability; a handler wrapping one decides
# what that means - usually falling back to another mechanism - before
# crawl_listing() sees it).
#
# dry_run/event_limit exist for handlers that do real DB writes inline, within this
# very call, rather than just discovering a URL list for the caller to work through
# afterwards (local_runner.py's own per-event loop, sliced there for --mode
# sanity-check/dry-run) - see listing_crawler._parkrun_handler's registrator-override
# path, the one handler that actually needs either: without them, local_runner.py's
# own "--mode sanity-check: only 1" and "--mode dry-run: preview, write nothing" have
# no way to reach in and constrain work that's already finished by the time this
# call returns. A handler that only ever discovers (never writes real event data
# itself) is free to ignore both - the caller's own slicing of whatever it returns
# already gives the same effect for it, same as before either parameter existed.
DiscoveryHandler = Callable[[Session, Organiser, dict, bool, bool, int | None], list[str]]

_HANDLERS: dict[str, DiscoveryHandler] = {}


def register_handler(name: str, handler: DiscoveryHandler) -> None:
    _HANDLERS[name] = handler


def get_handler(name: str) -> DiscoveryHandler | None:
    return _HANDLERS.get(name)
