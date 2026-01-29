from process_site import process_site
from datetime import datetime
from generate_site_configs import generate_site_config

if __name__ == "__main__":
    urls = [
        "https://www.zigzagrunning.co.uk/",
        "https://www.itsgrimupnorthrunning.co.uk/calendars/sport-events",
        "https://sportivaevents.co.uk/events/",
        "https://www.phoenixrunning.co.uk/events",
        # add more as needed
    ]

    for url in urls:
        config = generate_site_config(url)
        process_site(
            site_name=config.name,
            listing_url=config.listing_url,
            base_url=config.base_url,
            link_pattern=config.link_pattern,
            load_more_xpath=config.load_more_xpath,
            link_regex=config.link_regex,
            page_number=1,
            test_only=False,
        )

    print(f"\n{datetime.now():%Y-%m-%d %H:%M:%S} - All sites processed.")

