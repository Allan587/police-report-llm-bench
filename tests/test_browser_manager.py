import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from automation.browser import BrowserLaunchError, BrowserManager, ProfileInUseError


class BrowserManagerUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _playwright_mocks():
        context = Mock()
        context.close = AsyncMock()
        context.on = Mock()
        chromium = Mock()
        chromium.launch_persistent_context = AsyncMock(return_value=context)
        playwright = Mock(chromium=chromium)
        playwright.stop = AsyncMock()
        starter = Mock()
        starter.start = AsyncMock(return_value=playwright)
        return starter, playwright, context

    async def test_open_and_close_persistent_context(self) -> None:
        starter, playwright, context = self._playwright_mocks()
        manager = BrowserManager(self.profile_root, "chatgpt", headless=True)

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            opened_context = await manager.open()
            second_open = await manager.open()
            await manager.close()
            await manager.close()

        self.assertIs(opened_context, context)
        self.assertIs(second_open, context)
        playwright.chromium.launch_persistent_context.assert_awaited_once_with(
            user_data_dir=manager.profile_dir,
            headless=True,
            timeout=30_000,
        )
        context.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        self.assertFalse(manager.is_open)

    async def test_concurrent_open_reuses_single_context(self) -> None:
        starter, playwright, context = self._playwright_mocks()

        async def delayed_launch(**kwargs):
            await asyncio.sleep(0)
            return context

        playwright.chromium.launch_persistent_context.side_effect = delayed_launch
        manager = BrowserManager(self.profile_root, "chatgpt", headless=True)

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            first, second = await asyncio.gather(manager.open(), manager.open())
            await manager.close()

        self.assertIs(first, context)
        self.assertIs(second, context)
        playwright.chromium.launch_persistent_context.assert_awaited_once()

    async def test_forwards_headed_mode_and_custom_launch_timeout(self) -> None:
        starter, playwright, _ = self._playwright_mocks()
        manager = BrowserManager(
            self.profile_root,
            "chatgpt",
            headless=False,
            launch_timeout_ms=12_345,
        )

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            await manager.open()
            await manager.close()

        playwright.chromium.launch_persistent_context.assert_awaited_once_with(
            user_data_dir=manager.profile_dir,
            headless=False,
            timeout=12_345,
        )

    def test_rejects_non_positive_launch_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            BrowserManager(self.profile_root, "chatgpt", launch_timeout_ms=0)

    async def test_unexpected_context_close_releases_and_reopens_profile(self) -> None:
        first_context = Mock()
        first_context.close = AsyncMock()
        close_handlers = {}
        first_context.on = Mock(
            side_effect=lambda event, handler: close_handlers.setdefault(event, handler)
        )
        second_context = Mock()
        second_context.close = AsyncMock()
        second_context.on = Mock()

        first_playwright = Mock()
        first_playwright.stop = AsyncMock()
        first_playwright.chromium = Mock()
        first_playwright.chromium.launch_persistent_context = AsyncMock(
            return_value=first_context
        )
        second_playwright = Mock()
        second_playwright.stop = AsyncMock()
        second_playwright.chromium = Mock()
        second_playwright.chromium.launch_persistent_context = AsyncMock(
            return_value=second_context
        )
        starter = Mock()
        starter.start = AsyncMock(side_effect=[first_playwright, second_playwright])
        manager = BrowserManager(self.profile_root, "chatgpt", headless=True)

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            await manager.open()
            close_handlers["close"](first_context)
            self.assertFalse(manager.is_open)
            reopened = await manager.open()
            await manager.close()

        self.assertIs(reopened, second_context)
        first_playwright.stop.assert_awaited_once()
        second_playwright.stop.assert_awaited_once()

    async def test_rejects_concurrent_owner_of_same_profile(self) -> None:
        first = BrowserManager(self.profile_root, "gemini", headless=True)
        second = BrowserManager(self.profile_root, "gemini", headless=True)
        starter, _, _ = self._playwright_mocks()

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            await first.open()
            with self.assertRaisesRegex(ProfileInUseError, "already in use"):
                await second.open()
            await first.close()

    async def test_launch_failure_releases_profile(self) -> None:
        starter, playwright, _ = self._playwright_mocks()
        playwright.chromium.launch_persistent_context.side_effect = RuntimeError(
            "missing executable"
        )
        manager = BrowserManager(self.profile_root, "grok", headless=True)

        with patch(
            "automation.browser.manager.async_playwright",
            return_value=starter,
        ):
            with self.assertRaisesRegex(BrowserLaunchError, "playwright install"):
                await manager.open()

        replacement = BrowserManager(self.profile_root, "grok", headless=True)
        replacement._acquire_profile_lock()
        replacement._release_profile_lock()
        playwright.stop.assert_awaited_once()


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "set RUN_BROWSER_SMOKE=1 to launch local Chromium",
)
class BrowserManagerSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_chromium_opens_navigates_locally_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = BrowserManager(Path(temp_dir), "synthetic", headless=True)
            async with manager:
                page = await manager.context.new_page()
                await page.goto("data:text/html,<title>synthetic-smoke</title>")
                self.assertEqual(await page.title(), "synthetic-smoke")


if __name__ == "__main__":
    unittest.main()
