"""HTTP integration tests against the reconstructed existing app and real Manager."""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import socket
import time
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from ai_connections import ConnectionError, Manager, AUTO_CODEX_MODEL, AUTO_CODEX_KEY
from ai_runtime import install
from codex_connection import CodexError

SOURCE = Path(__file__).resolve().parent
OWNER = 'OWNER_AI_RUNTIME_FIXTURE_ONLY'


class FakeCodex:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = False

    def status(self):
        return {'available': True, 'connected': True, 'auth_mode': 'chatgpt', 'detail': 'Fixture connected'}

    def models(self):
        return [{'id': 'fixture-model', 'display_name': 'Fixture model'}]

    def login(self):
        if self.failure:
            raise CodexError('PRIVATE_TOKEN_MUST_NOT_ESCAPE')
        return {'state': 'complete'}

    def complete(self, messages, model):
        self.calls.append({'messages': messages, 'model': model})
        if any(m['content'] == 'WAIT_FOR_CONCURRENCY' for m in messages):
            self.entered.set()
            if not self.release.wait(8):
                raise RuntimeError('Test release missing')
        return 'PROJECT_BYTE_OK', 1


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pb-ai-runtime-')
        self.root = Path(self.tmp.name)/'app'
        self.root.mkdir()
        self.env = patch.dict(os.environ, {'HOME': str(Path(self.tmp.name)/'user'), 'XDG_STATE_HOME': str(Path(self.tmp.name)/'state')})
        self.env.start()
        self.app = types.ModuleType('ai_runtime_backend_fixture')
        self.app.__file__ = str(self.root/'backend.py')
        source = ''.join(p.read_text() for p in sorted((SOURCE/'server').glob('*.part')))
        exec(compile(source, self.app.__file__, 'exec'), self.app.__dict__)
        self.app.SECRET.write_text(OWNER)
        self.app.SECRET.chmod(0o600)
        self.app.init_db()
        self.legacy_calls = []
        self.transport_calls = []
        self.issues = []
        def transport(endpoint, path, provider, headers, payload=None, timeout=None):
            if path == '/api/tags': return {'models': [{'name': 'installed-fixture:latest'}]}
            self.transport_calls.append({'endpoint': endpoint, 'path': path, 'provider': provider, 'headers': headers, 'payload': payload})
            return {'message': {'content': 'LEGACY_MODEL_OK'}, 'output_text': 'LEGACY_MODEL_OK'}
        self.manager_patch = patch('ai_runtime.Manager', side_effect=lambda app, codex: Manager(app, codex, transport=transport))
        self.manager_patch.start()
        def legacy_call(key, messages):
            self.legacy_calls.append((key, messages))
            if messages[-1]['content'] == 'ELEVATE':
                return self.app.call_model('fixture/private', messages)
            return 'LEGACY_MODEL_OK', 2
        self.app.call_model = legacy_call
        self.app.create_github_issue = lambda *args: self.issues.append(args) or (123, 'https://github.com/fixture/repo/issues/123')
        self.codex = FakeCodex()
        class Silent(self.app.H):
            def log_message(self, *args):
                pass
        self.public_files = {}
        self.handler = install(self.app, Silent, self.public_files, self.codex)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tokens = {'owner': OWNER}
        with self.app.con() as db:
            for role in ('viewer', 'editor', 'admin'):
                key = role.upper()+'_AI_RUNTIME_FIXTURE_ONLY'
                self.tokens[role] = key
                db.execute('INSERT INTO collaborators VALUES(?,?,?,?,?,?,?)', (role, role.title(), role, self.app.digest(key), 1, self.app.now(), 0))
        self.app.save_settings('owner', {'general': {'density': 'compact'}, 'security': {'session_minutes': 120}, 'ai': {'default_model': 'ollama-local'}})
        self.app.save_settings('collab:editor', {'general': {'density': 'comfortable'}, 'ai': {'default_model': 'ollama-local'}})

    def tearDown(self):
        self.codex.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.manager_patch.stop(); self.env.stop(); self.tmp.cleanup()

    def request(self, route, body=None, role='owner', method=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        actual_headers = {'X-Access-Key': self.tokens.get(role, ''), 'Content-Type': 'application/json'}
        actual_headers.update(headers or {})
        data = None if body is None else json.dumps(body).encode()
        connection.request(method or ('GET' if body is None else 'POST'), route, data, actual_headers)
        response = connection.getresponse()
        raw = response.read(); status = response.status; connection.close()
        return status, json.loads(raw)

    def raw(self, payload=b'{}', headers=None, route='/api/ai-connections/login'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.putrequest('POST', route, skip_host=True, skip_accept_encoding=True)
        foundation = [('Host', '127.0.0.1:'+str(self.server.server_port)), ('X-Access-Key', OWNER)]
        for name, value in foundation + (headers if headers is not None else [('Content-Type', 'application/json'), ('Content-Length', str(len(payload)))]):
            connection.putheader(name, value)
        connection.endheaders(payload)
        response = connection.getresponse(); result = (response.status, json.loads(response.read())); connection.close(); return result

    def connect(self, set_default=False, key='fixture/private'):
        code, data = self.request('/api/ai-connections/connect', {'provider': 'codex-chatgpt', 'model_key': key, 'model_name': 'fixture-model', 'display_name': 'Private Codex', 'set_default': set_default})
        self.assertEqual(code, 200, data)
        return data

    def chat_count(self):
        with self.app.con() as db:
            return db.execute('SELECT count(*) FROM chat_messages').fetchone()[0]


    def test_install_supports_readonly_backend_mocks_without_initializing_manager(self):
        mock_app = types.SimpleNamespace()
        files = {}
        installed = install(mock_app, self.app.H, files, self.codex)
        self.assertTrue(issubclass(installed, self.app.H))
        self.assertIn('/ai-connections.js', files)
        self.assertTrue(callable(mock_app.route_model))

    def test_lazy_initialization_and_catalog_owner_boundary(self):
        with self.app.con() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='ai_connections'").fetchone())
        self.assertIn('/ai-connections.js', self.public_files)
        for role in ('', 'viewer', 'editor', 'admin'):
            self.assertEqual(self.request('/api/ai-connections', role=role)[0], 403)
        with self.app.con() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='ai_connections'").fetchone())
        code, data = self.request('/api/ai-connections')
        self.assertEqual(code, 200); self.assertTrue(data['codex']['connected'])
        self.assertIn('ollama', [p['id'] for p in data['providers']])
        self.assertEqual(data['default_model_key'], 'ollama-local')

    def test_owner_default_explicit_selection_and_other_settings_preserved(self):
        before_owner = self.app.get_settings('owner'); before_editor = self.app.get_settings('collab:editor')
        self.connect(set_default=True)
        code, result = self.request('/api/chat', {'message': 'Default uses subscription'})
        self.assertEqual(code, 200, result); self.assertEqual(result['model_key'], 'fixture/private')
        code, result = self.request('/api/chat', {'message': 'Explicit legacy provider', 'model_key': 'ollama-local'})
        self.assertEqual(code, 200, result); self.assertEqual(result['model_key'], 'ollama-local')
        code, result = self.request('/api/chat', {'message': 'Editor own default'}, role='editor')
        self.assertEqual(code, 200, result); self.assertEqual(result['model_key'], 'ollama-local')
        after = self.app.get_settings('owner')
        for field in ('general', 'security', 'notifications', 'filters'):
            self.assertEqual(after[field], before_owner[field])
        self.assertEqual(self.app.get_settings('collab:editor'), before_editor)
        self.assertEqual(len(self.codex.calls), 1)

    def test_owner_status_auto_connects_existing_subscription_and_routes_blank_default(self):
        before_editor=self.app.get_settings('collab:editor')
        self.app.save_settings('owner',{'ai':{'default_model':''}})
        with patch.object(self.codex,'models',return_value=[{'id':AUTO_CODEX_MODEL}]), patch.object(self.codex,'login',side_effect=AssertionError('Automatic status must never start sign-in')):
            code,result=self.request('/api/ai-connections')
            self.assertEqual(code,200,result)
            self.assertTrue(result['codex_auto_connection']['created'])
            self.assertEqual(result['default_model_key'],AUTO_CODEX_KEY)
            self.assertEqual(len(result['connections']),1)
            self.assertFalse(self.request('/api/ai-connections')[1]['codex_auto_connection']['created'])
        self.assertEqual(self.codex.calls,[])
        code,result=self.request('/api/chat',{'message':'Use detected subscription'})
        self.assertEqual(code,200,result);self.assertEqual(result['model_key'],AUTO_CODEX_KEY)
        self.assertEqual(self.app.get_settings('collab:editor'),before_editor)
        self.assertEqual(len(self.codex.calls),1)

    def test_owner_status_preserves_explicit_choice_and_exposes_unavailable_auto_setup(self):
        with patch.object(self.codex,'models',return_value=[{'id':AUTO_CODEX_MODEL}]):
            code,result=self.request('/api/ai-connections')
            self.assertEqual(code,200,result)
            self.assertEqual(result['default_model_key'],'ollama-local')
            self.assertFalse(result['codex_auto_connection']['default_set'])
        self.app.save_settings('owner',{'ai':{'default_model':''}})
        with patch.object(self.codex,'models',side_effect=CodexError('PRIVATE_PROVIDER_DETAIL')):
            code,result=self.request('/api/ai-connections')
            self.assertEqual(code,200,result)
            self.assertEqual(result['codex_auto_connection']['state'],'unavailable')
            self.assertTrue(result['codex']['connected'])
            self.assertIn('Automatic Astra setup is unavailable',result['codex']['detail'])
            self.assertNotIn('PRIVATE_PROVIDER_DETAIL',json.dumps(result))
        self.assertEqual(self.app.get_settings('owner')['ai']['default_model'],'')


    def test_legacy_supported_provider_rows_use_validated_transport_without_migration(self):
        with self.app.con() as db:
            before = dict(db.execute("SELECT * FROM models WHERE model_key='ollama-local'").fetchone())
            db.execute("UPDATE models SET enabled=1 WHERE model_key='openai/gpt-5.6-sol'")
            db.execute("INSERT INTO models(model_key,display_name,provider,model_name,enabled,updated_at) VALUES('fixture/legacy','Unknown legacy adapter','fixture-legacy','model',1,?)", (self.app.now(),))
        # Preserve blank legacy Ollama discovery by supplying a dynamic installed-model fixture.
        manager = self.handler.ai_manager(); original_transport = manager.transport
        def with_tags(endpoint, path, provider, headers, payload=None, timeout=None):
            if path == '/api/tags': return {'models': [{'name': 'installed-fixture:latest'}]}
            return original_transport(endpoint, path, provider, headers, payload, timeout)
        manager.transport = with_tags
        self.assertEqual(self.request('/api/chat', {'message': 'Existing Ollama'}, role='editor')[0], 200)
        self.assertEqual(self.transport_calls[-1]['payload']['model'], 'installed-fixture:latest')
        with patch.dict(os.environ, {'PROJECT_BYTE_OPENAI_API_KEY': 'LEGACY_CONFIGURED_FIXTURE_KEY'}):
            self.assertEqual(self.request('/api/chat', {'message': 'Existing OpenAI', 'model_key': 'openai/gpt-5.6-sol'})[0], 200)
        self.assertEqual(self.transport_calls[-1]['provider'], 'openai-responses')
        self.assertEqual(self.transport_calls[-1]['headers']['Authorization'], 'Bearer LEGACY_CONFIGURED_FIXTURE_KEY')
        self.assertFalse(manager.managed('ollama-local'))
        self.assertEqual(self.request('/api/chat', {'message': 'Legacy unknown fallback', 'model_key': 'fixture/legacy'})[0], 200)
        self.assertEqual(self.legacy_calls[-1][0], 'fixture/legacy')
        with self.app.con() as db:
            after = dict(db.execute("SELECT * FROM models WHERE model_key='ollama-local'").fetchone())
        for field in ('model_key', 'display_name', 'provider', 'endpoint', 'model_name', 'secret_env', 'enabled'):
            self.assertEqual(before[field], after[field])

    def test_legacy_model_creation_is_owner_validated_and_cannot_overwrite(self):
        body = {'model_key': 'fixture/legacy-created', 'display_name': 'Legacy client model', 'provider': 'ollama', 'model_name': 'installed:latest', 'endpoint': 'http://127.0.0.1:11434', 'secret_env': '', 'capabilities': ['chat'], 'local': True}
        for role in ('viewer', 'editor', 'admin'):
            self.assertEqual(self.request('/api/models', body, role=role)[0], 403)
        code, data = self.request('/api/models', body)
        self.assertEqual(code, 201, data); self.assertTrue(data['ok']); self.assertTrue(data['owner_only'])
        self.assertTrue(self.handler.ai_manager().managed(body['model_key']))
        self.assertEqual(self.request('/api/models', body)[0], 409)
        self.assertEqual(self.request('/api/models', {**body, 'model_key': 'fixture/metadata', 'endpoint': 'http://169.254.169.254'})[0], 400)
        self.assertEqual(self.request('/api/models', {**body, 'model_key': 'fixture/environment', 'secret_env': 'UNSCOPED_SERVER_TOKEN'})[0], 400)

    def test_private_chat_and_model_test_fail_before_writes_for_all_other_roles(self):
        self.connect()
        with self.app.con() as db:
            original = dict(db.execute('SELECT * FROM models WHERE model_key=?', ('fixture/private',)).fetchone())
        for role in ('', 'viewer', 'editor', 'admin'):
            for route, body in (('/api/chat', {'message': 'Do not record this', 'model_key': 'fixture/private'}), ('/api/models/test', {'model_key': 'fixture/private'})):
                with self.subTest(role=role, route=route):
                    self.assertEqual(self.request(route, body, role=role)[0], 403)
        self.assertEqual(self.chat_count(), 0); self.assertEqual(self.codex.calls, [])
        with self.app.con() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM models WHERE model_key=?', ('fixture/private',)).fetchone()), original)
        self.assertEqual(self.request('/api/models/test', {'model_key': 'ollama-local'}, role='admin')[0], 200)

    def test_disabled_and_removed_default_never_falls_back(self):
        self.connect(set_default=True)
        self.assertEqual(self.request('/api/ai-connections/disconnect', {'model_key': 'fixture/private'})[0], 200)
        for body in ({'message': 'Default disabled'}, {'message': 'Explicit disabled', 'model_key': 'fixture/private'}, {'message': 'Missing explicit', 'model_key': 'missing/model'}):
            self.assertEqual(self.request('/api/chat', body)[0], 409)
        self.assertEqual(self.chat_count(), 0); self.assertEqual(self.codex.calls, []); self.assertEqual(self.legacy_calls, []); self.assertEqual(self.transport_calls, [])
        self.app.save_settings('owner', {'ai': {'default_model': 'removed/model'}})
        self.assertEqual(self.request('/api/chat', {'message': 'Default removed'})[0], 409)

    def test_private_models_and_external_agents_are_hidden_from_nonowners(self):
        self.connect()
        manager = self.handler.ai_manager()
        agent = manager.connect({'provider': 'hermes', 'endpoint': 'http://127.0.0.1:8642/v1', 'api_key': 'FIXTURE_HERMES_KEY', 'model_name': 'agent', 'agent_template': 'hermes'}, {'role': 'owner', 'level': 4, 'subject': 'owner', 'name': 'Joe'})
        for role in ('viewer', 'editor', 'admin'):
            self.assertNotIn('fixture/private', [m['model_key'] for m in self.request('/api/models', role=role)[1]['models']])
            self.assertNotIn(agent['agent_key'], [a['agent_key'] for a in self.request('/api/agents', role=role)[1]['agents']])
        self.assertIn('fixture/private', [m['model_key'] for m in self.request('/api/models')[1]['models']])
        self.assertIn(agent['agent_key'], [a['agent_key'] for a in self.request('/api/agents')[1]['agents']])

    def task(self, repo='fixture/repo'):
        self.app.BRIDGES[repo] = '<!-- fixture-only -->'
        with self.app.con() as db:
            db.execute("INSERT INTO projects(name,repo,updated_at) VALUES('AI fixture',?,?)", (repo, self.app.now()))
        code, result = self.request('/api/tasks', {'title': 'Queue fixture', 'project': 'AI fixture', 'status': 'Backlog', 'priority': 'Medium'})
        self.assertEqual(code, 201)
        return result['id']

    def test_managed_queue_cannot_dispatch_and_owner_hold_wins(self):
        self.connect()
        tid = self.task()
        code, result = self.request('/api/tasks/'+tid+'/queue', {'model_key': 'fixture/private'})
        self.assertEqual(code, 409, result); self.assertIn('execution bridge', result['error']); self.assertEqual(self.issues, [])
        self.app.execution_hold = lambda repo: {'reason': 'Paused by owner fixture', 'reason_code': 'owner-paused', 'http_status': 409}
        code, result = self.request('/api/tasks/'+tid+'/queue', {'model_key': 'fixture/private'})
        self.assertEqual(code, 409); self.assertEqual(result['code'], 'owner-paused'); self.assertEqual(self.issues, [])
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_all_new_mutations_owner_only_and_strict_framing_csrf(self):
        for action in ('discover', 'connect', 'default', 'login', 'disconnect'):
            for role in ('', 'viewer', 'editor', 'admin'):
                self.assertEqual(self.request('/api/ai-connections/'+action, {}, role=role)[0], 403)
        for body in (b'{"provider":"ollama","provider":"codex-chatgpt"}', b'[]', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":', b'\xff'):
            self.assertEqual(self.raw(body)[0], 400)
        valid = [('Content-Type', 'application/json'), ('Content-Length', '2')]
        invalid = [valid+[('Content-Length', '2')], valid+[('Transfer-Encoding', 'chunked')], [('Content-Type', 'text/plain'), ('Content-Length', '2')], [('Content-Length', '2')], [('Content-Type', 'application/json'), ('Content-Length', '16385')], valid+[('Origin', 'https://other.example')], valid+[('Origin', 'http://127.0.0.1:'+str(self.server.server_port)+'/')], valid+[('Origin', 'null')], valid+[('Origin', 'http://[')], valid+[('Origin', 'http://127.0.0.1:'+str(self.server.server_port)), ('Origin', 'http://127.0.0.1:'+str(self.server.server_port))]]
        for headers in invalid:
            with self.subTest(headers=headers): self.assertEqual(self.raw(headers=headers)[0], 400)
        code, data = self.raw(headers=valid+[('Origin', 'http://127.0.0.1:'+str(self.server.server_port))])
        self.assertEqual(code, 200, data); self.assertEqual(data['login']['state'], 'complete')
        self.assertEqual(self.raw(route='/api/ai-connections/login?extra=1')[0], 400)
        self.codex.failure = True
        code, data = self.request('/api/ai-connections/login', {})
        self.assertEqual(code, 503); self.assertNotIn('PRIVATE_TOKEN', json.dumps(data))


    def test_body_read_has_a_whole_request_deadline(self):
        from ai_runtime import BODY_TIMEOUT
        self.assertEqual(BODY_TIMEOUT, 10)
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
        stopped = threading.Event()
        def trickle():
            while not stopped.wait(.03):
                try: connection.sendall(b' ')
                except OSError: return
        worker = threading.Thread(target=trickle, daemon=True)
        with patch('ai_runtime.BODY_TIMEOUT', .35):
            start = time.monotonic()
            request = ('POST /api/ai-connections/login HTTP/1.1\r\nHost: 127.0.0.1:'+str(self.server.server_port)+'\r\nX-Access-Key: '+OWNER+'\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{').encode()
            try:
                connection.sendall(request); worker.start()
                response = connection.recv(4096)
                self.assertIn(b' 400 ', response)
                self.assertLess(time.monotonic()-start, 1.3)
            finally:
                stopped.set(); connection.close(); worker.join(1)

    def test_overlapping_owner_and_editor_calls_keep_actor_context_separate(self):
        self.connect(set_default=True)
        with self.app.con() as db:
            db.execute("INSERT INTO models(model_key,display_name,provider,model_name,enabled,updated_at) VALUES('fixture/legacy','Unknown legacy adapter','fixture-legacy','model',1,?)", (self.app.now(),))
        with ThreadPoolExecutor(max_workers=2) as pool:
            owner_future = pool.submit(self.request, '/api/chat', {'message': 'WAIT_FOR_CONCURRENCY'})
            self.assertTrue(self.codex.entered.wait(3))
            code, result = self.request('/api/chat', {'message': 'Editor cannot borrow actor', 'model_key': 'fixture/private'}, role='editor')
            self.assertEqual(code, 403, result)
            # Even a nested invocation from the legacy adapter keeps this editor identity.
            code, result = self.request('/api/chat', {'message': 'ELEVATE', 'model_key': 'fixture/legacy'}, role='editor')
            self.assertEqual(code, 502); self.assertIn('Owner access', result['error'])
            self.codex.release.set()
            self.assertEqual(owner_future.result(timeout=5)[0], 200)
        self.assertEqual(len(self.codex.calls), 1)
        with self.assertRaises(ConnectionError):
            self.app.call_model('fixture/private', [{'role': 'user', 'content': 'No request context'}])


if __name__ == '__main__':
    unittest.main()
