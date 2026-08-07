"""Responsive audit across phone/tablet/desktop viewports."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).resolve().parent / "output"

VIEWPORTS = [("phone", 390, 844), ("tablet", 1024, 768), ("desktop", 1440, 900)]


def main() -> int:
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for name, w, h in VIEWPORTS:
            ctx = b.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            errs = []
            page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
            page.goto("https://mcso9tqzb9.tailb9395f.ts.net/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(7000)
            overflow = page.evaluate(
                "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
            sidebar_vis = page.evaluate(
                "() => { const s = document.querySelector('.desktop-sidebar'); return s ? s.getBoundingClientRect().width > 0 : false }")
            mob_btn = page.evaluate(
                "() => !!document.querySelector('[aria-label=\"Open navigation menu\"]')")
            nav_count = page.locator("nav.nav-list button.nav-button").count()
            print(
                f"{name}: {w}x{h} hscroll={overflow['sw']-overflow['cw']} "
                f"desktop_sidebar={sidebar_vis} mobile_menu_btn={mob_btn} "
                f"nav_count={nav_count} console_errors={len(errs)}")
            page.screenshot(path=str(OUT_DIR / f"responsive-{name}.png"))
            ctx.close()
    finally:
        b.close()
        p.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
