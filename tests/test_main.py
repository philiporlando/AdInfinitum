"""
Tests for AdInfinitum — main.py

Coverage:
  - Settings: validation, defaults, env var override
  - BrowserManager: options construction, execute_script typing, get() timeout handling
  - AdNauseamController: UUID discovery, activation, filter polling, vault scraping, reset/ready
  - AdInfinitum: URL loading, resource logging, browsing, setup, restart, run loop
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest
from pytest_mock import MockerFixture
from selenium.common.exceptions import TimeoutException

from src.main import AdInfinitum, AdNauseamController, BrowserManager, Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return a Settings instance with profile/heartbeat paths inside tmp_path."""
    return Settings(
        profile_dir=tmp_path / "profile",
        heartbeat_file=tmp_path / "heartbeat",
        urls_path=tmp_path / "urls.json",
        filter_poll_interval=1,
        filter_poll_timeout=10,
    )


@pytest.fixture
def browser(settings: Settings) -> BrowserManager:
    """Return a BrowserManager with no live driver."""
    return BrowserManager(settings)


@pytest.fixture
def mock_driver(mocker: MockerFixture) -> MagicMock:
    """Return a MagicMock standing in for a Firefox WebDriver."""
    return mocker.MagicMock()


@pytest.fixture
def browser_with_driver(
    browser: BrowserManager, mock_driver: MagicMock
) -> BrowserManager:
    """Return a BrowserManager with a mocked driver already attached."""
    browser.driver = mock_driver
    return browser


@pytest.fixture
def controller(
    settings: Settings, browser_with_driver: BrowserManager
) -> AdNauseamController:
    """Return an AdNauseamController with a mocked browser."""
    return AdNauseamController(settings, browser_with_driver)


@pytest.fixture
def controller_with_uuid(controller: AdNauseamController) -> AdNauseamController:
    """Return a controller with a UUID already set."""
    controller._uuid = "test-uuid-1234"
    return controller


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    """Tests for the Settings Pydantic model."""

    def test_defaults(self) -> None:
        """Default values should match documented constants."""
        s = Settings()
        assert s.profile_dir == Path("/tmp/adnauseam_profile")
        assert s.heartbeat_file == Path("/tmp/heartbeat")
        assert s.filter_poll_interval == 5
        assert s.filter_poll_timeout == 300
        assert s.page_load_timeout == 45
        assert s.session_restart_interval == 25
        assert s.default_urls == ["https://www.yahoo.com"]

    def test_xpi_path_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADNAUSEAM_XPI env var should override the default xpi_path."""
        monkeypatch.setenv("ADNAUSEAM_XPI", "/custom/path/adnauseam.xpi")
        # Re-import to pick up env var (field default is evaluated at class definition)
        import importlib
        import src.main

        importlib.reload(src.main)
        from src.main import Settings as ReloadedSettings

        s = ReloadedSettings()
        assert s.xpi_path == Path("/custom/path/adnauseam.xpi")

    def test_field_validation_filter_poll_interval(self) -> None:
        """filter_poll_interval must be >= 1."""
        with pytest.raises(Exception):
            Settings(filter_poll_interval=0)

    def test_field_validation_filter_poll_timeout(self) -> None:
        """filter_poll_timeout must be >= 10; 9 should fail."""
        with pytest.raises(Exception):
            Settings(filter_poll_timeout=9)

    def test_field_validation_scroll_steps(self) -> None:
        """scroll_steps_min must be >= 1."""
        with pytest.raises(Exception):
            Settings(scroll_steps_min=0)

    def test_field_validation_page_load_timeout(self) -> None:
        """page_load_timeout must be >= 5."""
        with pytest.raises(Exception):
            Settings(page_load_timeout=4)

    def test_custom_values(self, tmp_path: Path) -> None:
        """Custom values should be stored correctly."""
        s = Settings(
            profile_dir=tmp_path / "profile",
            filter_poll_interval=10,
            session_restart_interval=50,
        )
        assert s.profile_dir == tmp_path / "profile"
        assert s.filter_poll_interval == 10
        assert s.session_restart_interval == 50


# ---------------------------------------------------------------------------
# BrowserManager
# ---------------------------------------------------------------------------


class TestBrowserManager:
    """Tests for BrowserManager — options, script execution, navigation."""

    def test_build_options_sets_profile(self, browser: BrowserManager) -> None:
        """Firefox options should include the profile directory argument."""
        opts = browser._build_options()
        assert str(browser.settings.profile_dir) in opts.arguments

    def test_build_options_sets_dimensions(self, browser: BrowserManager) -> None:
        """Firefox options should include width and height arguments."""
        opts = browser._build_options()
        assert "--width=1920" in opts.arguments
        assert "--height=1080" in opts.arguments

    def test_execute_script_returns_none_without_driver(
        self, browser: BrowserManager
    ) -> None:
        """execute_script should return None when no driver is attached."""
        result = browser.execute_script(str, "return 'hello';")
        assert result is None

    def test_execute_script_returns_typed_result(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """execute_script should return the result when it matches the declared type."""
        mock_driver.execute_script.return_value = "hello"
        result = browser_with_driver.execute_script(str, "return 'hello';")
        assert result == "hello"

    def test_execute_script_returns_none_on_type_mismatch(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """execute_script should return None when the result is the wrong type."""
        mock_driver.execute_script.return_value = 42
        result = browser_with_driver.execute_script(str, "return 42;")
        assert result is None

    def test_execute_script_returns_dict(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """execute_script should correctly return dict results."""
        mock_driver.execute_script.return_value = {"key": "value"}
        result = browser_with_driver.execute_script(dict, "return {key: 'value'};")
        assert result == {"key": "value"}

    def test_get_returns_true_on_success(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """get() should return True when navigation succeeds."""
        result = browser_with_driver.get("https://example.com")
        assert result is True
        mock_driver.get.assert_called_once_with("https://example.com")

    def test_get_returns_false_on_timeout(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """get() should return False and log a warning on TimeoutException."""
        mock_driver.get.side_effect = TimeoutException()
        result = browser_with_driver.get("https://example.com")
        assert result is False

    def test_get_returns_false_without_driver(self, browser: BrowserManager) -> None:
        """get() should return False when no driver is attached."""
        result = browser.get("https://example.com")
        assert result is False

    def test_stop_quits_driver(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """stop() should call quit() on the driver and clear the reference."""
        browser_with_driver.stop()
        mock_driver.quit.assert_called_once()
        assert browser_with_driver.driver is None

    def test_stop_handles_quit_exception(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """stop() should not raise even if driver.quit() throws."""
        mock_driver.quit.side_effect = Exception("quit failed")
        browser_with_driver.stop()
        assert browser_with_driver.driver is None

    def test_stop_without_driver_is_safe(self, browser: BrowserManager) -> None:
        """stop() should be a no-op when no driver is attached."""
        browser.stop()  # Should not raise

    def test_set_page_load_timeout_without_driver_is_safe(
        self, browser: BrowserManager
    ) -> None:
        """set_page_load_timeout() should be a no-op when no driver is attached."""
        browser.set_page_load_timeout(30)  # Should not raise

    def test_set_page_load_timeout_with_driver(
        self, browser_with_driver: BrowserManager, mock_driver: MagicMock
    ) -> None:
        """set_page_load_timeout() should delegate to the driver."""
        browser_with_driver.set_page_load_timeout(30)
        mock_driver.set_page_load_timeout.assert_called_once_with(30)


# ---------------------------------------------------------------------------
# AdNauseamController
# ---------------------------------------------------------------------------


class TestAdNauseamControllerReset:
    """Tests for reset() and the ready property."""

    def test_initial_state_not_ready(self, controller: AdNauseamController) -> None:
        """Controller should not be ready on initialisation."""
        assert controller.ready is False

    def test_ready_requires_all_three(self, controller: AdNauseamController) -> None:
        """ready should only be True when UUID, activated, and filters are all set."""
        controller._uuid = "test-uuid"
        assert controller.ready is False
        controller._activated = True
        assert controller.ready is False
        controller._filters_ready = True
        assert controller.ready is True

    def test_reset_clears_state(
        self, controller_with_uuid: AdNauseamController
    ) -> None:
        """reset() should clear UUID, activated, and filters_ready."""
        controller_with_uuid._activated = True
        controller_with_uuid._filters_ready = True
        controller_with_uuid.reset()
        assert controller_with_uuid._uuid is None
        assert controller_with_uuid._activated is False
        assert controller_with_uuid._filters_ready is False
        assert controller_with_uuid.ready is False


class TestAdNauseamControllerUUID:
    """Tests for UUID discovery via prefs.js and about:debugging."""

    def test_uuid_from_prefs_success(
        self, controller: AdNauseamController, tmp_path: Path
    ) -> None:
        """_uuid_from_prefs should parse the UUID from a valid prefs.js."""
        prefs_content = (
            'user_pref("extensions.webextensions.uuids", '
            '"{\\"adnauseam@rednoise.org\\":\\"abc-123\\"}");\n'
        )
        prefs_file = controller.settings.profile_dir
        prefs_file.mkdir(parents=True, exist_ok=True)
        (prefs_file / "prefs.js").write_text(prefs_content)
        result = controller._uuid_from_prefs()
        assert result == "abc-123"

    def test_uuid_from_prefs_missing_file(
        self, controller: AdNauseamController
    ) -> None:
        """_uuid_from_prefs should return None when prefs.js does not exist."""
        result = controller._uuid_from_prefs()
        assert result is None

    def test_uuid_from_prefs_malformed_json(
        self, controller: AdNauseamController
    ) -> None:
        """_uuid_from_prefs should return None when the JSON is malformed."""
        prefs_file = controller.settings.profile_dir
        prefs_file.mkdir(parents=True, exist_ok=True)
        (prefs_file / "prefs.js").write_text(
            'user_pref("extensions.webextensions.uuids", "not-valid-json");\n'
        )
        result = controller._uuid_from_prefs()
        assert result is None

    def test_uuid_from_prefs_extension_id_missing(
        self, controller: AdNauseamController
    ) -> None:
        """_uuid_from_prefs should return None when the extension ID is absent."""
        prefs_content = (
            'user_pref("extensions.webextensions.uuids", '
            '"{\\"other@extension.org\\":\\"xyz-789\\"}");\n'
        )
        prefs_file = controller.settings.profile_dir
        prefs_file.mkdir(parents=True, exist_ok=True)
        (prefs_file / "prefs.js").write_text(prefs_content)
        result = controller._uuid_from_prefs()
        assert result is None

    def test_discover_uuid_uses_prefs_first(
        self, controller: AdNauseamController, mocker: MockerFixture
    ) -> None:
        """discover_uuid should use prefs.js and not fall back to debugger on success."""
        mocker.patch.object(controller, "_uuid_from_prefs", return_value="prefs-uuid")
        debugger_mock = mocker.patch.object(controller, "_uuid_from_debugger")
        result = controller.discover_uuid()
        assert result is True
        assert controller._uuid == "prefs-uuid"
        debugger_mock.assert_not_called()

    def test_discover_uuid_falls_back_to_debugger(
        self, controller: AdNauseamController, mocker: MockerFixture
    ) -> None:
        """discover_uuid should fall back to about:debugging when prefs.js fails."""
        mocker.patch.object(controller, "_uuid_from_prefs", return_value=None)
        mocker.patch.object(
            controller, "_uuid_from_debugger", return_value="debug-uuid"
        )
        result = controller.discover_uuid()
        assert result is True
        assert controller._uuid == "debug-uuid"

    def test_discover_uuid_returns_false_when_both_fail(
        self, controller: AdNauseamController, mocker: MockerFixture
    ) -> None:
        """discover_uuid should return False when both methods fail."""
        mocker.patch.object(controller, "_uuid_from_prefs", return_value=None)
        mocker.patch.object(controller, "_uuid_from_debugger", return_value=None)
        result = controller.discover_uuid()
        assert result is False
        assert controller._uuid is None

    def test_discover_uuid_skips_if_already_set(
        self, controller_with_uuid: AdNauseamController, mocker: MockerFixture
    ) -> None:
        """discover_uuid should be a no-op when UUID is already known."""
        prefs_mock = mocker.patch.object(controller_with_uuid, "_uuid_from_prefs")
        result = controller_with_uuid.discover_uuid()
        assert result is True
        prefs_mock.assert_not_called()


class TestAdNauseamControllerActivation:
    """Tests for activate() — enabling AdNauseam settings via the options page."""

    def test_activate_skips_without_uuid(self, controller: AdNauseamController) -> None:
        """activate() should return False and skip navigation when UUID is unset."""
        result = controller.activate()
        assert result is False

    def test_activate_skips_if_already_activated(
        self, controller_with_uuid: AdNauseamController
    ) -> None:
        """activate() should return True immediately when already activated."""
        controller_with_uuid._activated = True
        result = controller_with_uuid.activate()
        assert result is True

    def test_activate_success_all_already_on(
        self,
        controller_with_uuid: AdNauseamController,
        browser_with_driver: BrowserManager,
        mock_driver: MagicMock,
    ) -> None:
        """activate() should set _activated=True when all settings are already on."""
        mock_driver.execute_script.return_value = {
            "hidingAds": "already on",
            "clickingAds": "already on",
            "blockingMalware": "already on",
        }
        result = controller_with_uuid.activate()
        assert result is True
        assert controller_with_uuid._activated is True

    def test_activate_success_enables_settings(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """activate() should set _activated=True when settings are toggled on."""
        mock_driver.execute_script.return_value = {
            "hidingAds": "activated",
            "clickingAds": "activated",
            "blockingMalware": "activated",
        }
        result = controller_with_uuid.activate()
        assert result is True
        assert controller_with_uuid._activated is True

    def test_activate_handles_iframe_error(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """activate() should not set _activated when the iframe is missing."""
        mock_driver.execute_script.return_value = {"error": "no iframe found"}
        result = controller_with_uuid.activate()
        assert result is False
        assert controller_with_uuid._activated is False

    def test_activate_handles_exception(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """activate() should handle unexpected exceptions gracefully."""
        mock_driver.execute_script.side_effect = Exception("script failed")
        result = controller_with_uuid.activate()
        assert result is False


class TestAdNauseamControllerFilters:
    """Tests for filter list polling via _get_filter_count() and wait_for_filters()."""

    def test_get_filter_count_parses_correctly(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """_get_filter_count should parse '167,399 network filters' correctly."""
        mock_driver.execute_script.return_value = (
            "167,399 network filters / 42,753 cosmetic filters from:"
        )
        count = controller_with_uuid._get_filter_count()
        assert count == 167399

    def test_get_filter_count_returns_zero_on_none(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """_get_filter_count should return 0 when the element text is None."""
        mock_driver.execute_script.return_value = None
        count = controller_with_uuid._get_filter_count()
        assert count == 0

    def test_get_filter_count_returns_zero_on_exception(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """_get_filter_count should return 0 when an exception is raised."""
        mock_driver.execute_script.side_effect = Exception("navigation failed")
        count = controller_with_uuid._get_filter_count()
        assert count == 0

    def test_wait_for_filters_succeeds_immediately(
        self,
        controller_with_uuid: AdNauseamController,
        mocker: MockerFixture,
    ) -> None:
        """wait_for_filters should return True immediately when filters are ready."""
        mocker.patch.object(
            controller_with_uuid, "_get_filter_count", return_value=155000
        )
        result = controller_with_uuid.wait_for_filters()
        assert result is True
        assert controller_with_uuid._filters_ready is True

    def test_wait_for_filters_retries_then_succeeds(
        self,
        controller_with_uuid: AdNauseamController,
        mocker: MockerFixture,
    ) -> None:
        """wait_for_filters should retry and eventually succeed."""
        mocker.patch.object(
            controller_with_uuid,
            "_get_filter_count",
            side_effect=[0, 0, 155000],
        )
        mocker.patch("src.main.time.sleep")
        result = controller_with_uuid.wait_for_filters()
        assert result is True

    def test_wait_for_filters_times_out(
        self,
        controller_with_uuid: AdNauseamController,
        mocker: MockerFixture,
    ) -> None:
        """wait_for_filters should return False after the timeout is exceeded."""
        mocker.patch.object(controller_with_uuid, "_get_filter_count", return_value=0)
        mocker.patch("src.main.time.sleep")
        # Force immediate timeout by making time.time() advance past deadline
        call_count = 0
        original_time = __import__("time").time

        def fast_time() -> float:
            nonlocal call_count
            call_count += 1
            return original_time() + (call_count * 100)

        mocker.patch("src.main.time.time", side_effect=fast_time)
        result = controller_with_uuid.wait_for_filters()
        assert result is False
        assert controller_with_uuid._filters_ready is False

    def test_wait_for_filters_skips_if_already_ready(
        self,
        controller_with_uuid: AdNauseamController,
        mocker: MockerFixture,
    ) -> None:
        """wait_for_filters should be a no-op when filters are already ready."""
        controller_with_uuid._filters_ready = True
        count_mock = mocker.patch.object(controller_with_uuid, "_get_filter_count")
        result = controller_with_uuid.wait_for_filters()
        assert result is True
        count_mock.assert_not_called()

    def test_wait_for_filters_skips_without_uuid(
        self, controller: AdNauseamController, mocker: MockerFixture
    ) -> None:
        """wait_for_filters should return False immediately when UUID is unset."""
        count_mock = mocker.patch.object(controller, "_get_filter_count")
        result = controller.wait_for_filters()
        assert result is False
        count_mock.assert_not_called()


class TestAdNauseamControllerVault:
    """Tests for vault stat scraping."""

    def test_scrape_vault_returns_placeholders_without_uuid(
        self, controller: AdNauseamController
    ) -> None:
        """scrape_vault should return placeholder strings when UUID is unset."""
        clicked, collected, showing = controller.scrape_vault()
        assert clicked == "clicked ?"
        assert collected == "? ads collected"
        assert showing == "?"

    def test_scrape_vault_happy_path(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """scrape_vault should return parsed stats from the vault DOM."""
        mock_driver.execute_script.return_value = {
            "clicked": "clicked 42",
            "collected": "99 ads collected",
            "showing": "50",
        }
        clicked, collected, showing = controller_with_uuid.scrape_vault()
        assert clicked == "clicked 42"
        assert collected == "99 ads collected"
        assert showing == "50"

    def test_scrape_vault_returns_placeholders_on_none_result(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """scrape_vault should return placeholders when execute_script returns None."""
        mock_driver.execute_script.return_value = None
        clicked, collected, showing = controller_with_uuid.scrape_vault()
        assert clicked == "clicked ?"
        assert collected == "? ads collected"
        assert showing == "?"

    def test_scrape_vault_returns_placeholders_on_exception(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """scrape_vault should return placeholders when an exception is raised."""
        mock_driver.execute_script.side_effect = Exception("vault error")
        clicked, collected, showing = controller_with_uuid.scrape_vault()
        assert clicked == "clicked ?"
        assert collected == "? ads collected"
        assert showing == "?"

    def test_scrape_vault_falls_back_on_missing_keys(
        self,
        controller_with_uuid: AdNauseamController,
        mock_driver: MagicMock,
    ) -> None:
        """scrape_vault should use placeholder strings for any missing stat keys."""
        mock_driver.execute_script.return_value = {
            "clicked": "clicked 5",
            "collected": None,
            "showing": None,
        }
        clicked, collected, showing = controller_with_uuid.scrape_vault()
        assert clicked == "clicked 5"
        assert collected == "? ads collected"
        assert showing == "?"


# ---------------------------------------------------------------------------
# AdInfinitum
# ---------------------------------------------------------------------------


class TestAdInfiniumURLLoading:
    """Tests for _load_urls() — JSON loading and fallback behaviour."""

    def test_loads_urls_from_file(self, settings: Settings) -> None:
        """_load_urls should return URLs from a valid urls.json."""
        settings.urls_path.write_text(json.dumps(["https://a.com", "https://b.com"]))
        ai = AdInfinitum(settings)
        assert ai.seed_urls == ["https://a.com", "https://b.com"]

    def test_falls_back_on_missing_file(self, settings: Settings) -> None:
        """_load_urls should return default_urls when urls.json is absent."""
        ai = AdInfinitum(settings)
        assert ai.seed_urls == settings.default_urls

    def test_falls_back_on_empty_list(self, settings: Settings) -> None:
        """_load_urls should return default_urls when urls.json contains an empty list."""
        settings.urls_path.write_text("[]")
        ai = AdInfinitum(settings)
        assert ai.seed_urls == settings.default_urls

    def test_falls_back_on_malformed_json(self, settings: Settings) -> None:
        """_load_urls should return default_urls when urls.json is malformed."""
        settings.urls_path.write_text("not valid json{{")
        ai = AdInfinitum(settings)
        assert ai.seed_urls == settings.default_urls


class TestAdInfiniumHeartbeat:
    """Tests for _update_heartbeat()."""

    def test_creates_heartbeat_file(self, settings: Settings) -> None:
        """_update_heartbeat should create the heartbeat file if absent."""
        ai = AdInfinitum(settings)
        assert not settings.heartbeat_file.exists()
        ai._update_heartbeat()
        assert settings.heartbeat_file.exists()

    def test_touches_existing_heartbeat_file(self, settings: Settings) -> None:
        """_update_heartbeat should update the mtime of an existing heartbeat file."""
        settings.heartbeat_file.touch()
        old_mtime = settings.heartbeat_file.stat().st_mtime
        import time

        time.sleep(0.05)
        ai = AdInfinitum(settings)
        ai._update_heartbeat()
        new_mtime = settings.heartbeat_file.stat().st_mtime
        assert new_mtime >= old_mtime


class TestAdInfiniumResources:
    """Tests for _log_resources()."""

    def test_logs_resources_when_cgroup_available(
        self, settings: Settings, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """_log_resources should log RAM and profile size without raising."""
        cgroup_file = tmp_path / "memory.current"
        cgroup_file.write_text("1073741824")  # 1 GB
        mocker.patch("builtins.open", mocker.mock_open(read_data="1073741824"))
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        ai = AdInfinitum(settings)
        ai._log_resources()  # Should not raise

    def test_log_resources_silently_skips_on_error(self, settings: Settings) -> None:
        """_log_resources should not raise when cgroup file is absent."""
        ai = AdInfinitum(settings)
        ai._log_resources()  # Should not raise


class TestAdInfiniumBrowse:
    """Tests for _browse() — navigation and scroll simulation."""

    def test_browse_calls_get_and_scrolls(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """_browse should navigate to the URL and execute scroll scripts."""
        ai = AdInfinitum(settings)
        get_mock = mocker.patch.object(ai.browser, "get", return_value=True)
        script_mock = mocker.patch.object(ai.browser, "execute_script")
        mocker.patch("src.main.time.sleep")
        settings.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)

        ai._browse("https://example.com")

        get_mock.assert_called_once_with("https://example.com")
        assert script_mock.call_count >= settings.scroll_steps_min

    def test_browse_updates_heartbeat(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """_browse should touch the heartbeat file during scrolling."""
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.browser, "get", return_value=True)
        mocker.patch.object(ai.browser, "execute_script")
        mocker.patch("src.main.time.sleep")
        heartbeat_mock = mocker.patch.object(ai, "_update_heartbeat")

        ai._browse("https://example.com")

        assert heartbeat_mock.call_count >= 2  # once after get, once per scroll step


class TestAdInfiniumSetup:
    """Tests for _setup() — UUID, activation, and filter orchestration."""

    def test_setup_returns_false_when_uuid_fails(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """_setup should return False when UUID discovery fails."""
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.controller, "discover_uuid", return_value=False)
        activate_mock = mocker.patch.object(ai.controller, "activate")
        result = ai._setup()
        assert result is False
        activate_mock.assert_not_called()

    def test_setup_runs_all_steps_on_success(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """_setup should run discover, activate, and wait_for_filters in order."""
        ai = AdInfinitum(settings)
        discover_mock = mocker.patch.object(
            ai.controller, "discover_uuid", return_value=True
        )
        activate_mock = mocker.patch.object(
            ai.controller, "activate", return_value=True
        )
        filters_mock = mocker.patch.object(
            ai.controller, "wait_for_filters", return_value=True
        )

        result = ai._setup()

        assert result is True
        discover_mock.assert_called_once()
        activate_mock.assert_called_once()
        filters_mock.assert_called_once()


class TestAdInfiniumRestart:
    """Tests for _restart() — scheduled browser restart."""

    def test_restart_calls_browser_restart_and_resets_controller(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """_restart should restart the browser, reset the controller, and run setup."""
        ai = AdInfinitum(settings)
        restart_mock = mocker.patch.object(ai.browser, "restart", return_value=True)
        reset_mock = mocker.patch.object(ai.controller, "reset")
        setup_mock = mocker.patch.object(ai, "_setup", return_value=True)

        ai._restart()

        restart_mock.assert_called_once()
        reset_mock.assert_called_once()
        setup_mock.assert_called_once()


class TestAdInfiniumRunLoop:
    """Tests for the main run() loop — session management and error recovery."""

    def test_run_exits_when_browser_fails_to_start(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """run() should call sys.exit(1) when the browser fails to start."""
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.browser, "start", return_value=False)
        with pytest.raises(SystemExit) as exc_info:
            ai.run()
        assert exc_info.value.code == 1

    def test_run_executes_one_session(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """run() should complete one session and log vault stats before stopping."""
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.browser, "start", return_value=True)
        mocker.patch.object(ai, "_browse")
        mocker.patch.object(ai, "_log_resources")
        mocker.patch.object(
            type(ai.controller),
            "ready",
            new_callable=PropertyMock,
            return_value=True,
        )

        # Stop after one iteration by raising on the second call to random.choice
        call_count = 0
        original_choice = __import__("random").choice

        def limited_choice(seq: list) -> str:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt
            return original_choice(seq)

        mocker.patch("src.main.random.choice", side_effect=limited_choice)

        with pytest.raises((KeyboardInterrupt, SystemExit)):
            ai.run()

        assert ai.session_count == 1

    def test_run_recovers_from_loop_error(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """run() should restart the browser and reset the controller after an error."""
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.browser, "start", return_value=True)
        restart_mock = mocker.patch.object(ai.browser, "restart", return_value=True)
        reset_mock = mocker.patch.object(ai.controller, "reset")
        mocker.patch.object(ai, "_log_resources")

        call_count = 0

        def browse_side_effect(url: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("unexpected error")
            raise KeyboardInterrupt

        mocker.patch.object(ai, "_browse", side_effect=browse_side_effect)
        mocker.patch("src.main.random.choice", return_value="https://example.com")

        with pytest.raises((KeyboardInterrupt, SystemExit)):
            ai.run()

        restart_mock.assert_called()
        reset_mock.assert_called()

    def test_run_triggers_restart_at_interval(
        self, settings: Settings, mocker: MockerFixture
    ) -> None:
        """run() should call _restart() every session_restart_interval sessions."""
        settings.session_restart_interval = 2
        ai = AdInfinitum(settings)
        mocker.patch.object(ai.browser, "start", return_value=True)
        mocker.patch.object(ai, "_browse")
        mocker.patch.object(ai, "_log_resources")
        mocker.patch.object(
            ai.controller,
            "scrape_vault",
            return_value=("clicked 0", "0 ads collected", "0"),
        )
        mocker.patch.object(
            type(ai.controller),
            "ready",
            new_callable=PropertyMock,
            return_value=True,
        )
        restart_mock = mocker.patch.object(ai, "_restart")
        mocker.patch("src.main.random.choice", return_value="https://example.com")

        call_count = 0

        def stop_after_three(*args: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise KeyboardInterrupt
            return "https://example.com"

        mocker.patch("src.main.random.choice", side_effect=stop_after_three)

        with pytest.raises((KeyboardInterrupt, SystemExit)):
            ai.run()

        restart_mock.assert_called_once()
