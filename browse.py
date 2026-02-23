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

        target_pct = random.uniform(0.3, 0.7)  # Reduced from 0.4-0.9 to avoid excessive scrolling
        log.debug("Scrolling to %.0f%% of page (height=%dpx)", target_pct * 100, page_height)

        current = 0
        scroll_target = page_height * target_pct
        while current < scroll_target:
            scroll_by = random.randint(50, 250)  # Reduced from 100-400 to avoid WebDriver issues
            driver.execute_script(f"window.scrollBy(0, {scroll_by})")
            current += scroll_by

            sleep = random.uniform(0.3, 2.5)
            log.debug("Scrolled %dpx, sleeping %.1fs", scroll_by, sleep)
            time.sleep(sleep)

            if random.random() < 0.15:
                scroll_back = random.randint(25, 100)  # Reduced from 50-200
                driver.execute_script(f"window.scrollBy(0, -{scroll_back})")
                log.debug("Scrolled back %dpx (reading pause)", scroll_back)
                time.sleep(random.uniform(0.5, 1.5))

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
        driver.execute_script("window.open('about:blank', '_blank')")
        verify_handle = driver.window_handles[-1]
        driver.switch_to.window(verify_handle)

        driver.get("about:blank")
        driver.set_script_timeout(5)

        # Try to detect if AdNauseam content scripts are running
        result = driver.execute_script("""
            try {
                // Check if AdNauseam globals exist on pages
                const hasAdNauseam = !!(
                    window.AD_API ||
                    window.adn ||
                    window.__adnauseam__ ||
                    document.__adnauseam__
                );
                
                // Check for extension markers
                const hasExtension = !!document.querySelector('[adnauseam-marker]');
                
                return {
                    extensionActive: hasAdNauseam || hasExtension,
                    timestamp: new Date().toISOString(),
                };
            } catch(e) {
                return { extensionActive: false, error: e.message };
            }
        """)

        is_active = result and result.get("extensionActive", False)
        if is_active:
            log.info("✓ AdNauseam extension verified as active")
        else:
            log.debug("AdNauseam extension globals not detected on blank page (expected)")

        # Now try to check browser extension status via management API
        try:
            ext_info = driver.execute_async_script("""
                const done = arguments[0];
                try {
                    browser.management.getAll().then(exts => {
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
                    }).catch(() => done(null));
                } catch(e) { done(null); }
            """)

            if ext_info:
                log.info("✓ AdNauseam Extension: %s v%s (enabled=%s)",
                        ext_info.get("name"),
                        ext_info.get("version"),
                        ext_info.get("enabled"))
                return True
            else:
                log.debug("Could not query extension via browser.management")
        except Exception as exc:
            log.debug("Extension verification via management API failed: %s", exc)

        return is_active

    except Exception as exc:
        log.debug("AdNauseam verification check failed: %s", exc)
        return False

    finally:
        ensure_main_window(driver)


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
    Retrieve AdNauseam stats from storage or options page.
    Uses multiple fallback methods to retrieve stats.
    Handles invalid sessions gracefully.
    """
    try:
        # Check if session is still valid before proceeding
        try:
            main_handle = driver.window_handles[0]
        except Exception as exc:
            log.debug("WebDriver session lost, cannot collect stats: %s", exc)
            return

        driver.switch_to.window(main_handle)
        driver.execute_script("window.open('about:blank', '_blank')")
        stats_handle = driver.window_handles[-1]
        driver.switch_to.window(stats_handle)

        driver.get("about:blank")
        driver.set_script_timeout(5)

        # Try to access AdNauseam data via storage API
        storage_data = None
        try:
            storage_data = driver.execute_async_script("""
                const done = arguments[0];
                try {
                    // Try to get data from browser storage
                    browser.storage.local.get(null).then(data => {
                        const statKeys = Object.keys(data).filter(k => 
                            k.includes('count') || k.includes('stat') || k.includes('ad')
                        );
                        done({
                            storage: data,
                            statKeys: statKeys
                        });
                    }).catch(() => done(null));
                } catch(e) { done(null); }
            """)
            if storage_data and storage_data.get("statKeys"):
                log.debug("Found stat keys in storage: %s", storage_data["statKeys"][:5])
        except Exception as exc:
            log.debug("Storage API check failed: %s", exc)

        # Try to get extension info and options URL
        options_url = None
        try:
            ext_info = driver.execute_async_script("""
                const done = arguments[0];
                try {
                    browser.management.getAll().then(exts => {
                        const adn = exts.find(e =>
                            e.name && e.name.toLowerCase().includes('adnauseam')
                        );
                        done(adn || null);
                    }).catch(() => done(null));
                } catch(e) { done(null); }
            """)
            if ext_info:
                options_url = ext_info.get("optionsUrl")
                log.debug("AdNauseam extension found: %s v%s (enabled=%s)",
                         ext_info.get("name"),
                         ext_info.get("version"),
                         ext_info.get("enabled"))
        except Exception as exc:
            log.debug("Extension info query failed: %s", exc)

        # If we got options URL, navigate to it
        if options_url:
            try:
                log.debug("Attempting to load options from: %s", options_url)
                driver.get(options_url)
                time.sleep(1)
                
                # Try to extract stats from options page
                raw = driver.execute_script("return document.body ? document.body.innerText : ''")
                log.debug("Options page content (first 300 chars): %s", raw[:300])

                stats = driver.execute_script("""
                    const q = sel => {
                        const el = document.querySelector(sel);
                        return el ? el.textContent.trim() : null;
                    };
                    
                    return {
                        blocked: q('#blocked-count') || q('.blocked-count') || q('[data-blocked]'),
                        clicked: q('#clicked-count') || q('.clicked-count') || q('[data-clicked]'),
                        visited: q('#visited-count') || q('.visited-count') || q('[data-visited]'),
                    };
                """)

                if stats and (stats.get("blocked") or stats.get("clicked")):
                    stats_parts = []
                    if stats.get("blocked"):
                        stats_parts.append(f"blocked={stats['blocked']}")
                    if stats.get("clicked"):
                        stats_parts.append(f"clicked={stats['clicked']}")
                    if stats.get("visited"):
                        stats_parts.append(f"visited={stats['visited']}")
                    
                    if stats_parts:
                        log.info("AdNauseam — %s", " | ".join(stats_parts))
                else:
                    log.debug("Options page loaded but stats selectors found no data")
            except Exception as exc:
                log.debug("Failed to extract stats from options page: %s", exc)

    except WebDriverException as exc:
        log.debug("AdNauseam stats collection failed: %s", exc)
    except Exception as exc:
        log.debug("Unexpected error during stats collection: %s", exc)

    finally:
        try:
            ensure_main_window(driver)
        except Exception as exc:
            log.debug("Could not restore main window: %s", exc)


def build_driver() -> webdriver.Firefox:
    """Spin up a Firefox WebDriver with AdNauseam loaded."""
    log.info("Initialising Firefox...")
    options = Options()
    options.add_argument("--headless")
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
    log.info("Firefox started (window: 1280x900, anti-detection enabled)")

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
            idle = min(random.uniform(5, 30), remaining)
            log.debug("Idling for %.0fs", idle)
            time.sleep(idle)
            log.info("  → Idle for %.0fs (action #%d)", idle, action_count)

        elif action == "navigate_back" and driver.current_url != url:
            try:
                log.debug("Navigating back")
                driver.back()
                time.sleep(random.uniform(1, 3))
                log.info("  → Navigated back (action #%d)", action_count)
            except WebDriverException:
                pass

        time.sleep(random.uniform(0.5, 2.0))

    session_elapsed = time.time() - session_start
    log.info("Session #%d complete — %d actions taken in %.0fs", session_num, action_count, session_elapsed)
    
    # Check if AdNauseam collected ads on this page
    log.debug("Checking AdNauseam activity on page...")
    page_activity = check_adnauseam_on_page(driver)
    log.debug("Page activity result: %s", page_activity)
    
    if page_activity:
        if page_activity.get("adIframeCount", 0) > 0:
            log.info("✓ AdNauseam detected %d ad iframes on page", page_activity["adIframeCount"])
        if page_activity.get("blockedAdMarkers", 0) > 0:
            log.info("✓ AdNauseam found %d blocked ad markers", page_activity["blockedAdMarkers"])
        if page_activity.get("adnauseamGlobal"):
            log.debug("  AdNauseam global object present")
        if page_activity.get("adnauseamScripts", 0) > 0:
            log.debug("  %d AdNauseam script(s) detected", page_activity["adnauseamScripts"])
    else:
        log.debug("No activity data returned from page check")
    
    # Always collect complete stats from extension storage
    log.debug("Collecting AdNauseam stats...")
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