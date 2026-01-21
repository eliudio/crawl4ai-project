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
    enabled: bool = True


SITES: List[SiteConfig] = [
    SiteConfig(
        name="RunThrough",
        listing_url="https://www.runthrough.co.uk/events-timeline",
        base_url="https://www.runthrough.co.uk",
        link_pattern="event/",
        load_more_xpath="//*[contains(translate(text(), 'LMORE', 'lmore'), 'load more')]",
        enabled=False,  # ← toggle here
    ),
    SiteConfig(
        name="Race for Life",
        listing_url="https://raceforlife.cancerresearchuk.org/find-an-event?size=n_1000_n",
        base_url="https://raceforlife.cancerresearchuk.org",
        link_pattern="find-an-event/",
        load_more_xpath=None,
        enabled=False,
    ),
    SiteConfig(
        name="Saturn Running",
        listing_url="https://www.saturnrunning.co.uk/calendars/sport-events",
        base_url="https://www.saturnrunning.co.uk",
        link_pattern="e/",
        load_more_xpath=None,
        enabled=False,
    ),
    SiteConfig(
        name="UK Running Events",
        listing_url="https://www.ukrunningevents.co.uk/events",
        base_url="https://www.ukrunningevents.co.uk",
        link_pattern="events/",  # or make stricter: r'/events/[^/]+/[^/]+-\d{4}$'
        load_more_xpath=None,
        enabled=False,
    ),
    SiteConfig(
        name="ATW Events",
        listing_url="https://www.atwevents.co.uk/calendars/sport-events/",
        base_url="https://www.atwevents.co.uk",
        link_pattern="/e/",
        load_more_xpath=None,
        enabled=True,   # ← example: turn on only this one
    ),
]


def main():
    create_database()

    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} - Starting multi-site event scrape\n")

    active_sites = [site for site in SITES if site.enabled]

    if not active_sites:
        print("No sites enabled. Nothing to do.")
        return

    print(f"Processing {len(active_sites)} enabled site(s):\n")
    for site in active_sites:
        print(f"  • {site.name}")

    print("\n" + "=" * 70 + "\n")

    for site in active_sites:
        process_site(
            site_name=site.name,
            listing_url=site.listing_url,
            base_url=site.base_url,
            link_pattern=site.link_pattern,
            load_more_xpath=site.load_more_xpath,
            page_number=1,  # still fixed at 1; extend later if needed
        )

    print(f"\n{datetime.now():%Y-%m-%d %H:%M:%S} - All sites processed.")


if __name__ == "__main__":
    main()