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

SEED_URLS = [
    "https://www.theverge.com", "https://www.cnn.com", "https://www.msn.com",
    "https://www.reuters.com", "https://www.theguardian.com", "https://www.bloomberg.com",
    "https://www.reddit.com", "https://www.yahoo.com", "https://www.ebay.com"
]

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

        log.info("⏳   Warming up (20s for filter sync)...")
        time.sleep(20)
        return driver
    except Exception as e:
        log.error(f"❌   Boot failed: {e}")
        return None

# --- Main Logic ---

def main():
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    log.info("♾️    Ad Infinitum Started")
    driver = build_driver()
    if not driver:
        sys.exit(1)

    session_count = 0
    current_uuid = None

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
                log.warning("⏳   Load timed out, proceeding anyway...")

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

        except Exception as e:
            log.error(f"⚠️    Loop Error: {e}")
            driver = build_driver()
            current_uuid = None

if __name__ == "__main__":
    main()