"""Diagnose or prepare one portable local review environment."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from .browser import BrowserSessionFactory
from .config import PortableConfigError, load_config
from .inputs import InputAdapter


async def _browser_smoke(config) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            raise PortableConfigError("Chromium is missing; run python -m playwright install chromium")
    with tempfile.TemporaryDirectory(prefix="portable-browser-") as temporary:
        factory = BrowserSessionFactory(config.browser, profile_root=Path(temporary))
        manager = factory.create("doctor", "chatgpt")
        try:
            try:
                context = await manager.open()
            except RuntimeError as error:
                raise PortableConfigError(
                    "Chromium could not start; check installation, OS dependencies and local permissions"
                ) from error
            page = await context.new_page()
            await page.goto("data:text/html,<title>portable-doctor</title>")
            if await page.title() != "portable-doctor":
                raise PortableConfigError("Chromium local page check failed")
        finally:
            await manager.close()


def doctor(config) -> dict:
    checks = {}
    if sys.version_info[:2] != (3, 11):
        raise PortableConfigError("Python 3.11 is required")
    checks["python"] = sys.version.split()[0]
    if importlib.util.find_spec("playwright") is None:
        raise PortableConfigError("Playwright missing; install requirements-dev.txt")
    try:
        version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError as error:
        raise PortableConfigError("Playwright is not installed correctly") from error
    if version != "1.60.0":
        raise PortableConfigError("Playwright 1.60.0 is required; install requirements-automation.txt")
    checks["playwright"] = version
    inspected = InputAdapter(config).inspect()
    checks["cases"] = len(inspected.cases)
    parent = config.output_root if config.output_root.exists() else config.output_root.parent
    if config.output_root.exists() and not config.output_root.is_dir():
        raise PortableConfigError("output root is not a directory")
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise PortableConfigError("output ancestor is not a directory")
    try:
        with tempfile.TemporaryFile(dir=parent):
            pass
    except OSError as error:
        raise PortableConfigError("output parent is not writable") from error
    checks["output_writable"] = True
    asyncio.run(_browser_smoke(config))
    checks["chromium_local_page"] = "PASS"
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "prepare"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            result = doctor(config)
        else:
            prepared = InputAdapter(config).prepare()
            result = {"cases": len(prepared.cases), "source_sha256": prepared.source_sha256,
                      "output_root": str(config.output_root)}
        print(json.dumps({"status": "PASS", **result}, sort_keys=True))
        return 0
    except (PortableConfigError, OSError, RuntimeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
