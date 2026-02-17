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

    def __init__(self):
        self.session = None
        self.events: list[CyclingEvent] = []

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
