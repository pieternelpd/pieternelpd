"""Base scraper class for cycling event sources."""

import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CyclingEvent:
    """Represents a single cycling event."""
    name: str
    date: str  # ISO 8601 date string
    end_date: Optional[str]  # ISO 8601 date string, if multi-day
    venue: str
    address: str
    lat: Optional[float]
    lng: Optional[float]
    discipline: str  # road, track, mtb, gravel, bmx, criterium, cyclocross
    organiser: str
    source: str  # which scraper produced this
    url: Optional[str]
    state: Optional[str]  # WA, NSW, VIC, etc.
    description: Optional[str] = None

    def to_dict(self):
        return asdict(self)


# JS to inject into pages to hide Playwright automation markers
STEALTH_JS = """
() => {
    // Hide webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Fake plugins array
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // Fake languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en', 'en-AU'],
    });

    // Override permissions query
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);

    // Hide chrome automation indicators
    window.chrome = { runtime: {} };
}
"""


class BaseScraper:
    """Base class for event scrapers."""

    SOURCE_NAME = "unknown"

    def __init__(self, browser=None):
        self.session = None
        self.events: list[CyclingEvent] = []
        self._browser = browser

    def scrape(self) -> list[CyclingEvent]:
        """Scrape events from the source. Override in subclass."""
        raise NotImplementedError

    def _make_request(self, url, **kwargs):
        """Make an HTTP request with error handling."""
        import requests

        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ))
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "en-US,en;q=0.9,en-AU;q=0.8")
        try:
            resp = requests.get(url, headers=headers, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] Request failed for {url}: {e}")
            return None

    def _fetch_page(self, url, wait_for=None, wait_ms=3000, retries=2):
        """Fetch a page using Playwright headless browser with stealth.

        Injects stealth JS to hide automation markers before navigating.
        Retries on failure. Returns rendered HTML or None.
        """
        if not self._browser:
            logger.warning(f"[{self.SOURCE_NAME}] No browser available, falling back to requests")
            resp = self._make_request(url)
            return resp.text if resp else None

        for attempt in range(retries + 1):
            page = None
            try:
                page = self._browser.new_page()

                # Inject stealth script before navigation
                page.add_init_script(STEALTH_JS)

                page.set_extra_http_headers({
                    "Accept-Language": "en-US,en;q=0.9,en-AU;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                })

                resp = page.goto(url, wait_until="networkidle", timeout=30000)
                status = resp.status if resp else 0

                # Handle Cloudflare challenge page - wait for it to resolve
                if status == 403 or "challenge" in (page.title() or "").lower():
                    logger.info(f"[{self.SOURCE_NAME}] Cloudflare challenge detected, waiting...")
                    page.wait_for_timeout(8000)
                    # Check if challenge resolved
                    if "challenge" in (page.title() or "").lower():
                        logger.warning(f"[{self.SOURCE_NAME}] Cloudflare challenge not resolved for {url}")
                        page.close()
                        if attempt < retries:
                            time.sleep(2 ** attempt)
                            continue
                        return None

                if wait_for:
                    try:
                        page.wait_for_selector(wait_for, timeout=10000)
                    except Exception:
                        logger.debug(f"[{self.SOURCE_NAME}] Selector '{wait_for}' not found, continuing")

                # Wait for JS-rendered content
                page.wait_for_timeout(wait_ms)

                html = page.content()

                # Log page size for debugging
                logger.info(f"[{self.SOURCE_NAME}] Fetched {url} - {len(html)} bytes (status {status})")

                page.close()
                return html

            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] Playwright fetch failed for {url} (attempt {attempt+1}): {e}")
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
                if attempt < retries:
                    time.sleep(2 ** attempt)

        return None

    def _fetch_json_via_browser(self, url, retries=1):
        """Fetch a JSON API response using Playwright to bypass bot protection.

        Returns parsed JSON or None.
        """
        if not self._browser:
            resp = self._make_request(url)
            if resp:
                try:
                    return resp.json()
                except Exception:
                    return None
            return None

        for attempt in range(retries + 1):
            page = None
            try:
                page = self._browser.new_page()
                page.add_init_script(STEALTH_JS)
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)

                # Handle Cloudflare challenge
                if "challenge" in (page.title() or "").lower():
                    page.wait_for_timeout(8000)

                content = page.inner_text("body")
                page.close()
                return json.loads(content)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] JSON fetch failed for {url} (attempt {attempt+1}): {e}")
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
                if attempt < retries:
                    time.sleep(2 ** attempt)

        return None

    def _extract_via_js(self, url, js_extract_fn, wait_for=None, wait_ms=5000):
        """Navigate to a URL and run a JS function in the page context to extract data.

        This is more reliable than parsing HTML with BeautifulSoup as it runs
        in the actual browser context with full DOM access.

        Args:
            url: Page URL to navigate to.
            js_extract_fn: JavaScript function body (string) that returns data.
                          Will be wrapped in an async IIFE.
            wait_for: Optional CSS selector to wait for before extracting.
            wait_ms: Milliseconds to wait for JS content to render.

        Returns:
            The result of the JS function, or None on failure.
        """
        if not self._browser:
            logger.warning(f"[{self.SOURCE_NAME}] No browser available for JS extraction")
            return None

        page = None
        try:
            page = self._browser.new_page()
            page.add_init_script(STEALTH_JS)
            page.set_extra_http_headers({
                "Accept-Language": "en-US,en;q=0.9,en-AU;q=0.8",
            })

            resp = page.goto(url, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else 0

            # Handle Cloudflare challenge
            if status == 403 or "challenge" in (page.title() or "").lower():
                logger.info(f"[{self.SOURCE_NAME}] Cloudflare challenge on {url}, waiting...")
                page.wait_for_timeout(8000)

            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    pass

            page.wait_for_timeout(wait_ms)

            result = page.evaluate(js_extract_fn)
            logger.info(f"[{self.SOURCE_NAME}] JS extraction from {url}: got {type(result).__name__}")
            page.close()
            return result

        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] JS extraction failed for {url}: {e}")
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            return None
