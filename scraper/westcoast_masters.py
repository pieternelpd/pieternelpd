"""Scraper for West Coast Masters Cycling Council events via EntryBoss.

EntryBoss is a Rails app. We try:
1. Rails .json endpoint (most reliable if available)
2. Playwright JS extraction from rendered page
3. BeautifulSoup HTML parsing as fallback
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
    "herne hill": {"address": "Herne Hill, WA 6056", "lat": -31.8350, "lng": 116.0250},
    "chidlow": {"address": "Chidlow, WA 6556", "lat": -31.8600, "lng": 116.2700},
    "doghill": {"address": "Doghill Rd, Baldivis WA 6171", "lat": -32.3200, "lng": 115.7900},
    "baldivis": {"address": "Baldivis, WA 6171", "lat": -32.3200, "lng": 115.7900},
    "splendid park": {"address": "Splendid Park, WA", "lat": -31.9500, "lng": 115.8600},
}

ENTRYBOSS_URL = "https://entryboss.cc/calendar/westcoastmasterscc"
ENTRYBOSS_JSON_URL = "https://entryboss.cc/calendar/westcoastmasterscc.json"

CALENDAR_URLS = [
    "https://www.wcmasterscycling.asn.au/racing/calendar/",
]

WESTCYCLE_URL = "https://westcycle.org.au/event_club/wcmcc-west-coast-masters-cycling-council/"

# JS to extract race data from EntryBoss rendered page
ENTRYBOSS_EXTRACT_JS = """
() => {
    const events = [];

    // Try finding all links that point to /races/
    const raceLinks = document.querySelectorAll('a[href*="/races/"]');
    raceLinks.forEach(link => {
        const name = link.textContent.trim();
        if (name && name.length > 3) {
            // Look for date in parent/sibling elements
            let dateStr = '';
            const parent = link.closest('tr, div, li, article, section');
            if (parent) {
                const timeEl = parent.querySelector('time');
                if (timeEl) {
                    dateStr = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
                } else {
                    // Look for date-like text in parent
                    const text = parent.textContent;
                    const dateMatch = text.match(/(\\d{1,2}[\\s\\/\\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\\s\\/\\-]\\d{2,4})/i)
                        || text.match(/(\\d{4}-\\d{2}-\\d{2})/);
                    if (dateMatch) dateStr = dateMatch[1];
                }
            }
            events.push({
                name: name.substring(0, 200),
                date: dateStr,
                url: link.href,
            });
        }
    });

    // Try table rows
    if (events.length === 0) {
        document.querySelectorAll('table tr').forEach(row => {
            const cells = row.querySelectorAll('td');
            if (cells.length >= 2) {
                const dateStr = cells[0].textContent.trim();
                const name = cells[1].textContent.trim();
                const link = row.querySelector('a[href]');
                if (name && name.length > 3) {
                    events.push({
                        name: name.substring(0, 200),
                        date: dateStr,
                        url: link ? link.href : null,
                    });
                }
            }
        });
    }

    // Try any card/list-like elements
    if (events.length === 0) {
        document.querySelectorAll('.card, .list-group-item, [class*="race"], [class*="fixture"], [class*="event"]').forEach(el => {
            const titleEl = el.querySelector('h1, h2, h3, h4, h5, a, .title, [class*="title"], [class*="name"]');
            const dateEl = el.querySelector('time, .date, [class*="date"]');
            const linkEl = el.querySelector('a[href]');
            if (titleEl) {
                events.push({
                    name: titleEl.textContent.trim().substring(0, 200),
                    date: dateEl ? (dateEl.getAttribute('datetime') || dateEl.textContent.trim()) : '',
                    url: linkEl ? linkEl.href : null,
                });
            }
        });
    }

    // Capture page title and text snippet for debugging
    return {
        events: events,
        title: document.title,
        textPreview: document.body ? document.body.innerText.substring(0, 1000) : '',
        html_classes: [...new Set([...document.querySelectorAll('*')].slice(0, 200).flatMap(el => [...el.classList]))].slice(0, 50),
    };
}
"""


class WestCoastMastersScraper(BaseScraper):
    """Scrapes events from West Coast Masters Cycling Council.

    Uses Playwright with stealth mode to render JS-heavy pages and
    bypass bot protection. Tries EntryBoss first, then WCMCC website.
    """

    SOURCE_NAME = "wcmcc"

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Strategy 1: Try EntryBoss Rails .json endpoint
        self._try_entryboss_json()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss JSON")
            return self.events

        # Strategy 2: Try EntryBoss HTML via cloudscraper (bypasses Cloudflare)
        self._scrape_entryboss_cf()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss curl_cffi")
            return self.events

        # Strategy 3: Try EntryBoss with JS extraction (Playwright)
        self._scrape_entryboss_js()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss JS")
            return self.events

        # Strategy 4: Try EntryBoss with HTML parsing
        self._scrape_entryboss_html()

        if self.events:
            logger.info(f"[{self.SOURCE_NAME}] Got {len(self.events)} events from EntryBoss HTML")
            return self.events

        # Strategy 5: Try WCMCC calendar pages
        for url in CALENDAR_URLS:
            html = self._fetch_page(url, wait_for="table, .tribe-events, article", wait_ms=3000)
            if html:
                self._parse_calendar(html, url)

        # Strategy 6: Try WestCycle
        html = self._fetch_page(WESTCYCLE_URL, wait_for=".event, article", wait_ms=3000)
        if html:
            self._parse_westcycle(html)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events total")
        return self.events

    def _try_entryboss_json(self):
        """Try Rails .json endpoint on EntryBoss (common Rails convention)."""
        data = None

        # Try cloudscraper first (bypasses Cloudflare JS challenges)
        resp = self._make_cf_request(ENTRYBOSS_JSON_URL, headers={
            "Accept": "application/json",
        })
        if resp:
            try:
                data = resp.json()
            except Exception:
                logger.debug(f"[{self.SOURCE_NAME}] Cloudscraper .json response was not JSON")

        # Try Playwright browser
        if not data:
            data = self._fetch_json_via_browser(ENTRYBOSS_JSON_URL)

        # Try plain HTTP request
        if not data:
            resp = self._make_request(ENTRYBOSS_JSON_URL, headers={
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

        # Handle both array and object with 'races'/'events' key
        races = data
        if isinstance(data, dict):
            races = data.get("races", data.get("events", data.get("fixtures", [])))

        if not isinstance(races, list):
            logger.info(f"[{self.SOURCE_NAME}] EntryBoss JSON: unexpected format: {type(races)}")
            return

        for item in races:
            try:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", item.get("title", ""))
                date_str = item.get("date", item.get("start_date", item.get("starts_at", "")))
                url = item.get("url", item.get("link", ""))

                if name and len(name) > 3:
                    venue_info = self._match_venue(name)
                    self.events.append(CyclingEvent(
                        name=name[:200],
                        date=self._normalise_date(str(date_str)),
                        end_date=None,
                        venue=venue_info.get("address", "WA") if venue_info else "WA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=url if url else ENTRYBOSS_URL,
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss JSON item: {e}")

    def _scrape_entryboss_cf(self):
        """Scrape EntryBoss calendar HTML using curl_cffi to bypass Cloudflare."""
        resp = self._make_cf_request(ENTRYBOSS_URL)
        if not resp:
            return

        html = resp.text
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string if soup.title else "N/A"
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss CF: title='{title}', {len(html)} bytes")

        # If we got a Cloudflare challenge page, skip
        if "just a moment" in title.lower() or "challenge" in title.lower():
            logger.warning(f"[{self.SOURCE_NAME}] Got Cloudflare challenge page")
            return

        # Look for race links
        race_links = soup.select("a[href*='/races/']")
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss CF: {len(race_links)} race links found")

        if not race_links:
            all_links = soup.select("a[href]")
            logger.info(f"[{self.SOURCE_NAME}] Total links on page: {len(all_links)}")
            if all_links:
                sample_hrefs = [a.get("href", "")[:80] for a in all_links[:10]]
                logger.info(f"[{self.SOURCE_NAME}] Sample links: {sample_hrefs}")
            body_text = soup.get_text()[:500]
            logger.info(f"[{self.SOURCE_NAME}] Page text preview: {body_text[:300]}")

        for link in race_links:
            try:
                name = link.get_text(strip=True)
                if not name or len(name) <= 3:
                    continue

                url = link.get("href", "")
                if url and not url.startswith("http"):
                    url = "https://entryboss.cc" + url

                # Look for date in parent element
                date_str = self._extract_date_near(link)

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
                    url=url if url else ENTRYBOSS_URL,
                    state="WA",
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

                    if name and len(name) > 3 and not self._is_header(name):
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
                            url=url if url else ENTRYBOSS_URL,
                            state="WA",
                        ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss CF table row: {e}")

    def _extract_date_near(self, element):
        """Extract a date string from near a BeautifulSoup element."""
        parent = element.find_parent(["tr", "div", "li", "article", "section"])
        if not parent:
            return ""
        time_el = parent.find("time")
        if time_el:
            return time_el.get("datetime", time_el.get_text(strip=True))
        parent_text = parent.get_text()
        date_match = (
            re.search(r'(\d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*[\s/\-]\d{2,4})', parent_text, re.IGNORECASE)
            or re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4})', parent_text, re.IGNORECASE)
            or re.search(r'(\d{4}-\d{2}-\d{2})', parent_text)
            or re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', parent_text)
        )
        return date_match.group(1) if date_match else ""

    def _scrape_entryboss_js(self):
        """Use Playwright JS extraction to get events from EntryBoss page."""
        result = self._extract_via_js(
            ENTRYBOSS_URL,
            ENTRYBOSS_EXTRACT_JS,
            wait_for="a[href*='/races/'], table, .card",
            wait_ms=8000,
        )

        if not result:
            return

        # Log debug info
        if isinstance(result, dict):
            logger.info(f"[{self.SOURCE_NAME}] Page title: {result.get('title', 'N/A')}")
            logger.info(f"[{self.SOURCE_NAME}] CSS classes found: {result.get('html_classes', [])[:20]}")
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
                    venue_info = self._match_venue(name)
                    self.events.append(CyclingEvent(
                        name=name[:200],
                        date=self._normalise_date(item.get("date", "")),
                        end_date=None,
                        venue=venue_info.get("address", "WA") if venue_info else "WA",
                        address=venue_info.get("address", "WA") if venue_info else "WA",
                        lat=venue_info.get("lat") if venue_info else None,
                        lng=venue_info.get("lng") if venue_info else None,
                        discipline=self._guess_discipline(name),
                        organiser="West Coast Masters CC",
                        source=self.SOURCE_NAME,
                        url=item.get("url", ENTRYBOSS_URL),
                        state="WA",
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss JS item: {e}")

    def _scrape_entryboss_html(self):
        """Scrape EntryBoss using Playwright + BeautifulSoup HTML parsing."""
        html = self._fetch_page(
            ENTRYBOSS_URL,
            wait_for="a[href*='/races/'], table, .card",
            wait_ms=8000,
        )
        if not html:
            return

        soup = BeautifulSoup(html, "lxml")

        # Log page structure for debugging
        title = soup.title.string if soup.title else "N/A"
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss HTML page title: {title}")

        # Try race links first
        race_links = soup.select("a[href*='/races/']")
        if race_links:
            logger.info(f"[{self.SOURCE_NAME}] Found {len(race_links)} race links")

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
                parent = link.find_parent(["tr", "div", "li", "article"])
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
                logger.debug(f"Failed to parse EntryBoss link: {e}")

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

                    if name and len(name) > 3 and not self._is_header(name):
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
                logger.debug(f"Failed to parse table row: {e}")

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

                if name and not self._is_header(name):
                    venue_info = self._match_venue(name + " " + venue)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or (venue_info.get("address", "WA") if venue_info else "WA"),
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

                if name and ("wcm" in name.lower() or "west coast" in name.lower()):
                    venue_info = self._match_venue(name + " " + venue)
                    self.events.append(CyclingEvent(
                        name=name,
                        date=self._normalise_date(date_str),
                        end_date=None,
                        venue=venue or (venue_info.get("address", "WA") if venue_info else "WA"),
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
        if "time trial" in name_lower or " tt " in name_lower:
            return "road"
        return "road"

    def _is_header(self, text: str) -> bool:
        """Check if text is a table header."""
        headers = ["event", "date", "venue", "location", "state", "discipline", "name"]
        return text.lower().strip() in headers

