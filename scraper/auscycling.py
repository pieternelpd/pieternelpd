"""Scraper for AusCycling events (auscycling.org.au).

AusCycling uses Next.js behind Cloudflare, so a headless browser is required.
We also try the AusCycling EntryBoss calendar as a more accessible source.
"""

import logging
import re
from datetime import date
from bs4 import BeautifulSoup
from .base import BaseScraper, CyclingEvent

logger = logging.getLogger(__name__)


def _season_years() -> tuple[int, int]:
    """Return (start_year, end_year) for the current Australian cycling season.

    The season runs roughly July to June, so from July onward we use
    the current year as the start; before July we use the previous year.
    """
    today = date.today()
    if today.month >= 7:
        return today.year, today.year + 1
    return today.year - 1, today.year


def _build_discipline_urls() -> dict[str, str]:
    """Build AusCycling discipline calendar URLs for the current season."""
    start, end = _season_years()
    short_start = start % 100
    short_end = end % 100
    base = f"https://auscycling.org.au/event-hub/event-calendar-{start}-{end}"
    return {
        "road": f"{base}/road-events-calendar-{short_start}-{short_end}",
        "track": f"{base}/track-events-calendar",
        "mtb": f"{base}/mountain-bike-events-calendar-{short_start}-{short_end}",
        "gravel": f"{base}/gravel-events-calendar-{short_start}-{short_end}",
        "bmx": f"{base}/bmx-racing-events-calendar-{short_start}-{short_end}",
    }


# AusCycling event listing pages by discipline
DISCIPLINE_URLS = _build_discipline_urls()

EVENTS_URL = "https://auscycling.org.au/events"

# AusCycling EntryBoss calendars (often more accessible than the main site)
ENTRYBOSS_AC_URL = "https://entryboss.cc/calendar/ac"
ENTRYBOSS_AC_JSON_URL = "https://entryboss.cc/calendar/ac.json"

# State-specific EntryBoss calendars
ENTRYBOSS_STATE_URLS = {
    "WA": "https://entryboss.cc/calendar/acw",
    "QLD": "https://entryboss.cc/calendar/acq",
    "NSW": "https://entryboss.cc/calendar/acnsw",
    "VIC": "https://entryboss.cc/calendar/acv",
    "SA": "https://entryboss.cc/calendar/acsa",
}

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

    def _make_event(self, name, date="", url=None, venue="TBA",
                    discipline=None, state=None, **kwargs):
        disc = discipline or self._guess_discipline(name, kwargs.get("text", name))
        st = state or self._guess_state(url, venue, name)
        return super()._make_event(
            name, date=date, url=url, venue=venue,
            discipline=disc, state=st, organiser="AusCycling", **kwargs,
        )

    def scrape(self) -> list[CyclingEvent]:
        self.events = []

        # Try all sources and merge results (don't short-circuit).
        # EntryBoss and auscycling.org.au list different subsets of events.

        # Source 1: EntryBoss AusCycling JSON endpoint
        self._try_entryboss_json()
        logger.info(f"[{self.SOURCE_NAME}] After EntryBoss JSON: {len(self.events)} events")

        # Source 2: EntryBoss HTML via curl_cffi (bypasses Cloudflare)
        self._scrape_entryboss_cf()
        logger.info(f"[{self.SOURCE_NAME}] After EntryBoss curl_cffi: {len(self.events)} events")

        # Source 3: EntryBoss with JS extraction (Playwright)
        self._scrape_entryboss_js()
        logger.info(f"[{self.SOURCE_NAME}] After EntryBoss JS: {len(self.events)} events")

        # Source 4: AusCycling main events page with JS extraction
        result = self._extract_via_js(
            EVENTS_URL,
            AUSCYCLING_EXTRACT_JS,
            wait_for="[class*='event'], a[href*='/event']",
            wait_ms=5000,
        )
        if result:
            self._process_js_result(result)

        # Source 5: Main events page with HTML parsing
        html = self._fetch_page(EVENTS_URL, wait_for="[class*='event']", wait_ms=5000)
        if html:
            self._parse_events_page(html)

        # Source 6: Discipline-specific pages
        for discipline, url in DISCIPLINE_URLS.items():
            html = self._fetch_page(url, wait_for="table, [class*='event'], [class*='calendar']", wait_ms=5000)
            if html:
                self._parse_discipline_page(html, discipline)

        # Deduplicate within this scraper
        self.events = self._deduplicate(self.events)

        logger.info(f"[{self.SOURCE_NAME}] Scraped {len(self.events)} events total")
        return self.events

    def _deduplicate(self, events: list[CyclingEvent]) -> list[CyclingEvent]:
        """Remove duplicate events by normalised name + date."""
        seen = set()
        unique = []
        for event in events:
            key = (event.name.lower().strip(), event.date)
            if key not in seen:
                seen.add(key)
                unique.append(event)
        return unique

    def _try_entryboss_json(self):
        """Try Rails .json endpoints on EntryBoss calendars (national + state)."""
        json_urls = [ENTRYBOSS_AC_JSON_URL]
        # State calendars also support .json
        for state_url in ENTRYBOSS_STATE_URLS.values():
            json_urls.append(state_url + ".json")

        for json_url in json_urls:
            self._fetch_entryboss_json(json_url)

    def _fetch_entryboss_json(self, json_url):
        """Fetch and parse a single EntryBoss .json endpoint."""
        data = None

        # Try curl_cffi first (bypasses Cloudflare JS challenges)
        resp = self._make_cf_request(json_url, headers={
            "Accept": "application/json",
        })
        if resp:
            try:
                data = resp.json()
            except Exception:
                logger.debug(f"[{self.SOURCE_NAME}] curl_cffi .json response was not JSON for {json_url}")

        # Try Playwright browser
        if not data:
            data = self._fetch_json_via_browser(json_url)

        # Try plain requests
        if not data:
            resp = self._make_request(json_url, headers={
                "Accept": "application/json",
            })
            if resp:
                try:
                    data = resp.json()
                except Exception:
                    pass

        if not data:
            logger.info(f"[{self.SOURCE_NAME}] EntryBoss .json not available: {json_url}")
            return

        races = data
        if isinstance(data, dict):
            races = data.get("races", data.get("events", data.get("fixtures", [])))

        if not isinstance(races, list):
            return

        count = 0
        for item in races:
            try:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", item.get("title", ""))
                date_str = item.get("date", item.get("start_date", item.get("starts_at", "")))
                url = item.get("url", item.get("link", ""))

                if name and len(name) > 3:
                    self.events.append(self._make_event(
                        name, date=str(date_str),
                        url=url if url else ENTRYBOSS_AC_URL,
                    ))
                    count += 1
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss JSON item: {e}")

        logger.info(f"[{self.SOURCE_NAME}] {json_url}: {count} events from JSON")

    def _scrape_entryboss_cf(self):
        """Scrape EntryBoss calendar HTML using curl_cffi to bypass Cloudflare."""
        # Try national calendar plus state-specific calendars
        urls_to_try = [ENTRYBOSS_AC_URL] + list(ENTRYBOSS_STATE_URLS.values())

        for cal_url in urls_to_try:
            resp = self._make_cf_request(cal_url)
            if not resp:
                continue

            html = resp.text

            soup = BeautifulSoup(html, "lxml")
            title = soup.title.string if soup.title else "N/A"
            logger.info(f"[{self.SOURCE_NAME}] EntryBoss CF {cal_url}: title='{title}', {len(html)} bytes")

            # If we got a Cloudflare challenge page, skip
            if "just a moment" in title.lower() or "challenge" in title.lower():
                logger.warning(f"[{self.SOURCE_NAME}] Got Cloudflare challenge page for {cal_url}")
                continue

            self._parse_entryboss_html_content(soup, cal_url)

    def _parse_entryboss_html_content(self, soup, source_url):
        """Parse EntryBoss HTML content from BeautifulSoup tree."""
        # Look for race links
        race_links = soup.select("a[href*='/races/']")
        logger.info(f"[{self.SOURCE_NAME}] EntryBoss: {len(race_links)} race links in {source_url}")

        if not race_links:
            # Log page structure for debugging
            all_links = soup.select("a[href]")
            logger.info(f"[{self.SOURCE_NAME}] Total links on page: {len(all_links)}")
            if all_links:
                sample_hrefs = [a.get("href", "")[:80] for a in all_links[:10]]
                logger.info(f"[{self.SOURCE_NAME}] Sample links: {sample_hrefs}")
            body_text = soup.get_text()[:500]
            logger.info(f"[{self.SOURCE_NAME}] Page text preview: {body_text[:300]}")

        found_links = 0
        for link in race_links:
            try:
                name = link.get_text(strip=True)
                if not name or len(name) <= 3:
                    continue

                url = link.get("href", "")
                if url and not url.startswith("http"):
                    url = "https://entryboss.cc" + url

                # Look for date in parent element
                date_str = self._extract_date_near_element(link)

                self.events.append(self._make_event(
                    name, date=date_str,
                    url=url if url else source_url,
                ))
                found_links += 1
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss CF link: {e}")

        if found_links:
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

                    if name and len(name) > 3 and not self._is_header_row(name):
                        self.events.append(self._make_event(
                            name, date=date_str,
                            url=url if url else source_url,
                        ))
            except Exception as e:
                logger.debug(f"Failed to parse EntryBoss CF table row: {e}")

    def _extract_date_near_element(self, element):
        """Extract a date string from near a BeautifulSoup element."""
        parent = element.find_parent(["tr", "div", "li", "article", "section"])
        if not parent:
            return ""

        time_el = parent.find("time")
        if time_el:
            return time_el.get("datetime", time_el.get_text(strip=True))

        parent_text = parent.get_text()
        # Try: "Sat, 22 Feb 2025" or "22 Feb 2025" or "22/02/2025"
        date_match = (
            re.search(r'(\d{1,2}[\s/\-](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*[\s/\-]\d{2,4})', parent_text, re.IGNORECASE)
            or re.search(r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4})', parent_text, re.IGNORECASE)
            or re.search(r'(\d{4}-\d{2}-\d{2})', parent_text)
            or re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', parent_text)
        )
        if date_match:
            return date_match.group(1)
        return ""

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
            wait_ms=8000,
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
                    self.events.append(self._make_event(
                        name, date=item.get("date", ""), url=url,
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

                if name and len(name) > 3:
                    self.events.append(self._make_event(
                        name, date=date_str, url=url,
                        venue=venue or "TBA",
                        text=card.get_text(),
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
                            self.events.append(self._make_event(
                                name, date=date_str, url=url,
                                venue=venue, discipline=discipline,
                                state=state if state and len(state) <= 3 else None,
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
                    self.events.append(self._make_event(
                        name, date=date_str,
                        venue=venue or "TBA", discipline=discipline,
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
