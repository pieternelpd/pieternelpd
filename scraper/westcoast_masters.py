"""Scraper for West Coast Masters Cycling Council events.

Tries multiple sources using Playwright headless browser:
1. EntryBoss calendar (primary - most reliable for upcoming races)
2. WCMCC website (WordPress)
3. WestCycle listing
"""

import logging
import re
from bs4 import BeautifulSoup
from .base import BaseScraper, CyclingEvent

logger = logging.getLogger(__name__)

# Known WCMCC venues with coordinates
WCMCC_VENUES = {
    "bibra lake": {"address": "Bibra Lake, WA 6163", "lat": -32.0903, "lng": 115.8226},
    "kewdale": {"address": "Kewdale, WA 6105", "lat": -31.9750, "lng": 115.9500},
    "casuarina": {"address": "502 Orton Rd, Casuarina WA 6167", "lat": -32.2200, "lng": 115.8600},
    "motorplex": {"address": "Motorplex, Kwinana WA 6167", "lat": -32.2378, "lng": 115.8015},
    "rockingham": {"address": "Rockingham, WA 6168", "lat": -32.2772, "lng": 115.7302},
    "wangara": {"address": "Wangara, WA 6065", "lat": -31.7900, "lng": 115.8300},
    "perth airport": {"address": "Perth Airport, WA 6105", "lat": -31.9385, "lng": 115.9672},
    "drmc": {"address": "Perth Airport, WA 6105", "lat": -31.9385, "lng": 115.9672},
}

CALENDAR_URLS = [
    "https://www.wcmasterscycling.asn.au/racing/calendar/",
    "https://www.wcmasterscycling.asn.au/calendar/calendar.htm",
]

ENTRYBOSS_URL = "https://entryboss.cc/calendar/westcoastmasterscc"

WESTCYCLE_URL = "https://westcycle.org.au/event_club/wcmcc-west-coast-masters-cycling-council/"


class WestCoastMastersScraper(BaseScraper):
    """Scrapes events from West Coast Masters Cycling Council.

    Uses Playwright to render JS-heavy pages and bypass bot protection.
    """

    SOURCE_NAME = "wcmcc"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Try EntryBoss first (most reliable for upcoming races)
        self._scrape_entryboss()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss")
            return self.events

        # Try WordPress REST API
        self._try_wordpress_api()

        if self.events:
            return self.events

        # Try WCMCC calendar pages
        for url in CALENDAR_URLS:
            html = self._fetch_page(url, wait_for="table, .tribe-events, article", wait_ms=3000)
            if html:
                self._parse_calendar(html, url)

        # Try WestCycle
        html = self._fetch_page(WESTCYCLE_URL, wait_for=".event, article", wait_ms=3000)
        if html:
            self._parse_westcycle(html)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events")
        return self.events

    def _scrape_entryboss(self):
        """Scrape the EntryBoss fixture calendar page.

        EntryBoss is a Rails app that renders event calendars.
        We use Playwright to load the JS-rendered content.
        """
        html = self._fetch_page(
            ENTRYBOSS_URL,
            wait_for=".race-item, .fixture, a[href*='/races/'], table",
            wait_ms=5000,
        )
        if not html:
            return

        soup = BeautifulSoup(html, "lxml")

        # EntryBoss calendar pages typically list races with links
        # Try various selectors for the fixture list
        selectors_to_try = [
            # EntryBoss-specific patterns
            ".race-item", ".fixture-item", ".fixture-row",
            "[class*='race']", "[class*='fixture']",
            # Table rows with race links
            "table tr",
            # Generic card/list patterns
            ".card", ".list-group-item",
            # Links to individual races
            "a[href*='/races/']",
        ]

        items = []
        used_selector = None
        for sel in selectors_to_try:
            items = soup.select(sel)
            if items:
                used_selector = sel
                logger.info(f"[{self.SOURCE_NAME}] EntryBoss: found {len(items)} items with '{sel}'")
                break

        if not items:
            logger.warning(f"[{self.SOURCE_NAME}] EntryBoss: no items found on page")
            # Log a snippet of the page for debugging
            text = soup.get_text(strip=True)[:500]
            logger.debug(f"[{self.SOURCE_NAME}] Page text preview: {text}")
            return

        for item in items:
            try:
                name = ""
                date_str = ""
                url = None

                if used_selector == "a[href*='/races/']":
                    # Direct race links
                    name = item.get_text(strip=True)
                    url = item.get("href", "")
                    if url and not url.startswith("http"):
                        url = "https://entryboss.cc" + url
                elif used_selector == "table tr":
                    cells = item.select("td")
                    if len(cells) >= 2:
                        date_str = cells[0].get_text(strip=True)
                        name = cells[1].get_text(strip=True)
                        link = item.select_one("a[href]")
                        url = link["href"] if link else None
                        if url and not url.startswith("http"):
                            url = "https://entryboss.cc" + url
                else:
                    # Generic card/item
                    name_el = item.select_one("h2, h3, h4, a, .title, .name, [class*='title'], [class*='name']")
                    if name_el:
                        name = name_el.get_text(strip=True)
                    date_el = item.select_one("time, .date, [class*='date']")
                    if date_el:
                        date_str = date_el.get("datetime", date_el.get_text(strip=True))
                    link = item.select_one("a[href*='/races/']") or item.select_one("a[href]")
                    if link:
                        url = link.get("href", "")
                        if url and not url.startswith("http"):
                            url = "https://entryboss.cc" + url

                # Extract date from the text if not found separately
                if not date_str and name:
                    date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', item.get_text())
                    if date_match:
                        date_str = date_match.group(1)

                if name and len(name) > 3:
                    venue_info = self._match_venue(name)
                    self.events.append(CyclingEvent(
                        name=name[:200],
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue_info.get("address", "WA") if venue_info else "WA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss item: {e}")

    def _try_wordpress_api(self):
        """Try WordPress REST API endpoints via browser."""
        api_urls = [
            "https://www.wcmasterscycling.asn.au/wp-json/tribe/events/v1/events",
            "https://www.wcmasterscycling.asn.au/wp-json/wp/v2/posts?categories=events&per_page=50",
        ]
        for url in api_urls:
            data = self._fetch_json_via_browser(url)
            if not data:
                continue

            try:
                events = data.get("events", data) if isinstance(data, dict) else data
                for item in events:
                    venue_name = ""
                    if isinstance(item.get("venue"), dict):
                        venue_name = item["venue"].get("venue", "")
                    venue_info = self._match_venue(
                        venue_name or item.get("title", {}).get("rendered", "")
                    )
                    title = item.get("title", {}).get("rendered", item.get("title", ""))
                    self.events.append(CyclingEvent(
                        name=title,
                        date=item.get("start_date", item.get("date", "")),
                        end_date=item.get("end_date", None),
                        venue=venue_name or "TBA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(title),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=item.get("url", item.get("link", None)),
                        state="WA",
                    ))
                if self.events:
                    return
            except Exception as e:
                logger.debug(f"WordPress API parse error: {e}")

    def _parse_calendar(self, html: str, source_url: str):
        """Parse WCMCC calendar page (rendered HTML)."""
        soup = BeautifulSoup(html, "lxml")

        for item in soup.select(
            ".tribe-events-calendar-list__event, "
            ".type-tribe_events, "
            "table tr, "
            ".event-item, "
            "article"
        ):
            try:
                name = ""
                date_str = ""
                venue = ""

                title_el = item.select_one(
                    ".tribe-events-calendar-list__event-title, h2, h3, .entry-title"
                )
                if title_el:
                    name = title_el.get_text(strip=True)

                date_el = item.select_one(
                    ".tribe-events-calendar-list__event-datetime, time, .event-date"
                )
                if date_el:
                    date_str = date_el.get("datetime", date_el.get_text(strip=True))

                venue_el = item.select_one(
                    ".tribe-events-calendar-list__event-venue, .event-venue, .location"
                )
                if venue_el:
                    venue = venue_el.get_text(strip=True)

                # Try table rows
                if not name:
                    cells = item.select("td")
                    if len(cells) >= 2:
                        date_str = cells[0].get_text(strip=True)
                        name = cells[1].get_text(strip=True)
                        venue = cells[2].get_text(strip=True) if len(cells) > 2 else ""

                if name:
                    venue_info = self._match_venue(name + " " + venue)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or venue_info.get("address", "WA") if venue_info else "WA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=source_url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse calendar item: {e}")

    def _parse_westcycle(self, html: str):
        """Parse WestCycle event listings (rendered HTML)."""
        soup = BeautifulSoup(html, "lxml")

        for item in soup.select(".wc-event, .event-listing, article, .type-wc-event, [class*='event']"):
            try:
                name = ""
                date_str = ""
                venue = ""

                title_el = item.select_one("h2, h3, .event-title, a, [class*='title']")
                if title_el:
                    name = title_el.get_text(strip=True)

                date_el = item.select_one(".event-date, time, .date, [class*='date']")
                if date_el:
                    date_str = date_el.get("datetime", date_el.get_text(strip=True))

                venue_el = item.select_one(".event-venue, .venue, .location, [class*='venue']")
                if venue_el:
                    venue = venue_el.get_text(strip=True)

                link = item.select_one("a[href]")
                url = link["href"] if link else None

                if name and "wcm" in name.lower() or "west coast" in name.lower():
                    venue_info = self._match_venue(name + " " + venue)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or venue_info.get("address", "WA") if venue_info else "WA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse WestCycle item: {e}")

    def _match_venue(self, text: str) -> dict:
        """Match text against known WCMCC venues."""
        text_lower = text.lower()
        for key, info in WCMCC_VENUES.items():
            if key in text_lower:
                return info
        return {}

    def _guess_discipline(self, name: str) -> str:
        name_lower = name.lower()
        if "criterium" in name_lower or "crit" in name_lower:
            return "criterium"
        if "gravel" in name_lower:
            return "gravel"
        if "track" in name_lower:
            return "track"
        if "mtb" in name_lower or "mountain" in name_lower:
            return "mtb"
        if "time trial" in name_lower or "tt" in name_lower:
            return "road"
        return "road"

    def _normalise_date(self, date_str: str) -> str:
        """Try to normalise a date string to ISO format."""
        if not date_str:
            return ""

        # Already ISO format
        if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            return date_str[:10]

        # Try common date formats
        for fmt in [
            "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
            "%B %d, %Y", "%b %d, %Y", "%d %B", "%d %b",
        ]:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_str.strip(), fmt)
                if "%Y" not in fmt:
                    now = datetime.now()
                    dt = dt.replace(year=now.year)
                    if dt < now:
                        dt = dt.replace(year=now.year + 1)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return date_str
