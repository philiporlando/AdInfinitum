#!/usr/bin/env python3
"""
AdNauseam noise generator.
Uses Selenium + Firefox to run AdNauseam headlessly in a loop,
poisoning ad profiles via automated fake ad clicks.
"""

import os
import time
import random
import logging
import signal
import sys

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import WebDriverException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SESSION_DURATION = int(os.getenv("SESSION_DURATION", "300"))   # seconds per session
PAUSE_BETWEEN    = int(os.getenv("PAUSE_BETWEEN",    "60"))    # seconds between sessions
ADNAUSEAM_XPI    = os.getenv("ADNAUSEAM_XPI", "/extensions/adnauseam.xpi")

SEED_URLS = [
    "https://www.google.com",
    "https://www.bing.com",
    "https://search.yahoo.com",
    "https://www.reddit.com",
    "https://www.wikipedia.org",
    "https://news.ycombinator.com",
    "https://www.bbc.com",
    "https://www.nytimes.com",
    "https://www.theguardian.com",
    "https://www.cnn.com",
    "https://www.reuters.com",
    "https://www.ebay.com",
    "https://www.amazon.com",
]
# ─────────────────────────────────────────────────────────────────────────────


def build_driver() -> webdriver.Firefox:
    """Spin up a Firefox WebDriver with AdNauseam loaded."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--width=1280")
    options.add_argument("--height=900")

    # Suppress telemetry / first-run noise
    options.set_preference("datareporting.policy.dataSubmissionEnabled", False)
    options.set_preference("datareporting.healthreport.uploadEnabled", False)
    options.set_preference("toolkit.telemetry.enabled", False)
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "")
    options.set_preference("extensions.autoDisableScopes", 0)

    service = Service(log_output=os.devnull)
    driver = webdriver.Firefox(options=options, service=service)

    # Install AdNauseam XPI
    if os.path.exists(ADNAUSEAM_XPI):
        driver.install_addon(ADNAUSEAM_XPI, temporary=True)
        log.info("AdNauseam loaded from %s", ADNAUSEAM_XPI)
    else:
        log.error("AdNauseam XPI not found at %s — exiting.", ADNAUSEAM_XPI)
        driver.quit()
        sys.exit(1)

    # Brief pause to let the extension initialise
    time.sleep(3)
    return driver


def run_session(driver: webdriver.Firefox):
    """Navigate to a random seed URL and let AdNauseam work for SESSION_DURATION seconds."""
    url = random.choice(SEED_URLS)
    log.info("Session start → %s (running for %ds)", url, SESSION_DURATION)
    try:
        driver.get(url)
    except WebDriverException as exc:
        log.warning("Navigation error: %s", exc)

    time.sleep(SESSION_DURATION)
    log.info("Session complete.")


def shutdown_handler(sig, frame):
    log.info("Signal %s received — shutting down.", sig)
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    session_count = 0
    driver = None

    try:
        log.info("Starting Firefox with AdNauseam...")
        driver = build_driver()

        while True:
            session_count += 1
            log.info("─── Session #%d ───", session_count)
            run_session(driver)

            # Occasionally restart the browser to avoid memory bloat
            if session_count % 10 == 0:
                log.info("Restarting browser to clear memory...")
                driver.quit()
                driver = build_driver()

            log.info("Pausing %ds before next session...", PAUSE_BETWEEN)
            time.sleep(PAUSE_BETWEEN)

    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    main()