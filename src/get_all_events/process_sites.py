from datetime import datetime

from generate_site_configs import generate_site_config
from process_site import process_site


if __name__ == "__main__":
    urls = [
        "https://findarace.com/events",
        "https://www.zigzagrunning.co.uk/",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
    ]

    for url in urls:
        print(f"\n{'='*70}")
        print(f"Processing: {url}")
        print('='*70)

        config = generate_site_config(url)

        if config is None:
            print(f"⚠️  Failed to generate config for {url} — skipping.\n")
            continue

        if not config.enabled:
            print(f"⚠️  Config for {url} is marked disabled — skipping.\n")
            continue

        # Call the updated process_site with new parameters
        process_site(
            site_name=config.name,
            listing_url=config.listing_url,
            base_url=config.base_url,

            # New preferred way (more reliable)
            event_link_selector=config.event_link_selector,

            # Fallbacks (still supported)
            link_pattern=config.link_pattern,
            link_regex=config.link_regex,

            # Strategy & controls (NEW)
            load_strategy=config.load_strategy,
            load_more_selector=config.load_more_selector,
            next_button_selector=config.next_button_selector,
            max_load_clicks=config.max_load_clicks,
            max_pages=config.max_pages,

            # Control flags
            skip_actual_processing=True,   # ← Change to False when ready to actually store details
            page_number=1,
            test_only=False,
        )

    print(f"\n{datetime.now():%Y-%m-%d %H:%M:%S} - All sites processed.")