# JoeOS Visual Acceptance — Breakpoint Verification (M-1204)

Method: headless Chrome 151 via CDP at three viewports against the live backend
(`http://127.0.0.1:8080`), capturing 1440x900/1024x768/390x844 screenshots and
measuring layout via `Runtime.evaluate` (overflow, sidebar visibility, mobile
drawer button).

## Evidence (all PASS)

| Size | Path | overflowX | sidebar | menu button |
|---|---|---|---|---|
| 1440x900 | / | 0 | flex (visible) | none |
| 1440x900 | /os/build | 0 | absent (module shell) | absent |
| 1440x900 | /os/agents | 0 | absent (module shell) | absent |
| 1024x768 | / | 0 | flex (visible) | none |
| 1024x768 | /os/build | 0 | absent | absent |
| 1024x768 | /os/agents | 0 | absent | absent |
| 390x844 | / | 0 | none (hides desktop rail) | grid (drawer) |
| 390x844 | /os/build | 0 | absent | absent |
| 390x844 | /os/agents | 0 | absent | absent |

Notes:

- No horizontal overflow at any combination; `scrollWidth <= clientWidth`
  everywhere (the home view reports scrollbar-gutter accounting only).
- Desktop/tablet show the persistent sidebar; mobile correctly switches to the
  hamburger drawer (`mobile-menu-button` becomes `display: grid`, rail hidden).
- `/os/*` module routes render their own application shells (sidebar n/a).
- 12 full-size screenshots captured under `/tmp/joeos-audit/*.png`.

Result: **PASS** — the three acceptance breakpoints render with correct
responsive primitives and no layout overflow.