#!/usr/bin/env python3
"""Run all scrapers and output combined event data as JSON.

Uses Playwright headless browser to bypass Cloudflare and render
JS-driven content. Falls back to seed data if scraping fails.
"""

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


def _launch_browser():
    """Launch a Playwright Chromium browser instance."""
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-AU",
        )
        logger.info("Playwright browser launched successfully")
        return pw, browser, context
    except ImportError:
        logger.warning("Playwright not installed. Falling back to requests-only mode.")
        return None, None, None
    except Exception as e:
        logger.warning(f"Failed to launch Playwright browser: {e}")
        return None, None, None


def run_scrapers():
    """Run all scrapers and collect events."""
    pw, browser, context = _launch_browser()

    scrapers = [
        AusCyclingScraper(browser=context),
        WestCoastMastersScraper(browser=context),
    ]

    all_events = []
    for scraper in scrapers:
        try:
            events = scraper.scrape()
            all_events.extend(events)
            logger.info(f"{scraper.SOURCE_NAME}: {len(events)} events")
        except Exception as e:
            logger.error(f"{scraper.SOURCE_NAME} failed: {e}")

    # Clean up browser
    if context:
        try:
            context.close()
        except Exception:
            pass
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if pw:
        try:
            pw.stop()
        except Exception:
            pass

    return all_events


def main():
    logger.info("Starting cycling event scrapers...")

    events = run_scrapers()

    # If no events were scraped (likely due to bot protection), use seed data
    if not events:
        logger.info("No events scraped from live sources. Using seed data as fallback.")
        events = get_seed_events()
    else:
        # Merge with seed data to ensure we always have a baseline
        # Live-scraped events take priority over seed events (by name+date)
        seed_events = get_seed_events()
        seen = set()
        for event in events:
            seen.add((event.name.lower().strip(), event.date))

        # Add seed events that weren't found by scrapers
        for seed in seed_events:
            key = (seed.name.lower().strip(), seed.date)
            if key not in seen:
                events.append(seed)
                seen.add(key)

        logger.info(f"Merged: {len(events)} total events (scraped + seed fallback)")

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
