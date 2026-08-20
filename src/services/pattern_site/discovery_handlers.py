"""
A small, generic registry (the "factory") mapping an Organiser.handler name to
the callable that actually discovers that organiser's event URLs - see
listing_crawler.py's crawl_listing(), the one place that looks a handler up
by name and calls it.

Deliberately knows nothing about what handlers exist or how any of them
work - this module has zero dependency on listing_crawler.py. listing_crawler.py
registers its own handler(s) ("default") at its own module level instead of this
module importing them: the other way round would be circular (listing_crawler.py
already has to import this module to call get_handler() inside crawl_listing()
itself).

A structured-bulk-feed source (parkrun, ...) is deliberately NOT a handler
registered here - see feeds/feed_importers.py's own registry instead, a separate
mechanism for a pipeline this module's DiscoveryHandler contract was never shaped
for (see git history for the "parkrun" handler this superseded).
"""

from typing import Callable

from sqlalchemy.orm import Session

from services.common.models import Organiser

__all__ = ["DiscoveryHandler", "register_handler", "get_handler"]

# (session, organiser, handler_params, force, dry_run, event_limit) -> event URLs.
# Every registered handler is fully responsible for resolving to a concrete list -
# never returns None the way the lower-level sitemap_crawler.get_event_urls() still
# does (that keeps its own "couldn't resolve this way" contract for internal
# composability; a handler wrapping one decides what that means - usually falling
# back to another mechanism - before crawl_listing() sees it).
#
# dry_run/event_limit exist for a handler that might do real DB writes inline, within
# this very call, rather than just discovering a URL list for the caller to work
# through afterwards (local/local_event_scraper.py's own per-event loop, sliced there for
# --mode sanity-check/dry-run): without them, that handler would have no way to reach
# in and constrain work that's already finished by the time this call returns. No
# currently-registered handler needs either - "default" only ever discovers a URL
# list, never writes real event data itself, so it's free to ignore both; the
# caller's own slicing of whatever it returns already gives the same effect for it.
# Kept in the contract anyway: the old "parkrun" handler (see git history, superseded
# by feed_importers.py) is exactly the shape of handler these two parameters exist
# for, and a future one could need them again.
DiscoveryHandler = Callable[[Session, Organiser, dict, bool, bool, int | None], list[str]]

_HANDLERS: dict[str, DiscoveryHandler] = {}


def register_handler(name: str, handler: DiscoveryHandler) -> None:
    _HANDLERS[name] = handler


def get_handler(name: str) -> DiscoveryHandler | None:
    return _HANDLERS.get(name)
