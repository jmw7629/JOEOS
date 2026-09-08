"""Filesystem and hard-OS-sandbox broker for owner-scoped Codex runs.

The controller supplies trusted server paths, never paths from a browser. A run
receives an export of one immutable Git commit, without Git metadata, the source
checkout, credentials, or the host home. This module never publishes or merges.
Linux requires working bubblewrap PID/network namespaces; the privileged broker
drops UID/GID, supplementary groups, all capabilities and privilege escalation
before executing a command. macOS requires working sandbox-exec. Failed probes
disable commands; there is no
unsandboxed fallback. No command or run is resumed automatically.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid


MAX_FILE = 16 * 1024 * 1024
MAX_TREE = 128 * 1024 * 1024
MAX_FILES = 20000
MAX_OUTPUT = 1024 * 1024
MAX_PATCH = 8 * 1024 * 1024
MAX_INLINE_DIFF_FILE_BYTES = 512 * 1024
MAX_INLINE_DIFF_LINES = 20000
MAX_INLINE_DIFF_WORK = 4_000_000
MAX_SECONDS = 900
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
INTERNAL = frozenset((".sandbox-home", ".sandbox-tmp"))
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class SandboxError(RuntimeError):
    """A bounded error safe to return without host paths or command output."""


def _private_directory(path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise SandboxError("Sandbox storage must be an absolute private directory")
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise SandboxError("Sandbox storage must be an owner-private directory")
    return path.resolve(strict=True)


def _relative(value, *, root=False, internal=False):
    if not isinstance(value, str) or len(value) > 2048 or "\\" in value:
        raise SandboxError("Invalid workspace path")
    if root and value == "":
        return []
    parts = value.split("/")
    if (not value or any(c < " " or c == "\x7f" for c in value)
            or any(p in ("", ".", "..", ".git") or len(p.encode()) > 255 for p in parts)
            or (not internal and parts[0] in INTERNAL)):
        raise SandboxError("Invalid workspace path")
    return parts


def _directory_fd(root, parts, *, create=False):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(root, parts, limit=MAX_FILE):
    parent = _directory_fd(root, parts[:-1])
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise SandboxError("Workspace file is not a bounded private regular file")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise SandboxError("Workspace file is too large")
            return data, bool(info.st_mode & 0o111)
    finally:
        os.close(parent)


def _write(root, parts, data, executable=False):
    parent = _directory_fd(root, parts[:-1], create=True)
    temporary = ".sandbox-write-" + uuid.uuid4().hex
    made = False
    try:
        try:
            old = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old is not None and (not stat.S_ISREG(old.st_mode) or old.st_nlink != 1):
            raise SandboxError("Workspace writes require a regular file")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o755 if executable else 0o644, dir_fd=parent)
        made = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
        made = False
    finally:
        if made:
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)


def _kill_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _process(args, *, cwd, env, timeout, output_limit=MAX_OUTPUT, stop_event=None, on_output=None, stdin_data=None):
    """Drain both pipes with a single shared limit; always stop descendants."""
    start = time.monotonic()
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, close_fds=True)
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    timed_out = stopped = truncated = False
    selector = selectors.DefaultSelector()
    written = 0
    try:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if stdin_data is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map():
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if time.monotonic() - start >= timeout:
                timed_out = True
                break
            for key, _ in selector.select(min(.05, max(.001, timeout - (time.monotonic() - start)))):
                if key.data == "stdin":
                    if written < len(stdin_data):
                        written += os.write(key.fileobj.fileno(), stdin_data[written:written + 16384])
                    if written == len(stdin_data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining = max(0, output_limit - total)
                kept = chunk[:remaining]
                outputs[key.data].extend(kept)
                total += len(kept)
                if kept and on_output:
                    on_output(key.data, kept.decode("utf-8", errors="replace"))
                if len(chunk) > remaining:
                    truncated = True
            if truncated:
                break
        remaining = max(.001, timeout - (time.monotonic() - start))
        if not (timed_out or stopped or truncated):
            # A process may close its pipes while continuing to execute.
            while process.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    stopped = True
                    break
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    break
                time.sleep(min(.02, remaining))
    finally:
        selector.close()
        # This also stops descendants after their shell has already exited.
        _kill_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=.25)
        except subprocess.TimeoutExpired:
            pass
        _kill_group(process, signal.SIGKILL)
        process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
        if process.stdin:
            process.stdin.close()
    return {"exit_code": process.returncode, "stdout": bytes(outputs["stdout"]),
            "stderr": bytes(outputs["stderr"]), "timed_out": timed_out,
            "stopped": stopped, "truncated": truncated,
            "duration_ms": int((time.monotonic() - start) * 1000)}


def _host_environment(home):
    return {"PATH": SYSTEM_PATH, "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "NO_COLOR": "1"}


def _tree(root):
    """Return bounded content snapshots without following links, including races."""
    result, total, count = {}, 0, 0
    def visit(parts):
        nonlocal total, count
        directory = _directory_fd(root, parts)
        try:
            for name in sorted(os.listdir(directory)):
                if not parts and name in INTERNAL:
                    continue
                # Git state created by a tool is never a publishable artifact.
                if name == ".git":
                    continue
                path = parts + [name]
                _relative("/".join(path))
                count += 1
                if count > MAX_FILES:
                    raise SandboxError("Workspace contains too many entries")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    visit(path)
                elif stat.S_ISREG(info.st_mode):
                    data, executable = _read(root, path)
                    total += len(data)
                    if total > MAX_TREE:
                        raise SandboxError("Workspace snapshot is too large")
                    result["/".join(path)] = {"data": data, "executable": executable,
                                                  "sha256": hashlib.sha256(data).hexdigest()}
                else:
                    raise SandboxError("Workspace artifacts cannot contain links or special files")
        finally:
            os.close(directory)
    visit([])
    return result


class SandboxWorkspace:
    """Factory for exports from one server-selected trusted repository.

    ``create(run_id)`` returns RunWorkspace. ``capabilities()`` executes synthetic
    negative checks and returns ``available``, ``backend`` and a public ``detail``.
    The capability probe never reads owner credentials or contacts the Internet.
    """
    def __init__(self, run_root, trusted_repo, *, binary=None, platform=None, broker_socket=None):
        try:
            self.run_root = _private_directory(run_root, create=True)
            candidate = Path(trusted_repo)
            if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
                raise SandboxError("A trusted repository directory is required")
            self.trusted_repo = candidate.resolve(strict=True)
            if (self.run_root == self.trusted_repo or self.trusted_repo in self.run_root.parents
                    or self.run_root in self.trusted_repo.parents):
                raise SandboxError("Sandbox storage must be outside the source repository")
        except OSError:
            raise SandboxError("Sandbox storage or trusted repository is unavailable") from None
        self.platform = platform or sys.platform
        default = "/usr/bin/bwrap" if self.platform.startswith("linux") else "/usr/bin/sandbox-exec"
        self.binary = Path(binary or default)
        self.broker_socket = Path(broker_socket) if broker_socket else None
        self.worker_uid = self.worker_gid = None
        self.lock = threading.RLock()
        self.run_locks = {}
        self._proof = None

    def _arguments(self, workspace, command, *, mount_source=None):
        if not self.binary.is_absolute() or not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise SandboxError("A hard OS sandbox is not installed")
        binary_info = self.binary.stat()
        if binary_info.st_uid not in (0, os.getuid()) or binary_info.st_mode & 0o022:
            raise SandboxError("The OS sandbox executable is not trusted")
        if self.platform.startswith("linux"):
            args = [str(self.binary)]
            if self.worker_uid is not None:
                # Root broker: preserve the host UID mapping so uid-owned 0700
                # files remain writable. PID/network/etc remain separate. Only
                # these bootstrap capabilities survive until the fixed setpriv
                # entrypoint drops every capability and all supplementary groups.
                args.extend(("--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
                             "--unshare-cgroup", "--die-with-parent", "--new-session", "--cap-drop", "ALL"))
                for capability in ("CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP", "CAP_DAC_OVERRIDE"):
                    args.extend(("--cap-add", capability))
                dropper = Path("/usr/bin/setpriv")
                info = dropper.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not os.access(dropper, os.X_OK):
                    raise SandboxError("The fixed privilege-drop entrypoint is unavailable")
                entrypoint = [str(dropper), "--reuid=" + str(self.worker_uid), "--regid=" + str(self.worker_gid),
                              "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                              "--no-new-privs", "--", "/bin/sh", "-c", command]
            else:
                args.extend(("--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"))
                entrypoint = ["/bin/sh", "-c", command]
            args.append("--clearenv")
            # These are empty synthetic parents, never host directory mounts.
            # Explicit traversal modes must survive the later privilege drop;
            # some bwrap versions create implicit bind parents as root-only.
            for path in ("/usr", "/etc"):
                args.extend(("--dir", path, "--chmod", "0755", path))
            # Deliberately omit /home, /root, /run, /opt, /usr/local and host /tmp.
            for path in ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64", "/usr/libexec",
                         "/usr/share", "/bin", "/sbin", "/lib", "/lib64"):
                if Path(path).is_dir():
                    args.extend(("--ro-bind", path, path))
            for path in ("/etc/ld.so.cache", "/etc/alternatives"):
                if Path(path).exists():
                    args.extend(("--ro-bind", path, path))
            # Bind the pre-opened host directory before replacing /proc.
            args.extend(("--bind", str(mount_source or workspace), "/work",
                         "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                         "--tmpfs", "/run",
                         "--chdir", "/work"))
            worker_root = "/work"
            env = self._worker_environment(worker_root)
            for key, value in env.items():
                args.extend(("--setenv", key, value))
            return args + ["--", *entrypoint], _host_environment(self.run_root)
        if self.platform != "darwin":
            raise SandboxError("This operating system has no supported hard sandbox")
        def literal(path):
            return json.dumps(str(path))
        read_paths = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/libexec",
                      "/System", "/Library/Apple/System/Library")
        profile = ("(version 1)(deny default)(allow process-fork)(allow process-exec)"
                   "(allow signal (target same-sandbox))(allow sysctl-read)"
                   "(allow file-read* (literal \"/\") (literal \"/private/var/select/sh\"))"
                   "(allow file-read* " + " ".join("(subpath " + literal(p) + ")" for p in read_paths)
                   + " (subpath " + literal(workspace) + "))"
                   "(allow file-write* (subpath " + literal(workspace) + "))"
                   "(allow file-read* file-write* (literal \"/dev/null\") (literal \"/dev/zero\")"
                   " (literal \"/dev/random\") (literal \"/dev/urandom\"))")
        return [str(self.binary), "-p", profile, "/bin/sh", "-c", command], self._worker_environment(str(workspace))

    @staticmethod
    def _worker_environment(root):
        return {"PATH": SYSTEM_PATH, "HOME": root + "/.sandbox-home", "LANG": "C", "LC_ALL": "C",
                "XDG_CONFIG_HOME": root + "/.sandbox-home/config",
                "XDG_DATA_HOME": root + "/.sandbox-home/data",
                "XDG_CACHE_HOME": root + "/.sandbox-home/cache",
                "TMPDIR": root + "/.sandbox-tmp", "NO_COLOR": "1", "TERM": "dumb",
                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null"}

    def _diagnostic(self, **values):
        # Operator opt-in, root broker only, synthetic health probe only. Never
        # used for user commands, credentials, browser responses or file content.
        if self.worker_uid is not None and os.getenv("PRFKT_SANDBOX_DIAGNOSTICS") == "1":
            print("PRFKT_SANDBOX_PROBE " + json.dumps(values, sort_keys=True), file=sys.stderr, flush=True)

    def capabilities(self, *, refresh=False):
        if self.broker_socket:
            try:
                return _broker_client(self.broker_socket, {"op": "health"}, timeout=12)
            except (OSError, SandboxError):
                return {"available": False, "backend": "bubblewrap-broker",
                        "detail": "Hard sandbox broker is unavailable; execution is disabled"}
        with self.lock:
            if self._proof is not None and not refresh:
                return dict(self._proof)
            backend = "bubblewrap" if self.platform.startswith("linux") else "sandbox-exec" if self.platform == "darwin" else "unsupported"
            result = {"available": False, "backend": backend,
                      "detail": "Hard sandbox isolation is unavailable; execution is disabled"}
            stage = "create_probe"
            try:
                with tempfile.TemporaryDirectory(prefix=".probe-", dir=self.run_root) as temporary:
                    stage = "prepare_probe"
                    folder = Path(temporary)
                    workspace = folder / "workspace"
                    workspace.mkdir(mode=0o700)
                    for name in INTERNAL:
                        (workspace / name).mkdir(mode=0o700)
                    secret = folder / "outside-secret"
                    secret.write_text(uuid.uuid4().hex)
                    if self.worker_uid is not None:
                        for path in (folder, workspace, secret, *(workspace / name for name in INTERNAL)):
                            os.chown(path, self.worker_uid, self.worker_gid)
                    forbidden = folder / "outside-write"
                    command = ("if /bin/cat " + shlex.quote(str(secret)) + " >/dev/null 2>&1; then exit 21; fi\n"
                               "if ( : > " + shlex.quote(str(forbidden)) + " ) 2>/dev/null; then exit 22; fi\n"
                               "printf sandbox-proof > isolation-proof\n")
                    if self.worker_uid is not None and os.getenv("PRFKT_SANDBOX_DIAGNOSTICS") == "1":
                        command = ("/bin/ls -ld / /usr /usr/bin /bin >&2\n"
                                   "/bin/readlink /proc/self/ns/net >&2\n") + command
                    listener = None
                    try:
                        if self.platform.startswith("linux"):
                            namespace = os.readlink("/proc/self/ns/net")
                            command += ("ns=$(/usr/bin/readlink /proc/self/ns/net) || exit 23\n"
                                        "case \"$ns\" in 'net:['*']') ;; *) exit 23 ;; esac\n"
                                        "test \"$ns\" != " + shlex.quote(namespace) + " || exit 23\n")
                            if self.worker_uid is not None:
                                privilege_check = ("BEGIN { ok=1; caps=0 } "
                                    "/^Cap(Inh|Prm|Eff|Bnd|Amb):/ { if ($2 !~ /^0+$/) ok=0; caps++ } "
                                    "/^NoNewPrivs:/ { if ($2 != 1) ok=0; np=1 } "
                                    "/^Uid:/ { for (i=2;i<=5;i++) if ($i != " + str(self.worker_uid) + ") ok=0; user=1 } "
                                    "/^Gid:/ { for (i=2;i<=5;i++) if ($i != " + str(self.worker_gid) + ") ok=0; group=1 } "
                                    "/^Groups:/ { if (NF != 1) ok=0; groups=1 } "
                                    "END { exit !(ok && caps==5 && np && user && group && groups) }")
                                command += "/usr/bin/awk " + shlex.quote(privilege_check) + " /proc/self/status || exit 26\n"
                        elif self.platform == "darwin":
                            if not Path("/usr/bin/perl").is_file():
                                raise SandboxError("Network isolation cannot be verified")
                            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            listener.bind(("127.0.0.1", 0))
                            listener.listen(1)
                            # Interpreter startup itself must succeed before its negative test counts.
                            command += "/usr/bin/perl -MSocket -e " + shlex.quote(
                                "socket(S,PF_INET,SOCK_STREAM,0) or exit 0; "
                                "connect(S,sockaddr_in(" + str(listener.getsockname()[1])
                                + ",inet_aton('127.0.0.1'))) and exit 24; exit 0;") + " || exit 25\n"
                        command += "printf VERIFIED"
                        args, env = self._arguments(workspace, command,
                                                    mount_source="/proc/self/cwd" if self.worker_uid is not None else None)
                        stage = "launch_isolation"
                        proof = _process(args, cwd=workspace, env=env, timeout=8, output_limit=8192)
                        self._diagnostic(stage=stage, exit_code=proof["exit_code"], timed_out=proof["timed_out"],
                                         truncated=proof["truncated"], stdout_bytes=len(proof["stdout"]),
                                         stderr=proof["stderr"][:768].decode("utf-8", errors="replace"))
                        stage = "verify_canary"
                        if (proof["exit_code"] == 0 and proof["stdout"] == b"VERIFIED"
                                and not proof["timed_out"] and not proof["truncated"]
                                and (workspace / "isolation-proof").read_bytes() == b"sandbox-proof"
                                and not forbidden.exists()):
                            result.update(available=True, detail="Workspace, host-file and network isolation verified")
                    finally:
                        if listener:
                            listener.close()
            except (OSError, ValueError, SandboxError, subprocess.SubprocessError) as error:
                self._diagnostic(stage=stage, exception=type(error).__name__, errno=getattr(error, "errno", None))
            self._proof = dict(result)
            return result

    def create(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise SandboxError("Invalid run ID")
        with self.lock:
            folder = self.run_root / run_id
            try:
                folder.mkdir(mode=0o700)
            except FileExistsError:
                raise SandboxError("Run workspace already exists") from None
            try:
                workspace = folder / "workspace"
                baseline = folder / "baseline"
                workspace.mkdir(mode=0o700)
                baseline.mkdir(mode=0o700)
                env = _host_environment(folder)
                prefix = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
                head = _process(prefix + ["rev-parse", "--verify", "HEAD^{commit}"],
                                cwd=self.trusted_repo, env=env, timeout=10, output_limit=1024)
                commit = head["stdout"].decode("ascii", errors="replace").strip()
                if head["exit_code"] or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
                    raise SandboxError("Trusted repository commit is unavailable")
                # git archive/checkout can execute repository-configured smudge
                # filters. Read only raw immutable objects: no attributes,
                # filters, hooks, checkout, submodules or replacement objects.
                tree = _process(prefix + ["ls-tree", "-rlz", "--full-tree", commit], cwd=self.trusted_repo,
                                env=env, timeout=30, output_limit=8 * 1024 * 1024)
                if tree["exit_code"] or tree["timed_out"] or tree["truncated"]:
                    raise SandboxError("Trusted repository tree failed or exceeded its bound")
                entries, seen, size = [], set(), 0
                try:
                    for record in tree["stdout"].split(b"\x00"):
                        if not record:
                            continue
                        header, raw_name = record.split(b"\t", 1)
                        mode, kind, oid, raw_size = header.split()
                        if (mode not in (b"100644", b"100755") or kind != b"blob"
                                or not re.fullmatch(rb"[a-f0-9]{40}|[a-f0-9]{64}", oid)
                                or not raw_size.isdigit() or int(raw_size) > MAX_FILE):
                            raise SandboxError("Repository export contains unsupported or oversized objects")
                        name = raw_name.decode("utf-8")
                        parts = _relative(name)
                        if name in seen:
                            raise SandboxError("Repository export contains duplicate paths")
                        seen.add(name)
                        size += int(raw_size)
                        if len(seen) > MAX_FILES or size > MAX_TREE:
                            raise SandboxError("Repository export is too large")
                        entries.append((parts, mode == b"100755", oid, int(raw_size)))
                except (ValueError, UnicodeError):
                    raise SandboxError("Repository export tree is invalid") from None
                if entries:
                    blobs = _process(prefix + ["cat-file", "--batch"], cwd=self.trusted_repo, env=env,
                                     timeout=30, output_limit=MAX_TREE + MAX_FILES * 128,
                                     stdin_data=b"".join(item[2] + b"\n" for item in entries))
                    if blobs["exit_code"] or blobs["timed_out"] or blobs["truncated"]:
                        raise SandboxError("Trusted repository blobs failed or exceeded their bound")
                    data, offset = blobs["stdout"], 0
                    for parts, executable, oid, length in entries:
                        end = data.find(b"\n", offset)
                        expected_header = oid + b" blob " + str(length).encode()
                        if end < 0 or data[offset:end] != expected_header:
                            raise SandboxError("Repository blob identity is invalid")
                        content_start, content_end = end + 1, end + 1 + length
                        content = data[content_start:content_end]
                        if len(content) != length or data[content_end:content_end + 1] != b"\n":
                            raise SandboxError("Repository raw blob is incomplete")
                        hasher = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
                        actual = hasher(b"blob " + str(length).encode() + b"\x00" + content).hexdigest().encode()
                        if actual != oid:
                            raise SandboxError("Repository raw blob hash did not match")
                        for root in (baseline, workspace):
                            _write(root, parts, content, executable)
                        offset = content_end + 1
                    if offset != len(data):
                        raise SandboxError("Repository export contains unexpected raw data")
                for name in INTERNAL:
                    (workspace / name).mkdir(mode=0o700)
                metadata = {"schema_version": 1, "run_id": run_id, "base_commit": commit,
                            "files": {path: {"sha256": item["sha256"], "executable": item["executable"]}
                                      for path, item in _tree(baseline).items()}}
                encoded = (json.dumps(metadata, sort_keys=True) + "\n").encode()
                fd = os.open(folder / "metadata.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                return RunWorkspace(self, run_id, folder, metadata)
            except BaseException:
                shutil.rmtree(folder)
                raise

    def open(self, run_id):
        """Reopen existing files after restart; never rerun a command or task."""
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise SandboxError("Invalid run ID")
        with self.lock:
            folder = self.run_root / run_id
            try:
                for path in (folder, folder / "workspace", folder / "baseline"):
                    _private_directory(path)
                path = folder / "metadata.json"
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                        raise SandboxError("Workspace registration is not private")
                    raw = stream.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise SandboxError("Workspace registration exceeds its bound")
                metadata = _strict_object(raw)
                if (set(metadata) != {"schema_version", "run_id", "base_commit", "files"}
                        or type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1
                        or metadata["run_id"] != run_id or not isinstance(metadata["base_commit"], str)
                        or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", metadata["base_commit"])
                        or not isinstance(metadata["files"], dict) or len(metadata["files"]) > MAX_FILES):
                    raise SandboxError("Workspace registration is invalid")
                for name, item in metadata["files"].items():
                    _relative(name)
                    if (not isinstance(item, dict) or set(item) != {"sha256", "executable"}
                            or not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                            or type(item["executable"]) is not bool):
                        raise SandboxError("Workspace registration is invalid")
                run = RunWorkspace(self, run_id, folder, metadata)
                # Read baseline integrity only; current workspace may legitimately
                # need repair after an interrupted tool (e.g. an unpublishable link).
                baseline = {name: {"sha256": item["sha256"], "executable": item["executable"]}
                            for name, item in _tree(run.baseline).items()}
                if baseline != metadata["files"]:
                    raise SandboxError("The trusted baseline changed")
                return run
            except OSError:
                raise SandboxError("Existing workspace is unavailable or unsafe") from None


class RunWorkspace:
    def __init__(self, factory, run_id, folder, metadata):
        self.factory = factory
        self.run_id = run_id
        self.folder = folder
        self.path = folder / "workspace"
        self.baseline = folder / "baseline"
        self.base_commit = metadata["base_commit"]
        self.metadata = metadata
        with factory.lock:
            self.lock = factory.run_locks.setdefault(run_id, threading.RLock())
        self.closed = False

    def _check(self):
        if self.closed:
            raise SandboxError("Run workspace is closed")
        _private_directory(self.folder)
        _private_directory(self.path)

    def list(self, path=""):
        with self.lock:
            self._check()
            try:
                parts = _relative(path, root=True)
                directory = _directory_fd(self.path, parts)
                try:
                    names = sorted(os.listdir(directory))
                    if len(names) > MAX_FILES:
                        raise SandboxError("Workspace directory is too large")
                    result = []
                    for name in names:
                        if name == ".git" or (not parts and name in INTERNAL):
                            continue
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else "blocked"
                        result.append({"name": name, "path": "/".join(parts + [name]), "type": kind,
                                       "size": info.st_size if kind == "file" else 0})
                    return result
                finally:
                    os.close(directory)
            except OSError:
                raise SandboxError("Workspace directory is unavailable or contains an unsafe path") from None

    def read(self, path):
        with self.lock:
            self._check()
            try:
                return _read(self.path, _relative(path))[0]
            except OSError:
                raise SandboxError("Workspace file is unavailable or contains an unsafe path") from None

    def write(self, path, content, *, executable=False):
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes) or len(content) > MAX_FILE or type(executable) is not bool:
            raise SandboxError("Invalid or oversized workspace content")
        with self.lock:
            self._check()
            try:
                _write(self.path, _relative(path), content, executable)
                return {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            except OSError:
                raise SandboxError("Workspace write failed or contains an unsafe path") from None

    def execute(self, command, *, timeout=120, stop_event=None, on_output=None):
        if (not isinstance(command, str) or not command.strip() or len(command.encode()) > 16384
                or "\x00" in command or type(timeout) not in (int, float) or not 0 < timeout <= MAX_SECONDS):
            raise SandboxError("Invalid command or execution deadline")
        with self.lock:
            self._check()
            if stop_event is not None and stop_event.is_set():
                return {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False,
                        "stopped": True, "truncated": False, "duration_ms": 0}
            if not self.factory.capabilities()["available"]:
                raise SandboxError("Hard sandbox isolation is unavailable; execution is disabled")
            try:
                if self.factory.broker_socket:
                    return _broker_client(self.factory.broker_socket,
                                          {"op": "execute", "run_id": self.run_id,
                                           "command": command, "timeout": timeout},
                                          timeout=timeout + 5, stop_event=stop_event, on_output=on_output)
                args, env = self.factory._arguments(self.path, command)
                result = _process(args, cwd=self.path, env=env, timeout=timeout,
                                  stop_event=stop_event, on_output=on_output)
                return {**result, "stdout": result["stdout"].decode("utf-8", errors="replace"),
                        "stderr": result["stderr"].decode("utf-8", errors="replace")}
            except (OSError, subprocess.SubprocessError):
                raise SandboxError("The isolated command could not run") from None

    def snapshot(self):
        """Content-addressed inventory, with no host paths or file contents."""
        with self.lock:
            self._check()
            try:
                return {"run_id": self.run_id, "base_commit": self.base_commit,
                        "files": {path: {"sha256": item["sha256"], "size": len(item["data"]),
                                         "executable": item["executable"]}
                                  for path, item in _tree(self.path).items()}}
            except OSError:
                raise SandboxError("Workspace artifacts contain an unsafe or unavailable path") from None

    def diff(self):
        """Return changed-file evidence and bounded text patch for review.

        Binary/mode-only changes are explicit entries. Text above 512 KiB per
        file, 20,000 cumulative lines, or the comparison-work bound is explicitly
        omitted from the inline patch. Exact content hashes remain in the
        inventory. The patch is review text, not a publication command; a trusted publisher must use the changed-file
        inventory and read bytes through this broker, verify the base commit and
        preserve deletions/modes. No raw workspace path goes to a subprocess.
        """
        with self.lock:
            self._check()
            try:
                before, after = _tree(self.baseline), _tree(self.path)
                expected = {path: {"sha256": item["sha256"], "executable": item["executable"]}
                            for path, item in before.items()}
                if expected != self.metadata["files"]:
                    raise SandboxError("The trusted baseline changed")
                changes, patches, patch_size, inline_lines = [], [], 0, 0
                for path in sorted(before.keys() | after.keys()):
                    old, new = before.get(path), after.get(path)
                    if old == new:
                        continue
                    change = {"path": path, "status": "added" if old is None else "deleted" if new is None else "modified",
                              "before_sha256": old["sha256"] if old else None,
                              "after_sha256": new["sha256"] if new else None,
                              "before_executable": old["executable"] if old else None,
                              "after_executable": new["executable"] if new else None}
                    try:
                        a = old["data"].decode("utf-8") if old else ""
                        b = new["data"].decode("utf-8") if new else ""
                        if "\x00" in a or "\x00" in b:
                            raise UnicodeError()
                        change["binary"] = False
                        reason = None
                        if max(len(old["data"]) if old else 0, len(new["data"]) if new else 0) > MAX_INLINE_DIFF_FILE_BYTES:
                            reason = "File exceeds the 512 KiB inline diff limit"
                        else:
                            # Count before allocating millions of Python line
                            # objects. The cap is cumulative across this diff.
                            # Conservatively include every splitlines separator;
                            # CRLF counts twice, which can only omit earlier.
                            separators = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
                            lines = sum(a.count(char) + b.count(char) for char in separators) + int(bool(a)) + int(bool(b))
                            if inline_lines + lines > MAX_INLINE_DIFF_LINES:
                                reason = "Changes exceed the 20,000-line inline diff limit"
                            else:
                                left, right = a.splitlines(keepends=True), b.splitlines(keepends=True)
                                if len(left) * len(right) > MAX_INLINE_DIFF_WORK:
                                    reason = "Change exceeds the inline comparison work limit"
                        if reason:
                            change["inline_diff_omitted"] = reason
                            notice = ("Inline diff omitted for " + path + ": " + reason
                                      + ". The complete frozen Git patch is required before publication.\n")
                            patch_size += len(notice.encode())
                            if patch_size > MAX_PATCH:
                                raise SandboxError("Review patch exceeds its size bound")
                            patches.append(notice)
                        else:
                            inline_lines += lines
                            patch = "".join(difflib.unified_diff(left, right,
                                                                fromfile="a/" + path if old else "/dev/null",
                                                                tofile="b/" + path if new else "/dev/null"))
                            patch_size += len(patch.encode())
                            if patch_size > MAX_PATCH:
                                raise SandboxError("Review patch exceeds its size bound")
                            patches.append(patch)
                    except UnicodeError:
                        change["binary"] = True
                    changes.append(change)
                return {"run_id": self.run_id, "base_commit": self.base_commit,
                        "changes": changes, "patch": "".join(patches)}
            except OSError:
                raise SandboxError("Workspace artifacts contain an unsafe or unavailable path") from None

    def cleanup(self):
        """Remove only this factory-created run, never the trusted source."""
        with self.lock:
            if not self.closed:
                self._check()
                shutil.rmtree(self.folder)
                self.closed = True


def _strict_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SandboxError("Duplicate broker request field")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise SandboxError("Invalid sandbox broker frame") from None


def _broker_request(value):
    if value == {"op": "health"}:
        return value
    if (set(value) != {"op", "run_id", "command", "timeout"} or value.get("op") != "execute"
            or not isinstance(value["run_id"], str) or not RUN_ID.fullmatch(value["run_id"])
            or not isinstance(value["command"], str) or not value["command"].strip()
            or len(value["command"].encode()) > 16384 or "\x00" in value["command"]
            or type(value["timeout"]) not in (int, float) or not 0 < value["timeout"] <= MAX_SECONDS):
        raise SandboxError("Invalid sandbox broker request")
    return value


def _broker_client(path, request, *, timeout, stop_event=None, on_output=None):
    _broker_request(request)
    try:
        info = path.lstat()
        if (not path.is_absolute() or not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise SandboxError("Sandbox broker socket is not private")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(3)
            connection.connect(str(path))
            if not hasattr(socket, "SO_PEERCRED"):
                raise SandboxError("Sandbox broker identity cannot be verified")
            _, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer_uid != 0:
                raise SandboxError("Sandbox broker must be root-owned")
            connection.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            connection.settimeout(.1)
            deadline = time.monotonic() + timeout
            buffer = b""
            total = 0
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    return {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False,
                            "stopped": True, "truncated": False, "duration_ms": 0}
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise SandboxError("Sandbox broker disconnected before the result")
                buffer += chunk
                total += len(chunk)
                if len(buffer) > 8 * MAX_OUTPUT or total > 16 * MAX_OUTPUT:
                    raise SandboxError("Sandbox broker response exceeded its bound")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    event = _strict_object(line)
                    if set(event) == {"stream", "text"} and event["stream"] in ("stdout", "stderr") and isinstance(event["text"], str):
                        if on_output:
                            on_output(event["stream"], event["text"])
                    elif set(event) == {"result"} and isinstance(event["result"], dict):
                        return event["result"]
                    elif "error" in event:
                        raise SandboxError("The sandbox broker could not execute this request")
                    else:
                        raise SandboxError("Invalid sandbox broker response")
            raise SandboxError("Sandbox broker deadline expired")
        finally:
            # The broker treats EOF (including Stop/client crashes) as cancellation.
            connection.close()
    except OSError:
        raise SandboxError("Sandbox broker is unavailable") from None


def _broker_configuration(path):
    """Only root can change configuration or any directory containing it."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise SandboxError("Broker configuration must be a root-owned absolute file")
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SandboxError("Broker configuration parent is not root-controlled")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise SandboxError("Broker configuration must have root ownership and mode 0600")
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise SandboxError("Broker configuration is too large")
    value = _strict_object(raw)
    if (set(value) != {"schema_version", "run_root", "uid", "gid", "socket"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["uid"]) is not int or not 1 <= value["uid"] <= 2147483647
            or type(value["gid"]) is not int or not 1 <= value["gid"] <= 2147483647):
        raise SandboxError("Invalid sandbox broker configuration")
    for key in ("run_root", "socket"):
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute() or len(value[key]) > 2048:
            raise SandboxError("Invalid sandbox broker path")
    root = Path(value["run_root"])
    info = root.lstat()
    if (str(root.resolve(strict=True)) != str(root) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != value["uid"] or stat.S_IMODE(info.st_mode) != 0o700):
        raise SandboxError("Run storage must be a canonical private directory owned by the runner user")
    # A short fixed socket in root-controlled /run; callers cannot choose a destination.
    endpoint = Path(value["socket"])
    if endpoint.parent != Path("/run") or not re.fullmatch(r"[A-Za-z0-9_-]{1,70}\.sock", endpoint.name):
        raise SandboxError("Broker socket must be directly inside /run")
    info = endpoint.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SandboxError("Broker socket parent is not root-controlled")
    return value


class SandboxBroker:
    """Root-only Linux broker; the sole elevated operation is fixed bwrap launch.

    Config (root:root 0600, e.g. /etc/prfkt-codex-sandbox.json)::

      {"schema_version":1,"run_root":"/srv/prfkt-runs","uid":1000,
       "gid":1000,"socket":"/run/prfkt-codex-sandbox.sock"}

    Create run_root as uid:gid 0700. Install this module root:root, outside every
    app-writable directory. Start /usr/bin/python3 /usr/local/libexec/
    codex_sandbox.py --serve /etc/prfkt-codex-sandbox.json in a separate root
    service. Set a service TasksMax/MemoryMax for workload resource bounds and
    KillMode=control-group. Do not add sudo or relax the dashboard's
    NoNewPrivileges. The socket is 0600 uid:gid; SO_PEERCRED independently checks
    the caller. Configure the dashboard factory with broker_socket=that path.

    No readiness is asserted until actual filesystem and network isolation
    checks pass. bwrap failures, namespace restrictions, unavailable kernel
    features and unsupported hosts disable execution. Never use --share-net as
    a fallback. The broker does not access Git, authentication or source repos.
    """
    def __init__(self, config):
        if sys.platform != "linux" or os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
            raise SandboxError("The sandbox broker requires Linux root and peer credentials")
        self.config = _broker_configuration(config)
        self.root = Path(self.config["run_root"])
        self.socket_path = Path(self.config["socket"])
        self.uid, self.gid = self.config["uid"], self.config["gid"]
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.factory = SandboxWorkspace.__new__(SandboxWorkspace)
        # Pin the configured directory once; a caller cannot redirect later
        # privileged reads or probe writes by replacing an ancestor pathname.
        self.factory.run_root = Path("/proc/" + str(os.getpid()) + "/fd/" + str(self.root_fd))
        self.factory.trusted_repo = None
        self.factory.platform = "linux"
        self.factory.binary = Path("/usr/bin/bwrap")
        self.factory.broker_socket = None
        self.factory.worker_uid, self.factory.worker_gid = self.uid, self.gid
        self.factory.lock = threading.RLock()
        self.factory._proof = None
        self.slots = threading.BoundedSemaphore(2)
        self.connections = threading.BoundedSemaphore(8)
        self.active_lock = threading.Lock()
        self.active_runs = set()

    def _workspace_fd(self, run_id):
        _broker_request({"op": "execute", "run_id": run_id, "command": "true", "timeout": 1})
        fd = os.dup(self.root_fd)
        try:
            for component in (run_id, "workspace"):
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if info.st_uid != self.uid or stat.S_IMODE(info.st_mode) != 0o700:
                    raise SandboxError("The requested workspace is not private runner storage")
        except BaseException:
            os.close(fd)
            raise
        info = os.fstat(fd)
        if info.st_uid != self.uid or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise SandboxError("The requested workspace is not private runner storage")
        return fd

    def _handle(self, connection):
        stop = threading.Event()
        acquired = False
        run_id = None
        monitor = None
        def send(value):
            try:
                connection.sendall(json.dumps(value, separators=(",", ":")).encode() + b"\n")
            except OSError:
                stop.set()
                raise SandboxError("Sandbox client disconnected") from None
        try:
            connection.settimeout(5)
            _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.uid:
                raise SandboxError("Sandbox client identity is not authorized")
            raw = b""
            deadline = time.monotonic() + 5
            while b"\n" not in raw:
                if time.monotonic() >= deadline:
                    raise SandboxError("Sandbox broker request deadline expired")
                connection.settimeout(max(.01, deadline - time.monotonic()))
                chunk = connection.recv(8192)
                if not chunk:
                    return
                raw += chunk
                if len(raw) > 65536:
                    raise SandboxError("Sandbox broker request is too large")
            line, extra = raw.split(b"\n", 1)
            if extra:
                raise SandboxError("Only one broker request is allowed per connection")
            request = _broker_request(_strict_object(line))
            if request["op"] == "health":
                proof = self.factory.capabilities()
                send({"result": {**proof, "backend": "bubblewrap-broker"}})
                return
            if not self.factory.capabilities()["available"]:
                raise SandboxError("Hard sandbox isolation is unavailable")
            if not self.slots.acquire(blocking=False):
                raise SandboxError("Sandbox execution capacity is full")
            acquired = True
            with self.active_lock:
                if request["run_id"] in self.active_runs:
                    raise SandboxError("This workspace already has an active command")
                run_id = request["run_id"]
                self.active_runs.add(run_id)
            def disconnected():
                while not stop.is_set():
                    try:
                        # EOF or unsolicited additional input both cancel the command.
                        connection.recv(1)
                        stop.set()
                        return
                    except socket.timeout:
                        continue
                    except OSError:
                        stop.set()
                        return
            connection.settimeout(.2)
            monitor = threading.Thread(target=disconnected, daemon=True)
            monitor.start()
            fd = self._workspace_fd(run_id)
            try:
                # chdir resolves the pinned FD while still host root, before
                # exec/user-namespace setup. bwrap binds its own cwd, avoiding
                # cross-process /proc ptrace restrictions after unsharing.
                source = "/proc/" + str(os.getpid()) + "/fd/" + str(fd)
                args, env = self.factory._arguments(self.root / run_id / "workspace", request["command"], mount_source="/proc/self/cwd")
                result = _process(args, cwd=source, env=env, timeout=request["timeout"], stop_event=stop,
                                  on_output=lambda stream, text: send({"stream": stream, "text": text}))
                if not stop.is_set():
                    send({"result": {**result, "stdout": result["stdout"].decode("utf-8", errors="replace"),
                                     "stderr": result["stderr"].decode("utf-8", errors="replace")}})
            finally:
                os.close(fd)
        except (SandboxError, OSError, ValueError, subprocess.SubprocessError):
            if not stop.is_set():
                try:
                    send({"error": "Sandbox request was unavailable or rejected"})
                except (SandboxError, OSError):
                    pass
        finally:
            stop.set()
            if run_id:
                with self.active_lock:
                    self.active_runs.discard(run_id)
            if acquired:
                self.slots.release()
            connection.close()
            if monitor:
                monitor.join(timeout=.3)

    def serve(self):
        if self.socket_path.exists() or self.socket_path.is_symlink():
            info = self.socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.uid or stat.S_IMODE(info.st_mode) != 0o600:
                raise SandboxError("An unexpected file occupies the broker socket")
            # Never replace a listening service. Only recover its stale socket.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(1)
                probe.connect(str(self.socket_path))
            except ConnectionRefusedError:
                self.socket_path.unlink()
            else:
                raise SandboxError("A sandbox broker is already listening")
            finally:
                probe.close()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            previous = os.umask(0o177)
            try:
                listener.bind(str(self.socket_path))
            finally:
                os.umask(previous)
            os.chown(self.socket_path, self.uid, self.gid)
            os.chmod(self.socket_path, 0o600)
            listener.listen(4)
            while True:
                connection, _ = listener.accept()
                if not self.connections.acquire(blocking=False):
                    connection.close()
                    continue
                def handle(client):
                    try:
                        self._handle(client)
                    finally:
                        self.connections.release()
                threading.Thread(target=handle, args=(connection,), daemon=True).start()
        finally:
            listener.close()
            self.socket_path.unlink(missing_ok=True)
            os.close(self.root_fd)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fixed-path root-owned Linux sandbox broker")
    parser.add_argument("--serve", required=True, metavar="CONFIG")
    options = parser.parse_args()
    try:
        SandboxBroker(options.serve).serve()
    except (SandboxError, OSError):
        parser.exit(1, "Sandbox broker failed its configuration or isolation boundary.\n")
