"""Scraper for AusCycling events (auscycling.org.au).

AusCycling uses Next.js behind Cloudflare, so a headless browser is required.
We also try the AusCycling EntryBoss calendar as a more accessible source.
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

EVENTS_URL = "https://auscycling.org.au/events"

# AusCycling EntryBoss calendar (often more accessible than the main site)
ENTRYBOSS_AC_URL = "https://entryboss.cc/calendar/ac"
ENTRYBOSS_AC_JSON_URL = "https://entryboss.cc/calendar/ac.json"

# JS to extract events from AusCycling pages
AUSCYCLING_EXTRACT_JS = """
() => {
    const events = [];

    // Try event cards/links
    const selectors = [
        'a[href*="/event"]',
        '[class*="event"] a',
        '[class*="Event"] a',
        '.event-card', '.event-item',
        'article',
    ];

    for (const sel of selectors) {
        const elements = document.querySelectorAll(sel);
        if (elements.length > 0) {
            elements.forEach(el => {
                const name = el.textContent.trim().substring(0, 200);
                if (name && name.length > 5 && name.length < 200) {
                    const timeEl = el.querySelector('time') || el.closest('[class*="event"]')?.querySelector('time');
                    const dateStr = timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent.trim()) : '';
                    const href = el.tagName === 'A' ? el.href : (el.querySelector('a')?.href || '');

                    events.push({ name, date: dateStr, url: href });
                }
            });
            if (events.length > 0) break;
        }
    }

    // Try table rows
    if (events.length === 0) {
        document.querySelectorAll('table tr').forEach(row => {
            const cells = row.querySelectorAll('td');
            if (cells.length >= 2) {
                events.push({
                    name: cells[1].textContent.trim().substring(0, 200),
                    date: cells[0].textContent.trim(),
                    url: row.querySelector('a')?.href || '',
                });
            }
        });
    }

    return {
        events: events,
        title: document.title,
        textPreview: document.body ? document.body.innerText.substring(0, 1000) : '',
        html_classes: [...new Set([...document.querySelectorAll('*')].slice(0, 200).flatMap(el => [...el.classList]))].slice(0, 50),
    };
}
"""


class AusCyclingScraper(BaseScraper):
    """Scrapes events from AusCycling using a headless browser.

    AusCycling.org.au is a Next.js site behind Cloudflare protection.
    Playwright with stealth mode is used to render the JS content.
    Also tries the AusCycling EntryBoss calendar as a fallback.
    """

    SOURCE_NAME = "auscycling"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Strategy 1: Try EntryBoss AusCycling JSON endpoint
        self._try_entryboss_json()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss JSON")
            return self.events

        # Strategy 2: Try EntryBoss HTML via cloudscraper (bypasses Cloudflare)
        self._scrape_entryboss_cf()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss cloudscraper")
            return self.events

        # Strategy 3: Try EntryBoss AusCycling with JS extraction
        self._scrape_entryboss_js()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss JS")
            return self.events

        # Strategy 4: Try AusCycling main events page with JS extraction
        result = self._extract_via_js(
            EVENTS_URL,
            AUSCYCLING_EXTRACT_JS,
            wait_for="[class*='event'], a[href*='/event']",
            wait_ms=5000,
        )
        if result:
            self._process_js_result(result)

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from main page JS")
            return self.events

        # Strategy 5: Try main events page with HTML parsing
        html = self._fetch_page(EVENTS_URL, wait_for="[class*='event']", wait_ms=5000)
        if html:
            self._parse_events_page(html)

        # Strategy 6: Try discipline-specific pages
        for discipline, url in DISCIPLINE_URLS.items():
            html = self._fetch_page(url, wait_for="table, [class*='event'], [class*='calendar']", wait_ms=5000)
            if html:
                self._parse_discipline_page(html, discipline)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events total")
        return self.events

    def _try_entryboss_json(self):
        """Try Rails .json endpoint on AusCycling EntryBoss calendar."""
        data = None

        # Try cloudscraper first (bypasses Cloudflare JS challenges)
        resp = self._make_cf_request(ENTRYBOSS_AC_JSON_URL, headers={
            "Accept": "application/json",
        })
        if resp:
            try:
                data = resp.json()
            except Exception:
                logger.debug(f"[{self.SOURCE_NAME}] Cloudscraper .json response was not JSON")

        # Try Playwright browser
        if not data:
            data = self._fetch_json_via_browser(ENTRYBOSS_AC_JSON_URL)

        # Try plain requests
        if not data:
            resp = self._make_request(ENTRYBOSS_AC_JSON_URL, headers={
                "Accept": "application/json",
            })
            if resp:
                try:
                    data = resp.json()
                except Exception:
                    pass

        if not data:
            logger.info(f"[{self.SOURCE_NAME}] EntryBoss .json endpoint not available")
            return

        races = data
        if isinstance(data, dict):
            races = data.get("races", data.get("events", data.get("fixtures", [])))

        if not isinstance(races, list):
            return

        for item in races:
            try:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", item.get("title", ""))
                date_str = item.get("date", item.get("start_date", item.get("starts_at", "")))
                url = item.get("url", item.get("link", ""))

                if name and len(name) > 3:
                    discipline = self._guess_discipline(name, name)
                    state = self._guess_state(url, name, name)
                    self.events.append(CyclingEvent(
                        name=name[:200],
                        date=self._normalise_date(str(date_str)),
                        end_date=None,
                        venue="TBA",
                        address="TBA",
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=url if url else ENTRYBOSS_AC_URL,
                        state=state,
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss JSON item: {e}")

    def _scrape_entryboss_cf(self):
        """Scrape EntryBoss calendar HTML using cloudscraper to bypass Cloudflare."""
        resp = self._make_cf_request(ENTRYBOSS_AC_URL)
        if not resp:
            return

        html = resp.text
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss CF page: {len(html)} bytes")

        soup = BeautifulSoup(html, "lxml")

        # Look for race links
        race_links = soup.select("a[href*='/races/']")
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss CF: {len(race_links)} race links found")

        for link in race_links:
            try:
                name = link.get_text(strip=True)
                if not name or len(name) <= 3:
                    continue

                url = link.get("href", "")
                if url and not url.startswith("http"):
                    url = "https://entryboss.cc" + url

                # Look for date in parent element
                date_str = ""
                parent = link.find_parent(["tr", "div", "li", "article", "section"])
                if parent:
                    time_el = parent.find("time")
                    if time_el:
                        date_str = time_el.get("datetime", time_el.get_text(strip=True))
                    else:
                        parent_text = parent.get_text()
                        date_match = re.search(
                            r'(\d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*[\s/\-]\d{2,4})',
                            parent_text, re.IGNORECASE
                        ) or re.search(r'(\d{4}-\d{2}-\d{2})', parent_text)
                        if date_match:
                            date_str = date_match.group(1)

                discipline = self._guess_discipline(name, name)
                state = self._guess_state(url, name, name)
                self.events.append(CyclingEvent(
                    name=name[:200],
                    date=self._normalise_date(date_str),
                    end_date=None,
                    venue="TBA",
                    address="TBA",
                    lat=None,
                    lng=None,
                    discipline=discipline,
                    organiser="AusCycling",
                    source=self.SOURCE_NAME,
                    url=url if url else ENTRYBOSS_AC_URL,
                    state=state,
                ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss CF link: {e}")

        if self.events:
            return

        # Fallback: try table rows
        for row in soup.select("table tr"):
            try:
                cells = row.select("td")
                if len(cells) >= 2:
                    date_str = cells[0].get_text(strip=True)
                    name = cells[1].get_text(strip=True)
                    link = row.select_one("a[href]")
                    url = link["href"] if link else None
                    if url and not url.startswith("http"):
                        url = "https://entryboss.cc" + url

                    if name and len(name) > 3:
                        discipline = self._guess_discipline(name, name)
                        state = self._guess_state(url, name, name)
                        self.events.append(CyclingEvent(
                            name=name[:200],
                            date=self._normalise_date(date_str),
                            end_date=None,
                            venue="TBA",
                            address="TBA",
                            lat=None,
                            lng=None,
                            discipline=discipline,
                            organiser="AusCycling",
                            source=self.SOURCE_NAME,
                            url=url if url else ENTRYBOSS_AC_URL,
                            state=state,
                        ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss CF table row: {e}")

    def _scrape_entryboss_js(self):
        """Use Playwright JS extraction on AusCycling EntryBoss page."""
        # Reuse the same JS extraction function pattern
        extract_js = """
        () => {
            const events = [];
            const raceLinks = document.querySelectorAll('a[href*="/races/"]');
            raceLinks.forEach(link => {
                const name = link.textContent.trim();
                if (name && name.length > 3) {
                    let dateStr = '';
                    const parent = link.closest('tr, div, li, article, section');
                    if (parent) {
                        const timeEl = parent.querySelector('time');
                        if (timeEl) {
                            dateStr = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
                        } else {
                            const text = parent.textContent;
                            const dateMatch = text.match(/(\\d{1,2}[\\s\\/\\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\\s\\/\\-]\\d{2,4})/i)
                                || text.match(/(\\d{4}-\\d{2}-\\d{2})/);
                            if (dateMatch) dateStr = dateMatch[1];
                        }
                    }
                    events.push({ name: name.substring(0, 200), date: dateStr, url: link.href });
                }
            });

            // Try table rows
            if (events.length === 0) {
                document.querySelectorAll('table tr').forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        events.push({
                            name: cells[1].textContent.trim().substring(0, 200),
                            date: cells[0].textContent.trim(),
                            url: row.querySelector('a')?.href || '',
                        });
                    }
                });
            }

            return {
                events: events,
                title: document.title,
                textPreview: document.body ? document.body.innerText.substring(0, 1000) : '',
            };
        }
        """

        result = self._extract_via_js(
            ENTRYBOSS_AC_URL,
            extract_js,
            wait_for="a[href*='/races/'], table",
            wait_ms=5000,
        )
        if result:
            self._process_js_result(result)

    def _process_js_result(self, result):
        """Process JS extraction result into CyclingEvent objects."""
        if isinstance(result, dict):
            logger.info(f"[{self.SOURCE_NAME}] Page title: {result.get('title', 'N/A')}")
            preview = result.get("textPreview", "")
            if preview:
                logger.info(f"[{self.SOURCE_NAME}] Text preview: {preview[:300]}")
            events = result.get("events", [])
        else:
            events = result if isinstance(result, list) else []

        for item in events:
            try:
                name = item.get("name", "")
                if name and len(name) > 3:
                    url = item.get("url", "")
                    discipline = self._guess_discipline(name, name)
                    state = self._guess_state(url, name, name)
                    self.events.append(CyclingEvent(
                        name=name[:200],
                        date=self._normalise_date(item.get("date", "")),
                        end_date=None,
                        venue="TBA",
                        address="TBA",
                        lat=None,
                        lng=None,
                        discipline=discipline,
                        organiser="AusCycling",
                        source=self.SOURCE_NAME,
                        url=url,
                        state=state,
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse JS event: {e}")

    def _parse_events_page(self, html: str):
        """Parse the main /events listing page (JS-rendered)."""
        soup = BeautifulSoup(html, "lxml")

        # Log page structure for debugging
        title = soup.title.string if soup.title else "N/A"
        logger.info(f"[{self.SOURCE_NAME}] Events page title: {title}")

        selectors = [
            ".event-card", ".event-item", "article.event",
            "[class*='EventCard']", "[class*='event-card']",
            "[class*='EventList'] > div", "[class*='event-list'] > div",
            "[data-testid*='event']",
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
            cards = soup.select("a[href*='/event']")
            logger.info(f"[{self.SOURCE_NAME}] Fallback: found {len(cards)} event links")

        for card in cards:
            try:
                name = self._extract_text(card, "h2, h3, h4, .event-title, .title, [class*='title'], [class*='Title']")
                if not name:
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

                discipline = self._guess_discipline(name, card.get_text())
                state = self._guess_state(url, venue, name)

                if name and len(name) > 3:
                    self.events.append(CyclingEvent(
                        name=name[:200],
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

        # Try table rows first
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

    def _is_header_row(self, text: str) -> bool:
        """Check if a table row is a header rather than data."""
        headers = ["event", "date", "venue", "location", "state", "discipline", "name"]
        return text.lower().strip() in headers

    def _guess_discipline(self, name: str, text: str) -> str:
        name_lower = name.lower()
        text_lower = text.lower() if text else ""
        for keyword, disc in [
            ("track", "track"), ("velodrome", "track"),
            ("criterium", "criterium"), ("crit ", "criterium"),
            ("road", "road"),
            ("gravel", "gravel"),
            ("mtb", "mtb"), ("mountain", "mtb"), ("xco", "mtb"), ("downhill", "mtb"),
            ("bmx", "bmx"),
            ("cyclocross", "cyclocross"), ("cx ", "cyclocross"),
        ]:
            if keyword in name_lower or keyword in text_lower:
                return disc
        return "road"

    def _guess_state(self, url: str | None, venue: str | None, name: str | None) -> str | None:
        """Guess the Australian state from URL, venue or event name."""
        texts = [s for s in [url, venue, name] if s]
        combined = " ".join(texts).lower()

        if url:
            for code in ["wa", "nsw", "vic", "qld", "sa", "tas", "act", "nt"]:
                if f"/{code}/" in url.lower():
                    return code.upper()

        state_patterns = {
            "WA": [" wa ", "western australia", "perth", "fremantle"],
            "NSW": [" nsw ", "new south wales", "sydney"],
            "VIC": [" vic ", "victoria", "melbourne", "geelong", "ballarat", "shepparton"],
            "QLD": [" qld ", "queensland", "brisbane", "gold coast", "toowoomba"],
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
