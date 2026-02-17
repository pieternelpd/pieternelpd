"""Scraper for AusCycling events (auscycling.org.au).

AusCycling uses Next.js behind Cloudflare, so a headless browser is required
to render the JS-driven event listings.
"""

import logging
import re
from bs4 import BeautifulSoup
from .base import BaseScraper, CyclingEvent

logger = logging.getLogger(__name__)

# AusCycling event listing pages by discipline
DISCIPLINE_URLS = {
    "road": "https://auscycling.org.au/event-hub/event-calendar-2025-2026/road-events-calendar-25-26",
    "track": "https://auscycling.org.au/event-hub/event-calendar-2025-2026/track-events-calendar",
    "mtb": "https://auscycling.org.au/event-hub/event-calendar-2025-2026/mountain-bike-events-calendar-25-26",
    "gravel": "https://auscycling.org.au/event-hub/event-calendar-2025-2026/gravel-events-calendar-25-26",
    "bmx": "https://auscycling.org.au/event-hub/event-calendar-2025-2026/bmx-racing-events-calendar-25-26",
}

# Main events listing with filters
EVENTS_URL = "https://auscycling.org.au/events"


class AusCyclingScraper(BaseScraper):
    """Scrapes events from AusCycling using a headless browser.

    AusCycling.org.au is a Next.js site behind Cloudflare protection.
    Playwright is used to render the JS content and extract events.
    """

    SOURCE_NAME = "auscycling"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Try main events page first (JS-rendered event cards)
        html = self._fetch_page(EVENTS_URL, wait_for="[class*='event']", wait_ms=5000)
        if html:
            self._parse_events_page(html)

        # Try discipline-specific pages (often have table-based layouts)
        for discipline, url in DISCIPLINE_URLS.items():
            html = self._fetch_page(url, wait_for="table, [class*='event'], [class*='calendar']", wait_ms=5000)
            if html:
                self._parse_discipline_page(html, discipline)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events")
        return self.events

    def _parse_events_page(self, html: str):
        """Parse the main /events listing page (JS-rendered)."""
        soup = BeautifulSoup(html, "lxml")

        # Try various selectors that Next.js sites commonly use
        selectors = [
            ".event-card", ".event-item", "article.event",
            "[class*='EventCard']", "[class*='event-card']",
            "[class*='EventList'] > div", "[class*='event-list'] > div",
            "[data-testid*='event']",
            # Generic: any card/item inside an events container
            "[class*='event'] [class*='card']",
            "[class*='Event'] a[href*='/event']",
            "a[href*='/events/']",
        ]

        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                logger.info(f"[{self.SOURCE_NAME}] Found {len(cards)} cards with selector: {sel}")
                break

        if not cards:
            # Fallback: look for any links that point to event pages
            cards = soup.select("a[href*='/event']")
            logger.info(f"[{self.SOURCE_NAME}] Fallback: found {len(cards)} event links")

        for card in cards:
            try:
                name = self._extract_text(card, "h2, h3, h4, .event-title, .title, [class*='title'], [class*='Title']")
                if not name:
                    # If it's a link, use the link text
                    name = card.get_text(strip=True)

                date_str = self._extract_text(card, ".date, .event-date, time, [class*='date'], [class*='Date']")
                if not date_str:
                    time_el = card.select_one("time[datetime]")
                    if time_el:
                        date_str = time_el.get("datetime", "")

                venue = self._extract_text(card, ".venue, .location, .event-location, [class*='venue'], [class*='location'], [class*='Venue'], [class*='Location']")

                link = card.select_one("a[href]") if card.name != "a" else card
                url = link["href"] if link and link.has_attr("href") else None
                if url and not url.startswith("http"):
                    url = "https://auscycling.org.au" + url

                discipline = self._guess_discipline(name, card)
                state = self._guess_state(url, venue, name)

                if name and len(name) > 3:
                    self.events.append(CyclingEvent(
                        name=name[:200],  # Truncate overly long names
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or "TBA",
                        address=venue or "TBA",
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=url,
                        state=state,
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse event card: {e}")

    def _parse_discipline_page(self, html: str, discipline: str):
        """Parse a discipline-specific calendar page."""
        soup = BeautifulSoup(html, "lxml")

        # Try table rows first (these pages often use tables)
        tables = soup.select("table")
        for table in tables:
            rows = table.select("tr")
            for row in rows:
                try:
                    cells = row.select("td")
                    if len(cells) >= 2:
                        date_str = cells[0].get_text(strip=True)
                        name = cells[1].get_text(strip=True)
                        venue = cells[2].get_text(strip=True) if len(cells) > 2 else "TBA"
                        state = cells[3].get_text(strip=True) if len(cells) > 3 else None

                        link = row.select_one("a[href]")
                        url = link["href"] if link else None
                        if url and not url.startswith("http"):
                            url = "https://auscycling.org.au" + url

                        if name and not self._is_header_row(name):
                            self.events.append(CyclingEvent(
                                name=name,
                                date=self._normalise_date(date_str),
                                end_date=None,
                                venue=venue,
                                address=venue,
                                lat=None,
                                lng=None,
                                discipline=discipline,
                                organiser="AusCycling",
                                source=self.SOURCE_NAME,
                                url=url,
                                state=state if state and len(state) <= 3 else self._guess_state(url, venue, name),
                            ))
                except Exception as e:
                    logger.debug(f"Failed to parse row: {e}")

        # Also try generic event item selectors
        for item in soup.select(".event-row, .calendar-item, [class*='event-item'], [class*='EventItem']"):
            try:
                name = self._extract_text(item, "h2, h3, h4, .title, [class*='title']")
                date_str = self._extract_text(item, ".date, time, [class*='date']")
                venue = self._extract_text(item, ".venue, .location, [class*='venue']")

                if name:
                    self.events.append(CyclingEvent(
                        name=name,
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or "TBA",
                        address=venue or "TBA",
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=None,
                        state=self._guess_state(None, venue, name),
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse event item: {e}")

    def _extract_text(self, element, selectors: str) -> str:
        for selector in selectors.split(","):
            el = element.select_one(selector.strip())
            if el:
                return el.get_text(strip=True)
        return ""

    def _normalise_date(self, date_str: str) -> str:
        """Try to normalise a date string to ISO format."""
        if not date_str:
            return ""

        # Already ISO format
        if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            return date_str[:10]

        # Try common Australian date formats
        for fmt in [
            "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
            "%B %d, %Y", "%b %d, %Y", "%d %B", "%d %b",
        ]:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_str.strip(), fmt)
                # If no year was in the format, assume current/next year
                if "%Y" not in fmt:
                    now = datetime.now()
                    dt = dt.replace(year=now.year)
                    if dt < now:
                        dt = dt.replace(year=now.year + 1)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return date_str

    def _is_header_row(self, text: str) -> bool:
        """Check if a table row is a header rather than data."""
        headers = ["event", "date", "venue", "location", "state", "discipline", "name"]
        return text.lower().strip() in headers

    def _guess_discipline(self, name: str, card) -> str:
        name_lower = name.lower()
        text = card.get_text().lower()
        for keyword, disc in [
            ("track", "track"), ("velodrome", "track"),
            ("road", "road"), ("criterium", "criterium"), ("crit", "criterium"),
            ("gravel", "gravel"), ("mtb", "mtb"), ("mountain", "mtb"),
            ("bmx", "bmx"), ("cyclocross", "cyclocross"), ("cx", "cyclocross"),
        ]:
            if keyword in name_lower or keyword in text:
                return disc
        return "road"

    def _guess_state(self, url: str | None, venue: str | None, name: str | None) -> str | None:
        """Guess the Australian state from URL, venue or event name."""
        texts = [s for s in [url, venue, name] if s]
        combined = " ".join(texts).lower()

        # Check for state codes in URL paths
        if url:
            for code in ["wa", "nsw", "vic", "qld", "sa", "tas", "act", "nt"]:
                if f"/{code}/" in url.lower():
                    return code.upper()

        # Check for state names/abbreviations in text
        state_patterns = {
            "WA": [" wa ", "western australia", "perth", "fremantle"],
            "NSW": [" nsw ", "new south wales", "sydney"],
            "VIC": [" vic ", "victoria", "melbourne", "geelong", "ballarat"],
            "QLD": [" qld ", "queensland", "brisbane", "gold coast"],
            "SA": [" sa ", "south australia", "adelaide"],
            "TAS": [" tas ", "tasmania", "hobart", "launceston"],
            "ACT": [" act ", "canberra"],
            "NT": [" nt ", "northern territory", "darwin"],
        }
        for state, patterns in state_patterns.items():
            for pattern in patterns:
                if pattern in f" {combined} ":
                    return state

        return None
