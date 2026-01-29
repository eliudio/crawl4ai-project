from datetime import datetime
from typing import Optional, List, NamedTuple
from process_site import process_site

from events.events_manager import create_database

class SiteConfig(NamedTuple):
    name: str
    listing_url: str
    base_url: str
    link_pattern: str
    load_more_xpath: Optional[str] = None
    link_regex: Optional[str] = None           # ← new field
    enabled: bool = True



SITES: List[SiteConfig] = [
    SiteConfig(
        name="RunThrough",
        listing_url="https://www.runthrough.co.uk/events-timeline",
        base_url="https://www.runthrough.co.uk",
        link_pattern="event/",
        load_more_xpath="//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]",
        link_regex=r'/event/[^/]+$',           # stricter: slug only after /event/
        enabled=False,
    ),
    SiteConfig(
        name="Race for Life",
        listing_url="https://raceforlife.cancerresearchuk.org/find-an-event?size=n_1000_n",
        base_url="https://raceforlife.cancerresearchuk.org",
        link_pattern="find-an-event/",
        load_more_xpath=None,
        link_regex=r'/find-an-event/[^/]+$',   # avoid category/index pages
        enabled=False,
    ),
    SiteConfig(
        name="Saturn Running",
        listing_url="https://www.saturnrunning.co.uk/calendars/sport-events",
        base_url="https://www.saturnrunning.co.uk",
        link_pattern="e/",
        load_more_xpath=None,
        link_regex=r'/e/[^/]+-\d+$',           # slug-number pattern
        enabled=False,
    ),
    SiteConfig(
        name="UK Running Events",
        listing_url="https://www.ukrunningevents.co.uk/events",
        base_url="https://www.ukrunningevents.co.uk",
        link_pattern="events/",
        load_more_xpath=None,
        link_regex=r'/events/[^/]+/[^/]+-\d{4}(?:$|/)',  # category/slug-year
        enabled=False,
    ),
    SiteConfig(
        name="ATW Events",
        listing_url="https://www.atwevents.co.uk/calendars/sport-events/",
        base_url="https://www.atwevents.co.uk",
        link_pattern="/e/",
        load_more_xpath=None,
        link_regex=r'/e/[^/]+-\d+$',           # same as Saturn
        enabled=False,
    ),
    SiteConfig(
        name="Phoenix Running",
        listing_url="https://www.phoenixrunning.co.uk/events",
        base_url="https://www.phoenixrunning.co.uk",
        link_pattern="events/",
        load_more_xpath=None,
        link_regex=r'/events/[^/]+(?:-[^/]+)?$',  # slug or slug-more-slug
        enabled=False,                          # example: turn on for testing
    ),
    SiteConfig(
        name="Zig Zag Running",
        listing_url="https://www.zigzagrunning.co.uk/",
        base_url="https://zigzagrunning.eventrac.co.uk",
        link_pattern="e/",
        load_more_xpath="//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]",
        link_regex=r'/e/[^/]+-\d+$',  # matches /e/slug-12345 style
        enabled=False,  # ← set to True to include it
    ),
    SiteConfig(
        name="It's Grim Up North Running",
        listing_url="https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        base_url="https://www.itsgrimupnorthrunning.co.uk",
        link_pattern="e/",
        load_more_xpath=None,                  # no load more needed
        link_regex=r'/e/[^/]+-\d+$',           # slug-number pattern (very reliable)
        enabled=False,                          # set to True to include it now
    ),
    SiteConfig(
        name="Sportiva Events",
        listing_url="https://sportivaevents.co.uk/events/",
        base_url="https://sportivaevents.co.uk",
        link_pattern="events/",
        load_more_xpath="//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]",
        link_regex=r'/events/[^/]+/$',  # slug/ ending — very precise for this site
        enabled=False,  # ← enable to test now
    ),
    SiteConfig(
        name="Sportiva Events - All Events",
        listing_url="https://sportivaevents.co.uk/events/",
        base_url="https://sportivaevents.co.uk/",
        link_pattern="/events/",
        load_more_xpath='//*[contains(text(), "Load More") or contains(text(), "load more")]',  # case insensitive match
        link_regex=r'^/events/[-a-z0-9]+/?$',
        enabled=False
    ),
    SiteConfig(
        name="Sportiva Events",
        listing_url="https://sportivaevents.co.uk/events/",
        base_url="https://sportivaevents.co.uk",
        link_pattern="events/",
        load_more_xpath="//*[contains(translate(text(), 'LOADMORE', 'loadmore'), 'load more') or contains(@class, 'load-more')]",
        # Very precise regex: /events/ + slug chars + mandatory trailing /
        link_regex=r'/events/[\w\-]+/$',
        enabled=True,  # ← already set to True in your example — keep or change as needed
    ),

    # Generate in this conversation: https://x.com/i/grok?conversation=2013985939245158580
]

def main():
    #migrate_remove_unique_name()
    create_database()

    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} - Starting multi-site event scrape\n")

    active_sites = [site for site in SITES if site.enabled]

    if not active_sites:
        print("No sites enabled. Nothing to do.")
        return

    print(f"Processing {len(active_sites)} enabled site(s):\n")
    for site in active_sites:
        print(f"  • {site.name}   (using {'regex' if site.link_regex else 'pattern'})")

    print("\n" + "=" * 80 + "\n")

    for site in active_sites:
        process_site(
            site_name=site.name,
            listing_url=site.listing_url,
            base_url=site.base_url,
            link_pattern=site.link_pattern,
            load_more_xpath=site.load_more_xpath,
            link_regex=site.link_regex,
            page_number=1,
            test_only=False,
        )

    print(f"\n{datetime.now():%Y-%m-%d %H:%M:%S} - All sites processed.")


if __name__ == "__main__":
    main()