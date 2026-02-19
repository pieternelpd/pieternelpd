#!/usr/bin/env python3
"""Run all scrapers and output combined event data as JSON.

Uses Playwright headless browser to bypass Cloudflare and render
JS-driven content.
"""

import json
import logging
from pathlib import Path

from .auscycling import AusCyclingScraper
from .westcoast_masters import WestCoastMastersScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "data"


def _launch_browser():
    """Launch a Playwright Chromium browser instance with stealth mode.

    Uses playwright-stealth to automatically apply anti-detection evasions
    (webdriver flag, navigator properties, chrome runtime, WebGL, media
    codecs, etc.) to every page created in the context.
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        pw = sync_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--window-size=1920,1080",
        ]

        try:
            browser = pw.chromium.launch(headless=True, args=launch_args)
        except Exception as e:
            # Try finding the browser executable manually
            logger.warning(f"Default browser launch failed: {e}")
            cache_dir = Path.home() / ".cache" / "ms-playwright"
            chrome_paths = list(cache_dir.rglob("chrome")) + list(cache_dir.rglob("chromium"))
            if chrome_paths:
                exe = str(chrome_paths[0])
                logger.info(f"Trying browser at {exe}")
                browser = pw.chromium.launch(headless=True, executable_path=exe, args=launch_args)
            else:
                raise

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-AU",
            timezone_id="Australia/Perth",
            color_scheme="light",
        )

        # Apply stealth evasions to the browser context so every page
        # created from it gets anti-detection scripts injected.
        stealth = Stealth()
        stealth.apply_stealth_sync(context)

        logger.info("Playwright browser launched with stealth mode")
        return pw, browser, context
    except ImportError as e:
        logger.warning(f"Playwright or playwright-stealth not installed ({e}). Falling back to requests-only mode.")
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


def _load_existing_events(path: Path) -> list[dict]:
    """Load existing events.json to preserve data when scraping fails."""
    try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception as e:
        logger.warning(f"Could not load existing events: {e}")
    return []


def main():
    logger.info("Starting cycling event scrapers...")

    scraped = run_scrapers()

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "events.json"

    if scraped:
        # Deduplicate scraped events by name+date
        seen = set()
        unique_events = []
        for event in scraped:
            key = (event.name.lower().strip(), event.date)
            if key not in seen:
                seen.add(key)
                unique_events.append(event)

        # Merge with existing events (scraped events take priority)
        existing = _load_existing_events(output_path)
        for existing_event in existing:
            key = (existing_event.get("name", "").lower().strip(), existing_event.get("date", ""))
            if key not in seen:
                seen.add(key)
                # Keep existing event as-is (it's already a dict)
                unique_events.append(existing_event)

        # Write output - mix of CyclingEvent objects and dicts
        output = []
        for item in unique_events:
            if hasattr(item, "to_dict"):
                output.append(item.to_dict())
            else:
                output.append(item)

        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)

        logger.info(f"Wrote {len(output)} events to {output_path} ({len(scraped)} scraped, {len(existing)} existing)")
    else:
        logger.warning("No events scraped from live sources. Keeping existing events.json unchanged.")

    return scraped


if __name__ == "__main__":
    main()
