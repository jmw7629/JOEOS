"""Per-section network trace for the deployed JoeOS shell.

For each nav section, records every network request the app makes and the
resulting visible state, so we can classify REAL_BACKEND vs STATIC/FIXTURE.
Writes tests/browser/live_os_audit/output/section_trace.json.

Usage:
    python -m tests.browser.live_os_audit.section_trace
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Request

from .audit_harness import LiveAuditHarness, redact

OUT_DIR = Path(__file__).resolve().parent / "output"

NAV_LABELS = [
    "Mission Control", "Executive Dashboard", "CI/CD Pipeline",
    "Infrastructure Health", "Security & Logs", "Plugin Manager",
    "Automation", "Communications", "Device Manager", "Mobile Companion",
    "Security Center", "Performance Center", "Models & AI",
    "Production & Release", "Maintenance & Improvement", "Settings",
    "Bot Fleet",
]


def main() -> int:
    harness = LiveAuditHarness(headless=True)
    results = []
    try:
        harness.start()
        harness.open()
        page = harness.page

        for label in NAV_LABELS:
            print(f"tracing: {label}", flush=True)
            requests_this_section = []

            def _on_request(request: Request) -> None:
                requests_this_section.append({
                    "method": request.method,
                    "url": redact(request.url),
                })

            page.on("request", _on_request)
            try:
                # Dismiss any open overlay/drawer/palette/assistant first.
                page.evaluate("""() => {
                  document.querySelectorAll(
                    '[aria-label="Close"], [aria-label="Close navigation menu"], .dialog-close, .modal-close, .drawer-close, .assistant-toggle, [aria-label="Close assistant"]'
                  ).forEach((b) => { try { b.click(); } catch (e) {} });
                  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                }""")
                page.wait_for_timeout(500)
                nav = page.locator(
                    "nav.nav-list button.nav-button", has_text=label).first
                nav.scroll_into_view_if_needed(timeout=5000)
                nav.click(timeout=5000)
            except Exception as exc:  # noqa: BLE001
                results.append({"label": label, "nav_error": str(exc)[:300]})
                page.remove_listener("request", _on_request)
                page.evaluate("() => { const m = document.querySelector('.mobile-drawer button.icon-button, [aria-label=\"Close navigation menu\"]'); if (m) m.click(); }")
                page.wait_for_timeout(1000)
                continue

            page.wait_for_timeout(4000)
            page.remove_listener("request", _on_request)

            text = page.evaluate("() => document.body.innerText")
            section_heading = page.evaluate(
                "() => { const h = document.querySelector('main h1, main h2, [class*=\"section-title\"]'); return h ? h.innerText : ''; }"
            )
            results.append({
                "label": label,
                "heading": section_heading[:200],
                "requests": requests_this_section,
                "text_sample": text[:1500],
            })

            # reset event listeners for next section by reload
            page.reload()
            page.wait_for_timeout(3500)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / "section_trace.json"
        out.write_text(json.dumps(results, indent=2, default=str))
        print(f"wrote {out}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("FATAL:", exc)
        return 1
    finally:
        harness.stop()


if __name__ == "__main__":
    sys.exit(main())
