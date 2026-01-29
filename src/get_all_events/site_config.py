from typing import Optional, NamedTuple

class SiteConfig(NamedTuple):
    name: str
    listing_url: str
    base_url: str
    link_pattern: str
    load_more_xpath: Optional[str] = None
    link_regex: Optional[str] = None           # ← new field
    enabled: bool = True
