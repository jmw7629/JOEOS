"""Controller authorization/lifecycle tests with explicit in-process fixtures.

No provider, account, shell, GitHub, or sandbox commands run here. Actual native
protocol is independently exercised by test_codex_task_rpc's opt-in fixtures.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

import codex_tasks as tasks

OWNER = {'ok': True, 'subject': 'owner', 'role': 'owner', 'level': 4}


def rid():
    return str(uuid.uuid4())


def call(name='publish_pull_request', arguments=None, call_id='native-call-1'):
    return {'threadId': 'native-thread-1', 'turnId': 'native-turn-1', 'callId': call_id,
            'requestId': 0, 'namespace': None, 'tool': name,
            'arguments': arguments if arguments is not None else {'title': 'Review fixture', 'body': 'Synthetic patch'}}


class FixtureConnection:
    binary = Path('/fixture/codex')
    home = Path('/fixture/private-auth')
    workspace = Path('/fixture/private-native-workspace')
    def status(self):
        return {'available': True, 'connected': True, 'auth_mode': 'chatgpt'}


class FixtureWorkspace:
    def __init__(self):
        self.files = {'fixture.txt': b'Fixture workspace content'}
        self.writes = []; self.commands = []
    def list(self, path=''):
        return [{'path': name, 'name': name, 'type': 'file', 'size': len(content)} for name, content in self.files.items()]
    def read(self, path):
        return self.files[path]
    def write(self, path, content):
        self.writes.append((path, content)); self.files[path] = content.encode() if isinstance(content, str) else content
        return {'path': path, 'bytes': len(self.files[path])}
    def execute(self, command, *, timeout, stop_event, on_output=None):
        self.commands.append(command)
        if on_output:
            on_output('stdout', 'fixture stdout')
        return {'exit_code': 0, 'stdout': 'fixture stdout', 'stderr': '', 'timed_out': False, 'truncated': False}
    def diff(self):
        return {'changed': ['fixture.txt'], 'patch': 'synthetic diff'}


class FixtureSandbox:
    def __init__(self, run_root):
        self.run_root = Path(run_root)
        self.created = []; self.resumed = []; self.workspaces = {}
    def capabilities(self):
        return {'available': True}
    def create(self, name):
        if name in self.workspaces:
            raise RuntimeError('Run workspace already exists')
        (self.run_root / name).mkdir(parents=True, mode=0o700)
        self.created.append(name); self.workspaces[name] = FixtureWorkspace()
        return self.workspaces[name]
    def open(self, name):
        self.resumed.append(name)
        return self.workspaces[name]


class FixtureSession:
    def __init__(self, factory, kwargs):
        self.factory = factory; self.kwargs = kwargs; self.stop = threading.Event()
        self.closed = False; self.interrupted = 0; self.run_thread = None
        self.thread_id = 'native-thread-1'; self.turn_id = 'native-turn-1'
        self.cancel_event = self.stop
    def run(self, prompt, history=None):
        self.run_thread = threading.current_thread()
        self.factory.prompts.append(prompt); self.factory.entered.set()
        if self.factory.hook:
            return self.factory.hook(self, prompt)
        self.kwargs['on_event']({'method': 'item/agentMessage/delta', 'params': {
            'threadId': self.thread_id, 'turnId': self.turn_id, 'itemId': 'message-1', 'delta': 'Fixture reply'}})
        return {'threadId': self.thread_id, 'turnId': self.turn_id, 'status': 'completed', 'text': 'Fixture reply', 'error': None}
    def interrupt(self):
        self.interrupted += 1; self.stop.set(); return True
    def close(self):
        self.closed = True; self.stop.set()


class FixtureFactory:
    def __init__(self):
        self.instances = []; self.prompts = []; self.entered = threading.Event(); self.hook = None
        self.constructing = None; self.release_construction = None
    def __call__(self, **kwargs):
        if self.constructing:
            self.constructing.set(); self.release_construction.wait(3)
        session = FixtureSession(self, kwargs); self.instances.append(session)
        return session


class FixturePublisher:
    def __init__(self, repo='jmw7629/JOEOS'):
        self.prepared = []; self.published = []; self.repo = repo; self.workspaces = []
    def prepare(self, workspace, run, args, stop_event=None):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError('Stopped before preparation')
        frozen = {'review': 'Synthetic reviewed patch', 'public': {'title': args['title'],
            'patch_sha256': hashlib.sha256(b'fixture patch').hexdigest()}, 'snapshot': 'fixture-snapshot'}
        if self.repo is not None:
            frozen['public']['repo'] = self.repo
        self.workspaces.append(workspace)
        self.prepared.append(frozen)
        return frozen
    def publish(self, frozen, *, stop_event):
        if stop_event.is_set():
            raise RuntimeError('Stopped')
        self.published.append(copy.deepcopy(frozen))
        return {'issue_url': 'https://github.com/fixture/repository/issues/1',
                'pull_request_url': 'https://github.com/fixture/repository/pull/2'}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='byte-controller-fixture-')
        self.root = Path(self.tmp.name); self.state = self.root / 'private-state'
        self.connection = FixtureConnection(); self.sandbox = FixtureSandbox(self.state / 'workspaces'); self.factory = FixtureFactory()
        self.publisher = FixturePublisher(); self.controllers = []; self.background = []
        self.controller = self.make_controller()

    def make_controller(self, **kwargs):
        controller = tasks.Controller(self.state, self.root, connection=self.connection, sandbox=self.sandbox,
                                      session_factory=self.factory, publisher=self.publisher, **kwargs)
        self.controllers.append(controller)
        return controller

    def tearDown(self):
        for controller in self.controllers:
            with controller.condition:
                for context in controller.live.values():
                    context['stop'].set()
                    for session in context['sessions']:
                        session.interrupt(); session.close()
                controller.condition.notify_all()
        if self.factory.release_construction:
            self.factory.release_construction.set()
        for session in self.factory.instances:
            if session.run_thread and session.run_thread is not threading.current_thread():
                session.run_thread.join(timeout=3)
        for thread, done in self.background:
            done.wait(3); thread.join(.1)
        for controller in self.controllers:
            deadline = time.monotonic() + 3
            while controller.live and time.monotonic() < deadline:
                # Seeded test contexts have no execute thread and can be discarded.
                if all(context.get('fixture_seed') for context in controller.live.values()):
                    controller.live.clear(); break
                time.sleep(.01)
            if not controller.live:
                controller.db.close()
        self.tmp.cleanup()

    def wait(self, predicate, message='fixture condition not reached', timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail(message)

    def send(self, text='Make a private fixture change', cid=None, request=None, project='joeos'):
        body = {'request_id': request or rid(), 'conversation_id': cid, 'project_key': project, 'message': text}
        return body, self.controller.message(OWNER, body)

    def finished(self, run, controller=None):
        controller = controller or self.controller
        def terminal():
            with controller.lock:
                row = controller.db.execute('SELECT status FROM runs WHERE id=?', (run,)).fetchone()
                return row['status'] if row and row['status'] not in tasks.ACTIVE and run not in controller.live else None
        return self.wait(terminal)

    def seed(self, project='joeos'):
        controller = self.controller; cid = uuid.uuid4().hex; run = uuid.uuid4().hex; generation = 'fixture-generation'
        context = {'stop': threading.Event(), 'sessions': [], 'generation': generation, 'conversation': cid,
                   'tool_count': 0, 'agents': 0, 'deadline': time.monotonic() + 60,
                   'workspace': FixtureWorkspace(), 'fixture_seed': True, 'finished': threading.Event(),
                   'closing': False, 'inflight': 0}
        with controller.lock:
            controller.db.execute('INSERT INTO conversations VALUES(?,?,?,?,?)', (cid, 'owner', 'Fixture', project, time.time()))
            if project != 'joeos':
                controller.db.execute('INSERT INTO conversation_projects VALUES(?,?,?)', (cid, project, controller.projects[project].fingerprint))
            controller.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)', (run, cid, 'owner', 'working', tasks.MODEL, tasks.EFFORT, generation, time.time()))
            controller.db.commit(); controller.live[run] = context
        return run, context

    def registered_projects(self):
        from codex_projects import Project, default_project
        self.project_sources = {}
        self.project_sandboxes = {'joeos': self.sandbox}
        self.project_publishers = {'joeos': self.publisher}
        projects = [default_project(self.root, publication=True)]
        for key in ('alpha', 'beta'):
            source = (self.root / ('source-' + key)).resolve(); source.mkdir(exist_ok=True)
            self.project_sources[key] = source
            self.project_sandboxes[key] = FixtureSandbox(self.state / 'workspaces')
            self.project_publishers[key] = FixturePublisher('fixture/' + key)
            projects.append(Project(key, key.title(), 'fixture/' + key, source, 'main', 'execute', True,
                                    'Registered fixture', 'HISTORICAL_' + key + ': never new authorization'))
        projects.extend((Project('external', 'External owner', 'fixture/external', None, 'main', 'observe', False, 'Existing worker retains control'),
                         Project('missing', 'Missing source', None, None, None, 'blocked', False, 'Source is not mapped')))
        return tuple(projects)

    def configure_projects(self, projects=None):
        projects = projects or self.registered_projects()
        self.controller = self.make_controller(projects=projects, project_sandboxes=self.project_sandboxes,
                                               project_publishers=self.project_publishers)
        return projects

    def tool_background(self, run, context, native_call):
        result = {}; done = threading.Event()
        def worker():
            try:
                result['value'] = self.controller._tool(run, context, native_call, 'fixture-agent', 'coordinator')
            except Exception as exc:
                result['error'] = exc
            finally:
                done.set()
        thread = threading.Thread(target=worker, daemon=True); self.background.append((thread, done)); thread.start()
        return result, done

    def permission(self, run):
        def pending():
            with self.controller.lock:
                row = self.controller.db.execute("SELECT * FROM permissions WHERE run_id=? AND state='pending'", (run,)).fetchone()
                return dict(row) if row else None
        return self.wait(pending, 'native-shaped callback did not create permission')

    def decide(self, row, decision='approve_once', token=None, key=None):
        body = {'request_id': key or rid(), 'review_token': token if token is not None else row['review_token'],
                'decision': decision, 'note': ''}
        return body, self.controller.decide(OWNER, row['id'], body)

    def test_owner_is_required_on_every_public_read_and_mutation(self):
        bad = {'ok': True, 'subject': 'another-user', 'role': 'owner', 'level': 4}
        calls = [lambda: self.controller.catalog(bad), lambda: self.controller.message(bad, {}),
                 lambda: self.controller.conversation(bad, uuid.uuid4().hex),
                 lambda: self.controller.events(bad, uuid.uuid4().hex),
                 lambda: self.controller.artifact(bad, uuid.uuid4().hex),
                 lambda: self.controller.stop(bad, uuid.uuid4().hex, {}),
                 lambda: self.controller.decide(bad, uuid.uuid4().hex, {})]
        for operation in calls:
            with self.assertRaises(tasks.TaskError) as raised:
                operation()
            self.assertEqual(raised.exception.status, 403)

    def test_message_request_is_durable_idempotent_and_conflicting_reuse_is_rejected(self):
        body, result = self.send(); self.assertEqual(self.finished(result['run_id']), 'completed')
        self.assertEqual(self.controller.message(OWNER, body), result)
        with self.assertRaises(tasks.TaskError) as raised:
            self.controller.message(OWNER, {**body, 'message': 'Different work'})
        self.assertEqual(raised.exception.status, 409)
        restarted = self.make_controller(); self.assertEqual(restarted.message(OWNER, body), result)
        self.assertEqual(len(self.factory.instances), 1)
        self.assertEqual(len(restarted.conversation(OWNER, result['conversation_id'])['messages']), 2)

    def test_terminal_status_follows_agent_completion_and_streamed_text(self):
        _, result = self.send(); self.finished(result['run_id'])
        events = self.controller.events(OWNER, result['run_id'])['events']
        kinds = [event['type'] for event in events]
        self.assertIn('message_delta', kinds); self.assertIn('agent_completed', kinds)
        terminal = next(index for index, event in enumerate(events) if event['type'] == 'status' and event['data']['status'] == 'completed')
        self.assertLess(kinds.index('agent_completed'), terminal,
                        'Polling must not observe a terminal run before its agent is complete')
        self.assertLess(kinds.index('message_delta'), kinds.index('message'))
        self.assertEqual([event['seq'] for event in events], sorted({event['seq'] for event in events}))

    def test_conversation_workspace_resumes_after_controller_restart(self):
        _, first = self.send(); self.finished(first['run_id'])
        self.sandbox.workspaces[first['conversation_id']].files['persisted.txt'] = b'preserved edits'
        restarted = self.make_controller()
        body = {'request_id': rid(), 'conversation_id': first['conversation_id'], 'project_key': 'joeos', 'message': 'Continue the previous work'}
        second = restarted.message(OWNER, body)
        self.assertEqual(self.finished(second['run_id'], restarted), 'completed')
        self.assertEqual(self.sandbox.workspaces[first['conversation_id']].read('persisted.txt'), b'preserved edits')
        self.assertEqual(len(self.sandbox.created), 1)

    def test_workspace_read_bytes_and_command_text_are_returned(self):
        run, context = self.seed()
        for native in [call('workspace_read', {'path': 'fixture.txt'}), call('workspace_command', {'command': 'fixture test'})]:
            result = self.controller._tool(run, context, native, 'fixture-agent', 'coordinator')
            self.assertTrue(result['success'], result)
            self.assertIn('Fixture workspace content' if native['tool'] == 'workspace_read' else 'fixture stdout', result['text'])

    def test_permission_binds_native_ids_exact_patch_and_single_use_decision(self):
        run, context = self.seed(); result, done = self.tool_background(run, context, call()); row = self.permission(run)
        binding = json.loads(row['binding']); self.assertEqual(binding['requestId'], 0)
        self.assertEqual(binding['threadId'], 'native-thread-1'); self.assertEqual(binding['turnId'], 'native-turn-1')
        self.assertEqual(binding['callId'], 'native-call-1')
        review = json.loads(row['arguments']); self.assertEqual(review['patch_sha256'], self.publisher.prepared[0]['public']['patch_sha256'])
        with self.assertRaises(tasks.TaskError) as raised:
            self.decide(row, token='wrong-token')
        self.assertEqual(raised.exception.status, 409); self.assertEqual(self.publisher.published, [])
        body, approved = self.decide(row); self.assertTrue(done.wait(2), result); self.assertTrue(result['value']['success'])
        self.assertEqual(len(self.publisher.published), 1); self.assertEqual(self.publisher.published[0], self.publisher.prepared[0])
        self.assertEqual(self.controller.decide(OWNER, row['id'], body), approved)
        with self.assertRaises(tasks.TaskError) as raised:
            self.decide(row)
        self.assertEqual(raised.exception.status, 409); self.assertEqual(len(self.publisher.published), 1)

    def test_same_native_publish_call_cannot_be_replayed_after_approval(self):
        run, context = self.seed(); native = call()
        result, done = self.tool_background(run, context, native); row = self.permission(run)
        self.decide(row); self.assertTrue(done.wait(2), result)
        self.assertEqual(len(self.publisher.published), 1)
        replay = self.controller._tool(run, context, native, 'fixture-agent', 'coordinator')
        self.assertFalse(replay['success']); self.assertEqual(len(self.publisher.published), 1)
        rows = self.controller.db.execute('SELECT COUNT(*) FROM permissions WHERE run_id=?', (run,)).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_permission_from_old_generation_cannot_be_approved(self):
        run, context = self.seed(); result, done = self.tool_background(run, context, call()); row = self.permission(run)
        context['generation'] = 'different-generation'
        with self.assertRaises(tasks.TaskError) as raised:
            self.decide(row)
        self.assertEqual(raised.exception.status, 409); self.assertEqual(self.publisher.published, [])
        context['stop'].set()
        with self.controller.condition:
            self.controller.condition.notify_all()
        self.assertTrue(done.wait(2), result)

    def test_event_cursor_pages_are_ordered_and_do_not_drop_later_records(self):
        run, context = self.seed()
        for index in range(450):
            self.controller.event(run, 'tool_output', {'text': str(index)})
        first = self.controller.events(OWNER, run)['events']
        second = self.controller.events(OWNER, run, first[-1]['seq'])['events']
        third = self.controller.events(OWNER, run, second[-1]['seq'])['events']
        self.assertEqual([len(first), len(second), len(third)], [200, 200, 50])
        self.assertEqual([event['data']['text'] for event in first + second + third], [str(index) for index in range(450)])
        for cursor in (-1, True, '2'):
            with self.assertRaises(tasks.TaskError):
                self.controller.events(OWNER, run, cursor)

    def test_denial_and_expiry_do_not_dispatch_publisher(self):
        for choice in ('deny', 'expire'):
            with self.subTest(choice=choice):
                run, context = self.seed(); result, done = self.tool_background(run, context, call()); row = self.permission(run)
                if choice == 'deny':
                    self.decide(row, decision='deny')
                else:
                    with self.controller.condition:
                        self.controller.db.execute('UPDATE permissions SET expires_at=? WHERE id=?', (time.time() - 1, row['id']))
                        self.controller.db.commit(); self.controller.condition.notify_all()
                self.assertTrue(done.wait(2), result); self.assertFalse(result['value']['success'])
                self.assertEqual(self.publisher.published, [])
                with self.assertRaises(tasks.TaskError) as raised:
                    self.decide(row)
                self.assertEqual(raised.exception.status, 409)

    def test_stop_invalidates_pending_permission_and_is_idempotent(self):
        run, context = self.seed(); result, done = self.tool_background(run, context, call()); row = self.permission(run)
        body = {'request_id': rid()}; accepted = self.controller.stop(OWNER, run, body)
        self.assertEqual(self.controller.stop(OWNER, run, body), accepted); self.assertTrue(done.wait(2), result)
        self.assertFalse(result['value']['success']); self.assertEqual(self.publisher.published, [])
        with self.assertRaises(tasks.TaskError) as raised:
            self.decide(row)
        self.assertEqual(raised.exception.status, 409)

    def test_restart_expires_unexecuted_approvals_and_marks_dispatch_unknown(self):
        run, context = self.seed()
        with self.controller.lock:
            for state in ('pending', 'approved', 'dispatching'):
                self.controller.db.execute('INSERT INTO permissions(id,run_id,generation,binding,tool,summary,arguments,review_token,expires_at,state) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, run, context['generation'], json.dumps(call()), 'publish_pull_request', 'Fixture', '{}', state, time.time()+600, state))
            self.controller.db.commit()
        restarted = self.make_controller()
        rows = restarted.db.execute('SELECT review_token,state FROM permissions WHERE run_id=?', (run,)).fetchall()
        self.assertEqual({row['review_token']: row['state'] for row in rows}, {'pending': 'expired', 'approved': 'expired', 'dispatching': 'unknown'})
        self.assertEqual(restarted.events(OWNER, run)['run']['status'], 'interrupted')
        self.assertEqual(self.publisher.published, []); self.assertEqual(self.factory.instances, [])

    def test_specialists_are_real_factory_sessions_read_only_parallel_and_bounded(self):
        run, context = self.seed(); barrier = threading.Barrier(3)
        def hook(session, prompt):
            barrier.wait(timeout=2)
            return {'status': 'completed', 'text': 'Independent fixture finding'}
        self.factory.hook = hook
        reports = self.controller._delegate(run, context, [{'role': role, 'task': 'Inspect fixture'} for role in ('architect', 'reviewer', 'verifier')], 'parent-agent')
        self.assertEqual(len(reports), 3); self.assertTrue(all(report['status'] == 'completed' for report in reports))
        self.assertEqual(len(self.factory.instances), 3)
        for session in self.factory.instances:
            self.assertEqual({tool['name'] for tool in session.kwargs['tools']}, {'workspace_list', 'workspace_read'})
            self.assertTrue(session.closed)
        with self.assertRaises(tasks.TaskError):
            self.controller._delegate(run, context, [{'role': 'reviewer', 'task': 'Inspect'}] * 4, 'parent-agent')
        context['agents'] = 6
        with self.assertRaises(tasks.TaskError):
            self.controller._delegate(run, context, [{'role': 'reviewer', 'task': 'Inspect'}], 'parent-agent')
        prohibited = self.controller._tool(run, context, call('workspace_write', {'path': 'fixture.txt', 'content': 'bad'}), 'specialist', 'reviewer')
        self.assertFalse(prohibited['success']); self.assertEqual(context['workspace'].writes, [])

    def test_session_construction_failure_leaves_no_working_agent(self):
        def fail(**kwargs):
            raise RuntimeError('Synthetic constructor failure')
        self.controller.session_factory = fail
        _, result = self.send()
        self.assertEqual(self.finished(result['run_id']), 'failed')
        agents = self.controller.events(OWNER, result['run_id'])['agents']
        self.assertFalse(any(agent['status'] in ('queued', 'working') for agent in agents), agents)

    def test_delegate_timeout_cancels_its_native_child_sessions(self):
        run, context = self.seed(); context['deadline'] = time.monotonic() + .02
        def hook(session, prompt):
            session.stop.wait(3)
            return {'status': 'interrupted', 'text': 'Stopped fixture'}
        self.factory.hook = hook
        with self.assertRaises(tasks.TaskError):
            self.controller._delegate(run, context, [{'role': 'reviewer', 'task': 'Inspect fixture'}], 'parent-agent')
        self.assertTrue(context['stop'].is_set())
        self.assertTrue(self.factory.instances[0].stop.is_set(), 'Timed-out native specialist was not cancelled')

    def test_stop_during_session_construction_does_not_start_uncancelled_turn(self):
        self.factory.constructing = threading.Event(); self.factory.release_construction = threading.Event()
        running = threading.Event()
        def hook(session, prompt):
            running.set(); session.stop.wait(2)
            return {'status': 'interrupted' if session.stop.is_set() else 'completed', 'text': 'Fixture'}
        self.factory.hook = hook
        _, result = self.send(); self.assertTrue(self.factory.constructing.wait(2))
        self.controller.stop(OWNER, result['run_id'], {'request_id': rid()})
        self.factory.release_construction.set()
        # A stopped request may skip run() entirely, or interrupt the newly made session.
        self.wait(lambda: bool(self.factory.instances))
        session = self.factory.instances[0]
        self.wait(lambda: session.closed or session.stop.is_set() or result['run_id'] not in self.controller.live,
                  'A session created after Stop was never cancelled', timeout=.5)

    def test_premature_native_terminal_drains_command_before_admitting_new_work(self):
        for native_status in ('completed', 'failed'):
            with self.subTest(native_status=native_status):
                entered, cancel_seen, release, callback_done = (threading.Event() for _ in range(4))
                self.addCleanup(release.set)
                result_box = {}
                def hook(session, prompt):
                    with self.controller.lock:
                        context = next(iter(self.controller.live.values()))
                        workspace = context['workspace']
                    def slow_command(command, *, timeout, stop_event, on_output=None):
                        entered.set()
                        if stop_event.wait(2):
                            cancel_seen.set()
                        release.wait(3)
                        # Deliberately late output must be fenced by the controller.
                        if on_output:
                            on_output('stdout', 'late cancelled command output')
                        return {'exit_code': -15, 'stdout': '', 'stderr': '', 'stopped': True,
                                'timed_out': False, 'truncated': False}
                    workspace.execute = slow_command
                    def callback():
                        try:
                            result_box['tool'] = session.kwargs['on_tool'](call('workspace_command', {'command': 'synthetic slow command'}))
                        finally:
                            callback_done.set()
                    thread = threading.Thread(target=callback, daemon=True)
                    self.background.append((thread, callback_done))
                    thread.start()
                    self.assertTrue(entered.wait(2))
                    return {'status': native_status, 'text': 'Premature synthetic native terminal'}
                self.factory.hook = hook
                _, result = self.send()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(cancel_seen.wait(2), 'Native terminal did not cancel the live command callback')
                    current = self.controller.events(OWNER, result['run_id'])
                    self.assertIn(current['run']['status'], tasks.ACTIVE,
                                  'A terminal run was exposed before its command callback returned')
                    self.assertIn(result['run_id'], self.controller.live)
                    with self.assertRaises(tasks.TaskError) as denied:
                        self.send('New work while old command is still stopping')
                    self.assertEqual(denied.exception.status, 409)
                    self.assertFalse(callback_done.is_set())
                finally:
                    release.set()
                self.assertTrue(callback_done.wait(2))
                terminal = self.finished(result['run_id'])
                self.assertIn(terminal, ('completed', 'failed', 'stopped'))
                snapshot = self.controller.events(OWNER, result['run_id'])
                events = snapshot['events']
                terminal_index = next(i for i, event in enumerate(events)
                                      if event['type'] == 'status' and event['data']['status'] not in tasks.ACTIVE)
                self.assertFalse(any(event['type'] in ('tool_started', 'tool_output', 'tool_completed', 'agent_started', 'agent_completed')
                                     for event in events[terminal_index + 1:]), 'Tool or agent activity appeared after terminal state')
                session = self.factory.instances[-1]
                late = session.kwargs['on_tool'](call('workspace_write', {'path': 'late.txt', 'content': 'must not write'}))
                self.assertFalse(late['success'])
                self.assertNotIn('late.txt', self.sandbox.workspaces[result['conversation_id']].files)
                self.assertEqual(self.controller.events(OWNER, result['run_id'])['events'], events,
                                 'Rejected stale callback added post-terminal events')

    def test_premature_native_terminal_cancels_and_drains_permission_without_publication(self):
        original_permission = self.controller._permission
        for native_status in ('completed', 'failed'):
            with self.subTest(native_status=native_status):
                waiting, unwinding, release, callback_done = (threading.Event() for _ in range(4))
                self.addCleanup(release.set)
                row_box = {}
                def slow_unwind(*args, **kwargs):
                    try:
                        return original_permission(*args, **kwargs)
                    finally:
                        unwinding.set()
                        release.wait(3)
                self.controller._permission = slow_unwind
                def hook(session, prompt):
                    with self.controller.lock:
                        run = next(iter(self.controller.live))
                    def callback():
                        try:
                            session.kwargs['on_tool'](call())
                        finally:
                            callback_done.set()
                    thread = threading.Thread(target=callback, daemon=True)
                    self.background.append((thread, callback_done))
                    thread.start()
                    row_box['permission'] = self.permission(run)
                    waiting.set()
                    return {'status': native_status, 'text': 'Premature synthetic native terminal'}
                self.factory.hook = hook
                _, result = self.send()
                try:
                    self.assertTrue(waiting.wait(2))
                    self.assertTrue(unwinding.wait(2), 'Terminal native state did not release its pending permission')
                    self.assertIn(self.controller.events(OWNER, result['run_id'])['run']['status'], tasks.ACTIVE,
                                  'A terminal run was exposed while a permission callback was still unwinding')
                    with self.assertRaises(tasks.TaskError) as denied:
                        self.decide(row_box['permission'])
                    self.assertEqual(denied.exception.status, 409)
                    with self.assertRaises(tasks.TaskError) as denied:
                        self.send('New work while old permission is still stopping')
                    self.assertEqual(denied.exception.status, 409)
                    self.assertFalse(callback_done.is_set())
                    self.assertEqual(self.publisher.published, [])
                finally:
                    release.set()
                    self.controller._permission = original_permission
                self.assertTrue(callback_done.wait(2))
                self.finished(result['run_id'])
                snapshot = self.controller.events(OWNER, result['run_id'])
                states = {permission['state'] for permission in snapshot['permissions']}
                self.assertFalse(states & {'pending', 'approved', 'dispatching'})
                self.assertEqual(self.publisher.published, [])
                statuses = [event['data']['status'] for event in snapshot['events'] if event['type'] == 'status']
                terminal_index = next(i for i, status in enumerate(statuses) if status not in tasks.ACTIVE)
                self.assertFalse(any(status in tasks.ACTIVE for status in statuses[terminal_index + 1:]),
                                 'Permission unwind revived a terminal run')

    def test_terminal_run_rejects_late_specialist_start_and_completion(self):
        captured = {}
        def hook(session, prompt):
            with self.controller.lock:
                captured['context'] = next(iter(self.controller.live.values()))
            return {'status': 'completed', 'text': 'Synthetic complete task'}
        self.factory.hook = hook
        _, result = self.send()
        self.finished(result['run_id'])
        original = self.controller.events(OWNER, result['run_id'])
        agent = original['agents'][0]['id']
        count = len(self.factory.instances)
        self.controller._agent_done(result['run_id'], agent, 'completed')
        with self.assertRaises(tasks.TaskError):
            self.controller._session(result['run_id'], captured['context'], 'reviewer', agent)
        self.assertEqual(len(self.factory.instances), count, 'A specialist session was created after the run ended')
        self.assertEqual(self.controller.events(OWNER, result['run_id']), original,
                         'A late specialist start/completion mutated a terminal run')

    def test_project_catalog_is_publicly_scoped_and_observation_cannot_execute(self):
        self.configure_projects()
        catalog = self.controller.catalog(OWNER)
        entries = {p['key']: p for p in catalog['projects']}
        self.assertEqual(set(entries), {'joeos', 'alpha', 'beta', 'external', 'missing'})
        self.assertTrue(entries['alpha']['configured']); self.assertTrue(entries['alpha']['publication'])
        self.assertFalse(entries['external']['configured']); self.assertFalse(entries['missing']['publication'])
        self.assertNotIn('HISTORICAL_', json.dumps(catalog)); self.assertNotIn(str(self.root), json.dumps(catalog))
        for key in ('external', 'missing', 'unregistered', '../alpha'):
            with self.subTest(project=key), self.assertRaises(tasks.TaskError):
                self.send(project=key)
        self.assertFalse(self.factory.instances)
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        for publisher in self.project_publishers.values():
            self.assertFalse(publisher.prepared); self.assertFalse(publisher.published)
        with patch.object(self.project_sandboxes['alpha'], 'capabilities', return_value={'available': False}):
            self.assertTrue(self.controller.catalog(OWNER)['configured'], 'An unavailable project disabled every project')
            with self.assertRaises(tasks.TaskError) as denied:
                self.send(project='alpha')
            self.assertEqual(denied.exception.status, 503)
        self.assertFalse(self.factory.instances)

    def test_registered_projects_use_only_their_selected_sandbox_and_reference(self):
        self.configure_projects()
        seen = []
        def hook(session, prompt):
            context = next(iter(self.controller.live.values()))
            key = context['project'].key
            write = session.kwargs['on_tool'](call('workspace_write', {'path': 'selected.txt', 'content': key}))
            read = session.kwargs['on_tool'](call('workspace_read', {'path': 'selected.txt'}))
            self.assertTrue(write['success']); self.assertEqual(read['text'], key)
            seen.append((key, prompt))
            return {'status': 'completed', 'text': 'Isolated ' + key}
        self.factory.hook = hook
        _, a = self.send(project='alpha'); self.finished(a['run_id'])
        _, b = self.send(project='beta'); self.finished(b['run_id'])
        self.assertNotEqual(a['conversation_id'], b['conversation_id'])
        for key, result in (('alpha', a), ('beta', b)):
            self.assertEqual(self.project_sandboxes[key].created, [result['conversation_id']])
            self.assertEqual(self.project_sandboxes[key].workspaces[result['conversation_id']].read('selected.txt'), key.encode())
            history = self.controller.conversation(OWNER, result['conversation_id'])
            self.assertEqual(history['conversation']['project_key'], key)
        self.assertFalse(self.sandbox.created)
        for key, prompt in seen:
            other = 'beta' if key == 'alpha' else 'alpha'
            self.assertIn('HISTORICAL_' + key, prompt); self.assertNotIn('HISTORICAL_' + other, prompt)
            self.assertIn('untrusted background', prompt)

    def test_cross_project_conversation_and_request_reuse_are_rejected(self):
        self.configure_projects()
        original, a = self.send(project='alpha'); self.finished(a['run_id'])
        for body in ({**original, 'project_key': 'beta'},
                     {**original, 'request_id': rid(), 'conversation_id': a['conversation_id'], 'project_key': 'beta'}):
            with self.assertRaises(tasks.TaskError) as denied:
                self.controller.message(OWNER, body)
            self.assertEqual(denied.exception.status, 409)
        self.assertFalse(self.project_sandboxes['beta'].created)
        self.assertEqual(len(self.factory.instances), 1)
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)

    def test_project_identity_binding_survives_restart_and_rejects_source_changes(self):
        projects = self.configure_projects()
        _, a = self.send(project='alpha'); self.finished(a['run_id'])
        binding = dict(self.controller.db.execute('SELECT * FROM conversation_projects WHERE conversation_id=?', (a['conversation_id'],)).fetchone())
        changed = tuple(replace(p, repo='fixture/changed') if p.key == 'alpha' else p for p in projects)
        self.configure_projects(changed)
        with self.assertRaises(tasks.TaskError) as denied:
            self.send(project='alpha', cid=a['conversation_id'])
        self.assertEqual(denied.exception.status, 409)
        self.assertEqual(dict(self.controller.db.execute('SELECT * FROM conversation_projects WHERE conversation_id=?', (a['conversation_id'],)).fetchone()), binding)
        self.assertEqual(self.controller.conversation(OWNER, a['conversation_id'])['conversation']['project_key'], 'alpha')
        self.assertEqual(len(self.factory.instances), 1)
        self.configure_projects(projects)
        _, resumed = self.send(project='alpha', cid=a['conversation_id']); self.finished(resumed['run_id'])
        self.assertEqual(self.project_sandboxes['alpha'].resumed, [a['conversation_id']])

    def test_only_original_joeos_history_can_adopt_a_missing_project_binding(self):
        _, legacy = self.send(); self.finished(legacy['run_id'])
        self.controller.db.execute('DELETE FROM conversation_projects'); self.controller.db.commit()
        self.configure_projects()
        _, continued = self.send(cid=legacy['conversation_id']); self.finished(continued['run_id'])
        self.assertIsNotNone(self.controller.db.execute('SELECT 1 FROM conversation_projects WHERE conversation_id=?', (legacy['conversation_id'],)).fetchone())
        _, foreign = self.send(project='alpha'); self.finished(foreign['run_id'])
        self.controller.db.execute('DELETE FROM conversation_projects WHERE conversation_id=?', (foreign['conversation_id'],)); self.controller.db.commit()
        with self.assertRaises(tasks.TaskError) as denied:
            self.send(project='alpha', cid=foreign['conversation_id'])
        self.assertEqual(denied.exception.status, 409)

    def test_one_coordinator_admission_gate_covers_all_registered_projects(self):
        self.configure_projects()
        release = threading.Event(); self.addCleanup(release.set)
        def hook(session, prompt):
            release.wait(3)
            return {'status': 'completed', 'text': 'Held fixture'}
        self.factory.hook = hook
        _, a = self.send(project='alpha'); self.assertTrue(self.factory.entered.wait(2))
        try:
            with self.assertRaises(tasks.TaskError) as denied:
                self.send(project='beta')
            self.assertEqual(denied.exception.status, 409)
            self.assertFalse(self.project_sandboxes['beta'].created)
        finally:
            release.set()
        self.finished(a['run_id'])
        _, b = self.send(project='beta'); self.finished(b['run_id'])
        self.assertEqual(len(self.factory.instances), 2)

    def test_project_approval_is_bound_to_selected_publisher_and_identity(self):
        self.configure_projects()
        run, context = self.seed(project='alpha')
        result, done = self.tool_background(run, context, call())
        row = self.permission(run)
        review = json.loads(row['arguments']); binding = json.loads(row['binding'])
        self.assertEqual(review['project_key'], 'alpha'); self.assertEqual(review['repo'], 'fixture/alpha')
        self.assertEqual(binding['project_fingerprint'], self.controller.projects['alpha'].fingerprint)
        self.assertEqual(review['project_fingerprint'], binding['project_fingerprint'])
        self.decide(row); self.assertTrue(done.wait(2)); self.assertTrue(result['value']['success'])
        self.assertEqual(len(self.project_publishers['alpha'].published), 1)
        self.assertEqual(self.project_publishers['alpha'].workspaces, [context['workspace']])
        self.assertFalse(self.project_publishers['beta'].prepared); self.assertFalse(self.publisher.prepared)

    def test_changed_project_identity_cannot_approve_a_pending_publication(self):
        self.configure_projects()
        run, context = self.seed(project='alpha')
        result, done = self.tool_background(run, context, call())
        row = self.permission(run)
        current = self.controller.projects
        self.controller.projects = {**current, 'alpha': replace(current['alpha'], repo='fixture/other')}
        try:
            with self.assertRaises(tasks.TaskError) as denied:
                self.decide(row)
            self.assertEqual(denied.exception.status, 409)
            self.controller.stop(OWNER, run, {'request_id': rid()})
            self.assertTrue(done.wait(2)); self.assertFalse(result['value']['success'])
            self.assertFalse(self.project_publishers['alpha'].published)
        finally:
            self.controller.projects = current

    def test_publisher_repository_mismatch_never_creates_an_approval(self):
        self.configure_projects()
        self.project_publishers['alpha'].repo = 'fixture/beta'
        run, context = self.seed(project='alpha')
        result = self.controller._tool(run, context, call(), 'fixture-agent', 'coordinator')
        self.assertFalse(result['success']); self.assertIn('different repository', result['text'])
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM permissions').fetchone()[0], 0)
        self.assertFalse(self.project_publishers['alpha'].published)

    def test_publisher_missing_repository_never_creates_an_approval(self):
        self.configure_projects()
        self.project_publishers['alpha'].repo = None
        run, context = self.seed(project='alpha')
        result = self.controller._tool(run, context, call(), 'fixture-agent', 'coordinator')
        self.assertFalse(result['success']); self.assertIn('different repository', result['text'])
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM permissions').fetchone()[0], 0)
        self.assertEqual(self.controller.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
        self.assertFalse(self.project_publishers['alpha'].published)
