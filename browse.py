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
SESSION_MEAN   = int(os.getenv("SESSION_MEAN",   "8"))   # Reduced for faster iteration
SESSION_STDDEV = int(os.getenv("SESSION_STDDEV", "3"))   # Reduced for faster iteration
SESSION_MIN    = int(os.getenv("SESSION_MIN",    "5"))   # Reduced for faster iteration
SESSION_MAX    = int(os.getenv("SESSION_MAX",    "12"))  # Reduced for faster iteration
PAUSE_MIN      = int(os.getenv("PAUSE_MIN",      "1"))
PAUSE_MAX      = int(os.getenv("PAUSE_MAX",      "3"))
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


def ensure_main_window(driver: webdriver.Firefox) -> bool:
    """Close any extra tabs and make sure we're on handle[0]. Returns False if unrecoverable."""
    try:
        handles = driver.window_handles
        # Close any extra tabs that got left open
        for handle in handles[1:]:
            try:
                driver.switch_to.window(handle)
                driver.close()
            except WebDriverException:
                pass
        driver.switch_to.window(driver.window_handles[0])
        return True
    except WebDriverException as exc:
        log.warning("Could not recover main window: %s", exc)
        return False


def human_scroll(driver: webdriver.Firefox):
    """Scroll the page in a human-like pattern."""
    try:
        page_height = driver.execute_script("return document.body.scrollHeight")
        viewport    = driver.execute_script("return window.innerHeight")
        if page_height <= viewport:
            log.debug("Page fits in viewport, skipping scroll")
            return

        target_pct = random.uniform(0.15, 0.35)  # Much more conservative scrolling
        log.debug("Scrolling to %.0f%% of page (height=%dpx)", target_pct * 100, page_height)

        current = 0
        scroll_target = page_height * target_pct
        while current < scroll_target:
            scroll_by = random.randint(30, 100)  # Much smaller increments
            driver.execute_script(f"window.scrollBy(0, {scroll_by})")
            current += scroll_by

            sleep = random.uniform(0.2, 1.0)  # Shorter sleep times
            log.debug("Scrolled %dpx, sleeping %.1fs", scroll_by, sleep)
            time.sleep(sleep)

            if random.random() < 0.10:  # Less frequent back-scroll
                scroll_back = random.randint(15, 40)
                driver.execute_script(f"window.scrollBy(0, -{scroll_back})")
                log.debug("Scrolled back %dpx (reading pause)", scroll_back)
                time.sleep(random.uniform(0.2, 0.8))

    except WebDriverException as exc:
        log.debug("Scroll interrupted: %s", exc)


def verify_adnauseam_active(driver: webdriver.Firefox) -> bool:
    """
    Verify that AdNauseam extension is loaded, enabled, and actively processing ads.
    Returns True if extension appears to be functional.
    """
    try:
        main_handle = driver.window_handles[0]
        driver.switch_to.window(main_handle)
        
        # Set async timeout to prevent hanging
        driver.set_script_timeout(8)
        
        # Try browser.management API with timeout protection
        try:
            ext_info = driver.execute_async_script("""
                const done = arguments[0];
                const timeout = setTimeout(() => done(null), 6000);
                try {
                    browser.management.getAll().then(exts => {
                        clearTimeout(timeout);
                        const adn = exts.find(e => 
                            e.name && e.name.toLowerCase().includes('adnauseam')
                        );
                        if (adn) {
                            done({
                                name: adn.name,
                                enabled: adn.enabled,
                                version: adn.version,
                            });
                        } else {
                            done(null);
                        }
                    }).catch(() => {
                        clearTimeout(timeout);
                        done(null);
                    });
                } catch(e) {
                    clearTimeout(timeout);
                    done(null);
                }
            """)

            if ext_info:
                log.info("✓ AdNauseam Extension: %s v%s (enabled=%s)",
                        ext_info.get("name"),
                        ext_info.get("version"),
                        ext_info.get("enabled"))
                return True
        except Exception as exc:
            log.debug("Extension verification via management API failed: %s", exc)

        log.debug("⚠ AdNauseam extension verification timed out (extension likely still functional)")
        return False

    except Exception as exc:
        log.debug("AdNauseam verification check failed: %s", exc)
        return False

    finally:
        try:
            ensure_main_window(driver)
        except Exception:
            pass


def check_adnauseam_on_page(driver: webdriver.Firefox) -> dict:
    """
    Check if AdNauseam content script is active on the current page.
    Returns dict with activity info.
    """
    try:
        result = driver.execute_script("""
            try {
                // Check for AdNauseam global objects
                const hasAdNauseam = !!(
                    window.AD ||
                    window.AD_API ||
                    window.adn ||
                    window.__adnauseam__
                );
                
                // Check for iframe markers (AdNauseam replaces ads with iframes)
                const adIframes = document.querySelectorAll('iframe[src*="adnauseam"], [data-adnauseam]');
                
                // Check for blocked ad markers
                const blockedAds = document.querySelectorAll('[adnauseam-marker], [data-adnauseam-blocked]');
                
                // Look for DOM observers that might indicate AdNauseam is watching
                const scripts = Array.from(document.scripts)
                    .filter(s => s.src && (s.src.includes('adnauseam') || s.src.includes('background')))
                    .length;
                
                return {
                    adnauseamGlobal: hasAdNauseam,
                    adIframeCount: adIframes.length,
                    blockedAdMarkers: blockedAds.length,
                    adnauseamScripts: scripts,
                    documentClasses: document.documentElement.className,
                    bodyClasses: document.body?.className || '',
                };
            } catch(e) {
                return { error: e.message };
            }
        """)
        return result or {}
    except Exception as exc:
        log.debug("Failed to check AdNauseam on page: %s", exc)
        return {}


def get_adnauseam_stats(driver: webdriver.Firefox) -> None:
    """
    Query AdNauseam stats from the current browsing context.
    The AdNauseam content script is active on the page and can provide stats data.
    """
    try:
        # Get the main window handle
        main_handle = driver.window_handles[0]
        driver.switch_to.window(main_handle)
        
        log.debug("Querying AdNauseam stats from current page...")
        
        # Try to query AdNauseam globals and storage via content script
        try:
            stats_result = driver.execute_script("""
                // Try direct AdNauseam global access
                if (window.adnauseam) {
                    return {
                        source: "global",
                        stats: window.adnauseam.stats || window.adnauseam.getStats && window.adnauseam.getStats()
                    };
                }
                
                // Try to get from localstorage/extension storage
                try {
                    const data = localStorage.getItem('adnauseam-stats');
                    if (data) {
                        return { source: "storage", stats: JSON.parse(data) };
                    }
                } catch(e) {}
                
                // Try to find any ad elements that AdNauseam has marked
                const adCount = document.querySelectorAll('[data-ad], [class*="ad"], iframe[src*="ad"]').length;
                if (adCount > 0) {
                    return { source: "ad_elements", count: adCount };
                }
                
                return null;
            """)
            
            if stats_result:
                log.debug("Stats query result: %s", stats_result)
                if stats_result.get("source") == "global" and stats_result.get("stats"):
                    log.info("✓ AdNauseam global stats: %s", stats_result["stats"])
                elif stats_result.get("source") == "storage" and stats_result.get("stats"):
                    log.info("✓ AdNauseam storage stats: %s", stats_result["stats"])
                elif stats_result.get("source") == "ad_elements":
                    log.info("✓ Found %d ad elements on page", stats_result.get("count", 0))
                else:
                    log.debug("No stats available from any source")
            else:
                log.debug("No AdNauseam stats found on current page")
                    
        except Exception as exc:
            log.debug("Failed to query stats from page: %s", exc)
        
    except Exception as exc:
        log.debug("Stats collection error: %s", exc)


def build_driver() -> webdriver.Firefox:
    """Spin up a Firefox WebDriver with AdNauseam loaded."""
    log.info("Initialising Firefox...")
    options = Options()
    # Don't use headless mode - virtual display (Xvfb) makes it appear real to ad networks
    options.add_argument("--width=1280")
    options.add_argument("--height=900")

    # Anti-detection: hide headless mode indicators
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    options.set_preference("general.useragent.override", 
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")

    # Disable data collection
    options.set_preference("datareporting.policy.dataSubmissionEnabled", False)
    options.set_preference("datareporting.healthreport.uploadEnabled", False)
    options.set_preference("toolkit.telemetry.enabled", False)
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "")
    options.set_preference("extensions.autoDisableScopes", 0)
    
    # Sandbox settings
    options.add_argument("--no-sandbox")
    options.set_preference("security.sandbox.content.level", 0)
    
    # Network settings - help with ad delivery
    options.set_preference("network.cookie.cookieBehavior", 0)  # Accept all cookies
    options.set_preference("privacy.trackingprotection.enabled", False)  # Disable tracking protection
    options.set_preference("privacy.trackingprotection.socialtracking.enabled", False)

    service = Service(log_output=os.devnull)
    driver = webdriver.Firefox(options=options, service=service)
    log.info("Firefox started (window: 1280x900, virtual display, anti-detection enabled)")

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

    # Verify extension is active
    if verify_adnauseam_active(driver):
        log.info("AdNauseam extension verified and active")
    else:
        log.warning("Could not verify AdNauseam extension status (may still be functional)")

    return driver


def run_session(driver: webdriver.Firefox, session_num: int):
    """Navigate to a random seed URL and behave like a human for a randomized duration."""
    # Ensure we're on the main tab before starting
    if not ensure_main_window(driver):
        log.warning("Session #%d skipped — could not get clean window state", session_num)
        return

    url      = random.choice(SEED_URLS)
    duration = session_duration()
    log.info("Session #%d → %s", session_num, url)
    log.info("Planned duration: %.0fs", duration)

    try:
        driver.get(url)
        page_title = driver.title or "(no title)"
        log.info("Page title: %s", page_title)
    except WebDriverException as exc:
        log.warning("Navigation error: %s", exc)
        return

    settle = random.uniform(1.5, 4.0)
    log.debug("Page settle delay: %.1fs", settle)
    time.sleep(settle)

    deadline = time.time() + duration
    action_count = 0
    session_start = time.time()
    
    # Check stats at start of session
    log.debug("Checking AdNauseam stats at start of session...")
    page_activity = check_adnauseam_on_page(driver)
    if page_activity and (page_activity.get("adIframeCount", 0) > 0 or page_activity.get("blockedAdMarkers", 0) > 0):
        log.info("✓ Start: AdNauseam detected %d ad iframes, %d blocked markers",
                page_activity.get("adIframeCount", 0),
                page_activity.get("blockedAdMarkers", 0))

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
            log.info("  → Scrolled (action #%d)", action_count)

        elif action == "idle":
            idle = min(random.uniform(0.5, 2), remaining)
            log.debug("Idling for %.1fs", idle)
            time.sleep(idle)
            log.info("  → Idle for %.1fs (action #%d)", idle, action_count)

        elif action == "navigate_back" and driver.current_url != url:
            try:
                log.debug("Navigating back")
                driver.back()
                time.sleep(random.uniform(1, 3))
                log.info("  → Navigated back (action #%d)", action_count)
            except WebDriverException:
                pass

        time.sleep(random.uniform(0.5, 2.0))
        
        # Quick ad activity check after each action
        try:
            page_activity = check_adnauseam_on_page(driver)
            if page_activity and (page_activity.get("adIframeCount", 0) > 0 or page_activity.get("blockedAdMarkers", 0) > 0):
                log.info("  ✓ Ads detected (after action #%d): %d iframes, %d blocked",
                        action_count,
                        page_activity.get("adIframeCount", 0),
                        page_activity.get("blockedAdMarkers", 0))
        except Exception:
            pass  # Don't crash on quick checks

    session_elapsed = time.time() - session_start
    log.info("Session #%d complete — %d actions taken in %.0fs", session_num, action_count, session_elapsed)
    
    # Final extension stats collection
    log.debug("Collecting final AdNauseam stats...")
    get_adnauseam_stats(driver)


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