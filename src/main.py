#!/usr/bin/env python3
"""
AdInfinitum — Automated AdNauseam browsing agent.

Boots a headless Firefox instance with the AdNauseam extension injected,
visits ad-heavy seed URLs in a loop, and reports vault statistics (ads
clicked, collected, and showing) via structured logs.
"""

import json
import logging
import os
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import TimeoutException

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("AdInfinitum")

# TypeVar used to make execute_script generic over its return type.
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """
    Validated configuration for AdInfinitum.

    All fields have sensible defaults and can be overridden by subclassing
    or passing kwargs. The `xpi_path` field respects the ADNAUSEAM_XPI
    environment variable.
    """

    xpi_path: Path = Path(os.getenv("ADNAUSEAM_XPI", "/extensions/adnauseam.xpi"))
    """Path to the AdNauseam .xpi extension file."""

    profile_dir: Path = Path("/tmp/adnauseam_profile")
    """Firefox profile directory used across sessions."""

    heartbeat_file: Path = Path("/tmp/heartbeat")
    """Touched periodically so the Docker healthcheck knows the process is alive."""

    urls_path: Path = Path("/app/urls.json")
    """Path to the JSON file containing seed URLs to browse."""

    geckodriver_path: Path = Path("/usr/local/bin/geckodriver")
    """Path to the geckodriver binary."""

    filter_poll_interval: int = Field(default=5, ge=1)
    """Seconds between each poll of the AdNauseam filter list readiness check."""

    filter_poll_timeout: int = Field(default=300, ge=10)
    """Maximum seconds to wait for AdNauseam filter lists to download before proceeding."""

    scroll_min: int = Field(default=400, ge=0)
    """Minimum scroll distance in pixels per scroll step."""

    scroll_max: int = Field(default=1000, ge=0)
    """Maximum scroll distance in pixels per scroll step."""

    scroll_steps_min: int = Field(default=5, ge=1)
    """Minimum number of scroll steps per page visit."""

    scroll_steps_max: int = Field(default=10, ge=1)
    """Maximum number of scroll steps per page visit."""

    scroll_pause_min: float = Field(default=4.0, ge=0)
    """Minimum pause in seconds between scroll steps."""

    scroll_pause_max: float = Field(default=7.0, ge=0)
    """Maximum pause in seconds between scroll steps."""

    session_restart_interval: int = Field(default=25, ge=1)
    """Number of sessions between scheduled browser restarts."""

    page_load_timeout: int = Field(default=45, ge=5)
    """Default page load timeout in seconds."""

    default_urls: list[str] = ["https://www.yahoo.com"]
    """Fallback seed URL list used when urls.json is absent or unreadable."""


# ---------------------------------------------------------------------------
# Browser Manager
# ---------------------------------------------------------------------------


class BrowserManager:
    """
    Manages the Firefox WebDriver lifecycle.

    Responsible for booting Firefox with the correct profile and options,
    injecting the AdNauseam extension, and providing a clean interface for
    navigation and script execution. Teardown kills any orphaned Firefox
    and geckodriver processes before starting fresh.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialise the BrowserManager.

        Args:
            settings: Validated AdInfinitum settings instance.
        """
        self.settings = settings
        self.driver: webdriver.Firefox | None = None

    def _kill_orphans(self) -> None:
        """Kill any lingering Firefox and geckodriver processes from previous runs."""
        log.info("Clearing old browser instances...")
        subprocess.run(["pkill", "-9", "firefox"], capture_output=True)
        subprocess.run(["pkill", "-9", "geckodriver"], capture_output=True)

    def _build_options(self) -> Options:
        """
        Construct Firefox options for headless operation with the AdNauseam profile.

        Returns:
            A configured Firefox Options instance.
        """
        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--width=1920")
        opts.add_argument("--height=1080")
        opts.add_argument("-profile")
        opts.add_argument(str(self.settings.profile_dir))
        opts.set_preference("extensions.enabledScopes", 15)
        opts.set_preference("extensions.autoDisableScopes", 0)
        opts.set_preference("extensions.startupScanPolicy", 0)
        opts.set_preference("privacy.resistFingerprinting", False)
        opts.set_preference("dom.ipc.processCount", 1)
        return opts

    def start(self) -> bool:
        """
        Boot Firefox, inject AdNauseam, and prepare the driver for use.

        Returns:
            True if the browser started successfully, False otherwise.
        """
        self._kill_orphans()
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)

        log.info("Booting Firefox...")
        try:
            service = Service(executable_path=str(self.settings.geckodriver_path))
            self.driver = webdriver.Firefox(
                options=self._build_options(),
                service=service,
            )
            log.info("Injecting AdNauseam...")
            self.driver.install_addon(str(self.settings.xpi_path), temporary=True)
            return True
        except Exception as e:
            log.error(f"Boot failed: {e}")
            self.driver = None
            return False

    def stop(self) -> None:
        """Quit the WebDriver and clear the driver reference."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def restart(self) -> bool:
        """
        Stop the current browser instance and start a fresh one.

        Returns:
            True if the restart succeeded, False otherwise.
        """
        log.info("Restarting browser...")
        self.stop()
        return self.start()

    def get(self, url: str) -> bool:
        """
        Navigate to a URL, respecting the configured page load timeout.

        Args:
            url: The URL to navigate to.

        Returns:
            True if the page loaded successfully, False on timeout or missing driver.
        """
        if not self.driver:
            return False
        self.driver.set_page_load_timeout(self.settings.page_load_timeout)
        try:
            self.driver.get(url)
            return True
        except TimeoutException:
            log.warning("Page load timed out, proceeding anyway...")
            return False

    def execute_script(
        self, return_type: type[T], script: str, *args: object
    ) -> T | None:
        """
        Execute a JavaScript snippet in the current browser context.

        The caller declares the expected return type via the ``return_type``
        parameter so that ``ty`` can narrow the result at each call site without
        requiring casts.

        Args:
            return_type: The Python type expected back from the script (e.g. ``str``,
                ``dict``, ``int``).  Pass ``type(None)`` when no return value is needed.
            script: JavaScript source to execute.
            *args: Optional positional arguments forwarded to the script.

        Returns:
            The script's return value cast to ``T``, or ``None`` if the driver is
            unavailable or the result is not an instance of ``return_type``.

        Example::

            text = browser.execute_script(str, "return document.title;")
            stats = browser.execute_script(dict, "return {a: 1};")
        """
        if not self.driver:
            return None
        result = self.driver.execute_script(script, *args)
        if isinstance(result, return_type):
            return result
        return None

    def set_page_load_timeout(self, seconds: int) -> None:
        """
        Update the page load timeout on the active driver.

        Args:
            seconds: Timeout duration in seconds.
        """
        if self.driver:
            self.driver.set_page_load_timeout(seconds)


# ---------------------------------------------------------------------------
# AdNauseam Controller
# ---------------------------------------------------------------------------


class AdNauseamController:
    """
    Controls AdNauseam extension state within a running Firefox session.

    Handles UUID discovery (needed to construct internal moz-extension:// URLs),
    settings activation (ensuring ad hiding, clicking, and malware blocking are on),
    filter list readiness polling, and vault stat scraping.

    State is tied to a single driver lifetime. Call reset() after a browser
    restart to re-run discovery and activation cleanly.
    """

    EXTENSION_ID: str = "adnauseam@rednoise.org"
    """The permanent Firefox extension ID for AdNauseam."""

    def __init__(self, settings: Settings, browser: BrowserManager) -> None:
        """
        Initialise the controller.

        Args:
            settings: Validated AdInfinitum settings instance.
            browser: The active BrowserManager to use for navigation and scripting.
        """
        self.settings = settings
        self.browser = browser
        self._uuid: str | None = None
        self._activated: bool = False
        self._filters_ready: bool = False

    def reset(self) -> None:
        """
        Reset all per-session state.

        Called after a browser restart so that UUID discovery, activation,
        and filter polling are re-run against the new driver instance.
        """
        self._uuid = None
        self._activated = False
        self._filters_ready = False

    @property
    def ready(self) -> bool:
        """
        Whether the controller has completed all startup checks.

        Returns:
            True when UUID is known, settings are activated, and filters are loaded.
        """
        return bool(self._uuid) and self._activated and self._filters_ready

    # --- UUID Discovery ---

    def _uuid_from_prefs(self) -> str | None:
        """
        Read the AdNauseam internal UUID from Firefox's prefs.js.

        This is the most reliable method — the UUID is written to prefs.js
        as soon as the extension is installed and does not require browser
        navigation.

        Returns:
            The UUID string, or None if it cannot be found or parsed.
        """
        prefs_file = self.settings.profile_dir / "prefs.js"
        try:
            content = prefs_file.read_text()
            match = re.search(
                r'user_pref\("extensions\.webextensions\.uuids",\s*"(.*?)"\)',
                content,
            )
            if match:
                raw: str = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
                uuid_map: dict[str, str] = json.loads(raw)
                return uuid_map.get(self.EXTENSION_ID)
        except Exception as e:
            log.debug(f"Prefs UUID lookup failed: {e}")
        return None

    def _uuid_from_debugger(self) -> str | None:
        """
        Discover the AdNauseam UUID by scraping about:debugging.

        Used as a fallback when prefs.js lookup fails. Less reliable due to
        the async rendering of the debugging UI.

        Returns:
            The UUID string, or None if it cannot be found.
        """
        try:
            self.browser.get("about:debugging#/runtime/this-firefox")
            time.sleep(10)
            return self.browser.execute_script(
                str,
                """
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
            """,
            )
        except Exception as e:
            log.debug(f"Debugger UUID search failed: {e}")
            return None

    def discover_uuid(self) -> bool:
        """
        Attempt to discover the AdNauseam extension UUID.

        Tries prefs.js first, then falls back to about:debugging. The UUID
        is stored internally and used to construct all moz-extension:// URLs.

        Returns:
            True if the UUID was found, False otherwise.
        """
        if self._uuid:
            return True
        log.info("Locating AdNauseam extension...")
        self._uuid = self._uuid_from_prefs()
        if self._uuid:
            log.info("Extension located via prefs.js")
            return True
        log.info("Trying fallback detection via about:debugging...")
        self._uuid = self._uuid_from_debugger()
        if self._uuid:
            log.info("Extension located via debugger")
            return True
        log.warning("Extension not found")
        return False

    # --- Settings Activation ---

    def activate(self) -> bool:
        """
        Ensure AdNauseam's core features are enabled via the options page.

        Navigates to dashboard.html#options.html, accesses the settings iframe,
        and clicks any toggles that are off. Confirmed checkbox IDs from live
        DOM inspection of options.html: hidingAds, clickingAds, blockingMalware.

        Returns:
            True if activation succeeded, False otherwise.
        """
        if self._activated or not self._uuid:
            return self._activated
        options_url = f"moz-extension://{self._uuid}/dashboard.html#options.html"
        try:
            self.browser.set_page_load_timeout(20)
            self.browser.get(options_url)
            time.sleep(6)

            result: dict[str, str] | None = self.browser.execute_script(
                dict,
                """
                const iframe = document.getElementById('iframe');
                if (!iframe) return {error: 'no iframe found'};
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (!doc) return {error: 'cannot access iframe document'};
                const settings = ['hidingAds', 'clickingAds', 'blockingMalware'];
                const results = {};
                for (const name of settings) {
                    const el = doc.getElementById(name);
                    if (!el) { results[name] = 'not found'; continue; }
                    if (!el.checked) { el.click(); results[name] = 'activated'; }
                    else { results[name] = 'already on'; }
                }
                return results;
            """,
            )

            if result is not None and "error" not in result:
                enabled = [k for k, v in result.items() if v == "activated"]
                if enabled:
                    log.info(f"Enabled: {', '.join(enabled)}")
                else:
                    log.info("All ad detection settings already active")
                self._activated = True
            else:
                log.warning(f"Settings check returned unexpected result: {result}")
        except Exception as e:
            log.warning(f"Settings activation failed: {e}")
        finally:
            self.browser.set_page_load_timeout(self.settings.page_load_timeout)

        return self._activated

    # --- Filter Readiness ---

    def _get_filter_count(self) -> int:
        """
        Read the current network filter count from the AdNauseam filter list page.

        Navigates to dashboard.html#3p-filters.html and reads the
        #listsOfBlockedHostsPrompt element from inside the iframe.

        Expected text format: "167,399 network filters / 42,753 cosmetic filters from:"

        Returns:
            The number of loaded network filters, or 0 if not yet available.
        """
        filters_url = f"moz-extension://{self._uuid}/dashboard.html#3p-filters.html"
        try:
            self.browser.set_page_load_timeout(20)
            self.browser.get(filters_url)
            time.sleep(3)
            text: str | None = self.browser.execute_script(
                str,
                """
                const iframe = document.getElementById('iframe');
                if (!iframe) return null;
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (!doc) return null;
                const el = doc.getElementById('listsOfBlockedHostsPrompt');
                return el ? el.innerText.trim() : null;
            """,
            )
            if text:
                match = re.search(r"([\d,]+)\s+network filters", text)
                if match:
                    return int(match.group(1).replace(",", ""))
        except Exception:
            pass
        finally:
            self.browser.set_page_load_timeout(self.settings.page_load_timeout)
        return 0

    def wait_for_filters(self) -> bool:
        """
        Poll the filter list page until AdNauseam's rules are fully downloaded.

        Confirms readiness by checking that the network filter count is non-zero.
        Polls every filter_poll_interval seconds up to filter_poll_timeout seconds.

        Returns:
            True if filters loaded within the timeout, False otherwise.
        """
        if self._filters_ready or not self._uuid:
            return self._filters_ready
        log.info("Waiting for ad detection rules to download...")
        deadline = time.time() + self.settings.filter_poll_timeout
        elapsed = 0
        while time.time() < deadline:
            count = self._get_filter_count()
            if count > 0:
                log.info(
                    f"Ad detection ready — {count:,} network rules loaded ({elapsed}s)"
                )
                self._filters_ready = True
                return True
            elapsed += self.settings.filter_poll_interval
            log.info(f"Still downloading rules... ({elapsed}s elapsed)")
            time.sleep(self.settings.filter_poll_interval)
        log.warning(
            f"Rule download timed out after {self.settings.filter_poll_timeout}s, proceeding anyway"
        )
        return False

    # --- Vault Stats ---

    def scrape_vault(self) -> tuple[str, str, str]:
        """
        Scrape click and collection statistics from the AdNauseam vault.

        Navigates to vault.html and reads the stats bar using confirmed
        selectors from the live AdNauseam v3.28.2 DOM:
            span.clicked  -> "clicked N"
            span.total    -> "N ads collected"
            span#detected -> N currently showing

        Returns:
            A tuple of (clicked, collected, showing) as human-readable strings.
            Falls back to placeholder strings on failure.
        """
        if not self._uuid:
            return "clicked ?", "? ads collected", "?"
        vault_url = f"moz-extension://{self._uuid}/vault.html"
        try:
            self.browser.set_page_load_timeout(20)
            self.browser.get(vault_url)
            time.sleep(4)
            stats: dict[str, str] | None = self.browser.execute_script(
                dict,
                """
                function getText(selector) {
                    const el = document.querySelector(selector);
                    return el ? el.innerText.trim() : null;
                }
                return {
                    clicked:   getText('span.clicked'),
                    collected: getText('span.total'),
                    showing:   getText('span#detected')
                };
            """,
            )
            if stats is None:
                return "clicked ?", "? ads collected", "?"
            return (
                stats.get("clicked") or "clicked ?",
                stats.get("collected") or "? ads collected",
                stats.get("showing") or "?",
            )
        except Exception as e:
            log.warning(f"Vault scrape failed: {e}")
            return "clicked ?", "? ads collected", "?"
        finally:
            self.browser.set_page_load_timeout(self.settings.page_load_timeout)


# ---------------------------------------------------------------------------
# AdInfinitum — Main Orchestrator
# ---------------------------------------------------------------------------


class AdInfinitum:
    """
    Main orchestrator for the AdInfinitum browsing agent.

    Loads seed URLs, manages the session loop, coordinates the BrowserManager
    and AdNauseamController, and handles scheduled restarts and error recovery.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialise AdInfinitum with the provided settings.

        Args:
            settings: Validated AdInfinitum settings instance.
        """
        self.settings = settings
        self.seed_urls: list[str] = self._load_urls()
        self.browser: BrowserManager = BrowserManager(settings)
        self.controller: AdNauseamController = AdNauseamController(
            settings, self.browser
        )
        self.session_count: int = 0

    def _load_urls(self) -> list[str]:
        """
        Load seed URLs from the configured JSON file.

        Falls back to settings.default_urls if the file is absent,
        empty, or cannot be parsed.

        Returns:
            A list of seed URL strings.
        """
        try:
            if self.settings.urls_path.exists():
                urls: list[str] = json.loads(self.settings.urls_path.read_text())
                if urls:
                    log.info(
                        f"Loaded {len(urls)} URLs from {self.settings.urls_path.name}"
                    )
                    return urls
        except Exception as e:
            log.warning(f"Failed to load urls.json: {e}")
        log.info("No urls.json found, using default")
        return self.settings.default_urls

    def _update_heartbeat(self) -> None:
        """Touch the heartbeat file so the Docker healthcheck knows the process is alive."""
        self.settings.heartbeat_file.touch(exist_ok=True)

    def _log_resources(self) -> None:
        """Log current RAM and Firefox profile disk usage. Silently skips on failure."""
        try:
            mem_bytes = int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
            profile_size = sum(
                f.stat().st_size
                for f in self.settings.profile_dir.rglob("*")
                if f.is_file()
            )
            log.info(
                f"RAM: {mem_bytes / 1024**2:.2f}MB | "
                f"Profile: {profile_size / 1024**2:.2f}MB"
            )
        except Exception:
            pass

    def _browse(self, url: str) -> None:
        """
        Visit a URL and simulate natural scrolling behaviour.

        Navigates to the URL, then performs a random number of scroll steps
        with random distances and pauses, updating the heartbeat throughout.

        Args:
            url: The URL to visit and scroll through.
        """
        self.browser.get(url)
        self._update_heartbeat()
        steps = random.randint(
            self.settings.scroll_steps_min,
            self.settings.scroll_steps_max,
        )
        for _ in range(steps):
            scroll = random.randint(self.settings.scroll_min, self.settings.scroll_max)
            self.browser.execute_script(str, f"window.scrollBy(0, {scroll});")
            time.sleep(
                random.uniform(
                    self.settings.scroll_pause_min,
                    self.settings.scroll_pause_max,
                )
            )
            self._update_heartbeat()

    def _setup(self) -> bool:
        """
        Run per-driver startup checks: UUID discovery, activation, and filter sync.

        Called once after each browser start or restart. Safe to call multiple
        times — each step is idempotent and skips itself if already complete.

        Returns:
            True if UUID was discovered successfully, False otherwise.
        """
        if not self.controller.discover_uuid():
            return False
        self.controller.activate()
        self.controller.wait_for_filters()
        return True

    def _restart(self) -> None:
        """Perform a scheduled browser restart and re-run startup checks."""
        log.info("Scheduled restart...")
        self.browser.restart()
        self.controller.reset()
        self._setup()

    def run(self) -> None:
        """
        Start the main browsing loop.

        Registers signal handlers for clean shutdown, boots the browser,
        then runs indefinitely — visiting seed URLs, logging vault stats,
        and restarting the browser on schedule or after errors.
        """
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

        log.info("AdInfinitum started")
        if not self.browser.start():
            sys.exit(1)

        while True:
            try:
                url = random.choice(self.seed_urls)
                self.session_count += 1
                log.info(f"Session #{self.session_count}: {url}")
                self._log_resources()

                self._browse(url)

                if not self.controller.ready:
                    self._setup()

                clicked, collected, showing = self.controller.scrape_vault()
                log.info(f"Vault: {clicked} | {collected} | {showing} showing")

                if self.session_count % self.settings.session_restart_interval == 0:
                    self._restart()

            except Exception as e:
                log.error(f"Loop error: {e}")
                self.browser.restart()
                self.controller.reset()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    AdInfinitum(settings=Settings()).run()
