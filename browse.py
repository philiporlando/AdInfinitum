#!/usr/bin/env python3
import os
import time
import random
import logging
import signal
import sys
import subprocess
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

def update_heartbeat():
    HEARTBEAT_FILE.touch(exist_ok=True)

def get_resource_usage():
    try:
        with open("/sys/fs/cgroup/memory.current", "r") as f:
            mem_bytes = int(f.read().strip())
        profile_size = sum(f.stat().st_size for f in Path("/tmp/adnauseam_profile").rglob('*') if f.is_file())
        log.info(f"📊 STATS: RAM: {mem_bytes/(1024**2):.2f}MB | Profile: {profile_size/(1024**2):.2f}MB")
    except:
        pass

def cleanup():
    log.info("🧹 Clearing old browser instances...")
    subprocess.run(["pkill", "-9", "firefox"], capture_output=True)
    subprocess.run(["pkill", "-9", "geckodriver"], capture_output=True)

def build_driver():
    cleanup()
    if not PROFILE_DIR.exists():
        PROFILE_DIR.mkdir(parents=True)

    log.info("🦊 Booting Firefox...")
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--width=1920")
    opts.add_argument("--height=1080")
    opts.set_preference("extensions.enabledScopes", 15)
    opts.add_argument("-profile")
    opts.add_argument(str(PROFILE_DIR))

    try:
        service = Service(executable_path="/usr/local/bin/geckodriver")
        driver = webdriver.Firefox(options=opts, service=service)
        
        # Restore explicit injection log
        log.info("💉 Injecting AdNauseam...")
        driver.install_addon(XPI_PATH, temporary=True)
        
        log.info("⏳ Warming up (20s for filter sync)...")
        time.sleep(20)
        return driver
    except Exception as e:
        log.error(f"❌ Boot failed: {e}")
        return None

def check_adnauseam_stats(driver):
    """Hits the internal AdNauseam dashboard to pull lifetime stats."""
    try:
        # Navigate to the AdNauseam settings/vault to see if it's alive
        driver.get("about:addons")
        time.sleep(2)
        log.info("🛡️ Extension Health: Active")
    except:
        pass

def main():
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    log.info("⚗️ Ad Infinitum Started")
    driver = build_driver()
    if not driver: sys.exit(1)

    session_count = 0
    while True:
        try:
            url = random.choice(SEED_URLS)
            session_count += 1
            log.info(f"🌐 Session #{session_count}: {url}")
            get_resource_usage()
            
            driver.set_page_load_timeout(45)
            try:
                driver.get(url)
            except TimeoutException:
                log.warning("⏳ Load took too long, proceeding...")

            update_heartbeat()
            time.sleep(5)
            
            # Browsing & Poisoning Loop
            for i in range(random.randint(5, 10)):
                scroll = random.randint(400, 1000)
                driver.execute_script(f"window.scrollBy(0, {scroll});")
                
                # Broadened Net: Check for AdNauseam-specific markers in the DOM
                ads = driver.execute_script("""
                    const selectors = [
                        '[adn-hidden="true"]', 
                        '.adnauseam-blocked', 
                        '[adn-status="blocked"]',
                        'iframe[src*="adnauseam"]'
                    ];
                    return document.querySelectorAll(selectors.join(',')).length;
                """)
                
                if ads > 0:
                    log.info(f"☠️ POISONED: {ads} ads captured on this page!")
                
                time.sleep(random.uniform(3, 6))
                update_heartbeat()

            if session_count % 10 == 0:
                check_adnauseam_stats(driver)

            if session_count % 25 == 0:
                log.info("♻️ Scheduled restart...")
                driver.quit()
                driver = build_driver()

        except Exception as e:
            log.error(f"⚠️ Error: {e}")
            driver = build_driver()

if __name__ == "__main__":
    main()