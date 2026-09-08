"""Owner-only bridge to real OpenCode 1.18.29 V1 permission requests.

No runner launch, prompt, policy change, merge, or automatic retry lives here.
The HTTP boundary authenticates the owner before constructing Gateway.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import urlencode, urlsplit

VERSION = "1.18.29"
ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


class PermissionError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def private_file(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("private regular file required")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversize")
    return raw


class Gateway:
    def __init__(self, root, hold=lambda repo: None, clock=time.time):
        self.root = Path(root).resolve()
        self.hold = hold
        self.clock = clock
        self.config = self.read_config()

    def read_config(self):
        path = self.root / "execution-permissions.json"
        try:
            raw = private_file(path, 16384)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise PermissionError("Permission runner registration is invalid or unavailable") from None
        try:
            cfg = decode(raw)
            fields = {"schema_version", "endpoint", "directory", "session_id", "session_created",
                      "project_id", "repository", "generation", "password_env", "ttl_seconds"}
            if not isinstance(cfg, dict) or set(cfg) != fields or type(cfg["schema_version"]) is not int or cfg["schema_version"] != 1:
                raise ValueError()
            if any(not isinstance(cfg[k], str) for k in fields - {"schema_version", "session_created", "ttl_seconds"}):
                raise ValueError()
            endpoint = urlsplit(cfg["endpoint"])
            if (endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "::1")
                    or endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment
                    or not endpoint.port):
                raise ValueError()
            host = "[::1]" if endpoint.hostname == "::1" else "127.0.0.1"
            if cfg["endpoint"] != "http://" + host + ":" + str(endpoint.port):
                raise ValueError()
            for field in ("session_id", "project_id", "generation"):
                if not isinstance(cfg[field], str) or not ID.fullmatch(cfg[field]):
                    raise ValueError()
            if len(cfg["generation"]) < 16 or type(cfg["session_created"]) is not int or cfg["session_created"] <= 0:
                raise ValueError()
            if type(cfg["ttl_seconds"]) is not int or not 30 <= cfg["ttl_seconds"] <= 600:
                raise ValueError()
            directory = Path(cfg["directory"])
            if not directory.is_absolute() or str(directory.resolve(strict=True)) != str(directory) or not directory.is_dir():
                raise ValueError()
            repo = cfg["repository"]
            if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise ValueError()
            # This integration must never take over the separate or owner-paused workers.
            excluded = ("stickdeath", "vitros", "project-byte-live-builder", "verifier-sandboxes")
            if any(word in (repo + "/" + str(directory)).lower() for word in excluded):
                raise ValueError()
            if not re.fullmatch(r"PB_PERMISSION_[A-Z0-9_]{1,80}", cfg["password_env"]):
                raise ValueError()
            secret = os.environ.get(cfg["password_env"], "")
            if not 24 <= len(secret) <= 512 or any(ord(c) < 32 for c in secret):
                raise ValueError()
            cfg["_secret"] = secret
            cfg["_binding"] = digest(cfg)
            return cfg
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            raise PermissionError("Permission runner registration is invalid or unavailable") from None

    def check_hold(self):
        marker = self.root / "PERMISSIONS_STOPPED_BY_OWNER"
        try:
            marker.lstat()
            held = True
        except FileNotFoundError:
            held = False
        if held or self.hold(self.config["repository"]):
            raise PermissionError("Execution permission decisions are held by the owner", 423)

    def call(self, method, path, body=None):
        cfg = self.config
        endpoint = urlsplit(cfg["endpoint"])
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=8)
        token = base64.b64encode(("opencode:" + cfg["_secret"]).encode()).decode()
        headers = {"Authorization": "Basic " + token, "Content-Type": "application/json", "Accept": "application/json"}
        try:
            connection.request(method, path + "?" + urlencode({"directory": cfg["directory"]}),
                               None if body is None else canonical(body), headers)
            response = connection.getresponse()
            raw = response.read(262145)
            if len(raw) > 262144 or response.status != 200:
                raise PermissionError("Runner request is no longer pending" if response.status == 404 else "Permission runner is unavailable",
                                      409 if response.status == 404 else 503)
            return decode(raw)
        except PermissionError:
            raise
        except (OSError, http.client.HTTPException, ValueError, RecursionError):
            raise PermissionError("Permission runner response is unavailable") from None
        finally:
            connection.close()

    def pending(self):
        self.check_hold()
        cfg = self.config
        health = self.call("GET", "/global/health")
        if not isinstance(health, dict) or health.get("healthy") is not True or health.get("version") != VERSION:
            raise PermissionError("Permission runner version or health does not match registration")
        session = self.call("GET", "/session/" + cfg["session_id"])
        if (not isinstance(session, dict) or session.get("id") != cfg["session_id"]
                or session.get("directory") != cfg["directory"] or session.get("projectID") != cfg["project_id"]
                or not isinstance(session.get("time"), dict)
                or session["time"].get("created") != cfg["session_created"]):
            raise PermissionError("Permission runner session does not match registration")
        requests = self.call("GET", "/permission")
        if not isinstance(requests, list) or len(requests) > 64:
            raise PermissionError("Permission runner queue cannot be completely verified")
        selected, seen = [], set()
        for item in requests:
            if not isinstance(item, dict) or len(canonical(item)) > 32768:
                raise PermissionError("Permission runner request is invalid")
            for key in ("id", "sessionID"):
                if not isinstance(item.get(key), str) or not ID.fullmatch(item[key]):
                    raise PermissionError("Permission runner request is invalid")
            if item["id"] in seen:
                raise PermissionError("Permission runner request is duplicated")
            seen.add(item["id"])
            if item["sessionID"] != cfg["session_id"]:
                continue
            if (not isinstance(item.get("permission"), str) or not 1 <= len(item["permission"]) <= 160
                    or not isinstance(item.get("metadata"), dict)
                    or any(not isinstance(item.get(k), list) or len(item[k]) > 64
                           or any(not isinstance(v, str) or len(v) > 8192 for v in item[k]) for k in ("patterns", "always"))
                    or not isinstance(item.get("tool"), dict)
                    or any(not isinstance(item["tool"].get(k), str) or not ID.fullmatch(item["tool"][k]) for k in ("messageID", "callID"))):
                raise PermissionError("Permission request has no verifiable tool binding")
            selected.append(item)
        return selected

    @contextlib.contextmanager
    def ledger(self):
        folder = self.root / ".execution-permissions"
        folder.mkdir(mode=0o700, exist_ok=True)
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("Permission audit storage is unavailable")
        def open_private(path):
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                os.close(fd)
                raise PermissionError("Permission audit storage is unavailable")
            return fd
        lock = open_private(folder / "lock")
        with os.fdopen(lock, "a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            dbpath = folder / "audit.sqlite3"
            fd = open_private(dbpath)
            os.close(fd)
            connection = sqlite3.connect(dbpath, timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("CREATE TABLE IF NOT EXISTS requests (handle TEXT PRIMARY KEY, binding TEXT, payload TEXT, fingerprint TEXT, first_seen REAL, expires REAL, state TEXT)")
                connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, handle TEXT, actor TEXT, decision TEXT, note TEXT, result TEXT)")
                # A previous process may have stopped after the runner accepted the POST.
                for row in connection.execute("SELECT handle FROM requests WHERE state='dispatching'").fetchall():
                    self.event(connection, row["handle"], {}, "", "", "unknown after interrupted dispatch")
                connection.execute("UPDATE requests SET state='unknown' WHERE state='dispatching'")
                connection.commit()
                yield connection
            finally:
                connection.close()

    def event(self, db, handle, actor, decision, note, result):
        db.execute("INSERT INTO events(ts,handle,actor,decision,note,result) VALUES (?,?,?,?,?,?)",
                   (self.clock(), handle, canonical(actor), decision, note, result))

    def sync(self, db, requests):
        now, binding = self.clock(), self.config["_binding"]
        handles = []
        for item in requests:
            # Registration/credential rotation must not revive a previously decided
            # or uncertain request from this same immutable runner session.
            handle = digest([self.config[k] for k in ("endpoint", "directory", "session_id", "session_created", "project_id")] + [item["id"]])
            handles.append(handle)
            row = db.execute("SELECT * FROM requests WHERE handle=?", (handle,)).fetchone()
            if row is None:
                if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 10000:
                    raise PermissionError("Permission audit storage needs operator maintenance")
                db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)", (handle, binding, canonical(item), digest(item), now, now + self.config["ttl_seconds"], "pending"))
            elif row["fingerprint"] != digest(item) or row["binding"] != binding:
                db.execute("UPDATE requests SET state='changed' WHERE handle=?", (handle,))
        for row in db.execute("SELECT * FROM requests WHERE binding=? AND state='pending'", (binding,)).fetchall():
            state = None
            if now < row["first_seen"] or now >= row["expires"]:
                state = "expired"
            elif row["handle"] not in handles:
                state = "no_longer_pending"
            if state:
                db.execute("UPDATE requests SET state=? WHERE handle=?", (state, row["handle"]))
        db.commit()
        token = digest([binding, sorted(digest(item) for item in requests)])
        rows = db.execute("SELECT * FROM requests WHERE binding=? ORDER BY (state='pending') DESC,first_seen DESC,handle LIMIT 64", (binding,)).fetchall()
        return token, rows

    def snapshot(self):
        if not self.config:
            return {"state": "not_connected", "requests": [], "history": []}
        with self.ledger() as db:
            try:
                requests = self.pending()
            except PermissionError as error:
                error.history = [dict(row) for row in db.execute("SELECT ts,handle,actor,decision,note,result FROM events ORDER BY id DESC LIMIT 20")]
                raise
            token, rows = self.sync(db, requests)
            visible = [{"handle": row["handle"], "review_token": token, "request": decode(row["payload"]),
                        "expires_at": row["expires"], "state": row["state"], "affected_count": len(requests)} for row in rows]
            history = [dict(row) for row in db.execute("SELECT ts,handle,actor,decision,note,result FROM events ORDER BY id DESC LIMIT 20")]
            return {"state": "connected", "repository": self.config["repository"], "session_id": self.config["session_id"],
                    "server_time": self.clock(), "requests": visible, "history": history}

    def decide(self, value, actor):
        if (not isinstance(value, dict) or set(value) - {"handle", "review_token", "decision", "message"}
                or any(not isinstance(value.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", value[k]) for k in ("handle", "review_token"))
                or value.get("decision") not in ("approve_once", "deny_session")
                or not isinstance(value.get("message", ""), str) or len(value.get("message", "")) > 1000):
            raise PermissionError("Invalid execution permission decision", 400)
        if not self.config:
            raise PermissionError("Execution permissions are not connected", 409)
        with self.ledger() as db:
            requests = self.pending()
            token, rows = self.sync(db, requests)
            row = next((r for r in rows if r["handle"] == value["handle"]), None)
            if not row or row["state"] != "pending" or token != value["review_token"]:
                raise PermissionError("Permission request expired or changed; refresh and review again", 409)
            self.check_hold()
            if self.read_config() != self.config:
                raise PermissionError("Permission runner registration changed; refresh and review again", 409)
            actor = {key: str(actor.get(key, ""))[:160] for key in ("subject", "name", "role")}
            decision, note = value["decision"], value.get("message", "")
            payload = decode(row["payload"])
            db.execute("UPDATE requests SET state='dispatching' WHERE handle=?", (row["handle"],))
            self.event(db, row["handle"], actor, decision, note, "dispatch intent")
            db.commit()  # Must be durable BEFORE the single upstream POST.
            try:
                reply = {"reply": "once" if decision == "approve_once" else "reject"}
                if decision == "deny_session" and note:
                    reply["message"] = note
                result = self.call("POST", "/permission/" + payload["id"] + "/reply", reply)
                if result is not True:
                    raise PermissionError("Permission reply could not be confirmed")
                state = "accepted" if decision == "approve_once" else "rejected"
            except PermissionError:
                state = "unknown"
            db.execute("UPDATE requests SET state=? WHERE handle=?", (state, row["handle"]))
            if state == "rejected":
                for sibling in rows:
                    if sibling["state"] == "pending" and sibling["handle"] != row["handle"]:
                        db.execute("UPDATE requests SET state='rejected' WHERE handle=?", (sibling["handle"],))
                        self.event(db, sibling["handle"], actor, decision, note, "rejected with session")
            self.event(db, row["handle"], actor, decision, note, state)
            db.commit()
            return {"state": state, "handle": row["handle"], "tool_completion_verified": False,
                    "message": "Runner reply is uncertain. Do not retry; inspect the runner." if state == "unknown" else "Runner accepted the permission decision. Tool completion is separate."}
