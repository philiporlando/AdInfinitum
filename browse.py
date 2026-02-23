#!/usr/bin/env python3
"""
AdNauseam noise generator.
Uses Selenium + Firefox to run AdNauseam headlessly in a loop,
poisoning ad profiles via automated fake ad clicks.
Randomized timing and behavior to mimic real user patterns.
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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Silence noisy selenium/urllib3 internals
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────
ADNAUSEAM_XPI  = os.getenv("ADNAUSEAM_XPI", "/extensions/adnauseam.xpi")
SESSION_MEAN   = int(os.getenv("SESSION_MEAN",   "240"))
SESSION_STDDEV = int(os.getenv("SESSION_STDDEV", "90"))
SESSION_MIN    = int(os.getenv("SESSION_MIN",    "60"))
SESSION_MAX    = int(os.getenv("SESSION_MAX",    "600"))
PAUSE_MIN      = int(os.getenv("PAUSE_MIN",      "15"))
PAUSE_MAX      = int(os.getenv("PAUSE_MAX",      "120"))
RESTART_EVERY  = int(os.getenv("RESTART_EVERY",  "10"))

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


def session_duration() -> float:
    duration = max(SESSION_MIN, min(SESSION_MAX,
        random.gauss(SESSION_MEAN, SESSION_STDDEV)
    ))
    log.debug("Session duration sampled: %.0fs (mean=%d, stddev=%d)", duration, SESSION_MEAN, SESSION_STDDEV)
    return duration


def pause_duration() -> float:
    beta = random.betavariate(2, 5)
    pause = PAUSE_MIN + beta * (PAUSE_MAX - PAUSE_MIN)
    log.debug("Pause duration sampled: %.0fs", pause)
    return pause


def human_scroll(driver: webdriver.Firefox):
    """Scroll the page in a human-like pattern."""
    try:
        page_height = driver.execute_script("return document.body.scrollHeight")
        viewport    = driver.execute_script("return window.innerHeight")
        if page_height <= viewport:
            log.debug("Page fits in viewport, skipping scroll")
            return

        target_pct = random.uniform(0.4, 0.9)
        log.debug("Scrolling to %.0f%% of page (height=%dpx)", target_pct * 100, page_height)

        current = 0
        scroll_target = page_height * target_pct
        while current < scroll_target:
            scroll_by = random.randint(100, 400)
            driver.execute_script(f"window.scrollBy(0, {scroll_by})")
            current += scroll_by

            sleep = random.uniform(0.3, 2.5)
            log.debug("Scrolled %dpx, sleeping %.1fs", scroll_by, sleep)
            time.sleep(sleep)

            if random.random() < 0.15:
                scroll_back = random.randint(50, 200)
                driver.execute_script(f"window.scrollBy(0, -{scroll_back})")
                log.debug("Scrolled back %dpx (reading pause)", scroll_back)
                time.sleep(random.uniform(0.5, 1.5))

    except WebDriverException as exc:
        log.debug("Scroll interrupted: %s", exc)


def get_adnauseam_stats(driver: webdriver.Firefox) -> None:
    """Try to read AdNauseam's blocked/clicked ad counts from localStorage."""
    try:
        stats = driver.execute_script("""
            try {
                const data = localStorage.getItem('adnauseam-data');
                if (!data) return null;
                const parsed = JSON.parse(data);
                return {
                    blocked: parsed.blocked || 0,
                    clicked: parsed.clicked || 0,
                    visits:  parsed.visits  || 0,
                };
            } catch(e) { return null; }
        """)
        if stats:
            log.info("AdNauseam stats — blocked: %s | clicked: %s | visits: %s",
                     stats.get("blocked", "?"),
                     stats.get("clicked", "?"),
                     stats.get("visits",  "?"))
        else:
            log.debug("AdNauseam stats not available on this page")
    except WebDriverException:
        log.debug("Could not read AdNauseam stats")


def build_driver() -> webdriver.Firefox:
    """Spin up a Firefox WebDriver with AdNauseam loaded."""
    log.info("Initialising Firefox...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--width=1280")
    options.add_argument("--height=900")

    options.set_preference("datareporting.policy.dataSubmissionEnabled", False)
    options.set_preference("datareporting.healthreport.uploadEnabled", False)
    options.set_preference("toolkit.telemetry.enabled", False)
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "")
    options.set_preference("extensions.autoDisableScopes", 0)
    options.add_argument("--no-sandbox")
    options.set_preference("security.sandbox.content.level", 0)

    service = Service(log_output=os.devnull)
    driver = webdriver.Firefox(options=options, service=service)
    log.info("Firefox started (window: 1280x900)")

    if os.path.exists(ADNAUSEAM_XPI):
        driver.install_addon(ADNAUSEAM_XPI, temporary=True)
        log.info("AdNauseam loaded from %s", ADNAUSEAM_XPI)
    else:
        log.error("AdNauseam XPI not found at %s — exiting.", ADNAUSEAM_XPI)
        driver.quit()
        sys.exit(1)

    init_sleep = random.uniform(2, 5)
    log.debug("Waiting %.1fs for extension to initialise...", init_sleep)
    time.sleep(init_sleep)
    return driver


def run_session(driver: webdriver.Firefox, session_num: int):
    """Navigate to a random seed URL and behave like a human for a randomized duration."""
    url      = random.choice(SEED_URLS)
    duration = session_duration()
    log.info("Session #%d → %s", session_num, url)
    log.info("Planned duration: %.0fs", duration)

    try:
        driver.get(url)
        log.debug("Page loaded: %s", driver.title or "(no title)")
    except WebDriverException as exc:
        log.warning("Navigation error: %s", exc)
        return

    settle = random.uniform(1.5, 4.0)
    log.debug("Page settle delay: %.1fs", settle)
    time.sleep(settle)

    deadline = time.time() + duration
    action_count = 0

    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break

        action = random.choices(
            ["scroll", "idle", "navigate_back"],
            weights=[0.5, 0.35, 0.15],
        )[0]
        action_count += 1
        log.debug("Action #%d: %s (%.0fs remaining)", action_count, action, remaining)

        if action == "scroll":
            human_scroll(driver)

        elif action == "idle":
            idle = min(random.uniform(5, 30), remaining)
            log.debug("Idling for %.0fs", idle)
            time.sleep(idle)

        elif action == "navigate_back" and driver.current_url != url:
            try:
                log.debug("Navigating back")
                driver.back()
                time.sleep(random.uniform(1, 3))
            except WebDriverException:
                pass

        time.sleep(random.uniform(0.5, 2.0))

    get_adnauseam_stats(driver)
    log.info("Session #%d complete — %d actions taken", session_num, action_count)


def shutdown_handler(sig, frame):
    log.info("Signal %s received — shutting down.", sig)
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    log.info("Starting AdNauseam noise generator")
    log.info("Config — session: %d±%ds [%d-%d] | pause: %d-%ds | restart every %d sessions",
             SESSION_MEAN, SESSION_STDDEV, SESSION_MIN, SESSION_MAX,
             PAUSE_MIN, PAUSE_MAX, RESTART_EVERY)

    session_count = 0
    driver = None

    try:
        driver = build_driver()

        while True:
            session_count += 1
            log.info("─── Session #%d ───", session_count)
            run_session(driver, session_count)

            if session_count % RESTART_EVERY == 0:
                log.info("Restarting browser after %d sessions to clear memory...", session_count)
                driver.quit()
                driver = build_driver()

            pause = pause_duration()
            log.info("Pausing %.0fs before next session...", pause)
            time.sleep(pause)

    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
    finally:
        if driver:
            driver.quit()
            log.info("Firefox closed.")


if __name__ == "__main__":
    main()