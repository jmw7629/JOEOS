"""Live route + control crawl for the deployed JoeOS shell.

Visits every top-level nav section, inventories DOM controls, clicks safe
controls, captures the resulting state, console/network errors. Writes a
machine-readable inventory to tests/browser/live_os_audit/output/.

Usage:
    python -m tests.browser.live_os_audit.crawl [--headed]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import Page

from .audit_harness import LiveAuditHarness, redact

OUT_DIR = Path(__file__).resolve().parent / "output"

DANGER_RE = re.compile(
    r"delete|revoke|disconnect|approve|deploy|push|sign|activate|destroy|wipe|"
    r"remove\b|clear\b|logout|sign out|send\b|submit approval", re.I
)


def is_safe_text(label: str) -> bool:
    return not bool(DANGER_RE.search(label))


def dom_inventory(page: Page) -> List[dict]:
    return page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();
          const walk = (root) => {
            root.querySelectorAll('a, button, [role="button"], [role="tab"], [role="menuitem"], input, select, [role="switch"], [role="checkbox"], [role="link"], [tabindex]').forEach((el) => {
              const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 120);
              const key = el.tagName + '|' + label + '|' + (el.getAttribute('href')||'');
              if (seen.has(key)) return;
              seen.add(key);
              const rect = el.getBoundingClientRect();
              out.push({
                tag: el.tagName,
                role: el.getAttribute('role') || '',
                label,
                href: el.getAttribute('href') || '',
                type: el.getAttribute('type') || '',
                name: el.getAttribute('name') || '',
                disabled: el.disabled || el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled'),
                visible: rect.width > 0 && rect.height > 0,
                selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''),
                ariaLabel: el.getAttribute('aria-label') || '',
              });
            });
          };
          walk(document);
          return out;
        }"""
    )


def screenshot(page: Page, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=False)


def crawl_section(harness: LiveAuditHarness, section_id: str, label: str) -> dict:
    page = harness.page
    result = {
        "section_id": section_id,
        "label": label,
        "controls": [],
        "network": [],
        "console_errors": [],
        "http_errors": [],
        "text_sample": "",
        "clicked": [],
    }
    # click nav item (nav buttons carry the section label as inner text)
    nav = page.locator("nav.nav-list button.nav-button", has_text=label).first
    if nav.count() == 0:
        nav = page.locator(f"[data-nav='{section_id}']").first
    if nav.count() == 0:
        result["note"] = "nav not found"
        return result
    nav.click()
    page.wait_for_timeout(2500)
    result["controls"] = dom_inventory(page)
    result["network"] = [r for r in harness.responses if any(
        m in r["url"] for m in ("/api/", "/sdk/"))]
    result["console_errors"] = list(harness.console_errors)
    result["http_errors"] = list(harness.http_errors)
    body = page.evaluate("() => document.body.innerText")
    result["text_sample"] = body[:2000]
    screenshot(page, f"section-{section_id}")

    # click safe controls: buttons/links within the active section
    buttons = page.locator("button, a[href], [role='button'], [role='tab']")
    count = buttons.count()
    clicked = 0
    for i in range(min(count, 60)):
        try:
            btn = buttons.nth(i)
            text = (btn.inner_text() or "").strip()[:80]
            if not text:
                continue
            if not is_safe_text(text):
                continue
            if text in ("↻", "⟳"):
                continue
            if text.startswith("Generate current brief"):
                continue
            if "Set up" in text or "Add " in text or "New " in text:
                continue
            btn.click(timeout=2000)
            clicked += 1
            page.wait_for_timeout(400)
            result["clicked"].append(text)
            if clicked >= 12:
                break
        except Exception:
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="live-os-crawl")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    harness = LiveAuditHarness(headless=not args.headed)
    try:
        harness.start()
        harness.open()
        page = harness.page

        nav_items = page.evaluate(
            """() => Array.from(document.querySelectorAll('nav.nav-list button.nav-button')).map(
                el => ({ id: (el.innerText||'').trim().split('\\n')[0].trim() || '',
                         label: (el.innerText||'').trim() }))"""
        )
        if not nav_items:
            nav_items = page.evaluate(
                """() => Array.from(document.querySelectorAll('nav a, aside a')).map(
                    el => ({ id: el.getAttribute('data-nav') || el.getAttribute('href') || '',
                             label: (el.innerText||'').trim() }))"""
            )
        print("NAV ITEMS:", json.dumps(nav_items, indent=2))

        results = []
        for item in nav_items:
            section_id = item["id"]
            if not section_id or section_id.startswith("javascript"):
                continue
            print(f"crawling section: {section_id} ({item['label']})")
            try:
                r = crawl_section(harness, section_id, item["label"])
            except Exception as exc:  # noqa: BLE001
                r = {"section_id": section_id, "label": item["label"],
                     "crawl_error": str(exc)}
                print(f"  section error: {exc}")
            results.append(r)
            harness.console_errors = []
            harness.http_errors = []
            harness.responses = []

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / "crawl_results.json"
        out_path.write_text(json.dumps(results, indent=2, default=str))
        print(f"wrote {out_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("CRAWL FATAL:", exc)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_DIR.joinpath("crawl_results.json").write_text(
            json.dumps(results, indent=2, default=str))
        return 1
    finally:
        harness.stop()


if __name__ == "__main__":
    sys.exit(main())
