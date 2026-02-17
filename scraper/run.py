#!/usr/bin/env python3
"""Run all scrapers and output combined event data as JSON."""

import json
import logging
import sys
from pathlib import Path

from .auscycling import AusCyclingScraper
from .westcoast_masters import WestCoastMastersScraper
from .seed_data import get_seed_events

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "data"


def run_scrapers():
    """Run all scrapers and collect events."""
    scrapers = [
        AusCyclingScraper(),
        WestCoastMastersScraper(),
    ]

    all_events = []
    for scraper in scrapers:
        try:
            events = scraper.scrape()
            all_events.extend(events)
            logger.info(f"{scraper.SOURCE_NAME}: {len(events)} events")
        except Exception as e:
            logger.error(f"{scraper.SOURCE_NAME} failed: {e}")

    return all_events


def main():
    logger.info("Starting cycling event scrapers...")

    events = run_scrapers()

    # If no events were scraped (likely due to bot protection), use seed data
    if not events:
        logger.info("No events scraped from live sources. Using seed data.")
        events = get_seed_events()

    # Deduplicate by name+date
    seen = set()
    unique_events = []
    for event in events:
        key = (event.name.lower().strip(), event.date)
        if key not in seen:
            seen.add(key)
            unique_events.append(event)

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "events.json"
    with open(output_path, "w") as f:
        json.dump([e.to_dict() for e in unique_events], f, indent=2)

    logger.info(f"Wrote {len(unique_events)} events to {output_path}")
    return unique_events


if __name__ == "__main__":
    main()
