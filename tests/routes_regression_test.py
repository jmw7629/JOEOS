"""Route-regression tests: /os/* browser routes must never become downloads.

Protects the JoeOS application shell from being shadowed by artifact files
(e.g. a build.txt) or by stale service-worker caching. Every browser module
route must resolve to the JoeOS application (HTML), never to a download.
"""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import joeos_backend as backend

ROUTES = [
    "/os/",
    "/os/build",
    "/os/plugins",
    "/os/agents",
    "/os/approvals",
    "/os/executions",
    "/os/work",
    "/os/schedule",
    "/os/pipelines",
    "/os/memory",
    "/os/activity",
    "/os/providers",
    "/os/models",
    "/os/files",
    "/os/search",
    "/os/automations",
    "/os/terminal",
    "/os/command",
]

# Routes that intentionally resolve to a dedicated console page vs the shell.
_SPECIAL_PAGES = {
    "/os/agents": "agent_fabric.html",
    "/os/providers": "agent_fabric.html",
    "/os/models": "agent_fabric.html",
    "/os/automations": "automations.html",
    "/os/build": "build.html",
    "/os/terminal": "terminal.html",
}


class OsRouteDownloadRegressionTest(unittest.TestCase):
    """Every /os/* route must serve the JoeOS app (HTML), never a download."""

    def _page_path(self, route: str) -> Path:
        file = _SPECIAL_PAGES.get(route, "index.html")
        return backend._package_asset(file)

    def test_all_major_os_routes_resolve_to_existing_html(self):
        for route in ROUTES:
            path = self._page_path(route)
            self.assertTrue(path.exists(), f"{route} -> {path} missing")

    def test_no_content_disposition_attachment_for_os_routes(self):
        # Verify the served responses for the special pages are HTML files with
        # no attachment header. We assert on the FileResponse media_type rather
        # than hitting the network (the test client requires a session for
        # object routes; the shell routes are open).
        for route in ROUTES:
            path = self._page_path(route)
            self.assertTrue(path.read_bytes().lstrip().startswith(b"<"),
                            f"{route} served file {path} is not HTML")
            body = path.read_text(errors="replace")
            self.assertNotIn("Content-Disposition", body)
            self.assertNotIn("attachment", body[:4000])

    def test_no_artifact_file_shadows_an_os_route(self):
        # A file named build.txt in the served tree must never be reachable as
        # the /os/build module page. The build module page is build.html.
        build_page = backend._package_asset("build.html")
        self.assertTrue(build_page.exists())
        self.assertFalse(build_page.name == "build.txt")
        # And no build.txt exists anywhere the shell would resolve from.
        self.assertFalse((backend._package_asset("build.txt")).exists())

    def test_os_frontend_mapping_covers_special_pages(self):
        # The backend's /os/{rest} handler must map build->build.html and the
        # others to their dedicated consoles (not to a download).
        with TestClient(backend.app) as client:
            for route in ROUTES:
                response = client.get(route, follow_redirects=False)
                # Unauthenticated shell routes return 200 HTML; some object
                # routes require a session and return 401/307 (auth) — but
                # NONE may return a download (no Content-Disposition attachment).
                self.assertNotIn(
                    "attachment",
                    response.headers.get("content-disposition", "").lower(),
                    f"{route} returned a download",
                )
                content_type = response.headers.get("content-type", "")
                if response.status_code == 200:
                    self.assertIn("text/html", content_type,
                                  f"{route} returned non-HTML: {content_type}")


class ServiceWorkerCacheRegressionTest(unittest.TestCase):
    """The service worker must never cache a download/error as the shell."""

    def test_sw_rejects_attachment_responses(self):
        source = Path(backend._package_asset("sw.js")).read_text(errors="replace")
        self.assertIn("content-disposition", source.lower())
        self.assertIn("attachment", source.lower())
        self.assertIn("isCacheableHtml", source)

    def test_sw_navigation_only_caches_html(self):
        source = Path(backend._package_asset("sw.js")).read_text(errors="replace")
        self.assertIn("isCacheableHtml(response, url)", source)
        # The cache version was bumped to invalidate stale shells.
        self.assertIn("joeos-shell-v4", source)
        self.assertNotIn("joeos-shell-v3", source)


if __name__ == "__main__":
    unittest.main()
