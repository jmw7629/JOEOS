"""Native telemetry fixtures: no external provider or publisher calls."""
import json
import threading
import unittest
import codex_tasks as tasks
import test_codex_tasks as fixtures
from test_codex_tasks import OWNER, call, rid

class TraceTests(unittest.TestCase):
    setUp = fixtures.ControllerTests.setUp
    tearDown = fixtures.ControllerTests.tearDown
    make_controller = fixtures.ControllerTests.make_controller
    wait = fixtures.ControllerTests.wait

    def test_correlation_usage_visible_messages_and_privacy(self):
        def hook(session, prompt):
            emit, invoke = session.kwargs['on_event'], session.kwargs['on_tool']
            for count in (10, 10, 15):
                emit({'method':'thread/tokenUsage/updated','params':{'threadId':'thread','turnId':'turn','tokenUsage':{'total':{'totalTokens':count,'inputTokens':8,'cachedInputTokens':True,'reasoningOutputTokens':-1},'last':{'totalTokens':5},'token':'SECRET'}}})
            emit({'method':'item/completed','params':{'item':{'type':'reasoning','text':'HIDDEN_SECRET'}}})
            emit({'method':'account/updated','params':{'token':'AUTH_SECRET'}})
            workers=[threading.Thread(target=invoke,args=(c,)) for c in [call('workspace_read',{'path':'fixture.txt'},'a'),call('workspace_list',{},'b')]]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            emit({'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':{'type':'agentMessage','id':'visible','text':'Visible reply','phase':'final_answer'}}})
            return {'status':'completed','text':'Visible reply'}
        self.factory.hook=hook
        result=self.controller.message(OWNER,{'request_id':rid(),'project_key':'joeos','conversation_id':None,'message':'Trace fixture'})
        self.wait(lambda: result['run_id'] not in self.controller.live)
        snapshot=self.controller.events(OWNER,result['run_id']); events=snapshot['events']
        starts={e['data']['tool_id']:e['data'] for e in events if e['type']=='tool_started'}
        ends={e['data']['tool_id']:e['data'] for e in events if e['type']=='tool_completed'}
        self.assertEqual(set(starts),set(ends)); self.assertEqual(len(starts),2)
        for tid,end in ends.items():
            self.assertGreaterEqual(end['duration_ms'],0);self.assertGreaterEqual(end['ended_at'],starts[tid]['started_at']);self.assertIn('text',end['result'])
        self.assertEqual(len(snapshot['usage']),1);self.assertEqual(snapshot['usage'][0]['total'],{'total_tokens':15,'input_tokens':8})
        final=next(e['data'] for e in events if e['type']=='message');native=next(e['data'] for e in events if e['type']=='agent_message')
        self.assertEqual(final['native_message_ids'],[native['message_id']])
        self.assertEqual(final['message_id'],self.controller.conversation(OWNER,result['conversation_id'])['messages'][-1]['id'])
        self.assertNotIn('SECRET',json.dumps(snapshot))
        with self.assertRaises(tasks.TaskError):self.controller.events({**OWNER,'level':3},result['run_id'])

    def test_bounds_identity_and_invalid_usage(self):
        data=tasks.envelope({'text':'🌕'*9000},16384)
        self.assertTrue(data['truncated']);self.assertLessEqual(len(data['text'].encode()),16384);self.assertEqual(data['format'],'json')
        self.assertEqual(tasks.tool_identity('run',call()),tasks.tool_identity('run',{**call(),'project_fingerprint':'ignored'}))
        self.assertNotEqual(tasks.tool_identity('run',call()),tasks.tool_identity('other',call()))
        self.assertIsNone(tasks.tool_identity('run',{'tool':'workspace_read'}))
        self.assertIsNone(tasks.usage_data({'threadId':'thread','tokenUsage':{'total':{'totalTokens':float('inf')}}},'agent'))

    def test_pending_permission_links_without_exposing_capability(self):
        def hook(session,prompt):
            self.assertFalse(session.kwargs['on_tool'](call())['success'])
            return {'status':'completed','text':'Denied'}
        self.factory.hook=hook
        result=self.controller.message(OWNER,{'request_id':rid(),'project_key':'joeos','conversation_id':None,'message':'Permission fixture'})
        snapshot=self.wait(lambda: (s if (s:=self.controller.events(OWNER,result['run_id']))['permissions'] else None))
        permission=snapshot['permissions'][0];start=next(e['data'] for e in snapshot['events'] if e['type']=='tool_started')
        self.assertEqual(permission['tool_id'],start['tool_id']);self.assertLessEqual(permission['expires_at']-snapshot['server_time'],600)
        self.assertNotIn(permission['review_token'],json.dumps(snapshot['events']))
        inbox=self.controller.catalog(OWNER)['execution_permissions']
        self.assertTrue(inbox['capable']);self.assertEqual(inbox['decision_scope'],'request')
        self.assertEqual(len(inbox['requests']),1)
        self.assertTrue(inbox['requests'][0]['can_decide'])
        self.assertEqual(inbox['requests'][0]['review_token'],permission['review_token'])
        self.assertNotIn('project_fingerprint',json.dumps(inbox))
        with self.assertRaises(tasks.TaskError):self.controller.permission_snapshot({**OWNER,'level':3})

        self.controller.decide(OWNER,permission['id'],{'request_id':rid(),'review_token':permission['review_token'],'decision':'deny','note':'Fixture'})
        self.wait(lambda:result['run_id'] not in self.controller.live)
        self.assertEqual(self.publisher.published,[])
        inbox=self.controller.permission_snapshot(OWNER)
        self.assertEqual(inbox['requests'],[])
        self.assertEqual(inbox['history'][0]['state'],'denied')
        self.assertNotIn('review_token',json.dumps(inbox))
        self.assertTrue(any(e['type']=='tool_completed' and not e['data']['success'] for e in self.controller.events(OWNER,result['run_id'])['events']))

if __name__=='__main__':unittest.main()
