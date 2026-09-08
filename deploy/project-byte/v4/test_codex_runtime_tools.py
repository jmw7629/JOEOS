"""Opt-in native tool-inventory acceptance against pinned, unauthenticated Codex.

PROJECT_BYTE_TEST_CODEX_BIN=/absolute/path/to/codex python3 test_codex_runtime_tools.py
Optional PROJECT_BYTE_CODEX_EVIDENCE_DIR saves only synthetic request/evidence data.
The local Responses fixture never emits tool calls. No owner authentication is used.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import codex_connection as codex

PINNED_VERSION = '0.153.4'
FIXTURE_TEXT = 'PROJECT_BYTE_NATIVE_CHAT_FIXTURE'
# These do not access files or execute work. The production adapter rejects every
# server request, including item/tool/requestUserInput; unit tests verify that path.
PLANNING_ONLY_TOOLS = {'update_plan', 'request_user_input'}


def tool_names(tools):
    """Handle ordinary and namespaced Responses definitions without assuming one shape."""
    names = []
    for tool in tools:
        if tool.get('type') == 'namespace' and isinstance(tool.get('tools'), list):
            names.extend(tool_names(tool['tools']))
        else:
            names.append(tool.get('name') or (tool.get('function') or {}).get('name') or tool.get('type'))
    return names


class ResponseFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 4 * 1024 * 1024:
            self.send_error(413); return
        body = json.loads(self.rfile.read(length))
        self.server.requests.append({'path': self.path, 'body': body})
        if self.path != '/v1/responses':
            self.send_error(404); return
        item = {'id': 'msg_fixture', 'type': 'message', 'status': 'completed', 'role': 'assistant',
                'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': FIXTURE_TEXT, 'annotations': []}]}
        response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 1, 'model': body.get('model'),
                    'status': 'completed', 'output': [item],
                    'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        events = [
            {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0,
             'item': {**item, 'status': 'in_progress', 'content': []}},
            {'type': 'response.content_part.added', 'item_id': 'msg_fixture', 'output_index': 0,
             'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
            {'type': 'response.output_text.delta', 'item_id': 'msg_fixture', 'output_index': 0,
             'content_index': 0, 'delta': FIXTURE_TEXT},
            {'type': 'response.output_text.done', 'item_id': 'msg_fixture', 'output_index': 0,
             'content_index': 0, 'text': FIXTURE_TEXT},
            {'type': 'response.content_part.done', 'item_id': 'msg_fixture', 'output_index': 0,
             'content_index': 0, 'part': item['content'][0]},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': response},
        ]
        data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data); self.wfile.flush()


@unittest.skipUnless(os.getenv('PROJECT_BYTE_TEST_CODEX_BIN'), 'native Codex acceptance is explicitly opt-in')
class CodexNativeToolTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.environ['PROJECT_BYTE_TEST_CODEX_BIN']).resolve(strict=True)
        self.evidence = os.getenv('PROJECT_BYTE_CODEX_EVIDENCE_DIR')
        self.tmp = tempfile.TemporaryDirectory(prefix='byte-codex-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ResponseFixture)
        self.server.daemon_threads = True
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def capture(self, label, disabled, model='fixture-chat'):
        root = self.root / label; root.mkdir(mode=0o700)
        home = root / 'codex'; home.mkdir(mode=0o700)
        workspace = root / 'workspace'; workspace.mkdir(mode=0o700)
        login_home = root / 'home'; login_home.mkdir(mode=0o700)
        # This is a fixture-only provider with no API key and no real OpenAI endpoint.
        (home / 'config.toml').write_text('''model = MODEL
model_provider = "fixture"
[model_providers.fixture]
name = "PROJECT_BYTE isolated fixture"
base_url = "http://127.0.0.1:PORT/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
'''.replace('PORT', str(self.server.server_port)).replace('MODEL', json.dumps(model)))
        # The npm launcher uses /usr/bin/env node. setup-node can install Node
        # outside os.defpath; retain only that resolved executable directory,
        # rather than inheriting the caller's entire PATH or authentication env.
        node = shutil.which('node')
        isolated_path = os.pathsep.join(dict.fromkeys(
            ([str(Path(node).resolve(strict=True).parent)] if node else []) + os.defpath.split(os.pathsep)))
        env = {'PATH': isolated_path, 'HOME': str(login_home), 'LANG': 'en_US.UTF-8',
               'XDG_CONFIG_HOME': str(login_home / 'config'), 'XDG_DATA_HOME': str(login_home / 'data'),
               'XDG_CACHE_HOME': str(login_home / 'cache'), 'CODEX_HOME': str(home)}
        result = {'label': label, 'model': model, 'disabled': list(disabled), 'source_sha256': hashlib.sha256(
            Path(codex.__file__).read_bytes()).hexdigest()}
        binary_hash = hashlib.sha256()
        with self.binary.open('rb') as binary_stream:
            for chunk in iter(lambda: binary_stream.read(1024 * 1024), b''):
                binary_hash.update(chunk)
        result['runtime_sha256'] = binary_hash.hexdigest()
        before = len(self.server.requests)
        rpc = None
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(codex, 'DISABLED', disabled):
                version_process = subprocess.run([str(self.binary), '--version'], env=env, cwd=workspace,
                                                 text=True, capture_output=True, timeout=10)
                self.assertEqual(version_process.returncode, 0,
                                 f'Codex --version failed: stderr={version_process.stderr[:2048]!r}')
                version = version_process.stdout.strip()
                result['runtime_version'] = version
                self.assertEqual('codex-cli ' + PINNED_VERSION, version)
                rpc = codex._RPC(self.binary, home, workspace, timeout=30)
                deadline = time.monotonic() + 35
                created = rpc.request('thread/start', {'model': model, 'modelProvider': 'fixture',
                    'cwd': str(workspace), 'sandbox': 'read-only', 'approvalPolicy': 'never', 'ephemeral': True,
                    'developerInstructions': 'Reply only with the fixture response. Do not use tools.'}, deadline=deadline)
                result['effective'] = {key: created.get(key) for key in
                    ('modelProvider', 'sandbox', 'approvalPolicy', 'cwd', 'instructionSources')}
                thread_id = created['thread']['id']; rpc.events.clear()
                started = rpc.request('turn/start', {'threadId': thread_id,
                    'input': [{'type': 'text', 'text': 'Return the fixture reply without using tools.'}]}, deadline=deadline)
                turn_id = started['turn']['id']
                item_types = []; replies = []
                while True:
                    message = rpc.events.pop(0) if rpc.events else rpc.read(deadline)
                    params = message.get('params') or {}
                    if message.get('method') == 'item/completed':
                        item = params.get('item') or {}; item_types.append(item.get('type'))
                        if item.get('type') == 'agentMessage': replies.append(item.get('text', ''))
                    if message.get('method') == 'turn/completed' and params.get('threadId') == thread_id:
                        self.assertEqual(params['turn']['id'], turn_id)
                        result['turn_status'] = params['turn']['status']
                        self.assertEqual(result['turn_status'], 'completed')
                        break
                result['completed_item_types'] = item_types
                result['fixture_reply_received'] = FIXTURE_TEXT in '\n'.join(replies)
                self.assertTrue(result['fixture_reply_received'])
        finally:
            if rpc: rpc.close()
            requests = self.server.requests[before:]
            result['request_paths'] = [r['path'] for r in requests]
            result['requests'] = requests
            result['tool_names'] = [name for request in requests for name in tool_names(request['body'].get('tools', []))]
            result['auth_file_created'] = (home / 'auth.json').exists()
            if self.evidence:
                out = Path(self.evidence); out.mkdir(parents=True, exist_ok=True)
                (out / (label + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        self.assertEqual(result['request_paths'], ['/v1/responses'])
        self.assertFalse(result['auth_file_created'])
        return result

    def test_native_tool_inventory_has_no_execution_tools_and_negative_control_detects_shell(self):
        safe = self.capture('tools-disabled', codex.DISABLED)
        # No tool invocation is generated: the negative control advertises a shell only.
        control = self.capture('shell-enabled-negative-control', tuple(x for x in codex.DISABLED if x != 'shell_tool'))
        self.assertTrue({'exec_command', 'write_stdin'} <= set(control['tool_names']),
                        'negative control did not expose the expected executable tools')
        self.assertEqual(set(safe['tool_names']) - PLANNING_ONLY_TOOLS, set(), safe['tool_names'])
        self.assertTrue(set(safe['completed_item_types']) <= {'userMessage', 'agentMessage', 'reasoning', 'plan'})

    def test_recognized_model_metadata_does_not_restore_execution_tools(self):
        # The synthetic model exercises fallback metadata. Known model families may
        # select different tool definitions, so also test those through the fixture.
        reviewed = {'gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.3-codex-spark'}
        self.assertEqual(codex.CHAT_MODELS, reviewed)
        for model in sorted(reviewed):
            with self.subTest(model=model):
                safe = self.capture('tools-disabled-' + model, codex.DISABLED, model)
                self.assertEqual(set(safe['tool_names']) - PLANNING_ONLY_TOOLS, set(), safe['tool_names'])
                self.assertTrue(set(safe['completed_item_types']) <= {'userMessage', 'agentMessage', 'reasoning', 'plan'})

    def test_excluded_models_remain_negative_controls_for_apply_patch(self):
        for model in ('gpt-5.5', 'gpt-5.4-mini'):
            with self.subTest(model=model):
                self.assertNotIn(model, codex.CHAT_MODELS)
                control = self.capture('excluded-model-' + model, codex.DISABLED, model)
                self.assertIn('apply_patch', control['tool_names'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
