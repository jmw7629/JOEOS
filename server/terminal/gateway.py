"""PTY session manager for the JoeOS human terminal.

Security model:
- The shell is the authenticated JoeOS backend user's own shell (never root).
- Session creation requires an authenticated application session (router layer).
- The WebSocket validates the session id AND a per-session token.
- Agents cannot reach the terminal: no agent tool exposes it, and the PTY is
  only reachable through the authenticated WebSocket.
- Output is bounded; idle sessions are reaped.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

MAX_SESSIONS = 8
SESSION_TTL_MS = 10 * 60 * 1000
MAX_BUFFER_CHARS = 200_000


class TerminalError(Exception):
    pass


@dataclass
class TerminalSession:
    session_id: str
    token: str
    shell: str
    pid: int
    master_fd: int
    cols: int
    rows: int
    created_at: float
    last_activity: float
    owner: str = ""
    closed: bool = False
    queue: "asyncio.Queue" = field(default_factory=asyncio.Queue)
    buffer: list = field(default_factory=list)


class TerminalGateway:
    def __init__(
        self,
        *,
        shell: Optional[str] = None,
        max_sessions: int = MAX_SESSIONS,
        ttl_ms: int = SESSION_TTL_MS,
        event_sink: Optional[callable] = None,
    ) -> None:
        self._shell = shell or os.environ.get("SHELL") or "/bin/bash"
        self._max = max_sessions
        self._ttl_ms = ttl_ms
        self._sessions: Dict[str, TerminalSession] = {}
        self._events = event_sink or (lambda level, source, message: None)

    def _set_winsize(self, fd: int, cols: int, rows: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def create(self, *, cols: int = 120, rows: int = 30, principal: Optional[dict] = None) -> dict:
        if len(self._sessions) >= self._max:
            raise TerminalError("Terminal session limit reached.")
        cols = max(20, min(int(cols or 120), 400))
        rows = max(5, min(int(rows or 30), 200))
        master, slave = pty.openpty()
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        try:
            # subprocess uses posix_spawn on modern CPython (no fork deadlock in
            # the threaded uvicorn process). start_new_session makes the shell a
            # session leader so the PTY becomes its controlling terminal.
            process = subprocess.Popen(
                [self._shell],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                close_fds=True,
                env=env,
            )
        finally:
            try:
                os.close(slave)
            except OSError:
                pass
        pid = process.pid
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._set_winsize(master, cols, rows)
        now = time.time()
        owner = ""
        if isinstance(principal, dict):
            user = principal.get("user") or {}
            owner = str(user.get("id") or principal.get("session_id") or "")
        session = TerminalSession(
            session_id="term-%s" % uuid.uuid4().hex[:12],
            token=uuid.uuid4().hex,
            shell=self._shell,
            pid=pid,
            master_fd=master,
            cols=cols,
            rows=rows,
            created_at=now,
            last_activity=now,
            owner=owner,
        )
        self._sessions[session.session_id] = session
        loop = asyncio.get_running_loop()
        loop.add_reader(master, self._on_readable, session)
        self._events("info", "terminal", "session %s started" % session.session_id)
        return {
            "session_id": session.session_id,
            "token": session.token,
            "shell": self._shell,
            "cols": cols,
            "rows": rows,
        }

    def _on_readable(self, session: TerminalSession) -> None:
        try:
            data = os.read(session.master_fd, 8192)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.close(session.session_id)
            return
        if not data:
            self.close(session.session_id)
            return
        session.last_activity = time.time()
        text = data.decode("utf-8", errors="replace")
        session.buffer.append(text)
        total = sum(len(chunk) for chunk in session.buffer)
        if total > MAX_BUFFER_CHARS:
            over = total - MAX_BUFFER_CHARS
            while over > 0 and session.buffer:
                head = session.buffer[0]
                if len(head) <= over:
                    over -= len(head)
                    session.buffer.pop(0)
                else:
                    session.buffer[0] = head[over:]
                    over = 0
        try:
            session.queue.put_nowait(text)
        except Exception:  # noqa: BLE001 - queue bound never blocks
            pass

    def write(self, session_id: str, data: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        try:
            os.write(session.master_fd, data.encode("utf-8", errors="replace")[:4096])
        except OSError:
            return False
        session.last_activity = time.time()
        return True

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        cols = max(20, min(int(cols or 120), 400))
        rows = max(5, min(int(rows or 30), 200))
        self._set_winsize(session.master_fd, cols, rows)
        session.cols, session.rows = cols, rows
        return True

    def get(self, session_id: str) -> Optional[TerminalSession]:
        return self._sessions.get(session_id)

    def snapshot(self, session_id: str) -> str:
        session = self._sessions.get(session_id)
        if session is None:
            return ""
        return "".join(session.buffer[-4000:])

    def list(self) -> list:
        now = time.time()
        out = []
        for session in self._sessions.values():
            out.append({
                "session_id": session.session_id,
                "shell": session.shell,
                "cols": session.cols,
                "rows": session.rows,
                "created_at": session.created_at,
                "idle_ms": int((now - session.last_activity) * 1000),
                "closed": session.closed,
            })
        return out

    def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        if not session.closed:
            session.closed = True
            try:
                session.queue.put_nowait(None)
            except Exception:  # noqa: BLE001
                pass
            try:
                os.kill(session.pid, signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            try:
                loop = asyncio.get_running_loop()
                loop.remove_reader(session.master_fd)
            except Exception:  # noqa: BLE001
                pass
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            self._events("info", "terminal", "session %s closed" % session_id)
        return True

    def reap(self) -> int:
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if not session.closed and (now - session.last_activity) * 1000 > self._ttl_ms
        ]
        for sid in expired:
            self.close(sid)
        return len(expired)
