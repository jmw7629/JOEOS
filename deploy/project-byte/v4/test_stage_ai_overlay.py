"""Code-only staging checks and real HTTP tests of d95 plus the exact AI overlay."""
from contextlib import ExitStack
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import stage_ai_overlay as overlay

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[2]
OWNER = 'OWNER_OVERLAY_FIXTURE_ONLY'
EDITOR = 'EDITOR_OVERLAY_FIXTURE_ONLY'


def protected_files():
    def read(path):
        return subprocess.run(['git', 'show', overlay.BASE_COMMIT + ':deploy/project-byte/v4/' + path],
                              cwd=REPO, check=True, capture_output=True, timeout=10).stdout
    index = b''.join(read('index/%02d.part' % n) for n in range(1, 8))
    for name in ('home.js', 'home-inspector.js', 'health-runtime.js'):
        marker = ('<script src="/' + name + '"></script>').encode()
        if marker not in index:
            index = index.replace(b'</body>', marker + b'</body>')
    files = {'server.py': read('runtime_server.py'), 'index.html': index,
             'backend.py': b''.join(read('server/%02d.part' % n) for n in range(1, 9))}
    files.update({name: read(name) for name in ('home.js', 'home-inspector.js', 'health-runtime.js')})
    return files


class ProtectedFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = protected_files()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pb-ai-overlay-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source_app = self.root / 'source-app'
        self.source_app.mkdir()
        for name, data in self.baseline.items():
            (self.source_app / name).write_bytes(data)
        # Deliberately present fixture-only state that must never be read/copied.
        (self.source_app / 'admin.secret').write_text('DO_NOT_COPY_FIXTURE_CREDENTIAL')
        (self.source_app / 'kanban.db').write_text('DO_NOT_COPY_FIXTURE_DATABASE')
        (self.source_app / 'execution_permissions.py').write_text('raise RuntimeError("pending feature")')
        self.output = self.root / 'staged-app'


class StagingTests(ProtectedFixture):
    def test_exact_overlay_contains_only_allowed_code_and_safe_hash_manifest(self):
        before = {name: (self.source_app / name).read_bytes() for name in self.baseline}
        manifest = overlay.stage(self.source_app, self.output)
        expected = set(overlay.BASE_HASHES) | set(overlay.FEATURE_FILES) | {'overlay-manifest.json'}
        self.assertEqual({p.name for p in self.output.iterdir()}, expected)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual(manifest['base_commit'], overlay.BASE_COMMIT)
        self.assertEqual(manifest, json.loads((self.output / 'overlay-manifest.json').read_text()))
        for name, expected_hash in manifest['output_sha256'].items():
            self.assertEqual(overlay.sha256((self.output / name).read_bytes()), expected_hash)
        self.assertEqual((self.output / 'server.py').read_bytes().replace(overlay.RUNTIME_INSERT, b'', 1), before['server.py'])
        self.assertEqual((self.output / 'index.html').read_bytes().replace(overlay.INDEX_INSERT, b'', 1), before['index.html'])
        for name in before:
            self.assertEqual((self.source_app / name).read_bytes(), before[name])
        for name in overlay.FEATURE_FILES:
            self.assertEqual((self.output / name).read_bytes(), (SOURCE / name).read_bytes())
        self.assertNotIn('DO_NOT_COPY', ''.join(p.read_text() for p in self.output.iterdir()))

    def test_every_baseline_hash_is_required_before_creating_output(self):
        for name, original in self.baseline.items():
            with self.subTest(name=name):
                (self.source_app / name).write_bytes(original + b'\n')
                with self.assertRaisesRegex(overlay.StageError, 'baseline hash mismatch'):
                    overlay.stage(self.source_app, self.output)
                self.assertFalse(self.output.exists())
                (self.source_app / name).write_bytes(original)

    def test_unknown_ai_installation_and_symlink_code_fail_closed(self):
        for name in ('ai_connections.py', 'ai-other.js', 'ai_unknown.py', 'codex_connection.py'):
            path = self.source_app / name
            path.write_text('unexpected installation')
            with self.subTest(name=name), self.assertRaisesRegex(overlay.StageError, 'existing AI file'):
                overlay.stage(self.source_app, self.output)
            self.assertFalse(self.output.exists()); path.unlink()
        runtime = self.source_app / 'server.py'
        runtime.unlink(); runtime.symlink_to(SOURCE / 'runtime_server.py')
        with self.assertRaises(OSError):
            overlay.stage(self.source_app, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_source_output_and_invalid_feature_do_not_mutate(self):
        self.output.mkdir(); sentinel = self.output / 'keep'; sentinel.write_text('preserve')
        with self.assertRaisesRegex(overlay.StageError, 'already exist'):
            overlay.stage(self.source_app, self.output)
        self.assertEqual(sentinel.read_text(), 'preserve')
        with self.assertRaisesRegex(overlay.StageError, 'outside'):
            overlay.stage(self.source_app, self.source_app / 'new-output')
        features = self.root / 'bad-features'; features.mkdir()
        for name in overlay.FEATURE_FILES:
            (features / name).write_bytes((SOURCE / name).read_bytes())
        (features / 'ai_runtime.py').write_text('def invalid syntax:')
        with self.assertRaises(SyntaxError):
            overlay.stage(self.source_app, self.root / 'must-not-exist', features)
        self.assertFalse((self.root / 'must-not-exist').exists())


class FakeCodex:
    def __init__(self):
        self.calls = []

    def status(self):
        return {'available': True, 'connected': True, 'auth_mode': 'chatgpt', 'detail': 'Isolated test fixture'}

    def models(self):
        return [{'id': 'overlay-fixture', 'name': 'Overlay fixture'}]

    def complete(self, messages, model=''):
        self.calls.append((messages, model))
        return 'OVERLAY_FIXTURE_OK', 1


class OverlayHTTPTests(ProtectedFixture):
    def setUp(self):
        super().setUp()
        overlay.stage(self.source_app, self.output)
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {'HOME': str(self.root / 'user'), 'XDG_STATE_HOME': str(self.root / 'state')}))
        stack.enter_context(patch.dict(sys.modules))

        def load(name, filename):
            spec = importlib.util.spec_from_file_location(name, self.output / filename)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            self.assertEqual(Path(module.__file__).parent, self.output)
            return module

        self.app = load('backend', 'backend.py')
        self.app.SECRET.write_text(OWNER); self.app.SECRET.chmod(0o600)
        self.app.init_db()
        connections = load('ai_connections', 'ai_connections.py')
        load('codex_connection', 'codex_connection.py')
        integration = load('ai_runtime', 'ai_runtime.py')
        self.codex = FakeCodex(); self.transport_calls = []; self.issues = []

        def transport(endpoint, path, provider, headers, payload=None, timeout=None):
            self.transport_calls.append((provider, path))
            if path == '/api/tags':
                return {'models': [{'name': 'existing-ollama-fixture'}]}
            return {'message': {'content': 'EXISTING_MODEL_OK'}}

        integration.Connection = lambda: self.codex
        integration.Manager = lambda app, codex: connections.Manager(app, codex, transport=transport)
        self.app.create_github_issue = lambda *args: self.issues.append(args) or (1, 'https://example.invalid/fixture')
        self.runtime = load('overlay_server_fixture', 'server.py')
        self.runtime.SafeHandler.log_message = lambda *args: None
        # Import did not start sync/workers or initialize connection metadata.
        with self.app.con() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='ai_connections'").fetchone())
            db.execute('INSERT INTO collaborators VALUES(?,?,?,?,?,?,?)',
                       ('overlay-editor', 'Fixture editor', 'editor', self.app.digest(EDITOR), 1, self.app.now(), 0))
        self.app.save_settings('owner', {'general': {'density': 'compact'}, 'ai': {'default_model': 'ollama-local'}})
        self.app.save_settings('collab:overlay-editor', {'ai': {'default_model': 'ollama-local'}})
        # Any unexpected CLI/provider command is a test failure, never a live call.
        stack.enter_context(patch('subprocess.run', side_effect=AssertionError('Unexpected external command in overlay fixture')))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.runtime.SafeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, path, body=None, key=OWNER):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        client.request('GET' if body is None else 'POST', path,
                       None if body is None else json.dumps(body).encode(),
                       {'X-Access-Key': key, 'Content-Type': 'application/json'})
        response = client.getresponse(); raw = response.read(); status = response.status
        kind = response.getheader('Content-Type', ''); client.close()
        return status, json.loads(raw) if 'application/json' in kind else raw

    def connect(self):
        status, result = self.request('/api/ai-connections/connect', {'provider': 'codex-chatgpt',
                                     'model_name': 'overlay-fixture', 'model_key': 'fixture/private', 'set_default': True})
        self.assertEqual(status, 200, result)

    def test_exact_runtime_serves_ai_asset_without_pending_feature_endpoints(self):
        self.assertEqual(self.request('/ai-connections.js'), (200, (self.output / 'ai-connections.js').read_bytes()))
        for path in ('/api/execution-permissions', '/api/workspace-profile', '/ai_connections.py', '/admin.secret', '/kanban.db'):
            self.assertEqual(self.request(path)[0], 404, path)
        for key in ('', EDITOR):
            self.assertEqual(self.request('/api/ai-connections', key=key)[0], 403)
        self.assertEqual(self.request('/api/ai-connections')[0], 200)
        self.assertEqual(self.codex.calls, [])

    def test_owner_default_chat_preserves_settings_and_collaborator_boundary(self):
        before_owner = self.app.get_settings('owner')
        before_editor = self.app.get_settings('collab:overlay-editor')
        self.connect()
        for key in ('', EDITOR):
            for path, body in (('/api/chat', {'message': 'private request', 'model_key': 'fixture/private'}),
                               ('/api/models/test', {'model_key': 'fixture/private'}),
                               ('/api/models', {'provider': 'ollama', 'model_name': 'manual'})):
                self.assertEqual(self.request(path, body, key)[0], 403)
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chat_messages').fetchone()[0], 0)
        code, result = self.request('/api/chat', {'message': 'Isolated default test'})
        self.assertEqual(code, 200, result); self.assertEqual(result['model_key'], 'fixture/private')
        self.assertEqual(len(self.codex.calls), 1)
        code, result = self.request('/api/chat', {'message': 'Existing model'}, EDITOR)
        self.assertEqual(code, 200, result); self.assertEqual(result['model_key'], 'ollama-local')
        self.assertEqual(self.app.get_settings('collab:overlay-editor'), before_editor)
        self.assertEqual(self.app.get_settings('owner')['general'], before_owner['general'])
        self.assertEqual(self.request('/api/ai-connections/disconnect', {'model_key': 'fixture/private'})[0], 200)
        count = len(self.transport_calls)
        self.assertEqual(self.request('/api/chat', {'message': 'Disabled default'})[0], 409)
        self.assertEqual(len(self.transport_calls), count); self.assertEqual(len(self.codex.calls), 1)

    def test_managed_queue_has_no_dispatch_and_existing_owner_hold_wins(self):
        self.connect(); self.app.BRIDGES['fixture/repo'] = '<!-- fixture only -->'
        with self.app.con() as db:
            db.execute("INSERT INTO projects(name,repo,updated_at) VALUES('Overlay fixture','fixture/repo',?)", (self.app.now(),))
        code, task = self.request('/api/tasks', {'title': 'Isolated queue test', 'project': 'Overlay fixture', 'status': 'Backlog', 'priority': 'Medium'})
        self.assertEqual(code, 201, task)
        path = '/api/tasks/' + task['id'] + '/queue'
        code, result = self.request(path, {'model_key': 'fixture/private'})
        self.assertEqual(code, 409, result); self.assertIn('execution bridge', result['error'])
        self.app.execution_hold = lambda repo: {'reason': 'Paused by owner fixture', 'reason_code': 'owner-paused', 'http_status': 409}
        code, result = self.request(path, {'model_key': 'fixture/private'})
        self.assertEqual(code, 409, result); self.assertEqual(result['code'], 'owner-paused')
        self.assertEqual(self.issues, []); self.assertEqual(self.codex.calls, [])
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?', (task['id'],)).fetchone()[0], 'Backlog')


if __name__ == '__main__':
    unittest.main()
