#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import backend as app

PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/home.js": ("home.js", "application/javascript; charset=utf-8"),
    "/home-inspector.js": ("home-inspector.js", "application/javascript; charset=utf-8"),
}

GET_API_PATHS = frozenset(
    {
        "/healthz",
        "/api/session",
        "/api/tasks",
        "/api/projects",
        "/api/intelligence",
        "/api/activity",
        "/api/models",
        "/api/agents",
        "/api/runs",
        "/api/settings",
        "/api/notifications",
        "/api/team",
        "/api/memory",
        "/api/chat",
        "/api/help",
    }
)
RUN_TERMINAL_RE = re.compile(r"^/api/runs/[^/]+/terminal$")


class SafeHandler(app.H):
    """Production HTTP boundary.

    The application backend still owns API behavior, but no request is allowed to
    fall through to SimpleHTTPRequestHandler's runtime-directory file server.
    """

    def _not_found(self):
        return self.sendj({"error": "not found"}, 404)

    def _send_file(self, path: Path, content_type: str, *, head: bool = False):
        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
            if not resolved.is_file():
                return self._not_found()
        except (FileNotFoundError, OSError):
            return self._not_found()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        if head:
            return
        with resolved.open("rb") as stream:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _public_file(self, path: str, *, head: bool = False):
        item = PUBLIC_FILES.get(path)
        if not item:
            return self._not_found()
        name, content_type = item
        candidate = (app.ROOT / name).resolve()
        try:
            if candidate.parent != app.ROOT.resolve():
                return self._not_found()
        except OSError:
            return self._not_found()
        return self._send_file(candidate, content_type, head=head)

    def _attachment_file(self, path: str, *, head: bool = False):
        stored = path[len("/uploads/") :]
        if (
            not stored
            or "/" in stored
            or "\\" in stored
            or Path(stored).name != stored
            or len(stored) > 180
        ):
            return self._not_found()

        try:
            with app.con() as conn:
                record = conn.execute(
                    "select stored_name,content_type from attachments where stored_name=? limit 1",
                    (stored,),
                ).fetchone()
        except Exception:
            return self._not_found()
        if not record:
            return self._not_found()

        uploads_root = app.UPLOADS.resolve()
        candidate = (app.UPLOADS / stored).resolve()
        if candidate.parent != uploads_root:
            return self._not_found()
        content_type = record["content_type"] or "application/octet-stream"
        return self._send_file(candidate, content_type, head=head)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in PUBLIC_FILES:
            return self._public_file(path)
        if path.startswith("/uploads/"):
            return self._attachment_file(path)
        if path in GET_API_PATHS or RUN_TERMINAL_RE.fullmatch(path):
            return app.H.do_GET(self)
        return self._not_found()

    def do_HEAD(self):
        path = unquote(urlparse(self.path).path)
        if path in PUBLIC_FILES:
            return self._public_file(path, head=True)
        if path.startswith("/uploads/"):
            return self._attachment_file(path, head=True)
        return self._not_found()


if __name__ == "__main__":
    app.init_db()
    threading.Thread(target=app.sync_loop, daemon=True).start()
    host = os.getenv("KANBAN_HOST", "127.0.0.1")
    port = int(os.getenv("KANBAN_PORT", "8094"))
    print(f"PROJECT_BYTE v4 listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), SafeHandler).serve_forever()
