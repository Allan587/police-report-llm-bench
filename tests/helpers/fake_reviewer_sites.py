"""Loopback-only pages with synthetic, deliberately non-provider DOM variants."""
from __future__ import annotations

import html
import json
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class FakeSiteServer:
    def __init__(self):
        self.scenarios: dict[tuple[str, str], str] = {}
        self.artifacts: dict[tuple[str, str], object] = {}
        self.visits: list[tuple[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = [unquote(part) for part in urlsplit(self.path).path.strip("/").split("/")]
                if len(parts) != 2 or parts[0] not in {"chatgpt", "gemini", "grok"}:
                    self.send_error(404)
                    return
                reviewer, slot = parts
                owner.visits.append((reviewer, slot))
                scenario = owner.scenarios.get((reviewer, slot), "complete")
                if scenario == "redirect_external":
                    self.send_response(302)
                    self.send_header("Location", "https://example.org/blocked")
                    self.end_headers()
                    return
                if scenario == "redirect_local":
                    self.send_response(302)
                    self.send_header("Location", "/unexpected")
                    self.end_headers()
                    return
                artifact = owner.artifacts.get((reviewer, slot))
                payload = json.dumps(artifact, ensure_ascii=False) if artifact is not None else ""
                payload_js = json.dumps(payload).replace("<", "\\u003c")
                # Identical data-role contract; wrappers differ for each fake site.
                wrappers = {
                    "chatgpt": ("section", "textarea", "button"),
                    "gemini": ("main", "textarea", "button"),
                    "grok": ("article", "input", "button"),
                }
                wrapper, entry, button = wrappers[reviewer]
                body = f'''<!doctype html><html><head><title>fake {html.escape(reviewer)}</title></head>
                <body><{wrapper} data-fake="{reviewer}"><{entry} data-role="prompt"></{entry}>
                <{button} data-role="submit">Send synthetic</{button}>
                <pre data-role="response" data-state="idle"></pre></{wrapper}>
                <script>
                const scenario = {json.dumps(scenario)};
                const payload = {payload_js};
                const output = document.querySelector('[data-role="response"]');
                document.querySelector('[data-role="submit"]').onclick = event => {{
                    event.target.dataset.clicks = String(Number(event.target.dataset.clicks || 0) + 1);
                    output.dataset.state = 'waiting';
                    const change = (state, text) => {{ output.dataset.state = state; output.textContent = text; }};
                    if (scenario === 'complete') setTimeout(() => change('complete', payload), 20);
                    if (scenario === 'streaming') {{
                        setTimeout(() => change('partial', payload.slice(0, 12)), 20);
                        setTimeout(() => change('complete', payload), 150);
                    }}
                    if (scenario === 'partial') setTimeout(() => change('partial', payload.slice(0, 12)), 20);
                    if (scenario === 'error') setTimeout(() => change('error', 'synthetic UI error'), 20);
                    if (scenario === 'expired') setTimeout(() => change('expired', 'synthetic expiry'), 20);
                    if (scenario === 'auth') setTimeout(() => change('auth', 'synthetic auth'), 20);
                    if (scenario === 'uncertain') setTimeout(() => change('partial', payload.slice(0, 12)), 20);
                }};
                </script></body></html>'''
                encoded = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def structured_bundle(artifacts: dict[str, dict], identity: dict) -> dict:
    """Fixture grammar, deliberately unrelated to provider DOM structures."""
    components = []
    for slot, output in artifacts.items():
        canonical = json.dumps(output, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        components.append({
            "slot": slot, "identity": dict(identity), "output": output,
            "content_sha256": hashlib.sha256(canonical).hexdigest(),
        })
    return {"schema_version": 1, "components": components}
