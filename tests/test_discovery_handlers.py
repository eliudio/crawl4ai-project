"""
Unit tests for discovery_handlers.py - the generic registry ("factory")
mapping an Organiser.handler name to whatever callable actually discovers
that organiser's event URLs. No real handlers here - listing_crawler.py owns
registering "default"/"parkrun" at its own module level (see that module's
own tests for those); this file only exercises the registry mechanism itself.
"""

from services import discovery_handlers


def test_register_and_get_handler_round_trips(monkeypatch):
    monkeypatch.setattr(discovery_handlers, "_HANDLERS", {})  # isolated from the real registry

    def my_handler(session, organiser, params, force):
        return ["https://example.com/x"]

    discovery_handlers.register_handler("my_handler", my_handler)

    assert discovery_handlers.get_handler("my_handler") is my_handler


def test_get_handler_returns_none_for_unregistered_name():
    assert discovery_handlers.get_handler("definitely_not_registered") is None


def test_registering_the_same_name_twice_overwrites(monkeypatch):
    monkeypatch.setattr(discovery_handlers, "_HANDLERS", {})

    discovery_handlers.register_handler("overwrite_me", lambda *a: ["first"])
    discovery_handlers.register_handler("overwrite_me", lambda *a: ["second"])

    handler = discovery_handlers.get_handler("overwrite_me")
    assert handler(None, None, {}, False) == ["second"]


def test_listing_crawler_registers_default_and_parkrun_on_import():
    # Confirms the actual registration listing_crawler.py performs at its own module
    # level really happened - a unit test against the registry mechanism alone
    # wouldn't catch listing_crawler.py forgetting to call register_handler() at all.
    from services import listing_crawler  # noqa: F401 (import triggers registration)

    assert discovery_handlers.get_handler("default") is not None
    assert discovery_handlers.get_handler("parkrun") is not None
