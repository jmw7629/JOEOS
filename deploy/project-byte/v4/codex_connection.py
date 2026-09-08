"""Private, owner-scoped chat transport through the official Codex app-server.

Codex owns subscription authentication and token refresh. This module never parses
auth.json or exposes tokens. Only models proven to advertise no execution tools
on the pinned runtime are accepted; interaction requests are always rejected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import threading
import time


DISABLED = ('shell_tool', 'shell_snapshot', 'unified_exec', 'apps', 'plugins', 'hooks', 'browser_use',
            'browser_use_external', 'computer_use', 'image_generation', 'view_image',
            'multi_agent', 'multi_agent_v2', 'code_mode', 'code_mode_host', 'memories',
            'workspace_dependencies', 'sleep_tool', 'in_app_browser', 'in_app_chat',
            'in_app_local_automation', 'skill_mcp_dependency_install')
LIMIT = 4 * 1024 * 1024
PINNED_VERSION = '0.153.4'
# The native fixture captures actual inference tool definitions for each model.
# Older models still advertise apply_patch despite feature flags; fail closed.
CHAT_MODELS = frozenset(('gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra',
                         'gpt-5.6-luna', 'gpt-5.3-codex-spark'))
DEFAULT_MODEL = 'gpt-6-astra'


class CodexError(RuntimeError):
    pass


class _RPC:
    def __init__(self, binary, home, workspace, timeout=20):
        args = [str(binary), 'app-server', '--stdio']
        for name in DISABLED:
            args += ['--disable', name]
        for setting in ('mcp_servers={}', 'web_search="disabled"', 'project_doc_max_bytes=0',
                        'features.skip_host_skill_discovery=true', 'analytics.enabled=false',
                        'cli_auth_credentials_store="file"'):
            args += ['-c', setting]
        # No inherited provider keys, tool configuration or alternate auth modes.
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
        env.update(CODEX_HOME=str(home), NO_COLOR='1')
        self.process = subprocess.Popen(args, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                                        start_new_session=True)
        self.buffer = b''
        os.set_blocking(self.process.stdin.fileno(), False)
        self.closed = False
        self.release = None
        self.seq = 0
        self.events = []
        self.timeout = timeout
        try:
            self.request('initialize', {'clientInfo': {'name': 'project_byte', 'title': 'PRFKT_PROJECT', 'version': '1.0'}})
            self.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()
        if self.release:
            self.release()
            self.release = None

    def send(self, message, deadline=None):
        raw = json.dumps(message, separators=(',', ':')).encode() + b'\n'
        if len(raw) > LIMIT:
            raise CodexError('Codex request is too large')
        try:
            deadline = deadline or time.monotonic() + self.timeout
            sent = 0
            while sent < len(raw):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self.process.stdin], [], remaining)[1]:
                    raise CodexError('Codex request timed out')
                count = os.write(self.process.stdin.fileno(), raw[sent:])
                if count <= 0:
                    raise CodexError('Codex connection ended')
                sent += count
        except (OSError, ValueError):
            raise CodexError('Codex connection ended') from None

    def read(self, deadline):
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexError('Codex request timed out')
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                raise CodexError('Codex request timed out')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise CodexError('Codex connection ended')
            self.buffer += chunk
            if len(self.buffer) > LIMIT:
                raise CodexError('Codex response is too large')
        line, self.buffer = self.buffer.split(b'\n', 1)
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            raise CodexError('Codex returned an invalid response') from None
        if not isinstance(message, dict):
            raise CodexError('Codex returned an invalid response')
        if 'method' in message and 'id' in message:
            self.send({'id': message['id'], 'error': {'code': -32000, 'message': 'Tool execution is unavailable in workspace chat'}})
            raise CodexError('This request requires an execution connection; no tool was approved')
        return message

    def request(self, method, params=None, deadline=None):
        self.seq += 1
        request_id = self.seq
        deadline = deadline or time.monotonic() + self.timeout
        self.send({'id': request_id, 'method': method, 'params': params or {}}, deadline=deadline)
        while True:
            message = self.read(deadline)
            if message.get('id') == request_id:
                if 'error' in message:
                    raise CodexError('Codex could not complete the request; check its connection and model access')
                return message.get('result') or {}
            if len(self.events) >= 1000:
                raise CodexError('Codex sent too many events')
            self.events.append(message)


class Connection:
    def __init__(self, binary=None, home=None, workspace=None):
        self.binary = Path(binary or os.getenv('PROJECT_BYTE_CODEX_BIN', '/nonexistent/project-byte-codex'))
        self.home = Path(home or os.getenv('PROJECT_BYTE_CODEX_HOME', '/nonexistent/project-byte-codex-auth'))
        self.workspace = Path(workspace or os.getenv('PROJECT_BYTE_CODEX_WORKSPACE', '/nonexistent/project-byte-codex-workspace'))
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.RLock()
        self.cached = None
        self.cached_at = 0
        self.pending = None
        self.login_timer = None

    def available(self):
        try:
            return (self.binary.is_absolute() and self.binary.is_file() and os.access(self.binary, os.X_OK)
                    and all(p.is_absolute() and p.is_dir() and not p.is_symlink()
                            and p.stat().st_uid == os.getuid() and not stat.S_IMODE(p.stat().st_mode) & 0o077
                            for p in (self.home, self.workspace)))
        except OSError:
            return False

    def _open(self, timeout=20):
        if not self.available():
            raise CodexError('Codex is not installed for this workspace')
        if not self.slots.acquire(blocking=False):
            raise CodexError('Codex is busy; try again shortly')
        try:
            env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG') if key in os.environ}
            env['CODEX_HOME'] = str(self.home)
            try:
                version = subprocess.run([str(self.binary), '--version'], cwd=self.workspace,
                                         env=env, capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                raise CodexError('The verified Codex runtime is unavailable') from None
            if version.returncode or version.stdout.strip() != 'codex-cli ' + PINNED_VERSION:
                raise CodexError('This Codex runtime version needs a new chat capability review')
            rpc = _RPC(self.binary, self.home, self.workspace, timeout)
            rpc.release = self.slots.release
            return rpc
        except Exception:
            self.slots.release()
            raise

    def status(self):
        with self.lock:
            if self.cached and time.monotonic() - self.cached_at < 10 and not self.pending:
                return dict(self.cached)
            if not self.available():
                return {'available': False, 'connected': False, 'auth_mode': None, 'detail': 'Codex runtime is not installed'}
            rpc = self.pending['rpc'] if self.pending else None
            own_rpc = rpc is None
            try:
                rpc = rpc or self._open()
                response = rpc.request('account/read', {'refreshToken': False})
                account = response.get('account') or {}
                connected = account.get('type') == 'chatgpt'
                result = {'available': True, 'connected': connected, 'auth_mode': 'chatgpt' if connected else None,
                          'detail': 'ChatGPT subscription connected' if connected else 'Sign in with ChatGPT to connect your subscription'}
                if self.pending:
                    result['login'] = dict(self.pending['public'])
                    failed = any(event.get('method') == 'account/login/completed' and (event.get('params') or {}).get('success') is False for event in rpc.events)
                    if failed:
                        result['login'] = {'state': 'failed'}
                        result['detail'] = 'ChatGPT sign-in ended; start a new sign-in'
                        self._close_login()
                    elif connected:
                        result['login']['state'] = 'complete'
                        self._close_login()
                self.cached, self.cached_at = dict(result), time.monotonic()
                return result
            except (CodexError, OSError):
                if self.pending:
                    self._close_login()
                return {'available': True, 'connected': False, 'auth_mode': None, 'detail': 'Codex connection is unavailable; try again'}
            finally:
                if own_rpc and rpc:
                    rpc.close()

    def _close_login(self):
        with self.lock:
            if self.pending:
                self.pending['rpc'].close()
                self.pending = None
            if self.login_timer:
                self.login_timer.cancel()
                self.login_timer = None
            self.cached = None

    def login(self):
        with self.lock:
            state = self.status()
            if state['connected']:
                return {'state': 'complete'}
            if self.pending:
                return dict(self.pending['public'])
            rpc = self._open()
            try:
                response = rpc.request('account/login/start', {'type': 'chatgptDeviceCode'})
                url = response.get('verificationUrl', '')
                code = response.get('userCode', '')
                if url != 'https://auth.openai.com/codex/device' or not re.fullmatch(r'[A-Z0-9-]{6,24}', code):
                    raise CodexError('Codex did not provide a supported sign-in flow')
                public = {'state': 'pending', 'verification_url': url, 'user_code': code}
                self.pending = {'rpc': rpc, 'public': public}
                self.login_timer = threading.Timer(600, self._close_login)
                self.login_timer.daemon = True
                self.login_timer.start()
                return dict(public)
            except Exception:
                rpc.close()
                raise

    def models(self):
        if not self.status()['connected']:
            raise CodexError('Sign in with ChatGPT before choosing a Codex model')
        rpc = self._open()
        try:
            cursor = None
            models = []
            for _ in range(5):
                response = rpc.request('model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor})
                for model in response.get('data', []):
                    name = model.get('model') or model.get('id')
                    if isinstance(name, str) and name in CHAT_MODELS:
                        models.append({'id': name, 'name': str(model.get('displayName') or name)[:100],
                                       'is_default': bool(model.get('isDefault')), 'description': str(model.get('description') or '')[:300]})
                cursor = response.get('nextCursor')
                if not cursor:
                    break
            return models
        finally:
            rpc.close()

    def complete(self, messages, model=DEFAULT_MODEL):
        if not isinstance(model, str) or model not in CHAT_MODELS:
            raise CodexError('This Codex model is not verified for workspace chat; choose a detected model')
        if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
            raise CodexError('Invalid chat messages')
        parts = []
        for item in messages:
            if not isinstance(item, dict) or item.get('role') not in ('system', 'developer', 'user', 'assistant') or not isinstance(item.get('content'), str):
                raise CodexError('Invalid chat message')
            parts.append(item['role'].upper() + ':\n' + item['content'])
        prompt = '\n\n'.join(parts)
        if len(prompt.encode()) > 100000:
            raise CodexError('Chat context is too large')
        rpc = None
        start = time.monotonic()
        deadline = start + 160
        try:
            rpc = self._open()
            account = rpc.request('account/read', {'refreshToken': False}, deadline=deadline).get('account') or {}
            if account.get('type') != 'chatgpt':
                raise CodexError('Sign in with ChatGPT to use this subscription connection')
            params = {'cwd': str(self.workspace), 'modelProvider': 'openai', 'sandbox': 'read-only', 'approvalPolicy': 'never', 'ephemeral': True,
                      'serviceName': 'project_byte_chat', 'developerInstructions': 'You are AI_BYTE, the PRFKT_PROJECT workspace assistant. Answer using the supplied conversation context. This connection supports conversation only; do not run tools, access files, start agents, or claim to execute work.'}
            if model:
                params['model'] = model
            created = rpc.request('thread/start', params, deadline=deadline)
            if (created.get('model') != model or created.get('modelProvider') != 'openai' or created.get('approvalPolicy') != 'never'
                    or created.get('sandbox') != {'type': 'readOnly', 'networkAccess': False}
                    or created.get('cwd') != str(self.workspace) or created.get('instructionSources') != []):
                raise CodexError('Codex did not apply the required private chat configuration')
            thread = created.get('thread') or {}
            if not isinstance(thread.get('id'), str):
                raise CodexError('Codex did not create a chat session')
            rpc.events.clear()
            turn = rpc.request('turn/start', {'threadId': thread['id'], 'input': [{'type': 'text', 'text': prompt}], 'effort': 'low'}, deadline=deadline)
            turn_id = (turn.get('turn') or {}).get('id')
            text = []
            size = 0
            while True:
                event = rpc.events.pop(0) if rpc.events else rpc.read(deadline)
                method = event.get('method')
                values = event.get('params') or {}
                if method in ('item/started', 'item/completed'):
                    if values.get('threadId') != thread['id'] or values.get('turnId') != turn_id:
                        raise CodexError('Codex returned an event for another conversation')
                    item = values.get('item') or {}
                    if item.get('type') not in ('agentMessage', 'reasoning', 'userMessage', 'plan'):
                        raise CodexError('This request requires an execution connection; workspace chat stopped')
                    if method == 'item/completed' and item.get('type') == 'agentMessage':
                        value = item.get('text')
                        if isinstance(value, str):
                            size += len(value.encode())
                            if size > 1024 * 1024:
                                raise CodexError('Codex reply is too large')
                            text.append(value)
                if method == 'turn/completed' and values.get('threadId') == thread['id']:
                    finished = values.get('turn') or {}
                    if finished.get('id') != turn_id or finished.get('status') != 'completed':
                        raise CodexError('Codex did not complete the reply; check model access or subscription limits')
                    answer = '\n'.join(text).strip()
                    if not answer:
                        raise CodexError('Codex returned an empty reply')
                    return answer, int((time.monotonic() - start) * 1000)
        except OSError:
            raise CodexError('Codex connection is unavailable') from None
        finally:
            if rpc:
                rpc.close()
