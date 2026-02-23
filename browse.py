#!/usr/bin/env python3
import os
import time
import random
import logging
import signal
import sys
import subprocess
import re
import json
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import WebDriverException, TimeoutException

# --- Standardized Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout
)
log = logging.getLogger("AdNauseam")

XPI_PATH = os.getenv("ADNAUSEAM_XPI", "/extensions/adnauseam.xpi")
PROFILE_DIR = Path("/tmp/adnauseam_profile")
HEARTBEAT_FILE = Path("/tmp/heartbeat")
FILTER_POLL_INTERVAL = 5    # seconds between filter readiness checks
FILTER_POLL_TIMEOUT  = 300  # max seconds to wait for filter lists

def load_seed_urls():
    config_path = Path("/app/urls.json")
    try:
        if config_path.exists():
            urls = json.loads(config_path.read_text())
            if urls:
                log.info(f"📋   Loaded {len(urls)} URLs from urls.json")
                return urls
    except Exception as e:
        log.warning(f"Failed to load urls.json: {e}")
    log.info("📋   No urls.json found, using default")
    return ["https://www.yahoo.com"]

SEED_URLS = load_seed_urls()

# --- Helper Functions ---

def update_heartbeat():
    HEARTBEAT_FILE.touch(exist_ok=True)

def get_resource_usage():
    try:
        with open("/sys/fs/cgroup/memory.current", "r") as f:
            mem_bytes = int(f.read().strip())
        profile_size = sum(f.stat().st_size for f in PROFILE_DIR.rglob('*') if f.is_file())
        log.info(f"📊   STATS: RAM: {mem_bytes/(1024**2):.2f}MB | Profile: {profile_size/(1024**2):.2f}MB")
    except:
        pass

def cleanup():
    log.info("🧹   Clearing old browser instances...")
    subprocess.run(["pkill", "-9", "firefox"], capture_output=True)
    subprocess.run(["pkill", "-9", "geckodriver"], capture_output=True)

def find_uuid_from_prefs():
    """
    Read the UUID directly from Firefox's prefs.js.
    Most reliable method — works as soon as the extension is installed.
    """
    prefs_file = PROFILE_DIR / "prefs.js"
    try:
        content = prefs_file.read_text()
        match = re.search(
            r'user_pref\("extensions\.webextensions\.uuids",\s*"(.*?)"\)',
            content
        )
        if match:
            raw = match.group(1).replace('\\"', '"').replace('\\\\', '\\')
            uuid_map = json.loads(raw)
            return uuid_map.get("adnauseam@rednoise.org")
    except Exception as e:
        log.debug(f"Prefs UUID lookup failed: {e}")
    return None

def find_uuid_from_debugger(driver):
    """
    Fallback UUID discovery via about:debugging.
    Less reliable due to async rendering, but kept as a backup.
    """
    try:
        driver.get("about:debugging#/runtime/this-firefox")
        time.sleep(10)
        uuid = driver.execute_script("""
            const labels = document.querySelectorAll('.debug-target-details-label');
            for (let label of labels) {
                if (label.textContent.includes('Internal UUID')) {
                    let parent = label.closest('.debug-target-item');
                    if (parent && parent.textContent.includes('AdNauseam')) {
                        return label.nextElementSibling.textContent.trim();
                    }
                }
            }
            return null;
        """)
        return uuid
    except Exception as e:
        log.debug(f"Debugger UUID search failed: {e}")
        return None

def find_uuid(driver):
    """
    Attempt UUID discovery via prefs.js first, fall back to about:debugging.
    """
    log.info("🔍   Checking prefs.js for UUID...")
    uuid = find_uuid_from_prefs()
    if uuid:
        log.info(f"✅   UUID found in prefs: {uuid}")
        return uuid

    log.info("🔍   Falling back to about:debugging...")
    uuid = find_uuid_from_debugger(driver)
    if uuid:
        log.info(f"✅   UUID found via debugger: {uuid}")
    return uuid

def activate_adnauseam(driver, uuid):
    """
    Navigate to the AdNauseam options page and ensure hidingAds, clickingAds,
    and blockingMalware are all enabled. Checkboxes live inside the #iframe.
    Confirmed IDs from live DOM inspection of options.html:
      #hidingAds, #clickingAds, #blockingMalware
    """
    options_url = f"moz-extension://{uuid}/dashboard.html#options.html"
    try:
        driver.set_page_load_timeout(20)
        driver.get(options_url)
        time.sleep(6)  # wait for iframe + JS to fully render

        result = driver.execute_script("""
            const iframe = document.getElementById('iframe');
            if (!iframe) return {error: 'no iframe found'};
            const doc = iframe.contentDocument || iframe.contentWindow.document;
            if (!doc) return {error: 'cannot access iframe document'};

            const settings = ['hidingAds', 'clickingAds', 'blockingMalware'];
            const results = {};

            for (const name of settings) {
                const el = doc.getElementById(name);
                if (!el) {
                    results[name] = 'not found';
                    continue;
                }
                if (!el.checked) {
                    el.click();
                    results[name] = 'activated';
                } else {
                    results[name] = 'already on';
                }
            }
            return results;
        """)

        if isinstance(result, dict) and 'error' not in result:
            activated = [k for k, v in result.items() if v == 'activated']
            if activated:
                log.info(f"⚙️    Enabled: {', '.join(activated)}")
            else:
                log.info(f"⚙️    All ad detection settings already active")
        else:
            log.warning(f"⚙️    Settings check returned unexpected result: {result}")

    except Exception as e:
        log.warning(f"Settings activation failed: {e}")
    finally:
        driver.set_page_load_timeout(45)

def get_filter_count(driver, uuid):
    """
    Reads #listsOfBlockedHostsPrompt from the 3p-filters.html iframe.
    Returns the network filter count as an integer, or 0 if not yet loaded.
    Expected text format: "167,399 network filters / 42,753 cosmetic filters from:"
    """
    filters_url = f"moz-extension://{uuid}/dashboard.html#3p-filters.html"
    try:
        driver.set_page_load_timeout(20)
        driver.get(filters_url)
        time.sleep(3)

        text = driver.execute_script("""
            const iframe = document.getElementById('iframe');
            if (!iframe) return null;
            const doc = iframe.contentDocument || iframe.contentWindow.document;
            if (!doc) return null;
            const el = doc.getElementById('listsOfBlockedHostsPrompt');
            return el ? el.innerText.trim() : null;
        """)

        if text:
            # Parse "167,399 network filters..." -> 167399
            match = re.search(r'([\d,]+)\s+network filters', text)
            if match:
                return int(match.group(1).replace(',', ''))
    except:
        pass
    return 0

def wait_for_filters(driver, uuid):
    """
    Poll #listsOfBlockedHostsPrompt until network filter count is non-zero,
    confirming AdNauseam's filter lists have fully downloaded and are active.
    """
    log.info("⏳   Waiting for ad detection rules to download...")
    deadline = time.time() + FILTER_POLL_TIMEOUT
    elapsed = 0

    while time.time() < deadline:
        count = get_filter_count(driver, uuid)
        if count > 0:
            log.info(f"✅   Ad detection ready — {count:,} rules loaded ({elapsed}s)")
            return True
        elapsed += FILTER_POLL_INTERVAL
        log.info(f"⏳   Still downloading rules... ({elapsed}s elapsed)")
        update_heartbeat()
        time.sleep(FILTER_POLL_INTERVAL)

    log.warning(f"⚠️   Rule download timed out after {FILTER_POLL_TIMEOUT}s, proceeding anyway")
    return False

def scrape_vault_stats(driver, uuid):
    """
    Visits vault.html and extracts clicked/collected counts from the #stats bar.
    Selectors based on the live AdNauseam v3.28.2 DOM:
      span.clicked  -> "clicked N"
      span.total    -> "N ads collected"
      span#detected -> N currently showing
    """
    vault_url = f"moz-extension://{uuid}/vault.html"
    try:
        driver.set_page_load_timeout(20)
        driver.get(vault_url)
        time.sleep(4)

        stats = driver.execute_script("""
            function getText(selector) {
                const el = document.querySelector(selector);
                return el ? el.innerText.trim() : null;
            }
            return {
                clicked:   getText('span.clicked'),
                collected: getText('span.total'),
                showing:   getText('span#detected')
            };
        """)

        clicked   = stats.get("clicked")   or "clicked ?"
        collected = stats.get("collected") or "? ads collected"
        showing   = stats.get("showing")   or "?"

        return clicked, collected, showing

    except Exception as e:
        log.warning(f"Vault scrape failed: {e}")
        return "clicked ?", "? ads collected", "?"
    finally:
        driver.set_page_load_timeout(45)

def build_driver():
    cleanup()
    if not PROFILE_DIR.exists():
        PROFILE_DIR.mkdir(parents=True)

    log.info("🦊   Booting Firefox...")
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--width=1920")
    opts.add_argument("--height=1080")
    opts.set_preference("extensions.enabledScopes", 15)
    opts.set_preference("extensions.autoDisableScopes", 0)
    opts.set_preference("extensions.startupScanPolicy", 0)
    opts.set_preference("privacy.resistFingerprinting", False)
    opts.set_preference("dom.ipc.processCount", 1)
    opts.add_argument("-profile")
    opts.add_argument(str(PROFILE_DIR))

    try:
        service = Service(executable_path="/usr/local/bin/geckodriver")
        driver = webdriver.Firefox(options=opts, service=service)

        log.info("💉   Injecting AdNauseam...")
        driver.install_addon(XPI_PATH, temporary=True)

        return driver
    except Exception as e:
        log.error(f"❌   Boot failed: {e}")
        return None

# --- Main Logic ---

def main():
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    log.info("♾️    AdInfinitum Started")
    driver = build_driver()
    if not driver:
        sys.exit(1)

    session_count = 0
    current_uuid = None
    activated = False
    filters_ready = False

    while True:
        try:
            url = random.choice(SEED_URLS)
            session_count += 1
            log.info(f"🌐   Session #{session_count}: {url}")
            get_resource_usage()

            driver.set_page_load_timeout(45)
            try:
                driver.get(url)
            except TimeoutException:
                log.warning("⏳   Page load timed out, proceeding anyway...")

            update_heartbeat()

            # Browsing Phase
            for i in range(random.randint(5, 10)):
                scroll = random.randint(400, 1000)
                driver.execute_script(f"window.scrollBy(0, {scroll});")
                time.sleep(random.uniform(4, 7))
                update_heartbeat()

            # UUID Discovery (once per driver lifetime)
            if not current_uuid:
                current_uuid = find_uuid(driver)

            # Activation (once per driver lifetime)
            if current_uuid and not activated:
                activate_adnauseam(driver, current_uuid)
                activated = True

            # Filter readiness check (once per driver lifetime)
            if current_uuid and activated and not filters_ready:
                filters_ready = wait_for_filters(driver, current_uuid)

            # Vault Stats
            if current_uuid:
                clicked, collected, showing = scrape_vault_stats(driver, current_uuid)
                log.info(f"☠️    VAULT — {clicked} | {collected} | {showing} showing")
            else:
                log.warning("⚠️    UUID Discovery failed — vault stats unavailable.")

            # Scheduled Restart
            if session_count % 25 == 0:
                log.info("♻️    Scheduled restart...")
                driver.quit()
                driver = build_driver()
                current_uuid = None
                activated = False
                filters_ready = False

        except Exception as e:
            log.error(f"⚠️    Loop Error: {e}")
            driver = build_driver()
            current_uuid = None
            activated = False
            filters_ready = False

if __name__ == "__main__":
    main()