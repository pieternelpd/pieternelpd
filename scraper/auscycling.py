"""Scraper for AusCycling events (auscycling.org.au)."""

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

# Also try the main events listing
EVENTS_URL = "https://auscycling.org.au/events"


class AusCyclingScraper(BaseScraper):
    """Scrapes events from AusCycling.

    Note: auscycling.org.au is behind Cloudflare protection.
    This scraper will attempt direct requests but may need a headless
    browser (Playwright) for reliable production scraping.
    """

    SOURCE_NAME = "auscycling"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Try main events page first
        resp = self._make_request(EVENTS_URL)
        if resp:
            self._parse_events_page(resp.text)

        # Try discipline-specific pages
        for discipline, url in DISCIPLINE_URLS.items():
            resp = self._make_request(url)
            if resp:
                self._parse_discipline_page(resp.text, discipline)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events")
        return self.events

    def _parse_events_page(self, html: str):
        """Parse the main /events listing page."""
        soup = BeautifulSoup(html, "lxml")

        # Look for event cards/items - structure depends on actual page
        for card in soup.select(".event-card, .event-item, article.event, [class*='event']"):
            try:
                name = self._extract_text(card, "h2, h3, .event-title, .title")
                date_str = self._extract_text(card, ".date, .event-date, time")
                venue = self._extract_text(card, ".venue, .location, .event-location")
                link = card.select_one("a[href]")
                url = link["href"] if link else None
                if url and not url.startswith("http"):
                    url = "https://auscycling.org.au" + url

                discipline = self._guess_discipline(name, card)

                if name and date_str:
                    self.events.append(CyclingEvent(
                        name=name,
                        date=date_str,
                        end_date=None,
                        venue=venue or "TBA",
                        address=venue or "TBA",
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=url,
                        state=self._guess_state(url, venue),
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse event card: {e}")

    def _parse_discipline_page(self, html: str, discipline: str):
        """Parse a discipline-specific calendar page."""
        soup = BeautifulSoup(html, "lxml")

        # These pages typically have tables or lists of events
        for row in soup.select("table tr, .event-row, .calendar-item"):
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

                    self.events.append(CyclingEvent(
                        name=name,
                        date=date_str,
                        end_date=None,
                        venue=venue,
                        address=venue,
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=url,
                        state=state,
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse row: {e}")

    def _extract_text(self, element, selectors: str) -> str:
        for selector in selectors.split(","):
            el = element.select_one(selector.strip())
            if el:
                return el.get_text(strip=True)
        return ""

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

    def _guess_state(self, url: str, venue: str) -> str | None:
        if url:
            for code in ["wa", "nsw", "vic", "qld", "sa", "tas", "act", "nt", "nat"]:
                if f"/{code}/" in url.lower():
                    return code.upper()
        return None
