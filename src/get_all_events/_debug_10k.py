import sys
sys.path.insert(0, '.')
from generate_site_configs import generate_site_config
from process_site import get_event_detail_urls

url = "https://findarace.com/10k-runs"
cfg = generate_site_config(url, force_refresh=True)
print("\nCONFIG:", cfg)

if cfg is not None:
    urls = get_event_detail_urls(
        listing_url=cfg.listing_url,
        base_url=cfg.base_url,
        event_link_selector=cfg.event_link_selector,
        link_pattern=cfg.link_pattern,
        link_regex=cfg.link_regex,
        load_strategy=cfg.load_strategy,
        load_more_selector=cfg.load_more_selector,
        next_button_selector=cfg.next_button_selector,
        max_pages=3,
        max_load_clicks=3,
    )
    print(f"\nTOTAL: {len(urls)} unique event URLs")
    for u in urls[:10]:
        print("  ", u)
