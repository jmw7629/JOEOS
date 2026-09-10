"""HTTP acceptance for the dashboard task overlay using explicit local fixtures.

Uses the reconstructed backend and existing strict AI request parser. The task
controller is real; Codex, workspace, and publisher implementations are fixtures.
No account, provider, GitHub, shell, or sandbox work is performed.
"""
from contextlib import closing
from dataclasses import asdict
import gc
import json
import runpy
import sqlite3
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import uuid
from urllib.parse import urlencode

import codex_tasks_runtime as overlay
from codex_projects import Project, default_project
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
        # A lazy anchor can have been created by an HTTP worker. Dropping the
        # fixture's ownership lets SQLite finalize it without cross-thread use.
        if hasattr(self.app, '_codex_workspace_wal_anchor'):
            del self.app._codex_workspace_wal_anchor
        gc.collect()
        ai_fixture.RuntimeTests.tearDown(self)

    def message_body(self, text='Review the fixture workspace', cid=None):
        return {'request_id': str(uuid.uuid4()), 'conversation_id': cid, 'project_key': 'joeos', 'message': text}

    def test_private_registry_reaches_controller_and_exact_publisher_with_legacy_state_preserved(self):
        source = self.root / 'memory-source'
        source.mkdir()
        source = source.resolve()
        projects = (default_project(self.root, publication=True),
                    Project('memory', 'Memory', 'fixture/MEMORY', source, 'main', 'execute', True, '',
                            'PRIVATE_REFERENCE_MUST_NOT_REACH_CATALOG'))
        registry = self.root / 'projects.json'
        registry.write_text(json.dumps({'schema_version': 1, 'projects': [asdict(p) for p in projects]}, default=str))
        registry.chmod(0o600)
        self.server.RequestHandlerClass = overlay.install(self.app, self.handler, self.public_files)
        environment = {'PRFKT_CODEX_STATE': str(self.state), 'PRFKT_CODEX_REPO': str(self.root),
                       'PRFKT_CODEX_PROJECTS': str(registry.resolve()), 'PRFKT_CODEX_PUBLISH': '1',
                       'PRFKT_CODEX_BASE_BRANCH': 'project-byte-deploy'}
        with patch.dict('os.environ', environment), patch.object(overlay, 'Controller', return_value=self.controller) as construct, \
                patch('codex_publisher.Publisher', return_value=self.publisher) as publish:
            code, catalog = self.request(ROOT)
            self.assertEqual(code, 200)
            self.assertEqual(construct.call_args.kwargs['projects'], projects)
            self.assertEqual(set(construct.call_args.kwargs['project_publishers']), {'joeos', 'memory'})
            self.assertEqual(publish.call_args_list[0].args[0], self.state / 'publisher')
            self.assertEqual(publish.call_args_list[1].args[0], self.state / 'publishers' / 'memory')
            self.assertEqual(publish.call_args_list[1].kwargs,
                             {'repo': 'fixture/MEMORY', 'registered_repo': 'fixture/MEMORY', 'base_branch': 'main'})
            self.assertNotIn('PRIVATE_REFERENCE', json.dumps(catalog))

    def test_invalid_registry_fails_closed_before_any_controller_or_publisher(self):
        registry = self.root / 'projects.json'
        registry.write_text('{"PRIVATE_REFERENCE_MUST_NOT_LEAK":true}')
        registry.chmod(0o600)
        self.server.RequestHandlerClass = overlay.install(self.app, self.handler, self.public_files)
        with patch.dict('os.environ', {'PRFKT_CODEX_STATE': str(self.state), 'PRFKT_CODEX_REPO': str(self.root),
                                      'PRFKT_CODEX_PROJECTS': str(registry.resolve())}), \
                patch.object(overlay, 'Controller') as construct, patch('codex_publisher.Publisher') as publish:
            self.assertEqual(self.request(ROOT, role='viewer')[0], 403)
            code, result = self.request(ROOT)
            self.assertEqual(code, 503)
            self.assertEqual(result, {'error': 'Codex task workspace is unavailable'})
            construct.assert_not_called()
            publish.assert_not_called()

    def test_catalog_is_owner_scoped_and_does_not_launch_a_session(self):
        code, catalog = self.request(ROOT)
        self.assertEqual(code, 200, catalog); self.assertTrue(catalog['configured']); self.assertTrue(catalog['connected'])
        self.assertEqual(catalog['projects'][0]['key'], 'joeos'); self.assertEqual(self.factory.instances, [])
        self.assertIn('/codex-workspace.js', self.public_files)
        self.assertEqual(self.request('/api/ai-connections')[0], 200)

    def test_project_history_paginates_without_model_calls_and_is_owner_only(self):
        with self.controller.lock:
            rows = [(f'{n:032x}', 'owner', 'Saved chat ' + str(n), 'joeos', 10) for n in range(105)]
            rows += [('f' * 32, 'someone-else', 'PRIVATE OTHER OWNER', 'joeos', 20),
                     ('e' * 32, 'owner', 'Older project', 'archived-project', 5)]
            self.controller.db.executemany('INSERT INTO conversations VALUES(?,?,?,?,?)', rows)
            self.controller.db.commit()
        self.assertEqual(self.request(ROOT + '/conversations')[0], 200)
        route = ROOT + '/conversations?project=joeos'
        code, first = self.request(route)
        self.assertEqual(code, 200)
        self.assertEqual(len(first['conversations']), 100)
        code, second = self.request(route + '&' + urlencode({'cursor': first['next_cursor']}))
        self.assertEqual(code, 200)
        self.assertEqual(len(second['conversations']), 5)
        self.assertIsNone(second['next_cursor'])
        ids = [r['id'] for r in first['conversations'] + second['conversations']]
        self.assertEqual(len(set(ids)), 105, 'equal timestamps never duplicate or omit a page boundary')
        self.assertNotIn('PRIVATE OTHER OWNER', json.dumps(first))
        code, filtered = self.request(ROOT + '/conversations?' + urlencode({'project': 'archived-project', 'search': 'Older'}))
        self.assertEqual(code, 200)
        self.assertEqual(filtered['conversations'][0]['title'], 'Older project')
        self.assertEqual(self.request(route, role='viewer')[0], 403)
        for query in ['cursor=nope', 'cursor=%5BNaN,%22' + 'a' * 32 + '%22%5D', 'project=a&project=b', 'unexpected=x']:
            self.assertEqual(self.request(ROOT + '/conversations?' + query)[0], 400)
        self.assertEqual(self.factory.instances, [], 'history reads never start a model session')

    def test_install_and_injected_controller_do_not_eagerly_open_an_app_connection(self):
        self.assertFalse(hasattr(self.app, '_codex_workspace_wal_anchor'))
        initialize = self.app.init_db
        with patch.object(self.app, 'con', side_effect=AssertionError('install must not touch the database')):
            installed = overlay.install(self.app, self.handler, {}, self.controller)
            self.assertTrue(issubclass(installed, self.handler))
        self.assertIs(self.app.init_db, initialize, 'reinstallation must not wrap init_db twice')
        self.assertEqual(self.request(ROOT)[0], 200)
        self.assertFalse(hasattr(self.app, '_codex_workspace_wal_anchor'), 'an injected controller remains a fixture seam')
        mock_app = types.SimpleNamespace()
        self.assertTrue(issubclass(overlay.install(mock_app, self.handler, {}, self.controller), self.handler))

    def test_wrapped_init_retains_idle_wal_through_gc_and_gateway_auth_updates(self):
        # This is the real gateway reader, used without constructing its source
        # mapping store or starting a gateway process. It only reads kanban.db.
        gateway = runpy.run_path(str(ai_fixture.SOURCE.parent / 'public-access/gateway.py'))
        database_root = self.root / 'exclusive-wal-fixture'
        database_root.mkdir()
        database = database_root / 'kanban.db'
        # Keep the baseline independent of HTTP fixture setup connections.
        # Closing this sole writer reproduces the sidecar removal condition.
        with closing(sqlite3.connect(database)) as initial:
            initial.execute('PRAGMA journal_mode=WAL')
            initial.execute('CREATE TABLE exclusive_fixture(value INTEGER)')
            initial.commit()
            self.assertEqual(tuple(initial.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()), (0, 0, 0))
        database_patch = patch.object(self.app, 'DB', database)
        database_patch.start()
        self.addCleanup(database_patch.stop)
        access = object.__new__(gateway['Access'])
        access.root = database_root
        access.owner_key = lambda: ai_fixture.OWNER
        sidecars = [self.app.DB.with_name(self.app.DB.name + suffix) for suffix in ('-wal', '-shm')]
        # Apple's SQLite can persist sidecars after its sole connection closes.
        # Reproduce Linux last-close cleanup only in this closed, checkpointed,
        # disposable database; never unlink sidecars from a live app database.
        for path in sidecars:
            path.unlink(missing_ok=True)
        gc.collect()
        self.assertTrue(all(not path.exists() for path in sidecars), 'fixture starts without a retained app connection')
        self.app.init_db()  # Runtime startup invokes this before binding HTTP.
        anchor = self.app._codex_workspace_wal_anchor
        self.assertFalse(anchor.in_transaction)
        with closing(anchor.execute('PRAGMA query_only')) as cursor:
            self.assertEqual(cursor.fetchone()[0], 1)
        gc.collect()
        self.assertTrue(all(path.exists() for path in sidecars), 'last transient close must not remove WAL sidecars')
        with closing(self.app.con()) as writer:
            writer.execute('INSERT INTO collaborators VALUES(?,?,?,?,?,?,?)',
                           ('editor', 'Editor fixture', 'editor', self.app.digest(self.tokens['editor']), 1, self.app.now(), 0))
            writer.execute('INSERT INTO user_settings VALUES(?,?,?)',
                           ('owner', json.dumps({'security': {'session_minutes': 120}}), self.app.now()))
            writer.commit()
        original_connect = sqlite3.connect
        reads = []

        def read_only_connect(database, *args, **kwargs):
            self.assertTrue(str(database).endswith('?mode=ro'))
            self.assertTrue(kwargs.get('uri'))
            db = original_connect(database, *args, **kwargs)
            db.set_trace_callback(reads.append)
            return db

        def identity(token):
            with patch.object(gateway['sqlite3'], 'connect', side_effect=read_only_connect):
                result = access.identity(self.app.digest(token))
            gc.collect()
            self.assertTrue(all(path.exists() for path in sidecars))
            self.assertFalse(anchor.in_transaction)
            return result

        self.assertEqual(identity(self.tokens['editor'])[0]['role'], 'editor')
        with closing(self.app.con()) as writer:
            writer.execute("UPDATE collaborators SET role='admin' WHERE id='editor'")
            writer.execute("UPDATE user_settings SET data=? WHERE subject='owner'",
                           (json.dumps({'security': {'session_minutes': 90}}),))
            writer.commit()
        # These changes can still be in WAL: immutable reads would be stale.
        self.assertEqual(identity(self.tokens['editor'])[0]['role'], 'admin')
        self.assertEqual(identity(self.tokens['owner'])[1], 90 * 60)
        with closing(self.app.con()) as writer:
            writer.execute("UPDATE collaborators SET active=0 WHERE id='editor'")
            writer.commit()
            checkpoint = writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            self.assertEqual(tuple(checkpoint), (0, 0, 0), 'idle anchor must not pin a read transaction')
        self.assertIsNone(identity(self.tokens['editor']), 'revocation remains visible after checkpoint and GC')
        self.assertTrue(reads)
        self.assertTrue(all(sql.lstrip().upper().startswith(('SELECT ', 'PRAGMA QUERY_ONLY=')) for sql in reads), reads)
        self.app.init_db()
        self.assertIs(self.app._codex_workspace_wal_anchor, anchor, 'subsequent initialization reuses one app anchor')

    def test_lazy_manager_anchor_precedes_controller_allocation_and_survives_its_gc(self):
        self.server.RequestHandlerClass = overlay.install(self.app, self.handler, self.public_files)
        self.assertFalse(hasattr(self.app, '_codex_workspace_wal_anchor'))
        sidecars = [self.app.DB.with_name(self.app.DB.name + suffix) for suffix in ('-wal', '-shm')]

        def construct(*args, **kwargs):
            anchor = self.app._codex_workspace_wal_anchor
            self.assertFalse(anchor.in_transaction)
            gc.collect()  # Native runtime allocation triggered this production failure.
            self.assertTrue(all(path.exists() for path in sidecars))
            with closing(sqlite3.connect(self.app.DB.as_uri() + '?mode=ro', uri=True)) as reader:
                reader.execute('PRAGMA query_only=ON')
                self.assertGreater(reader.execute('SELECT count(*) FROM collaborators').fetchone()[0], 0)
            return self.controller

        with patch.dict('os.environ', {'PRFKT_CODEX_STATE': str(self.state), 'PRFKT_CODEX_REPO': str(self.root), 'PRFKT_CODEX_PUBLISH': ''}), patch.object(overlay, 'Controller', side_effect=construct) as constructor:
            self.assertEqual(self.request(ROOT)[0], 200)
            self.assertEqual(self.request(ROOT)[0], 200)
            constructor.assert_called_once()
        gc.collect()
        self.assertTrue(all(path.exists() for path in sidecars))

    def test_init_return_and_error_behavior_remain_transparent_and_setup_failure_closes(self):
        missing = self.root / 'absent-app.db'
        initializer = Mock(return_value='INITIALIZER_RESULT_FIXTURE')
        connection = Mock()
        connection.in_transaction = False
        fixture = types.SimpleNamespace(DB=self.app.DB, con=Mock(return_value=connection),
                                        init_db=lambda *args, **kwargs: initializer(*args, **kwargs))
        overlay.install(fixture, self.handler, {}, self.controller)
        self.assertEqual(fixture.init_db('argument', option=True), 'INITIALIZER_RESULT_FIXTURE')
        initializer.assert_called_once_with('argument', option=True)
        self.assertIs(fixture._codex_workspace_wal_anchor, connection)
        connection.close.assert_not_called()
        failed_initializer = Mock(side_effect=RuntimeError('fixture init failed'))
        failed = types.SimpleNamespace(DB=missing, con=Mock(), init_db=lambda: failed_initializer())
        overlay.install(failed, self.handler, {}, self.controller)
        with self.assertRaisesRegex(RuntimeError, 'fixture init failed'):
            failed.init_db()
        failed.con.assert_not_called()
        absent = types.SimpleNamespace(DB=missing, con=Mock(), init_db=lambda: None)
        overlay.install(absent, self.handler, {}, self.controller)
        with self.assertRaisesRegex(overlay.TaskError, 'database is unavailable'):
            absent.init_db()
        absent.con.assert_not_called()
        self.assertFalse(missing.exists())
        bad_connection = Mock()
        bad_connection.execute.side_effect = sqlite3.OperationalError('fixture read failed')
        broken = types.SimpleNamespace(DB=self.app.DB, con=lambda: bad_connection, init_db=lambda: None)
        overlay.install(broken, self.handler, {}, self.controller)
        with self.assertRaises(sqlite3.OperationalError):
            broken.init_db()
        bad_connection.close.assert_called_once()
        self.assertFalse(hasattr(broken, '_codex_workspace_wal_anchor'))

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
