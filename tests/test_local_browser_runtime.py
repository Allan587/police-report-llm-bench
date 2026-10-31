import asyncio
import base64
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from automation.browser.local_runtime import (
    BrowserOperationState,
    LocalBrowserError,
    LocalBrowserRuntime,
    LocalBrowserTimeouts,
    NavigationFailedError,
    UnsafeNavigationError,
)
from automation.browser.sessions import UnsafeProfileRootError
from automation.contracts import Reviewer


class LocalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(0.3)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/blocked")
            self.end_headers()
            return
        body = b"<html><title>synthetic</title><div id='ready'>ready</div></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, *_args):
        return


class WebSocketProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/forbidden" and self.headers.get("Upgrade", "").lower() == "websocket":
            self.server.connections += 1
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            return
        self.send_error(404)

    def log_message(self, *_args):
        return


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def websocket_probe():
    server = ThreadingHTTPServer(("127.0.0.1", 0), WebSocketProbeHandler)
    server.connections = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
async def test_indirect_websocket_is_blocked_before_server_connection(
    tmp_path, local_server, websocket_probe, host
):
    target = f"ws://{host}:{websocket_probe.server_port}/forbidden"
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.CHATGPT)
        await browser.navigate(local_server + "/ok")
        outcome = await browser.page.evaluate("""url => new Promise(resolve => {
            const socket = new WebSocket(url);
            const timer = setTimeout(() => resolve(`timeout:${socket.readyState}`), 2000);
            socket.onopen = () => { clearTimeout(timer); resolve('open'); socket.close(); };
            socket.onerror = () => { clearTimeout(timer); resolve('error'); };
            socket.onclose = () => { clearTimeout(timer); resolve('closed'); };
        })""", target)
        assert websocket_probe.connections == 0, (outcome, websocket_probe.connections)
        assert outcome in {"error", "closed"}
        assert browser.state is BrowserOperationState.READY
    assert browser.page.is_closed()
    assert websocket_probe.connections == 0


@pytest.mark.asyncio
async def test_open_navigate_isolate_and_close(tmp_path, local_server):
    runtime = LocalBrowserRuntime(tmp_path, timeouts=LocalBrowserTimeouts(2000, 200))
    async with runtime:
        chatgpt = await runtime.open(Reviewer.CHATGPT)
        gemini = await runtime.open(Reviewer.GEMINI)
        await chatgpt.navigate(local_server + "/ok")
        await gemini.navigate(local_server + "/ok")
        assert await chatgpt.page.title() == "synthetic"
        await chatgpt.page.evaluate("document.cookie = 'isolation=chatgpt'")
        assert "isolation=chatgpt" not in await gemini.page.evaluate("document.cookie")
        assert await runtime.open(Reviewer.CHATGPT) is chatgpt
        assert chatgpt.state is BrowserOperationState.READY
    assert chatgpt.page.is_closed()
    assert gemini.page.is_closed()


@pytest.mark.asyncio
async def test_navigation_guard_blocks_external_and_redirect(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.GROK)
        for url in ("https://example.invalid", "file:///etc/passwd", "http://localhost:1234"):
            with pytest.raises(UnsafeNavigationError):
                await browser.navigate(url)
        with pytest.raises(NavigationFailedError):
            await browser.navigate(local_server + "/redirect")
        assert browser.state is BrowserOperationState.FAILED


@pytest.mark.asyncio
async def test_navigation_timeout_and_sanitized_error(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path, timeouts=LocalBrowserTimeouts(50, 50)) as runtime:
        browser = await runtime.open(Reviewer.CHATGPT)
        secret = "secret-local-token"
        with pytest.raises(NavigationFailedError) as raised:
            await browser.navigate(local_server + "/slow?token=" + secret)
        assert secret not in str(raised.value)
        assert browser.state is BrowserOperationState.FAILED


@pytest.mark.asyncio
async def test_manual_session_expiry_and_response_states(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path, timeouts=LocalBrowserTimeouts(1000, 50)) as runtime:
        browser = await runtime.open(Reviewer.GEMINI)
        await browser.navigate(local_server + "/ok")
        browser.require_user()
        assert browser.state is BrowserOperationState.WAITING_FOR_USER
        browser.user_resumed()
        browser.begin_response_wait()
        assert browser.state is BrowserOperationState.WAITING_FOR_RESPONSE
        await browser.wait_for_local_selector("#ready")
        assert browser.state is BrowserOperationState.READY
        browser.begin_response_wait()
        with pytest.raises(LocalBrowserError, match="uncertain"):
            await browser.wait_for_local_selector("#absent")
        assert browser.state is BrowserOperationState.RESPONSE_UNCERTAIN
        with pytest.raises(LocalBrowserError):
            browser.begin_response_wait()


@pytest.mark.asyncio
async def test_unexpected_close_after_possible_send_is_uncertain(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.GROK)
        await browser.navigate(local_server + "/ok")
        browser.begin_response_wait()
        await browser.context.close()
        assert browser.state is BrowserOperationState.RESPONSE_UNCERTAIN
        await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_unexpected_close_before_send_is_failed(tmp_path):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.CHATGPT)
        await browser.context.close()
        assert browser.state is BrowserOperationState.FAILED
        await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_explicit_cancel_releases_context(tmp_path):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.CHATGPT)
        await browser.cancel()
        assert browser.state is BrowserOperationState.CANCELLED
        assert browser.page.is_closed()


@pytest.mark.asyncio
async def test_task_cancellation_of_wait_is_uncertain_and_cleans_up(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.CHATGPT)
        await browser.navigate(local_server + "/ok")
        browser.begin_response_wait()
        pending = asyncio.create_task(browser.wait_for_local_selector("#never"))
        await asyncio.sleep(0.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert browser.state is BrowserOperationState.RESPONSE_UNCERTAIN
        assert browser.page.is_closed()


def test_timeouts_reject_invalid_values():
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            LocalBrowserTimeouts(navigation_ms=value)


@pytest.mark.asyncio
async def test_cancel_navigation_and_reopen_after_loss(tmp_path, local_server):
    async with LocalBrowserRuntime(tmp_path, timeouts=LocalBrowserTimeouts(1000, 100)) as runtime:
        browser = await runtime.open(Reviewer.GROK)
        pending = asyncio.create_task(browser.navigate(local_server + "/slow"))
        await asyncio.sleep(0.05)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert browser.state is BrowserOperationState.CANCELLED
        assert browser.page.is_closed()
        reopened = await runtime.open(Reviewer.GROK)
        assert reopened is not browser
        await reopened.navigate(local_server + "/ok")
        assert reopened.state is BrowserOperationState.READY



def test_rejects_profile_root_inside_git_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    with pytest.raises(UnsafeProfileRootError):
        LocalBrowserRuntime(checkout / "browser-state")



@pytest.mark.asyncio
async def test_cancel_after_possible_send_remains_uncertain(tmp_path):
    async with LocalBrowserRuntime(tmp_path) as runtime:
        browser = await runtime.open(Reviewer.GEMINI)
        browser.begin_response_wait()
        await browser.cancel()
        assert browser.state is BrowserOperationState.RESPONSE_UNCERTAIN
        assert browser.page.is_closed()
