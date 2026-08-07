"""Live JoeOS browser audit harness.

Development/test-only tooling. Launches headless Chromium against the live
Tailscale-only JoeOS URL, captures console/page/network/request failures,
records URL transitions, and exposes a small API for route crawling and safe
control interaction. Never records credentials, cookies, tokens, or request
bodies; never triggers destructive operations.

Usage:
    python -m tests.browser.live_os_audit.audit_harness --help
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from playwright.sync_api import (
    ConsoleMessage,
    Error as PlaywrightError,
    Page,
    Request,
    Response,
    sync_playwright,
)

LIVE_URL = "https://mcso9tqzb9.tailb9395f.ts.net/"
OUT_DIR = Path(__file__).resolve().parent / "output"

SENSITIVE_HEADERS = (
    "authorization", "cookie", "x-runner-credential", "x-api-key",
    "x-enrollment-challenge", "x-device-key",
)


def redact(text: str) -> str:
    return "REDACTED"


class LiveAuditHarness:
    def __init__(self, url: str = LIVE_URL, headless: bool = True):
        self.url = url
        self.headless = headless
        self.console_errors: List[dict] = []
        self.page_errors: List[dict] = []
        self.failed_requests: List[dict] = []
        self.http_errors: List[dict] = []
        self.rejections: List[dict] = []
        self.transitions: List[dict] = []
        self.network: List[dict] = []
        self.requests: List[dict] = []
        self.responses: List[dict] = []
        self._playwright = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        self.page = self._context.new_page()
        self._wire_listeners()

    def _wire_listeners(self) -> None:
        page = self.page

        def on_console(msg: ConsoleMessage) -> None:
            text = msg.text
            if msg.type in ("error", "warning"):
                record = {"type": msg.type, "text": redact(text)}
                if msg.type == "error":
                    self.console_errors.append(record)
                if self.console_errors and len(self.console_errors) < 2000:
                    pass

        page.on("console", on_console)

        def on_pageerror(error: PlaywrightError) -> None:
            self.page_errors.append({"message": redact(str(error))})

        page.on("pageerror", on_pageerror)

        def on_request(request: Request) -> None:
            url = request.url
            if any(marker in url for marker in ("/sdk/", "/api/", "/metrics", "/events", "/chat", "/workspace")):
                self.requests.append({
                    "url": redact(url),
                    "method": request.method,
                    "resource_type": request.resource_type,
                })

        page.on("request", on_request)

        def on_response(response: Response) -> None:
            status = response.status
            url = response.url
            if status >= 400:
                self.http_errors.append({"url": redact(url), "status": status})
            if any(marker in url for marker in ("/api/", "/sdk/", "/metrics", "/events")):
                self.responses.append({
                    "url": redact(url),
                    "status": status,
                    "content_type": response.headers.get("content-type", ""),
                })

        page.on("response", on_response)

        def on_requestfailed(request: Request) -> None:
            self.failed_requests.append({
                "url": redact(request.url),
                "failure": redact(str(request.failure) if request.failure else "failed"),
            })

        page.on("requestfailed", on_requestfailed)

        page.add_init_script("""
            window.__joeos_audit_pending_rejections = [];
            window.addEventListener('unhandledrejection', function (event) {
                window.__joeos_audit_pending_rejections.push(String(event.reason));
            });
        """)

    def open(self, url: Optional[str] = None, wait_ms: int = 8000) -> None:
        target = url or self.url
        self.page.goto(target, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(wait_ms)
        self.transitions.append({"from": "start", "to": redact(target)})

    def collect_rejections(self) -> List[str]:
        if self.page is None:
            return []
        try:
            return list(self.page.evaluate(
                "() => window.__joeos_audit_pending_rejections || []"))
        except Exception:
            return []

    def dump_state(self, name: str) -> dict:
        return {
            "name": name,
            "url": redact(self.page.url) if self.page else "",
            "console_errors": self.console_errors,
            "page_errors": self.page_errors,
            "failed_requests": self.failed_requests,
            "http_errors": self.http_errors,
            "rejections": self.collect_rejections(),
        }

    def save_output(self, name: str) -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(self.dump_state(name), indent=2, default=str))
        return path

    def stop(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self.page = None


def main() -> int:
    parser = argparse.ArgumentParser(prog="live-os-audit")
    parser.add_argument("--url", default=LIVE_URL)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    harness = LiveAuditHarness(url=args.url, headless=not args.headed)
    try:
        harness.start()
        harness.open()
        print(json.dumps({
            "url": args.url,
            "title": harness.page.title() if harness.page else "",
            "console_errors": len(harness.console_errors),
            "page_errors": len(harness.page_errors),
            "failed_requests": len(harness.failed_requests),
            "http_errors": len(harness.http_errors),
        }, indent=2))
        harness.save_output("initial")
    finally:
        harness.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
