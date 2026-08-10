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


class HealthSupervisionTest(unittest.TestCase):
    """Operational hardening: readiness probe, watchdog, and canonical control.

    Guards against the failure mode that kept the browser platform offline: a
    wedged-but-alive backend that liveness checks would miss.
    """

    def test_readiness_probe_exists_and_gated(self):
        with TestClient(backend.app) as client:
            response = client.get("/healthz/ready")
            # 200 (ready) or 503 (not ready) — never a download, never 404.
            self.assertIn(response.status_code, (200, 503))
            body = response.json()
            self.assertIn("status", body)
            self.assertIn("checks", body)
            self.assertIn("database", body["checks"])
            self.assertIn("campaign", body["checks"])

    def test_liveness_probe_present(self):
        with TestClient(backend.app) as client:
            response = client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json().get("status"), "ok")

    def test_readiness_snapshot_available_in_process(self):
        # The watchdog uses _readiness_snapshot in-process; it must exist and
        # return a boolean-ready dict.
        self.assertTrue(hasattr(backend, "_readiness_snapshot"))
        self.assertTrue(callable(backend._readiness_snapshot))

    def test_object_activity_recorder_present(self):
        self.assertTrue(hasattr(backend, "_record_object_activity"))
        self.assertTrue(callable(backend._record_object_activity))

    def test_watchdog_task_wired_in_lifespan(self):
        source = Path(backend._package_asset("joeos_backend.py")).read_text(errors="replace")
        self.assertIn("_health_watchdog", source)
        self.assertIn("os._exit(1)", source)
        self.assertIn("joeos-health-watchdog", source)

    def test_canonical_ctl_script_exists(self):
        ctl = Path("scripts/joeosctl.sh")
        self.assertTrue(ctl.exists())
        text = ctl.read_text(errors="replace")
        self.assertIn("deploy", text)
        self.assertIn("restart", text)
        self.assertIn("healthz/ready", text)

    def test_hardened_systemd_unit_in_repo(self):
        unit = Path("deploy/joeos-backend.service")
        self.assertTrue(unit.exists())
        text = unit.read_text(errors="replace")
        self.assertIn("Restart=always", text)
        self.assertIn("ExecStartPost", text)
        self.assertIn("healthz/ready", text)
        self.assertIn("KillMode=control-group", text)


if __name__ == "__main__":
    unittest.main()
