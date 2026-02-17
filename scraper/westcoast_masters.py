"""Scraper for West Coast Masters Cycling Council events."""

import logging
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
}

CALENDAR_URLS = [
    "https://www.wcmasterscycling.asn.au/racing/calendar/",
    "https://www.wcmasterscycling.asn.au/calendar/calendar.htm",
]

ENTRYBOSS_URL = "https://entryboss.cc/calendar/westcoastmasterscc"

# WestCycle listing for WCMCC
WESTCYCLE_URL = "https://westcycle.org.au/event_club/wcmcc-west-coast-masters-cycling-council/"


class WestCoastMastersScraper(BaseScraper):
    """Scrapes events from West Coast Masters Cycling Council.

    Tries multiple sources:
    1. WCMCC website (WordPress)
    2. EntryBoss calendar
    3. WestCycle listing
    """

    SOURCE_NAME = "wcmcc"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Try WordPress REST API first (most structured)
        self._try_wordpress_api()

        # Try WCMCC calendar pages
        for url in CALENDAR_URLS:
            resp = self._make_request(url)
            if resp:
                self._parse_calendar(resp.text, url)

        # Try WestCycle
        resp = self._make_request(WESTCYCLE_URL)
        if resp:
            self._parse_westcycle(resp.text)

        # Try EntryBoss
        resp = self._make_request(ENTRYBOSS_URL)
        if resp:
            self._parse_entryboss(resp.text)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events")
        return self.events

    def _try_wordpress_api(self):
        """Try WordPress REST API endpoints."""
        api_urls = [
            "https://www.wcmasterscycling.asn.au/wp-json/tribe/events/v1/events",
            "https://www.wcmasterscycling.asn.au/wp-json/wp/v2/posts?categories=events&per_page=50",
        ]
        for url in api_urls:
            resp = self._make_request(url)
            if resp:
                try:
                    data = resp.json()
                    events = data.get("events", data) if isinstance(data, dict) else data
                    for item in events:
                        venue_name = ""
                        if isinstance(item.get("venue"), dict):
                            venue_name = item["venue"].get("venue", "")
                        venue_info = self._match_venue(
                            venue_name or item.get("title", {}).get("rendered", "")
                        )
                        self.events.append(CyclingEvent(
                            name=item.get("title", {}).get("rendered", item.get("title", "")),
                            date=item.get("start_date", item.get("date", "")),
                            end_date=item.get("end_date", None),
                            venue=venue_name or "TBA",
                            address=venue_info.get("address", "WA"),
                            lat=venue_info.get("lat"),
                            lng=venue_info.get("lng"),
                            discipline=self._guess_discipline(
                                item.get("title", {}).get("rendered", item.get("title", ""))
                            ),
                            organiser="West Coast Masters CC",
                            source=self.SOURCE_NAME,
                            url=item.get("url", item.get("link", None)),
                            state="WA",
                        ))
                    if self.events:
                        return  # Got data from API, skip other sources
                except Exception as e:
                    logger.debug(f"WordPress API parse error: {e}")

    def _parse_calendar(self, html: str, source_url: str):
        """Parse WCMCC calendar page."""
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

                # Try structured event markup
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
                        date=date_str,
                        end_date=None,
                        venue=venue or venue_info.get("address", "WA"),
                        address=venue_info.get("address", "WA"),
                        lat=venue_info.get("lat"),
                        lng=venue_info.get("lng"),
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=source_url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse calendar item: {e}")

    def _parse_westcycle(self, html: str):
        """Parse WestCycle event listings."""
        soup = BeautifulSoup(html, "lxml")

        for item in soup.select(".wc-event, .event-listing, article, .type-wc-event"):
            try:
                name = ""
                date_str = ""
                venue = ""

                title_el = item.select_one("h2, h3, .event-title, a")
                if title_el:
                    name = title_el.get_text(strip=True)

                date_el = item.select_one(".event-date, time, .date")
                if date_el:
                    date_str = date_el.get("datetime", date_el.get_text(strip=True))

                venue_el = item.select_one(".event-venue, .venue, .location")
                if venue_el:
                    venue = venue_el.get_text(strip=True)

                link = item.select_one("a[href]")
                url = link["href"] if link else None

                if name:
                    venue_info = self._match_venue(name + " " + venue)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=date_str,
                        end_date=None,
                        venue=venue or venue_info.get("address", "WA"),
                        address=venue_info.get("address", "WA"),
                        lat=venue_info.get("lat"),
                        lng=venue_info.get("lng"),
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse WestCycle item: {e}")

    def _parse_entryboss(self, html: str):
        """Parse EntryBoss calendar page."""
        soup = BeautifulSoup(html, "lxml")

        for item in soup.select(".race-item, .event-card, [class*='race'], [class*='event']"):
            try:
                name = ""
                date_str = ""

                title_el = item.select_one("h2, h3, .race-name, .event-name, a")
                if title_el:
                    name = title_el.get_text(strip=True)

                date_el = item.select_one(".race-date, .date, time")
                if date_el:
                    date_str = date_el.get("datetime", date_el.get_text(strip=True))

                link = item.select_one("a[href]")
                url = link["href"] if link else None
                if url and not url.startswith("http"):
                    url = "https://entryboss.cc" + url

                if name:
                    venue_info = self._match_venue(name)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=date_str,
                        end_date=None,
                        venue=venue_info.get("address", "WA"),
                        address=venue_info.get("address", "WA"),
                        lat=venue_info.get("lat"),
                        lng=venue_info.get("lng"),
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=url,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss item: {e}")

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
