"""
SiteConfig definition for event listing scrapers.
Enhanced to support multiple loading strategies.
"""

from typing import NamedTuple, Optional, Literal


class SiteConfig(NamedTuple):
    """Configuration for scraping event listings from a single website."""

    name: str                                    # Short human name for the organiser/site
    listing_url: str                             # Starting URL of the events listing page
    base_url: str                                # Base for urljoin (important for relative links or cross-domain)

    # === How to find event detail links on the (fully loaded) page ===
    event_link_selector: Optional[str] = None    # Preferred: precise CSS selector, e.g. 'a[href*="/e/"]' or '.event-card a'
    link_pattern: Optional[str] = None           # Fallback: substring that must appear in href (case-insensitive)
    link_regex: Optional[str] = None             # Precise regex for href (compiled with re.IGNORECASE)

    # === Loading strategy ===
    load_strategy: Literal["single_page", "load_more", "pagination"] = "single_page"

    # load_more strategy (zigzagrunning, sportivaevents, etc.)
    load_more_selector: Optional[str] = None     # CSS or XPath for the "Load More" / "Show More" button
    max_load_clicks: int = 40                    # Safety limit

    # pagination strategy (findarace.com style)
    next_button_selector: Optional[str] = None   # CSS or XPath for the "Next" button / page link
    max_pages: int = 200                         # Safety limit

    enabled: bool = True
    notes: Optional[str] = None                  # Human notes, e.g. "Wix site; event links on eventrac.co.uk subdomain"
