"""Opt-in full native dashboard-controller acceptance against synthetic services.

PROJECT_BYTE_TEST_CODEX_BIN=/absolute/path/codex python3 -m unittest test_codex_workspace_native
Optional PROJECT_BYTE_CODEX_EVIDENCE_DIR stores synthetic proof data.
On Linux, PRFKT_TEST_BROKER_ROOT and PRFKT_TEST_BROKER_SOCKET may select an
already-provisioned private broker. Both are required together; only fresh UUID
conversation directories created by this test are used and cleaned afterward.
Explicit native opt-in fails when hard isolation is unavailable.

Uses actual pinned app-server and real OS sandbox against a disposable Git
fixture. Responses are scripted locally; account verification alone is stubbed.
The publisher is an explicit test sink: no real account, API, GitHub, VPS, or
production-repository changes occur.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time
import types
import unittest
from unittest.mock import patch
import uuid

import codex_sandbox as sandbox
import codex_task_rpc as transport
import codex_tasks as tasks
import test_codex_sandbox as sandbox_fixture
from test_codex_runtime_tools import tool_names, wire_tools

OWNER = {'ok': True, 'subject': 'owner', 'role': 'owner', 'level': 4}
FINAL = 'NATIVE_CONTROLLER_AND_SANDBOX_COMPLETE'
CHILD_FINAL = 'NATIVE_SPECIALIST_READ_COMPLETE'
CHANGED = 'Changed by the native controller fixture\n'


class ScriptedResponses(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 4 * 1024 * 1024 or self.path != '/v1/responses':
            self.send_error(400); return
        body = json.loads(self.rfile.read(length))
        names = set(tool_names(wire_tools(body)))
        key = body['prompt_cache_key']
        specialist = 'delegate_agents' not in names
        with self.server.lock:
            step = self.server.steps.get(key, 0)
            self.server.steps[key] = step + 1
            self.server.requests.append({'path': self.path, 'thread': key, 'specialist': specialist, 'body': body})
        if specialist:
            script = [('workspace_read', {'path': 'hello.txt'})]
        elif self.server.scenario == 'stop':
            script = [('workspace_write', {'path': 'hello.txt', 'content': CHANGED}),
                      ('publish_pull_request', {'title': 'Synthetic native stop fixture', 'body': 'Review actual isolated fixture changes.'})]
        else:
            script = [
                ('workspace_read', {'path': 'hello.txt'}),
                ('workspace_write', {'path': 'hello.txt', 'content': CHANGED}),
                ('workspace_command', {'command': 'printf NATIVE_SANDBOX_COMMAND; /bin/cat hello.txt', 'timeout': 10}),
                ('delegate_agents', {'tasks': [{'role': 'reviewer', 'task': 'Read hello.txt and report its content.'},
                                              {'role': 'verifier', 'task': 'Independently read hello.txt and report.'}]}),
                ('workspace_diff', {}),
                ('publish_pull_request', {'title': 'Synthetic native controller fixture', 'body': 'Review actual isolated fixture changes.'}),
            ]
        calling = step < len(script)
        identity = key + '-' + str(step)
        if calling:
            name, arguments = script[step]
            item = {'id': 'tool-' + identity, 'type': 'function_call', 'status': 'completed',
                    'name': name, 'arguments': json.dumps(arguments), 'call_id': 'call-' + identity}
        else:
            text = CHILD_FINAL if specialist else FINAL
            item = {'id': 'message-' + identity, 'type': 'message', 'status': 'completed', 'role': 'assistant',
                    'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
        response = {'id': 'response-' + identity, 'object': 'response', 'created_at': 1, 'model': body['model'],
                    'status': 'completed', 'output': [item],
                    'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        events = [
            {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0,
             'item': item if calling else {**item, 'status': 'in_progress', 'content': []}},
        ]
        if not calling:
            events.extend([
                {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'delta': text},
                {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'text': text},
                {'type': 'response.content_part.done', 'item_id': item['id'], 'output_index': 0,
                 'content_index': 0, 'part': item['content'][0]},
            ])
        events.extend([{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                       {'type': 'response.completed', 'response': response}])
        data = ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n' for event in events).encode()
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data))); self.end_headers()
        self.wfile.write(data); self.wfile.flush()


class LocalPublisherSink:
    """Review a real local diff, then record the publish intent without any I/O."""
    def __init__(self):
        self.prepared = []; self.published = []
    def prepare(self, workspace, run, args, stop_event=None):
        assert stop_event is None or not stop_event.is_set(), 'stopped preparation must not continue'
        diff = workspace.diff()
        assert diff['changes'], 'fixture must present a real patch for review'
        frozen = {'review': diff['patch'], 'public': {'repo': 'jmw7629/JOEOS', 'title': args['title'], 'base_commit': diff['base_commit'],
            'patch_sha256': hashlib.sha256(diff['patch'].encode()).hexdigest(), 'files': [row['path'] for row in diff['changes']]}}
        self.prepared.append(frozen)
        return frozen
    def publish(self, frozen, *, stop_event):
        assert not stop_event.is_set(), 'stopped action must never reach the publisher sink'
        self.published.append(frozen)
        return {'fixture_publish_intent_recorded': True}


@unittest.skipUnless(os.getenv('PROJECT_BYTE_TEST_CODEX_BIN'), 'full native controller acceptance is explicitly opt-in')
class NativeWorkspaceControllerTests(unittest.TestCase):
    git = sandbox_fixture.RepositoryFixture.git

    def setUp(self):
        self.evidence = os.getenv('PROJECT_BYTE_CODEX_EVIDENCE_DIR')
        # Capture broker settings before the model process environment is cleared.
        broker_root = os.getenv('PRFKT_TEST_BROKER_ROOT')
        broker_socket = os.getenv('PRFKT_TEST_BROKER_SOCKET')
        self.assertEqual(bool(broker_root), bool(broker_socket),
                         'Both PRFKT_TEST_BROKER_ROOT and PRFKT_TEST_BROKER_SOCKET are required')
        if broker_root:
            self.assertTrue(Path(broker_root).is_absolute(), 'Broker root must be absolute')
            self.assertTrue(Path(broker_socket).is_absolute(), 'Broker socket must be absolute')
        self.broker_configured = bool(broker_root)
        self.created_workspaces = []
        self.binary = Path(os.environ['PROJECT_BYTE_TEST_CODEX_BIN']).resolve(strict=True)
        sandbox_fixture.RepositoryFixture.setUp(self)
        self.state = self.root / 'controller-state'; self.state.mkdir(mode=0o700)
        self.worker = sandbox.SandboxWorkspace(Path(broker_root) if broker_root else self.state / 'workspaces',
                                               self.repo, broker_socket=broker_socket)
        original_create = self.worker.create
        def create_conversation_workspace(cid):
            # The injected broker may serve other work. Never reuse an existing
            # directory or clean anything outside this exact created-object list.
            self.assertEqual(uuid.UUID(hex=cid).hex, cid)
            workspace = original_create(cid)
            self.created_workspaces.append(workspace)
            return workspace
        self.worker.create = create_conversation_workspace
        proof = self.worker.capabilities()
        self.assertTrue(proof['available'], proof)
        self.auth = self.root / 'native-auth'; self.auth.mkdir(mode=0o700)
        self.native_workspace = self.root / 'native-workspace'; self.native_workspace.mkdir(mode=0o700)
        self.login_home = self.root / 'empty-home'; self.login_home.mkdir(mode=0o700)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ScriptedResponses)
        self.server.daemon_threads = True; self.server.requests = []; self.server.steps = {}; self.server.lock = threading.Lock()
        self.server.scenario = 'complete'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        (self.auth / 'config.toml').write_text('model = "gpt-6-astra"\nmodel_provider = "fixture"\n'
            '[model_providers.fixture]\nname = "Synthetic complete native controller fixture"\n'
            'base_url = "http://127.0.0.1:' + str(self.server.server_port) + '/v1"\nwire_api = "responses"\n'
            'requires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
        node = shutil.which('node')
        fixture_path = os.pathsep.join(dict.fromkeys(([str(Path(node).resolve().parent)] if node else []) + os.defpath.split(os.pathsep)))
        env = {'PATH': fixture_path, 'HOME': str(self.login_home), 'LANG': 'en_US.UTF-8',
               'XDG_CONFIG_HOME': str(self.login_home / 'config'), 'XDG_DATA_HOME': str(self.login_home / 'data'),
               'XDG_CACHE_HOME': str(self.login_home / 'cache'), 'CODEX_HOME': str(self.auth)}
        for context in (patch.dict(os.environ, env, clear=True), patch.object(transport, 'MODEL_PROVIDER', 'fixture'),
                        patch.object(transport.NativeTaskSession, '_verify_account', return_value=None)):
            context.start(); self.addCleanup(context.stop)
        self.sessions = []; self.native_calls = []; self.native_events = []
        def session_factory(**kwargs):
            original_tool, original_event = kwargs['on_tool'], kwargs['on_event']
            def tool_callback(params):
                self.native_calls.append(dict(params))
                return original_tool(params)
            def event_callback(event):
                self.native_events.append(event)
                original_event(event)
            session = transport.NativeTaskSession(**{**kwargs, 'on_tool': tool_callback, 'on_event': event_callback},
                                                  timeout=30, request_timeout=10)
            self.sessions.append(session)
            return session
        connection = types.SimpleNamespace(binary=self.binary, home=self.auth, workspace=self.native_workspace,
            status=lambda: {'connected': True, 'available': True})
        self.publisher = LocalPublisherSink()
        self.controller = tasks.Controller(self.state, self.repo, connection=connection, sandbox=self.worker,
                                           session_factory=session_factory, publisher=self.publisher)
        self.result = {'sandbox': proof, 'broker_configured': self.broker_configured, 'passed': False}
        self.addCleanup(self.cleanup_controller)

    def cleanup_controller(self):
        with self.controller.condition:
            for context in self.controller.live.values():
                context['stop'].set()
            self.controller.condition.notify_all()
        for session in self.sessions:
            session.close()
        deadline = time.monotonic() + 5
        while self.controller.live and time.monotonic() < deadline:
            time.sleep(.01)
        self.result.update(native_calls=self.native_calls, native_events=self.native_events,
                           provider_requests=self.server.requests, publisher_intents=len(self.publisher.published),
                           auth_file_created=(self.auth / 'auth.json').exists(), sessions=len(self.sessions))
        self.assertFalse(self.controller.live, 'Native callbacks must drain before fixture workspace cleanup')
        self.controller.db.close()
        cleaned = []
        for workspace in self.created_workspaces:
            self.assertIs(workspace.factory, self.worker)
            self.assertEqual(workspace.folder.parent, self.worker.run_root)
            self.assertEqual(uuid.UUID(hex=workspace.run_id).hex, workspace.run_id)
            workspace.cleanup()
            self.assertFalse(workspace.folder.exists())
            cleaned.append(workspace.run_id)
        self.result['cleaned_fixture_workspaces'] = cleaned
        if self.evidence:
            destination = Path(self.evidence); destination.mkdir(parents=True, exist_ok=True)
            (destination / ('native-controller-' + self.server.scenario + '.json')).write_text(json.dumps(self.result, indent=2) + '\n')

    def wait(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        with self.controller.lock:
            events = [dict(row) for row in self.controller.db.execute('SELECT type,data FROM events ORDER BY seq')]
        self.fail('Native controller fixture timed out: ' + json.dumps(events[-8:]))

    def launch(self):
        result = self.controller.message(OWNER, {'request_id': str(uuid.uuid4()), 'conversation_id': None,
            'project_key': 'joeos', 'message': 'Complete the explicitly synthetic workspace fixture.'})
        self.result.update(result)
        return result

    def pending_permission(self, run):
        def lookup():
            with self.controller.lock:
                row = self.controller.db.execute("SELECT * FROM permissions WHERE run_id=? AND state='pending'", (run,)).fetchone()
                return dict(row) if row else None
        return self.wait(lookup)

    def test_controller_native_tools_real_sandbox_delegation_and_exact_approval(self):
        created = self.launch(); run, cid = created['run_id'], created['conversation_id']
        permission = self.pending_permission(run)
        self.assertEqual(self.publisher.published, [])
        binding = json.loads(permission['binding'])
        genuine = [value for value in self.native_calls if value['tool'] == 'publish_pull_request']
        self.assertEqual(len(genuine), 1)
        expected_binding = {key: genuine[0][key] for key in ('threadId', 'turnId', 'callId', 'requestId', 'tool')}
        expected_binding.update(project_key='joeos', project_fingerprint=self.controller.projects['joeos'].fingerprint)
        self.assertEqual(binding, expected_binding)
        self.assertEqual(self.controller.workspaces[cid].read('hello.txt'), CHANGED.encode())
        self.assertEqual((self.repo / 'hello.txt').read_text(), 'first\nsecond\n')
        self.assertEqual(len(self.sessions), 3)
        specialist_threads = {row['thread'] for row in self.server.requests if row['specialist']}
        self.assertEqual(len(specialist_threads), 2)
        for row in self.server.requests:
            names = set(tool_names(wire_tools(row['body'])))
            self.assertFalse(names & {'exec', 'exec_command', 'apply_patch', 'spawn_agent', 'write_stdin'})
            self.assertEqual(row['body']['model'], 'gpt-6-astra')
            self.assertEqual(row['body']['reasoning']['effort'], 'xhigh')
            if row['specialist']:
                self.assertLessEqual(names, {'workspace_list', 'workspace_read', 'curr_time', 'request_user_input', 'request_user_input_async', 'update_plan'})
        inbox = self.controller.catalog(OWNER)['execution_permissions']
        self.assertEqual([r['id'] for r in inbox['requests']], [permission['id']])
        self.assertTrue(inbox['requests'][0]['can_decide'])
        self.assertEqual(inbox['requests'][0]['review_token'], permission['review_token'])
        code = {'request_id': str(uuid.uuid4()), 'review_token': permission['review_token'], 'decision': 'approve_once', 'note': 'Synthetic local publisher sink only'}
        self.controller.decide(OWNER, permission['id'], code)
        self.wait(lambda: run not in self.controller.live)
        stored = self.controller.conversation(OWNER, cid)
        self.assertEqual(stored['runs'][0]['status'], 'completed')
        self.assertEqual(stored['messages'][-1]['text'], FINAL)
        self.assertEqual(len(self.publisher.published), 1)
        events = self.controller.events(OWNER, run)
        self.assertEqual(len(events['agents']), 3); self.assertTrue(all(row['status'] == 'completed' for row in events['agents']))
        self.assertTrue(any(row['type'] == 'tool_output' and 'NATIVE_SANDBOX_COMMAND' in str(row['data']) for row in events['events']))
        self.assertTrue(any(row['method'] == 'item/agentMessage/delta' for row in self.native_events))
        self.assertTrue(any(row['name'] == 'workspace-changes.txt' for row in events['artifacts']))
        starts = {e['data']['tool_id']: e['data'] for e in events['events'] if e['type'] == 'tool_started'}
        completions = {e['data']['tool_id']: e['data'] for e in events['events'] if e['type'] == 'tool_completed'}
        self.assertEqual(set(starts), set(completions))
        self.assertGreaterEqual(len(starts), 6)
        for tid, completion in completions.items():
            self.assertGreaterEqual(completion['duration_ms'], 0)
            self.assertGreaterEqual(completion['ended_at'], starts[tid]['started_at'])
            self.assertIn('arguments', starts[tid])
            self.assertIn('result' if completion['success'] else 'error', completion)
        self.assertEqual(events['permissions'][0]['tool_id'], tasks.tool_identity(run, genuine[0]))
        self.assertNotIn(permission['review_token'], json.dumps(events['events']))
        self.assertTrue(any(e['type'] == 'agent_message' for e in events['events']))
        self.assertFalse((self.auth / 'auth.json').exists())
        self.result.update(passed=True, final_status='completed', approval_binding=binding,
                           correlated_tools=len(starts), native_trace_verified=True)

    def test_stop_cancels_actual_native_pending_publish_without_dispatch(self):
        self.server.scenario = 'stop'
        created = self.launch(); run = created['run_id']; permission = self.pending_permission(run)
        self.assertEqual(self.publisher.published, [])
        self.controller.stop(OWNER, run, {'request_id': str(uuid.uuid4())})
        self.wait(lambda: run not in self.controller.live)
        events = self.controller.events(OWNER, run)
        self.assertEqual(events['run']['status'], 'stopped')
        self.assertEqual(self.publisher.published, [])
        self.assertEqual(len(self.server.requests), 2)
        self.assertTrue(any(row['method'] == 'turn/completed' and row['params']['turn']['status'] == 'interrupted' for row in self.native_events))
        self.assertTrue(all(row['state'] in ('cancelled', 'expired') for row in events['permissions']))
        with self.assertRaises(tasks.TaskError):
            self.controller.decide(OWNER, permission['id'], {'request_id': str(uuid.uuid4()),
                'review_token': permission['review_token'], 'decision': 'approve_once', 'note': ''})
        self.assertFalse((self.auth / 'auth.json').exists())
        self.result.update(passed=True, final_status='stopped')


if __name__ == '__main__':
    unittest.main()
