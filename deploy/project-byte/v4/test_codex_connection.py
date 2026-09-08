"""Credential-free transport and lifecycle regressions for workspace Codex chat."""
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import codex_connection as codex


FIXTURE_CHILD = '''import hashlib,json,sys,time
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request:
        continue
    method=request['method']
    if method=='test/no-response':
        continue
    result={}
    if method=='test/echo':
        result={'sha256':hashlib.sha256(request['params']['text'].encode()).hexdigest()}
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
    if method=='test/stop-reading':
        time.sleep(600)
'''


def event(item_type='agentMessage', text='Fixture reply', thread='thread-fixture', turn='turn-fixture'):
    return {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn,
            'item': {'id': 'item-fixture', 'type': item_type, 'text': text}}}


def finished(status='completed', turn='turn-fixture'):
    return {'method': 'turn/completed', 'params': {'threadId': 'thread-fixture',
            'turn': {'id': turn, 'status': status}}}


class FakeRPC:
    def __init__(self, workspace, account='chatgpt', events=None, effective=None):
        self.workspace = workspace
        self.account = account
        self.reply_events = copy.deepcopy(events if events is not None else [event(), finished()])
        self.effective = effective or {}
        self.events = []
        self.requests = []
        self.closed = False
        self.release = None

    def request(self, method, params=None, deadline=None):
        self.requests.append((method, params))
        if method == 'account/read':
            return {'account': {'type': self.account} if self.account else None}
        if method == 'account/login/start':
            return {'type': 'chatgptDeviceCode', 'loginId': 'fresh-login',
                    'verificationUrl': 'https://auth.openai.com/codex/device', 'userCode': 'FRESH-1234'}
        if method == 'thread/start':
            return {**{'thread': {'id': 'thread-fixture'}, 'modelProvider': 'openai',
                       'model': params['model'],
                       'cwd': str(self.workspace), 'approvalPolicy': 'never',
                       'sandbox': {'type': 'readOnly', 'networkAccess': False},
                       'instructionSources': []}, **self.effective}
        if method == 'turn/start':
            self.events.extend(self.reply_events)
            return {'turn': {'id': 'turn-fixture'}}
        raise AssertionError('Unexpected fixture method: ' + method)

    def read(self, deadline):
        raise codex.CodexError('Codex request timed out')

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.release:
            self.release()
            self.release = None


class CodexTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='byte-codex-unit-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir(mode=0o700)
        self.workspace = self.root / 'workspace'; self.workspace.mkdir(mode=0o700)
        self.binary = self.root / 'fixture-codex'
        source = self.root / 'fixture.py'; source.write_text(FIXTURE_CHILD)
        self.binary.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -u '
                               + shlex.quote(str(source)) + ' "$@"\n')
        self.binary.chmod(0o700)

    def rpc(self, timeout=1):
        rpc = codex._RPC(self.binary, self.home, self.workspace, timeout=timeout)
        self.addCleanup(rpc.close)
        return rpc

    def test_large_backpressured_write_uses_request_deadline(self):
        rpc = self.rpc()
        rpc.request('test/stop-reading')
        start = time.monotonic()
        with self.assertRaisesRegex(codex.CodexError, 'timed out'):
            rpc.request('test/echo', {'text': 'x' * 200000}, deadline=start + .15)
        self.assertLess(time.monotonic() - start, 1.5)

    def test_short_writes_deliver_the_entire_utf8_json_request(self):
        import hashlib
        rpc = self.rpc()
        original = os.write
        calls = []
        def short_write(fd, data):
            calls.append(len(data))
            return original(fd, data[:317])
        value = 'UTF8\N{SNOWMAN}' * 6000
        with patch.object(codex.os, 'write', side_effect=short_write):
            reply = rpc.request('test/echo', {'text': value}, deadline=time.monotonic() + 3)
        self.assertGreater(len(calls), 10)
        self.assertEqual(reply['sha256'], hashlib.sha256(value.encode()).hexdigest())

    def test_response_timeout_closes_and_reaps_child(self):
        rpc = self.rpc(); rpc.timeout = .1
        released = []; rpc.release = lambda: released.append(True)
        with self.assertRaisesRegex(codex.CodexError, 'timed out'):
            rpc.request('test/no-response')
        rpc.close(); rpc.close()
        self.assertIsNotNone(rpc.process.poll())
        self.assertEqual(released, [True])

    def test_server_requests_get_an_error_never_an_approval(self):
        for method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
                       'item/tool/call', 'item/permissions/requestApproval', 'item/tool/requestUserInput'):
            with self.subTest(method=method):
                rpc = codex._RPC.__new__(codex._RPC)
                rpc.buffer = (json.dumps({'id': 91, 'method': method, 'params': {}}) + '\n').encode()
                sent = []
                rpc.send = lambda message, **kwargs: sent.append(message)
                with self.assertRaisesRegex(codex.CodexError, 'no tool was approved'):
                    rpc.read(time.monotonic() + 1)
                self.assertEqual(sent[0]['id'], 91)
                self.assertIn('error', sent[0]); self.assertNotIn('result', sent[0])

    def test_malformed_json_and_nonobject_frames_fail_closed(self):
        for raw in (b'not-json\n', b'[]\n', b'null\n', b'"text"\n'):
            with self.subTest(raw=raw):
                rpc = codex._RPC.__new__(codex._RPC); rpc.buffer = raw
                with self.assertRaisesRegex(codex.CodexError, 'invalid response'):
                    rpc.read(time.monotonic() + 1)

    def test_request_size_is_checked_before_any_write(self):
        rpc = codex._RPC.__new__(codex._RPC)
        with self.assertRaisesRegex(codex.CodexError, 'too large'):
            rpc.send({'text': 'x' * codex.LIMIT})

    def test_rpc_constructor_filters_inherited_credentials(self):
        actual = codex.subprocess.Popen
        seen = []
        def capture(*args, **kwargs):
            seen.append(kwargs['env'].copy())
            return actual(*args, **kwargs)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-do-not-inherit',
                                   'ANTHROPIC_API_KEY': 'fixture-do-not-inherit',
                                   'CODEX_HOME': 'fixture-wrong-home'}), \
                patch.object(codex.subprocess, 'Popen', side_effect=capture):
            rpc = self.rpc()
        self.assertNotIn('OPENAI_API_KEY', seen[0]); self.assertNotIn('ANTHROPIC_API_KEY', seen[0])
        self.assertEqual(seen[0]['CODEX_HOME'], str(self.home))
        rpc.close()


class CodexConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='byte-codex-lifecycle-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir(mode=0o700)
        self.workspace = self.root / 'workspace'; self.workspace.mkdir(mode=0o700)
        self.connection = codex.Connection(Path(sys.executable).resolve(), self.home, self.workspace)
        version = patch.object(codex.subprocess, 'run', return_value=types.SimpleNamespace(
            returncode=0, stdout='codex-cli 0.153.4\n'))
        version.start(); self.addCleanup(version.stop)
        self.addCleanup(self.connection._close_login)

    def complete(self, rpc):
        with patch.object(self.connection, '_open', return_value=rpc):
            return self.connection.complete([{'role': 'user', 'content': 'Fixture conversation'}], 'gpt-6-astra')

    def test_expected_reply_uses_ephemeral_read_only_openai_thread(self):
        rpc = FakeRPC(self.workspace)
        answer, elapsed = self.complete(rpc)
        self.assertEqual(answer, 'Fixture reply'); self.assertGreaterEqual(elapsed, 0)
        params = dict(rpc.requests)['thread/start']
        self.assertEqual(params['modelProvider'], 'openai')
        self.assertEqual(params['sandbox'], 'read-only'); self.assertTrue(params['ephemeral'])
        self.assertEqual(params['approvalPolicy'], 'never'); self.assertTrue(rpc.closed)

    def test_wrong_account_never_starts_a_thread(self):
        for account in ('apiKey', 'amazonBedrock', None):
            with self.subTest(account=account):
                rpc = FakeRPC(self.workspace, account=account)
                with self.assertRaisesRegex(codex.CodexError, 'Sign in with ChatGPT'):
                    self.complete(rpc)
                self.assertEqual([m for m, _ in rpc.requests], ['account/read'])
                self.assertTrue(rpc.closed)

    def test_effective_security_configuration_is_checked_before_turn(self):
        for field, value in [('modelProvider', 'other'), ('cwd', '/another-workspace'),
                             ('model', 'gpt-5.5'),
                             ('sandbox', {'type': 'dangerFullAccess'}),
                             ('sandbox', {'type': 'readOnly', 'networkAccess': True}),
                             ('approvalPolicy', 'on-request'), ('instructionSources', ['/fixture/AGENTS.md']),
                             ('instructionSources', None)]:
            with self.subTest(field=field, value=value):
                rpc = FakeRPC(self.workspace, effective={field: value})
                with self.assertRaisesRegex(codex.CodexError, 'required private chat configuration'):
                    self.complete(rpc)
                self.assertNotIn('turn/start', dict(rpc.requests)); self.assertTrue(rpc.closed)

    def test_cross_thread_and_cross_turn_items_are_rejected(self):
        for bad_event in (event(thread='other-thread'), event(turn='other-turn')):
            rpc = FakeRPC(self.workspace, events=[bad_event, finished()])
            with self.assertRaisesRegex(codex.CodexError, 'another conversation'):
                self.complete(rpc)
            self.assertTrue(rpc.closed)

    def test_tool_lifecycle_items_never_become_chat_success(self):
        for item_type in ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall',
                          'collabAgentToolCall', 'webSearch', 'imageGeneration', 'imageView'):
            with self.subTest(item_type=item_type):
                rpc = FakeRPC(self.workspace, events=[event(item_type), finished()])
                with self.assertRaisesRegex(codex.CodexError, 'execution connection'):
                    self.complete(rpc)
                self.assertTrue(rpc.closed)

    def test_partial_text_is_not_returned_after_failure_timeout_or_wrong_turn(self):
        for tail in ([finished('failed')], [finished('interrupted')], [finished(turn='other-turn')], []):
            with self.subTest(tail=tail):
                rpc = FakeRPC(self.workspace, events=[event(text='Not a successful reply')] + tail)
                with self.assertRaises(codex.CodexError):
                    self.complete(rpc)
                self.assertTrue(rpc.closed)

    def test_empty_and_oversized_replies_fail_closed(self):
        for text in ('', 'x' * (1024 * 1024 + 1)):
            rpc = FakeRPC(self.workspace, events=[event(text=text), finished()])
            with self.assertRaises(codex.CodexError):
                self.complete(rpc)
            self.assertTrue(rpc.closed)

    def test_failed_native_login_is_cleared_and_retry_gets_fresh_code(self):
        old = FakeRPC(self.workspace, account=None)
        old.events = [{'method': 'account/login/completed', 'params': {'loginId': 'old-login', 'success': False}}]
        self.connection.pending = {'rpc': old, 'public': {'state': 'pending', 'user_code': 'OLD-1234'}}
        self.assertEqual(self.connection.status()['login']['state'], 'failed')
        self.assertIsNone(self.connection.pending); self.assertTrue(old.closed)
        fresh = FakeRPC(self.workspace, account=None)
        with patch.object(self.connection, '_open', return_value=fresh):
            self.assertEqual(self.connection.login()['user_code'], 'FRESH-1234')

    def test_disconnected_login_transport_is_cleared(self):
        old = FakeRPC(self.workspace, account=None)
        old.request = lambda *args, **kwargs: (_ for _ in ()).throw(codex.CodexError('fixture ended'))
        self.connection.pending = {'rpc': old, 'public': {'state': 'pending', 'user_code': 'OLD-1234'}}
        self.assertFalse(self.connection.status()['connected'])
        self.assertIsNone(self.connection.pending); self.assertTrue(old.closed)

    def test_only_two_runtime_slots_and_idempotent_close_releases_once(self):
        created = []
        def make(*args, **kwargs):
            rpc = FakeRPC(self.workspace); created.append(rpc); return rpc
        with patch.object(codex, '_RPC', side_effect=make):
            first = self.connection._open(); second = self.connection._open()
            with self.assertRaisesRegex(codex.CodexError, 'busy'):
                self.connection._open()
            first.close(); first.close()
            third = self.connection._open()
            with self.assertRaisesRegex(codex.CodexError, 'busy'):
                self.connection._open()
            second.close(); third.close()
        self.assertEqual(len(created), 3)

    def test_startup_failure_does_not_leak_runtime_slots(self):
        with patch.object(codex, '_RPC', side_effect=codex.CodexError('fixture startup failure')):
            for _ in range(4):
                with self.assertRaisesRegex(codex.CodexError, 'startup failure'):
                    self.connection._open()
        self.assertTrue(self.connection.slots.acquire(blocking=False))
        self.assertTrue(self.connection.slots.acquire(blocking=False))
        self.assertFalse(self.connection.slots.acquire(blocking=False))
        self.connection.slots.release(); self.connection.slots.release()

    def test_unreviewed_and_implicit_models_are_rejected_before_rpc(self):
        with patch.object(self.connection, '_open') as opened:
            for model in ('gpt-5.5', 'gpt-5.4-mini', 'new-unreviewed-model', '', None):
                with self.subTest(model=model), self.assertRaises(codex.CodexError):
                    self.connection.complete([{'role': 'user', 'content': 'Fixture'}], model)
            opened.assert_not_called()
        rpc = FakeRPC(self.workspace)
        with patch.object(self.connection, '_open', return_value=rpc):
            self.connection.complete([{'role': 'user', 'content': 'Default fixture'}])
        self.assertEqual(dict(rpc.requests)['thread/start']['model'], 'gpt-6-astra')

    def test_model_discovery_drops_unreviewed_runtime_models(self):
        rpc = FakeRPC(self.workspace)
        rows = [{'model': name, 'displayName': name, 'isDefault': name == 'gpt-6-astra'} for name in
                ('gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.3-codex-spark',
                 'gpt-5.5', 'gpt-5.4-mini', 'future-model')]
        rpc.request = lambda *args, **kwargs: {'data': rows, 'nextCursor': None}
        with patch.object(self.connection, 'status', return_value={'connected': True}), \
                patch.object(self.connection, '_open', return_value=rpc):
            self.assertEqual({m['id'] for m in self.connection.models()}, codex.CHAT_MODELS)
        self.assertTrue(rpc.closed)

    def test_runtime_version_mismatch_or_timeout_fails_before_rpc_without_slot_leak(self):
        for outcome in (types.SimpleNamespace(returncode=0, stdout='codex-cli 0.153.40\n'),
                        types.SimpleNamespace(returncode=1, stdout='codex-cli 0.153.4\n'),
                        codex.subprocess.TimeoutExpired('fixture version', 5)):
            with self.subTest(outcome=type(outcome).__name__), patch.object(codex, '_RPC') as opened:
                options = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
                with patch.object(codex.subprocess, 'run', **options):
                    with self.assertRaises(codex.CodexError): self.connection._open()
                opened.assert_not_called()
                self.assertTrue(self.connection.slots.acquire(blocking=False))
                self.assertTrue(self.connection.slots.acquire(blocking=False))
                self.assertFalse(self.connection.slots.acquire(blocking=False))
                self.connection.slots.release(); self.connection.slots.release()

    def test_insecure_or_symlinked_auth_and_workspace_paths_are_unavailable(self):
        self.assertTrue(self.connection.available())
        self.home.chmod(0o755); self.assertFalse(self.connection.available()); self.home.chmod(0o700)
        link = self.root / 'linked-workspace'; link.symlink_to(self.workspace, target_is_directory=True)
        self.connection.workspace = link
        self.assertFalse(self.connection.available())


if __name__ == '__main__':
    unittest.main(verbosity=2)
