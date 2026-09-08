"""HTTP acceptance for the dashboard task overlay using explicit local fixtures.

Uses the reconstructed backend and existing strict AI request parser. The task
controller is real; Codex, workspace, and publisher implementations are fixtures.
No account, provider, GitHub, shell, or sandbox work is performed.
"""
import json
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import codex_tasks_runtime as overlay
import test_ai_runtime as ai_fixture
import test_codex_tasks as controller_fixture

ROOT = '/api/codex-workspace'


class CodexWorkspaceHTTPTests(unittest.TestCase):
    request = ai_fixture.RuntimeTests.request
    raw = ai_fixture.RuntimeTests.raw
    wait = controller_fixture.ControllerTests.wait
    seed = controller_fixture.ControllerTests.seed
    permission = controller_fixture.ControllerTests.permission
    tool_background = controller_fixture.ControllerTests.tool_background

    def setUp(self):
        ai_fixture.RuntimeTests.setUp(self)
        self.background = []
        self.state = self.root / 'codex-workspace-state'
        self.factory = controller_fixture.FixtureFactory()
        self.publisher = controller_fixture.FixturePublisher()
        self.controller = controller_fixture.tasks.Controller(self.state, self.root,
            connection=controller_fixture.FixtureConnection(),
            sandbox=controller_fixture.FixtureSandbox(self.state / 'workspaces'),
            session_factory=self.factory, publisher=self.publisher)
        self.server.RequestHandlerClass = overlay.install(self.app, self.handler, self.public_files, self.controller)

    def tearDown(self):
        with self.controller.condition:
            for context in self.controller.live.values():
                context['stop'].set()
                for session in context['sessions']:
                    session.interrupt(); session.close()
            self.controller.condition.notify_all()
        for session in self.factory.instances:
            if session.run_thread and session.run_thread is not threading.current_thread():
                session.run_thread.join(3)
        for thread, done in self.background:
            done.wait(3); thread.join(.1)
        self.controller.db.close()
        ai_fixture.RuntimeTests.tearDown(self)

    def message_body(self, text='Review the fixture workspace', cid=None):
        return {'request_id': str(uuid.uuid4()), 'conversation_id': cid, 'project_key': 'joeos', 'message': text}

    def test_catalog_is_owner_scoped_and_does_not_launch_a_session(self):
        code, catalog = self.request(ROOT)
        self.assertEqual(code, 200, catalog); self.assertTrue(catalog['configured']); self.assertTrue(catalog['connected'])
        self.assertEqual(catalog['projects'][0]['key'], 'joeos'); self.assertEqual(self.factory.instances, [])
        self.assertIn('/codex-workspace.js', self.public_files)
        self.assertEqual(self.request('/api/ai-connections')[0], 200)

    def test_all_task_routes_reject_nonowners_before_side_effects(self):
        identity = uuid.uuid4().hex
        routes = [(ROOT, None), (ROOT + '/conversations/' + identity, None),
                  (ROOT + '/runs/' + identity + '/events', None), (ROOT + '/artifacts/' + identity, None),
                  (ROOT + '/message', self.message_body()),
                  (ROOT + '/runs/' + identity + '/stop', {'request_id': str(uuid.uuid4())}),
                  (ROOT + '/permissions/' + identity + '/decision', {})]
        for role in ('', 'viewer', 'editor', 'admin'):
            for route, body in routes:
                with self.subTest(role=role, route=route):
                    self.assertEqual(self.request(route, body, role=role)[0], 403)
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        self.assertEqual(self.factory.instances, []); self.assertEqual(self.publisher.published, [])

    def test_message_returns_durable_run_and_conversation_reads_and_cursor_work(self):
        body = self.message_body()
        code, created = self.request(ROOT + '/message', body)
        self.assertEqual(code, 202, created)
        run, cid = created['run_id'], created['conversation_id']
        self.wait(lambda: run not in self.controller.live)
        code, conversation = self.request(ROOT + '/conversations/' + cid)
        self.assertEqual(code, 200); self.assertEqual(len(conversation['messages']), 2)
        self.assertEqual(conversation['runs'][0]['status'], 'completed')
        code, events = self.request(ROOT + '/runs/' + run + '/events?after=0')
        self.assertEqual(code, 200); self.assertEqual(events['run']['status'], 'completed')
        last = events['events'][-1]['seq']
        self.assertEqual(self.request(ROOT + '/runs/' + run + '/events?after=' + str(last))[1]['events'], [])
        self.assertEqual(self.request(ROOT + '/message', body), (202, created)); self.assertEqual(len(self.factory.instances), 1)
        conflict = {**body, 'message': 'Different request with reused ID'}
        self.assertEqual(self.request(ROOT + '/message', conflict)[0], 409)

    def test_advertised_24kb_message_fits_the_bounded_http_parser(self):
        code, result = self.request(ROOT + '/message', self.message_body('x' * 24000))
        self.assertEqual(code, 202, result)
        self.wait(lambda: result['run_id'] not in self.controller.live)
        self.assertEqual(self.request(ROOT + '/message', self.message_body('x' * 24001))[0], 400)

    def test_route_and_query_shapes_are_exact(self):
        identity = uuid.uuid4().hex
        invalid_get = [ROOT + '/', ROOT + '?extra=1', ROOT + '/unknown', ROOT + '-other',
                       ROOT + '/conversations/' + identity + '?extra=1',
                       ROOT + '/runs/' + identity + '/events?after=-1',
                       ROOT + '/runs/' + identity + '/events?after=01',
                       ROOT + '/runs/' + identity + '/events?after=0&after=1',
                       ROOT + '/runs/' + identity + '/events?after=99999999999999999999',
                       ROOT + '/artifacts/%2e%2e']
        for route in invalid_get:
            with self.subTest(route=route):
                self.assertEqual(self.request(route)[0], 404)
        for route in (ROOT + '/message?extra=1', ROOT + '/message/', ROOT + '/message%2f', ROOT + '/unknown'):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, self.message_body())[0], 404)
        self.assertEqual(self.factory.instances, [])

    def test_duplicate_json_ambiguous_framing_and_cross_origin_are_rejected(self):
        route = ROOT + '/message'
        for payload in (b'{"message":"first","message":"second"}', b'[]', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":', b'\xff'):
            with self.subTest(payload=payload):
                self.assertEqual(self.raw(payload=payload, route=route)[0], 400)
        valid = [('Content-Type', 'application/json'), ('Content-Length', '2')]
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        invalid = [valid + [('Content-Length', '2')], valid + [('Transfer-Encoding', 'chunked')],
                   [('Content-Type', 'text/plain'), ('Content-Length', '2')], [('Content-Length', '2')],
                   [('Content-Type', 'application/json'), ('Content-Length', '999999')],
                   valid + [('Origin', 'https://other.example')], valid + [('Origin', origin + '/')],
                   valid + [('Origin', 'null')], valid + [('Origin', 'http://[')],
                   valid + [('Origin', origin), ('Origin', origin)]]
        for headers in invalid:
            with self.subTest(headers=headers):
                self.assertEqual(self.raw(route=route, headers=headers)[0], 400)
        body = self.message_body(); data = json.dumps(body).encode()
        code, created = self.raw(data, [('Content-Type', 'application/json'), ('Content-Length', str(len(data))), ('Origin', origin)], route)
        self.assertEqual(code, 202, created)
        self.wait(lambda: created['run_id'] not in self.controller.live)
        self.assertEqual(len(self.factory.instances), 1)

    def test_permission_decision_and_stop_are_authenticated_exact_bound_mutations(self):
        run, context = self.seed(); result, done = self.tool_background(run, context, controller_fixture.call())
        row = self.permission(run)
        code, state = self.request(ROOT + '/runs/' + run + '/events')
        self.assertEqual(code, 200); exposed = state['permissions'][0]
        self.assertEqual(exposed['id'], row['id']); self.assertEqual(exposed['review_token'], row['review_token'])
        decision = {'request_id': str(uuid.uuid4()), 'review_token': 'wrong', 'decision': 'approve_once', 'note': ''}
        route = ROOT + '/permissions/' + row['id'] + '/decision'
        self.assertEqual(self.request(route, decision)[0], 409)
        decision['review_token'] = row['review_token']; decision['request_id'] = str(uuid.uuid4())
        code, accepted = self.request(route, decision)
        self.assertEqual(code, 200, accepted); self.assertTrue(done.wait(2), result)
        self.assertTrue(result['value']['success']); self.assertEqual(len(self.publisher.published), 1)
        self.assertEqual(self.request(route, decision), (200, accepted)); self.assertEqual(len(self.publisher.published), 1)
        decision['request_id'] = str(uuid.uuid4()); self.assertEqual(self.request(route, decision)[0], 409)
        artifact = self.controller.events(controller_fixture.OWNER, run)['artifacts'][0]
        code, content = self.request(ROOT + '/artifacts/' + artifact['id'])
        self.assertEqual(code, 200); self.assertEqual(content['content_type'], 'text/plain')
        stop = {'request_id': str(uuid.uuid4())}
        self.assertEqual(self.request(ROOT + '/runs/' + run + '/stop', stop)[0], 200)
        self.assertEqual(self.request(ROOT + '/runs/' + run + '/stop', stop)[0], 200)

    def test_internal_errors_are_generic_and_configuration_absence_is_visible(self):
        with patch.object(self.controller, 'catalog', side_effect=RuntimeError('/private/secret PRIVATE_TOKEN_FIXTURE')):
            code, error = self.request(ROOT)
        self.assertEqual(code, 503); self.assertNotIn('PRIVATE_TOKEN', json.dumps(error)); self.assertNotIn('/private', json.dumps(error))
        self.server.RequestHandlerClass = overlay.install(self.app, self.handler, self.public_files)
        with patch.dict('os.environ', {'PRFKT_CODEX_STATE': '', 'PRFKT_CODEX_REPO': ''}):
            code, catalog = self.request(ROOT)
            self.assertEqual(code, 200); self.assertFalse(catalog['configured']); self.assertFalse(catalog['connected'])
            code, error = self.request(ROOT + '/message', self.message_body())
            self.assertEqual(code, 503); self.assertIn('not configured', error['error'])
        self.assertEqual(self.factory.instances, [])


if __name__ == '__main__':
    unittest.main()
