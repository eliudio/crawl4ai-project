"""
Robust event listing scraper supporting three strategies:
- single_page: everything visible on first load
- load_more: repeatedly click "Load More" until gone or max reached
- pagination: follow Next / numbered pages

Uses Selenium + BeautifulSoup. Designed to be driven by SiteConfig.
"""

import time
import re
from datetime import datetime
from typing import Optional, List
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException,
)
from bs4 import BeautifulSoup

from store_details import store_details
from site_config import SiteConfig


def _is_xpath(selector: str) -> bool:
    """Heuristic: treat as XPath if it starts with / or // or contains typical XPath syntax."""
    if not selector:
        return False
    s = selector.strip()
    return s.startswith(('/', '//', '(')) or '::' in s or (s.startswith('[') and ']' in s)


def _find_element(driver, selector: str):
    """Find single element. Try CSS first, fall back to XPath."""
    if not selector:
        return None
    try:
        if _is_xpath(selector):
            return driver.find_element(By.XPATH, selector)
        return driver.find_element(By.CSS_SELECTOR, selector)
    except NoSuchElementException:
        if not _is_xpath(selector):
            try:
                return driver.find_element(By.XPATH, selector)
            except NoSuchElementException:
                return None
        return None
    except Exception:
        return None


def _find_elements(driver, selector: str):
    """Find all matching elements. Try CSS first, fall back to XPath."""
    if not selector:
        return []
    try:
        if _is_xpath(selector):
            return driver.find_elements(By.XPATH, selector)
        return driver.find_elements(By.CSS_SELECTOR, selector)
    except Exception:
        if not _is_xpath(selector):
            try:
                return driver.find_elements(By.XPATH, selector)
            except Exception:
                return []
        return []


def _wait_for_link_count_increase(driver, selector: str, previous_count: int, timeout: int = 12) -> bool:
    """
    Wait until the number of elements matching selector is > previous_count.
    Returns True if count increased, False on timeout.
    """
    try:
        def _count_increased(d):
            try:
                current = len(_find_elements(d, selector))
                return current > previous_count
            except StaleElementReferenceException:
                return False

        WebDriverWait(driver, timeout).until(_count_increased)
        return True
    except TimeoutException:
        return False


def get_event_detail_urls(
    listing_url: str,
    base_url: str,
    event_link_selector: Optional[str] = None,
    link_pattern: Optional[str] = None,
    link_regex: Optional[str] = None,
    load_strategy: str = "single_page",
    load_more_selector: Optional[str] = None,
    next_button_selector: Optional[str] = None,
    max_load_clicks: int = 40,
    max_pages: int = 200,
    test_only: bool = False,
    min_wait_after_action: float = 1.8,
) -> List[str]:
    """
    Unified, robust scraper for event detail URLs.
    Supports single_page, load_more, and pagination strategies.
    """
    print(f"{datetime.now():%H:%M:%S} - Starting scrape of: {listing_url} (strategy={load_strategy})")

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1920,1080')

    driver = webdriver.Chrome(options=options)
    all_urls: List[str] = []
    seen = set()

    try:
        driver.get(listing_url)
        time.sleep(3.5)  # initial render

        # Determine the best selector for waiting / counting event links
        wait_selector = event_link_selector or (f'a[href*="{link_pattern}"]' if link_pattern else 'a[href]')

        # Initial wait for any event-like links
        try:
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector) if not _is_xpath(wait_selector) else (By.XPATH, wait_selector))
            )
            print(f"{datetime.now():%H:%M:%S} - Initial event links detected")
        except TimeoutException:
            print(f"{datetime.now():%H:%M:%S} - Warning: No initial event links found with {wait_selector}")

        # ========== STRATEGY: LOAD_MORE ==========
        if load_strategy == "load_more" and load_more_selector:
            print(f"{datetime.now():%H:%M:%S} - Using LOAD_MORE strategy")
            clicks = 0
            while clicks < max_load_clicks:
                load_btn = _find_element(driver, load_more_selector)
                if not load_btn:
                    print(f"{datetime.now():%H:%M:%S} - No more 'Load More' button found")
                    break

                try:
                    # Count current links before click
                    before_count = len(_find_elements(driver, wait_selector))

                    # Scroll into view + click (with fallback to JS click)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", load_btn)
                    time.sleep(0.4)

                    try:
                        load_btn.click()
                    except (ElementClickInterceptedException, StaleElementReferenceException):
                        driver.execute_script("arguments[0].click();", load_btn)

                    clicks += 1
                    print(f"{datetime.now():%H:%M:%S} - Clicked Load More ({clicks}/{max_load_clicks})")

                    # Wait for content to actually increase
                    increased = _wait_for_link_count_increase(driver, wait_selector, before_count, timeout=15)
                    if not increased:
                        print(f"{datetime.now():%H:%M:%S} - Content did not increase after click — stopping")
                        break

                    time.sleep(min_wait_after_action)

                    if test_only:
                        break

                except Exception as e:
                    print(f"{datetime.now():%H:%M:%S} - Load More click failed: {type(e).__name__}: {e}")
                    break

            print(f"{datetime.now():%H:%M:%S} - Finished load_more after {clicks} clicks")

        # ========== STRATEGY: PAGINATION ==========
        elif load_strategy == "pagination" and next_button_selector:
            print(f"{datetime.now():%H:%M:%S} - Using PAGINATION strategy")
            page_num = 1

            while page_num <= max_pages:
                print(f"{datetime.now():%H:%M:%S} - Scraping page {page_num}")

                # Parse current page
                soup = BeautifulSoup(driver.page_source, "html.parser")
                page_urls = _extract_event_urls_from_soup(
                    soup, base_url, event_link_selector, link_pattern, link_regex
                )
                for u in page_urls:
                    if u not in seen:
                        seen.add(u)
                        all_urls.append(u)

                # Try to find and go to next page
                next_btn = _find_element(driver, next_button_selector)
                if not next_btn:
                    print(f"{datetime.now():%H:%M:%S} - No 'Next' button found — end of pagination")
                    break

                try:
                    href = next_btn.get_attribute("href")
                    if href:
                        next_url = urljoin(driver.current_url, href)
                        print(f"{datetime.now():%H:%M:%S} - Navigating to next page: {next_url}")
                        driver.get(next_url)
                        time.sleep(3.0)
                    else:
                        # Fallback to click
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
                        time.sleep(0.3)
                        try:
                            next_btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", next_btn)
                        time.sleep(min_wait_after_action + 1.0)

                    page_num += 1
                    if test_only:
                        break

                except Exception as e:
                    print(f"{datetime.now():%H:%M:%S} - Pagination navigation failed: {type(e).__name__}: {e}")
                    break

            print(f"{datetime.now():%H:%M:%S} - Finished pagination after {page_num} pages")

        # ========== STRATEGY: SINGLE_PAGE (default) ==========
        else:
            print(f"{datetime.now():%H:%M:%S} - Using SINGLE_PAGE strategy (no extra loading)")

        # Final parse of whatever is currently loaded in the driver
        if load_strategy != "pagination":
            # For load_more and single_page we parse the final DOM once
            soup = BeautifulSoup(driver.page_source, "html.parser")
            final_urls = _extract_event_urls_from_soup(
                soup, base_url, event_link_selector, link_pattern, link_regex
            )
            for u in final_urls:
                if u not in seen:
                    seen.add(u)
                    all_urls.append(u)

        print(f"{datetime.now():%H:%M:%S} - Found {len(all_urls)} unique event URLs")
        return all_urls

    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Scrape failed: {type(e).__name__}: {e}")
        return []
    finally:
        driver.quit()


def _extract_event_urls_from_soup(
    soup: BeautifulSoup,
    base_url: str,
    event_link_selector: Optional[str],
    link_pattern: Optional[str],
    link_regex: Optional[str],
) -> List[str]:
    """Extract and normalize event detail URLs from BeautifulSoup."""
    urls: List[str] = []
    seen = set()

    # 1. Preferred: precise CSS selector
    if event_link_selector:
        try:
            candidates = soup.select(event_link_selector)
        except Exception:
            candidates = []
        for link in candidates:
            href = link.get("href")
            if href:
                full = urljoin(base_url, href)
                if full not in seen:
                    seen.add(full)
                    urls.append(full)

    # 2. Regex
    if link_regex and not urls:  # only if selector gave nothing (fallback)
        try:
            pattern = re.compile(link_regex, re.IGNORECASE)
            for link in soup.find_all("a", href=pattern):
                href = link.get("href")
                if href:
                    full = urljoin(base_url, href)
                    if full not in seen:
                        seen.add(full)
                        urls.append(full)
        except re.error:
            pass

    # 3. Simple contains pattern (last resort)
    if link_pattern and not urls:
        lp = link_pattern.lower()
        for link in soup.find_all("a", href=lambda h: h and lp in str(h).lower()):
            href = link.get("href")
            if href:
                full = urljoin(base_url, href)
                if full not in seen:
                    seen.add(full)
                    urls.append(full)

    # Basic junk filter (customize per site if needed)
    junk_substrings = [
        "facebook", "twitter", "instagram", "linkedin", "#", "login", "signup",
        "donate", "/checkout", "/book/", "/tickets/", "/cart", "/account",
        "mailto:", "tel:", "javascript:", "/privacy", "/terms"
    ]
    filtered = []
    for u in urls:
        lower = u.lower()
        if any(x in lower for x in junk_substrings):
            continue
        filtered.append(u)

    return filtered


def process_site(
    site_name: str,
    listing_url: str,
    base_url: str,
    event_link_selector: Optional[str] = None,
    link_pattern: Optional[str] = None,
    link_regex: Optional[str] = None,
    load_strategy: str = "single_page",
    load_more_selector: Optional[str] = None,
    next_button_selector: Optional[str] = None,
    max_load_clicks: int = 40,
    max_pages: int = 200,
    skip_actual_processing: bool = False,
    page_number: int = 1,
    test_only: bool = False,
):
    """High-level orchestrator. Calls the scraper then (optionally) processes each detail URL."""
    print(f"\n{'=' * 75}")
    print(f"{datetime.now():%H:%M:%S} - Processing {site_name} (page {page_number}, strategy={load_strategy})")
    print(f"{'=' * 75}\n")

    detail_urls = get_event_detail_urls(
        listing_url=listing_url,
        base_url=base_url,
        event_link_selector=event_link_selector,
        link_pattern=link_pattern,
        link_regex=link_regex,
        load_strategy=load_strategy,
        load_more_selector=load_more_selector,
        next_button_selector=next_button_selector,
        max_load_clicks=max_load_clicks,
        max_pages=max_pages,
        test_only=test_only,
    )

    if not detail_urls:
        print(f"{datetime.now():%H:%M:%S} - No events found for {site_name}")
        return

    print(f"{datetime.now():%H:%M:%S} - Processing {len(detail_urls)} events...")

    for i, detail_url in enumerate(detail_urls, 1):
        print(f"{datetime.now():%H:%M:%S} - [{i}/{len(detail_urls)}] {detail_url}")
        if not skip_actual_processing:
            try:
                store_details(page_number, detail_url)
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} - store_details failed for {detail_url}: {e}")
        if test_only:
            break

    print(f"{datetime.now():%H:%M:%S} - Finished processing {site_name}")
