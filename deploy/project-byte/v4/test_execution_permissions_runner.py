#!/usr/bin/env python3
"""Disposable OpenCode 1.18.29 V1 permission test using real model tool calls.

Only the model HTTP response is synthetic. Permission creation, blocking, reply,
tool execution, and replay behavior come from the pinned OpenCode executable.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def now():
    return round(time.monotonic(), 3)


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(tempfile.gettempdir()) / "project-byte-permission-proof")
    parser.add_argument("--gateway", type=Path, default=Path(__file__).with_name("execution_permissions.py"), help="Exercise this actual execution_permissions.py Gateway instead of direct replies")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="v1-real-", dir=args.output))
    sandbox = run_dir / "isolated"
    workspace = sandbox / "workspace"
    workspace.mkdir(parents=True)
    for folder in ("home", "config", "data", "cache", "state", "tmp"):
        (sandbox / folder).mkdir()
    password = secrets.token_urlsafe(32)
    auth = "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()
    sequence = []
    fixture_requests = []
    gateway_sequence = []
    results = {"binary": str(args.binary), "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
               "run_directory": str(run_dir), "canonical_directory": str(workspace.resolve()),
               "protocol": "V1", "cases": {}, "status": "running"}
    gateway_module = None
    gateway_root = sandbox / "app-root"
    prior_gateway_secret = os.environ.get("PB_PERMISSION_TEST_SECRET")
    if args.gateway:
        gateway_root.mkdir(mode=0o700)
        results["gateway_source"] = str(args.gateway.resolve())
        results["gateway_source_sha256"] = hashlib.sha256(args.gateway.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location("fixture_execution_permissions", args.gateway)
        gateway_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gateway_module)
        os.environ["PB_PERMISSION_TEST_SECRET"] = password
    commands = {name: "printf '%s\\n' '" + name + "' >> " + name + ".marker" for name in ("approve-once", "reject-with-message")}
    emitted = set()
    fixture_lock = threading.Lock()

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(length))
            messages = body.get("messages", [])
            text = json.dumps(messages)
            case = next((name for name in commands if name in text), None)
            tool_names = [x.get("function", {}).get("name") for x in body.get("tools", [])]
            has_result = any(m.get("role") == "tool" for m in messages)
            with fixture_lock:
                use_tool = case is not None and "bash" in tool_names and case not in emitted and not has_result
                if use_tool:
                    emitted.add(case)
                fixture_requests.append({"at": now(), "path": self.path, "case": case,
                                         "stream": body.get("stream"), "tool_names": tool_names,
                                         "has_tool_result": has_result, "emitted_bash": use_tool})
            if use_tool:
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_" + case.replace("-", "_"),
                          "type": "function", "function": {"name": "bash", "arguments": json.dumps({
                              "command": commands[case], "description": "Write one disposable permission marker", "timeout": 10000})}}]}
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "Fixture tool cycle complete."}
                finish = "stop"
            if body.get("stream"):
                chunks = [
                    {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                     "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "chatcmpl-fixture", "object": "chat.completion.chunk", "created": 1,
                     "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}},
                ]
                payload = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                payload = json.dumps({"id": "chatcmpl-fixture", "object": "chat.completion", "created": 1,
                                      "model": "fixture", "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                                      "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    model.daemon_threads = True
    threading.Thread(target=model.serve_forever, daemon=True).start()
    port = unused_port()
    config = {
        "model": "fixture/fixture", "small_model": "fixture/fixture", "share": "disabled",
        "permission": {"*": "deny", "bash": "ask"}, "enabled_providers": ["fixture"],
        "provider": {"fixture": {"npm": "@ai-sdk/openai-compatible", "name": "Local fixture",
                     "options": {"baseURL": f"http://127.0.0.1:{model.server_port}/v1", "apiKey": "fixture-not-a-secret"},
                     "models": {"fixture": {"name": "Fixture", "limit": {"context": 128000, "output": 4096},
                                            "tool_call": True}}}},
    }
    # Construct child environment from scratch; no inherited credentials or personal config.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(sandbox / "home"),
           "XDG_CONFIG_HOME": str(sandbox / "config"), "XDG_DATA_HOME": str(sandbox / "data"),
           "XDG_CACHE_HOME": str(sandbox / "cache"), "XDG_STATE_HOME": str(sandbox / "state"),
           "TMPDIR": str(sandbox / "tmp"), "OPENCODE_SERVER_PASSWORD": password,
           "OPENCODE_CONFIG_CONTENT": json.dumps(config), "NO_PROXY": "127.0.0.1,localhost",
           "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
           "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true",
           "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
           "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true",
           "OPENCODE_DISABLE_LSP_DOWNLOAD": "true", "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "true"}
    process = None
    log_file = (run_dir / "server.log").open("wb")
    started = time.monotonic()

    def request(method, path, body=None, authenticated=True, directory=True, timeout=30):
        target = f"http://127.0.0.1:{port}" + path
        if directory:
            target += ("&" if "?" in target else "?") + urllib.parse.urlencode({"directory": str(workspace.resolve())})
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = auth
        req = urllib.request.Request(target, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        try:
            data = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = raw.decode(errors="replace")
        sequence.append({"at": now(), "method": method, "path": path, "directory": directory,
                         "authenticated": authenticated, "status": response.status, "request": body, "response": data})
        return response.status, data

    def wait_for(check, label, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise AssertionError(f"OpenCode exited ({process.returncode}) while {label}")
            result = check()
            if result:
                return result
            time.sleep(.15)
        raise AssertionError("Timed out: " + label)

    try:
        version = subprocess.run([str(args.binary), "--version"], env=env, cwd=workspace, capture_output=True, timeout=30, text=True)
        results["version"] = version.stdout.strip()
        assert version.returncode == 0 and version.stdout.strip() == "1.18.29", results["version"]
        process = subprocess.Popen([str(args.binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                   env=env, cwd=workspace, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
        print("Run directory: " + str(run_dir), flush=True)

        def ready():
            try:
                status, data = request("GET", "/global/health", directory=False, timeout=2)
                return status == 200 and data
            except (urllib.error.URLError, TimeoutError):
                return None
        health = wait_for(ready, "server readiness")
        assert health.get("version") == "1.18.29", health
        assert request("GET", "/permission", authenticated=False)[0] == 401
        print("PASS pinned version, loopback readiness and Basic-auth denial", flush=True)

        for case, reply in (("approve-once", "once"), ("reject-with-message", "reject")):
            status, session = request("POST", "/session", {"title": "Disposable " + case})
            assert status == 200 and session.get("id"), (status, session)
            session_id = session["id"]
            marker = workspace / (case + ".marker")
            assert not marker.exists()
            payload = {"model": {"providerID": "fixture", "modelID": "fixture"}, "agent": "build",
                       "parts": [{"type": "text", "text": "Run the disposable permission proof case " + case + "."}]}
            status, data = request("POST", f"/session/{session_id}/prompt_async", payload)
            assert status == 204, (status, data)

            def native_pending():
                status, pending = request("GET", "/permission")
                assert status == 200 and isinstance(pending, list), (status, pending)
                return next((item for item in pending if item["sessionID"] == session_id), None)
            pending = wait_for(native_pending, case + " native pending permission")
            assert pending["permission"] == "bash", pending
            assert pending.get("tool", {}).get("messageID") and pending["tool"].get("callID"), pending
            assert pending.get("metadata", {}).get("command") == commands[case], pending
            assert not marker.exists(), "Tool ran before native approval"
            # Observe the same pending request over a bounded interval, not just one race-prone instant.
            time.sleep(.6)
            assert native_pending()["id"] == pending["id"]
            assert not marker.exists(), "Tool ran while permission still pending"
            print("PASS " + case + " genuine tool request blocks marker", flush=True)
            decision = {"reply": reply}
            if reply == "reject":
                decision["message"] = "Do not write the marker; this is a deliberate isolated denial."
            reply_path = "/permission/" + pending["id"] + "/reply"
            gateway_decision = None
            if gateway_module:
                registration = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
                                "directory": str(workspace.resolve()), "session_id": session_id,
                                "session_created": session["time"]["created"], "project_id": session["projectID"],
                                "repository": "jmw7629/JOEOS", "generation": secrets.token_hex(16),
                                "password_env": "PB_PERMISSION_TEST_SECRET", "ttl_seconds": 120}
                registration_path = gateway_root / "execution-permissions.json"
                fd = os.open(registration_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(registration, stream)
                gateway = gateway_module.Gateway(gateway_root)
                original_call = gateway.call

                def observed_call(method, path, body=None):
                    record = {"at": now(), "method": method, "path": path, "body": body}
                    gateway_sequence.append(record)
                    try:
                        value = original_call(method, path, body)
                        record["response"] = value
                        return value
                    except gateway_module.PermissionError as error:
                        record["error_status"] = error.status
                        record["error"] = str(error)
                        raise
                gateway.call = observed_call
                snapshot = gateway.snapshot()
                assert snapshot["state"] == "connected", snapshot
                row = next(row for row in snapshot["requests"] if row["request"]["id"] == pending["id"])
                assert row["state"] == "pending" and row["request"] == pending, row
                assert not marker.exists(), "Gateway snapshot released tool"
                gateway_decision = {"handle": row["handle"], "review_token": row["review_token"],
                                    "decision": "approve_once" if reply == "once" else "deny_session",
                                    "message": decision.get("message", "")}
                actor = {"subject": "fixture-owner", "name": "Fixture Owner", "role": "owner"}
                gateway_result = gateway.decide(gateway_decision, actor)
                assert gateway_result["state"] == ("accepted" if reply == "once" else "rejected"), gateway_result
                assert gateway_result["tool_completion_verified"] is False
                print("PASS " + case + " real tool decision through Gateway.decide", flush=True)
            else:
                status, data = request("POST", reply_path, decision)
                assert status == 200 and data is True, (status, data)

            def finished_tool():
                status, messages = request("GET", f"/session/{session_id}/message")
                assert status == 200 and isinstance(messages, list), (status, messages)
                for message in messages:
                    for part in message.get("parts", []):
                        if part.get("type") == "tool" and part.get("callID") == pending["tool"]["callID"]:
                            if part.get("state", {}).get("status") in ("completed", "error"):
                                return part
                return None
            tool = wait_for(finished_tool, case + " terminal tool result")
            if reply == "once":
                assert tool["state"]["status"] == "completed", tool
                assert marker.read_text().splitlines() == [case], "Marker did not run exactly once"
            else:
                assert tool["state"]["status"] == "error", tool
                assert not marker.exists(), "Rejected tool wrote its marker"
                assert decision["message"] in tool["state"].get("error", ""), tool
            if gateway_module:
                try:
                    gateway.decide({**gateway_decision, "decision": "approve_once"}, actor)
                    raise AssertionError("Gateway accepted replay")
                except gateway_module.PermissionError as error:
                    replay_status = error.status
                    assert replay_status == 409, (error.status, str(error))
                posts = [entry for entry in gateway_sequence if entry["method"] == "POST" and entry["path"] == reply_path]
                assert len(posts) == 1, "Gateway sent multiple POSTs for the same request"
            else:
                replay_status, replay = request("POST", reply_path, {"reply": "once"})
                assert replay_status == 404, (replay_status, replay)
            time.sleep(.3)
            if reply == "once":
                assert marker.read_text().splitlines() == [case]
            else:
                assert not marker.exists()
            status, pending_after = request("GET", "/permission")
            assert status == 200 and not any(item["id"] == pending["id"] for item in pending_after)
            results["cases"][case] = {"session": session, "native_permission": pending, "decision": decision,
                                      "terminal_tool": tool, "marker_lines": marker.read_text().splitlines() if marker.exists() else [],
                                      "replay_status": replay_status, "result": "passed"}
            if gateway_module:
                with sqlite3.connect(gateway_root / ".execution-permissions/audit.sqlite3") as db:
                    db.row_factory = sqlite3.Row
                    ledger_row = dict(db.execute("SELECT * FROM requests WHERE handle=?", (gateway_decision["handle"],)).fetchone())
                    events = [dict(row) for row in db.execute("SELECT * FROM events WHERE handle=? ORDER BY id", (gateway_decision["handle"],))]
                assert ledger_row["state"] == gateway_result["state"]
                assert [row["result"] for row in events] == ["dispatch intent", gateway_result["state"]], events
                results["cases"][case]["gateway"] = {"registration": registration, "decision": gateway_decision,
                                                       "result": gateway_result, "ledger": ledger_row, "events": events,
                                                       "upstream_reply_posts": len(posts)}
            print("PASS " + case + " decision, terminal tool result and replay rejection", flush=True)
        results["status"] = "passed"
    except Exception as error:
        results["status"] = "failed"
        results["error"] = str(error)
        results["traceback"] = traceback.format_exc()
        print("FAIL " + str(error), flush=True)
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        model.shutdown()
        model.server_close()
        log_file.close()
        log_path = run_dir / "server.log"
        log_path.write_text(log_path.read_text(errors="replace").replace(password, "[REDACTED]").replace(auth, "[REDACTED]"))
        results["elapsed_seconds"] = round(time.monotonic() - started, 2)
        results["server_stopped"] = process is None or process.poll() is not None
        (run_dir / "result.json").write_text(json.dumps(results, indent=2) + "\n")
        (run_dir / "request-sequence.json").write_text(json.dumps(sequence, indent=2) + "\n")
        (run_dir / "fixture-model-requests.json").write_text(json.dumps(fixture_requests, indent=2) + "\n")
        (run_dir / "gateway-request-sequence.json").write_text(json.dumps(gateway_sequence, indent=2) + "\n")
        if args.gateway:
            if prior_gateway_secret is None:
                os.environ.pop("PB_PERMISSION_TEST_SECRET", None)
            else:
                os.environ["PB_PERMISSION_TEST_SECRET"] = prior_gateway_secret
        print("Result: " + str(run_dir / "result.json"), flush=True)
    return 0 if results["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
