"""Base scraper class for cycling event sources."""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
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
            "Chrome/120.0.0.0 Safari/537.36"
        ))
        try:
            resp = requests.get(url, headers=headers, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"[{self.SOURCE_NAME}] Request failed for {url}: {e}")
            return None

    def _fetch_page(self, url, wait_for=None, wait_ms=3000):
        """Fetch a page using Playwright headless browser.

        This bypasses Cloudflare and JS-rendered content.
        Returns the rendered HTML content, or None on failure.
        """
        if not self._browser:
            logger.warning(f"[{self.SOURCE_NAME}] No browser available, falling back to requests")
            resp = self._make_request(url)
            return resp.text if resp else None

        try:
            page = self._browser.new_page()
            page.set_extra_http_headers({
                "Accept-Language": "en-US,en;q=0.9",
            })
            page.goto(url, wait_until="networkidle", timeout=30000)

            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    logger.debug(f"[{self.SOURCE_NAME}] Selector '{wait_for}' not found, continuing")

            # Extra wait for JS-rendered content
            page.wait_for_timeout(wait_ms)

            html = page.content()
            page.close()
            return html
        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] Playwright fetch failed for {url}: {e}")
            try:
                page.close()
            except Exception:
                pass
            return None

    def _fetch_json_via_browser(self, url):
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

        try:
            page = self._browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            content = page.inner_text("body")
            page.close()
            return json.loads(content)
        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] JSON fetch failed for {url}: {e}")
            try:
                page.close()
            except Exception:
                pass
            return None
