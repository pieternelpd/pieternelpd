"""Base scraper class for cycling event sources."""

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
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
        self.events: list[CyclingEvent] = []
        self._browser = browser

    def scrape(self) -> list[CyclingEvent]:
        """Scrape events from the source. Override in subclass."""
        raise NotImplementedError

    def _make_event(self, name, date="", url=None, venue="TBA",
                    discipline=None, state=None, **kwargs) -> CyclingEvent:
        """Create a CyclingEvent with sensible defaults.

        Subclasses can override to set organiser, source, and other defaults.
        """
        return CyclingEvent(
            name=name[:200],
            date=self._normalise_date(date),
            end_date=kwargs.get("end_date"),
            venue=venue,
            address=kwargs.get("address", venue),
            lat=kwargs.get("lat"),
            lng=kwargs.get("lng"),
            discipline=discipline or "road",
            organiser=kwargs.get("organiser", "Unknown"),
            source=self.SOURCE_NAME,
            url=url,
            state=state,
        )

    def _make_request(self, url, **kwargs):
        """Make an HTTP request with error handling."""
        import requests

        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
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

    def _make_cf_request(self, url, **kwargs):
        """Make an HTTP request using curl_cffi to bypass Cloudflare.

        curl_cffi impersonates real browser TLS fingerprints, which is the
        primary way Cloudflare detects automated traffic.
        """
        try:
            from curl_cffi import requests as cf_requests
        except ImportError:
            logger.warning(f"[{self.SOURCE_NAME}] curl_cffi not installed, falling back to requests")
            return self._make_request(url, **kwargs)

        headers = kwargs.pop("headers", {})
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "en-US,en;q=0.9,en-AU;q=0.8")

        # Try multiple browser impersonation profiles
        for browser_type in ["chrome", "safari", "chrome110"]:
            try:
                resp = cf_requests.get(
                    url, headers=headers, timeout=30,
                    impersonate=browser_type, **kwargs,
                )
                if resp.status_code == 200:
                    logger.info(f"[{self.SOURCE_NAME}] curl_cffi ({browser_type}) OK for {url} - {len(resp.text)} bytes")
                    return resp
                elif resp.status_code == 403:
                    logger.info(f"[{self.SOURCE_NAME}] curl_cffi ({browser_type}) got 403 for {url}, trying next")
                    continue
                else:
                    resp.raise_for_status()
                    return resp
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] curl_cffi ({browser_type}) failed for {url}: {e}")
                continue

        logger.warning(f"[{self.SOURCE_NAME}] All curl_cffi attempts failed for {url}")
        return None

    def _fetch_page(self, url, wait_for=None, wait_ms=3000, retries=2):
        """Fetch a page using Playwright headless browser with stealth.

        Stealth evasions are applied automatically by playwright-stealth
        at the context level. Retries on failure. Returns rendered HTML or None.
        """
        if not self._browser:
            logger.warning(f"[{self.SOURCE_NAME}] No browser available, falling back to requests")
            resp = self._make_request(url)
            return resp.text if resp else None

        for attempt in range(retries + 1):
            page = None
            try:
                page = self._browser.new_page()

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

    def _normalise_date(self, date_str: str) -> str:
        """Normalise a date string to ISO format (YYYY-MM-DD).

        Handles common formats from EntryBoss, AusCycling and other sources:
        - ISO 8601 with/without timezone
        - Day-of-week prefixes (Sat 22 Feb 2025, Saturday, 22 February 2025)
        - Ordinal suffixes (22nd Feb 2025, 1st March 2025)
        - Australian date formats (22/02/2025)
        - US date formats (Feb 22, 2025)
        - Unix timestamps
        """
        if not date_str:
            return ""

        date_str = str(date_str).strip()

        # Already ISO format (handles 2025-02-22T08:00:00.000+08:00 etc.)
        if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            return date_str[:10]

        # Unix timestamp (integer or string of digits)
        if re.match(r"^\d{9,13}$", date_str):
            try:
                ts = int(date_str)
                if ts > 1e12:  # milliseconds
                    ts = ts / 1000
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        # Strip day-of-week prefixes (Mon, Monday, Tue, Tuesday, etc.)
        date_str = re.sub(
            r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
            r"(?:day|nesday|rsday|urday)?"
            r"[,\s]+",
            "", date_str, flags=re.IGNORECASE,
        )

        # Strip ordinal suffixes (1st, 2nd, 3rd, 4th, 11th, 22nd, etc.)
        date_str = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_str)

        # Try common date formats
        for fmt in [
            "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
            "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
            "%d %B", "%d %b",
            "%Y-%m-%dT%H:%M:%S",
        ]:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if "%Y" not in fmt:
                    from datetime import timedelta
                    now = datetime.now()
                    dt = dt.replace(year=now.year)
                    # Only push to next year if the date is more than 30 days
                    # in the past (avoids bumping recent events to next year)
                    if dt < now - timedelta(days=30):
                        dt = dt.replace(year=now.year + 1)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Last resort: try dateutil parser
        try:
            from dateutil import parser as dateutil_parser
            dt = dateutil_parser.parse(date_str, dayfirst=True)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        return date_str

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
