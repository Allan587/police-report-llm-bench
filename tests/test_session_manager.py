import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from automation.browser import (
    BrowserManager,
    ProfileInUseError,
    SessionCloseError,
    SessionManager,
    UnknownProviderError,
    UnsafeProfileRootError,
    resolve_profile_root,
)


class FakeBrowserManager:
    instances = []

    def __init__(
        self,
        profile_root,
        profile_name,
        *,
        headless=False,
        launch_timeout_ms=30_000,
    ):
        self.profile_root = profile_root
        self.profile_name = profile_name
        self.headless = headless
        self.launch_timeout_ms = launch_timeout_ms
        self.context = object()
        self.is_open = False
        self.close_error = None
        self.__class__.instances.append(self)

    async def open(self):
        self.is_open = True
        return self.context

    async def close(self):
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error


class SessionManagerUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeBrowserManager.instances.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_uses_one_independent_browser_manager_per_provider(self) -> None:
        sessions = SessionManager(
            self.profile_root,
            headless=True,
            launch_timeout_ms=12_345,
            browser_manager_factory=FakeBrowserManager,
        )

        chatgpt = await sessions.open("ChatGPT")
        same_chatgpt = await sessions.open("chatgpt")
        gemini = await sessions.open("gemini")

        self.assertIs(chatgpt, same_chatgpt)
        self.assertIsNot(chatgpt, gemini)
        self.assertEqual(sessions.providers, ("chatgpt", "gemini"))
        self.assertEqual(
            [manager.profile_name for manager in FakeBrowserManager.instances],
            ["chatgpt", "gemini"],
        )
        self.assertTrue(all(manager.headless for manager in FakeBrowserManager.instances))
        self.assertTrue(
            all(
                manager.launch_timeout_ms == 12_345
                for manager in FakeBrowserManager.instances
            )
        )
        await sessions.close_all()
        self.assertFalse(any(manager.is_open for manager in FakeBrowserManager.instances))

    async def test_close_all_attempts_every_provider_before_reporting(self) -> None:
        sessions = SessionManager(
            self.profile_root,
            browser_manager_factory=FakeBrowserManager,
        )
        await sessions.open("chatgpt")
        await sessions.open("gemini")
        FakeBrowserManager.instances[0].close_error = RuntimeError("synthetic close")

        with self.assertRaises(SessionCloseError) as raised:
            await sessions.close_all()

        self.assertEqual(raised.exception.providers, ("chatgpt",))
        self.assertFalse(FakeBrowserManager.instances[1].is_open)

    async def test_rejects_unknown_provider(self) -> None:
        sessions = SessionManager(
            self.profile_root,
            browser_manager_factory=FakeBrowserManager,
        )
        with self.assertRaisesRegex(UnknownProviderError, "Unsupported"):
            await sessions.open("unknown")

    def test_rejects_known_synchronized_profile_roots(self) -> None:
        synchronized = (
            Path.home()
            / "Library"
            / "CloudStorage"
            / "OneDrive-Example"
            / "browser-profiles"
        )
        with self.assertRaisesRegex(UnsafeProfileRootError, "outside OneDrive"):
            resolve_profile_root(synchronized)

    def test_reads_configurable_profile_root_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"POLICE_BENCH_BROWSER_PROFILE_ROOT": str(self.profile_root)},
        ):
            self.assertEqual(resolve_profile_root(), self.profile_root.resolve())

    def test_linux_default_uses_xdg_state_home(self) -> None:
        xdg_root = self.profile_root / "xdg-state"
        with patch("automation.browser.sessions.sys.platform", "linux"), patch(
            "automation.browser.sessions.os.name", "posix"
        ), patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg_root)}):
            self.assertEqual(
                resolve_profile_root(),
                (
                    xdg_root
                    / "police-report-llm-bench"
                    / "browser-profiles"
                ).resolve(),
            )


class ProfileLockProcessTests(unittest.TestCase):
    def test_same_profile_is_blocked_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_root = Path(temp_dir)
            helper = Path(__file__).parent / "helpers" / "profile_lock_holder.py"
            process = subprocess.Popen(
                [sys.executable, str(helper), str(profile_root), "chatgpt"],
                cwd=Path(__file__).resolve().parent.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = process.stdout.readline().strip()
                if ready != "LOCKED":
                    stderr = process.stderr.read()
                    self.fail(f"lock helper failed: {ready} {stderr}")

                manager = BrowserManager(profile_root, "chatgpt", headless=True)
                starter = Mock()
                starter.start = AsyncMock()
                with patch(
                    "automation.browser.manager.async_playwright",
                    return_value=starter,
                ):
                    with self.assertRaises(ProfileInUseError):
                        asyncio.run(manager.open())
                starter.start.assert_not_awaited()
            finally:
                if process.stdin is not None and process.poll() is None:
                    try:
                        process.stdin.write("release\n")
                        process.stdin.flush()
                    except BrokenPipeError:
                        pass
                    finally:
                        process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

            self.assertEqual(process.returncode, 0)


class SyntheticPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        content = b"<!doctype html><title>synthetic-session</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        return


@unittest.skipUnless(
    os.environ.get("RUN_BROWSER_SESSION_TESTS") == "1",
    "set RUN_BROWSER_SESSION_TESTS=1 to launch local Chromium sessions",
)
class SessionManagerBrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_root = Path(self.temp_dir.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticPageHandler)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}/"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.temp_dir.cleanup()

    async def test_provider_storage_is_isolated_and_persists_after_restart(self) -> None:
        first_run = SessionManager(self.profile_root, headless=True)
        for provider, value in (
            ("chatgpt", "chatgpt-value"),
            ("gemini", "gemini-value"),
        ):
            context = await first_run.open(provider)
            page = await context.new_page()
            await page.goto(self.url)
            await page.evaluate(
                "value => localStorage.setItem('synthetic-session', value)",
                value,
            )
        await first_run.close_all()

        second_run = SessionManager(self.profile_root, headless=True)
        observed = {}
        for provider in ("chatgpt", "gemini"):
            context = await second_run.open(provider)
            page = await context.new_page()
            await page.goto(self.url)
            observed[provider] = await page.evaluate(
                "localStorage.getItem('synthetic-session')"
            )
        await second_run.close_all()

        self.assertEqual(
            observed,
            {"chatgpt": "chatgpt-value", "gemini": "gemini-value"},
        )

    async def test_session_recovers_after_unexpected_context_close(self) -> None:
        sessions = SessionManager(self.profile_root, headless=True)
        first_context = await sessions.open("grok")
        await first_context.close()

        self.assertFalse(sessions.is_open("grok"))
        reopened_context = await sessions.open("grok")
        page = await reopened_context.new_page()
        await page.goto(self.url)
        self.assertEqual(await page.title(), "synthetic-session")
        await sessions.close_all()

        self.assertIsNot(first_context, reopened_context)


if __name__ == "__main__":
    unittest.main()
