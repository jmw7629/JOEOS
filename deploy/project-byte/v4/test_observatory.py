import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
class ObservatoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        stub=types.ModuleType('backend');stub.H=object;stub.ROOT=self.root;stub.UPLOADS=self.root/'uploads'
        stub.BRIDGE_STATE={r:self.root/r.replace('/','__') for r in ['jmw7629/stickdeath-byte','jmw7629/vitros-web-dashboard']}
        stub.task_rows=lambda:[];stub.run_rows=lambda:[];sys.modules['backend']=stub
        for root in stub.BRIDGE_STATE.values():(root/'logs').mkdir(parents=True)
        spec=importlib.util.spec_from_file_location('observatory_runtime',HERE/'runtime_server.py');self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)
        self.log=stub.BRIDGE_STATE['jmw7629/stickdeath-byte']/'logs'/'issue-7.log'
    def event(self,identifier='tool-1',**patch):
        result={'type':'tool_use','timestamp':1700000002000,'sessionID':'session-fixture','part':{'id':identifier,'type':'tool','tool':'bash','state':{'status':'completed','input':{'command':'echo SUPER_PRIVATE_SENTINEL','api_key':'sk-PRIVATE_SENTINEL_123456789'},'output':'TOP_SECRET_OUTPUT','time':{'start':1700000001000,'end':1700000002000}}}}
        result.update(patch);return result
    def write(self,items):self.log.write_text('\n'.join(json.dumps(item) for item in items)+'\n')
    def test_metadata_only_redacts_arguments_responses_and_session_ids(self):
        self.write([self.event()]);snapshot=self.mod.observatory_snapshot();raw=json.dumps(snapshot)
        for secret in ['SUPER_PRIVATE_SENTINEL','PRIVATE_SENTINEL','TOP_SECRET_OUTPUT','session-fixture']:self.assertNotIn(secret,raw)
        event=snapshot['traces'][0]['events'][0];self.assertEqual(event['duration_ms'],1000);self.assertEqual(event['argument_fields'],['command','api_key']);self.assertEqual(event['response_chars'],17)
    def test_duplicate_event_id_does_not_double_count(self):
        e=self.event();self.write([e,e]);snap=self.mod.observatory_snapshot();self.assertEqual(snap['traces'][0]['event_count'],1)
    def test_completed_event_replaces_prior_pending_snapshot(self):
        first=self.event();first['part']['state']['status']='pending';last=self.event();self.write([first,last]);t=self.mod.observatory_snapshot()['traces'][0]
        self.assertEqual(t['event_count'],1);self.assertEqual(t['events'][0]['status'],'completed')
    def test_fifo_cannot_block_state_or_log_reads(self):
        root=self.mod.app.BRIDGE_STATE['jmw7629/stickdeath-byte'];os.mkfifo(root/'processed.json')
        self.assertEqual(self.mod._obs_processed(root),{})
        os.mkfifo(self.log)
        with self.assertRaises(ValueError):self.mod._obs_parse_log(self.log,'repo','project')
    def test_absent_metrics_remain_unknown(self):
        e=self.event();e['part']['state'].pop('time');self.write([e]);t=self.mod.observatory_snapshot()['traces'][0]
        self.assertIsNone(t['tokens']);self.assertIsNone(t['events'][0]['duration_ms']);self.assertEqual(t['status'],'historical')
    def test_step_tokens_and_delegation_are_only_recorded_values(self):
        e=self.event();e['part']['tool']='task';e['part']['state']['input']={'subagent_type':'explore','prompt':'PRIVATE_PROMPT'};e['part']['state']['metadata']={'sessionId':'child-fixture'}
        step={'type':'step_finish','timestamp':1700000003000,'sessionID':'session-fixture','part':{'id':'step-1','tokens':{'total':320,'input':300,'output':20}}};self.write([e,step]);t=self.mod.observatory_snapshot()['traces'][0]
        self.assertEqual(t['tokens'],320);self.assertEqual(t['events'][0]['delegate'],'explore');self.assertTrue(t['events'][0]['child_session']);self.assertNotIn('PRIVATE_PROMPT',json.dumps(t))
    def test_symlink_log_and_unapproved_names_are_never_read(self):
        target=self.root/'secret';target.write_text(json.dumps(self.event()));self.log.symlink_to(target);(self.log.parent/'password.log').write_text(json.dumps(self.event()))
        self.assertEqual(self.mod.observatory_snapshot()['traces'],[])
    def test_symlink_directory_is_unavailable(self):
        root=self.mod.app.BRIDGE_STATE['jmw7629/stickdeath-byte'];(root/'logs').rmdir();outside=self.root/'outside';outside.mkdir();(outside/'issue-1.log').write_text(json.dumps(self.event()));(root/'logs').symlink_to(outside,target_is_directory=True)
        snap=self.mod.observatory_snapshot();self.assertEqual(snap['traces'],[]);self.assertEqual(snap['state'],'partial')
    def test_tail_limit_and_malformed_json_are_explicit(self):
        self.log.write_text('x'*(self.mod.OBS_LOG_BYTES+100)+'\n{bad-json}\n'+json.dumps(self.event())+'\n');t=self.mod.observatory_snapshot()['traces'][0]
        self.assertTrue(t['partial']);self.assertEqual(t['skipped_records'],1);self.assertEqual(len(t['events']),1)
    def test_history_limit_and_disallowed_repository(self):
        self.write([self.event()]);self.mod.OBS_LOG_LIMIT=2
        for i in range(4):(self.log.parent/f'issue-{i+20}.log').write_text(json.dumps(self.event(str(i))))
        legacy=self.root/'legacy';(legacy/'logs').mkdir(parents=True);(legacy/'logs'/'issue-100.log').write_text(json.dumps(self.event()));self.mod.app.BRIDGE_STATE['jmw7629/StickDeath-Infinity-']=legacy
        snap=self.mod.observatory_snapshot();self.assertEqual(len(snap['traces']),2);self.assertTrue(snap['sources'][0]['partial']);self.assertNotIn('StickDeath-Infinity-',json.dumps(snap))
    def test_cache_reuses_snapshot_and_never_executes(self):
        self.write([self.event()])
        with mock.patch.object(self.mod.subprocess,'run',side_effect=AssertionError('must not execute')):
            one=self.mod.observatory_snapshot();two=self.mod.observatory_snapshot()
        self.assertIs(one,two);self.assertFalse(one['capabilities']['execution_permissions']);self.assertTrue(one['capabilities']['read_only'])
    def owner(self):return {'role':'owner','level':4,'subject':'owner'}
    def test_owner_can_read_actual_tool_prompt_arguments_response_and_visible_message(self):
        e=self.event();e['part']['tool']='task';e['part']['state']['input']['prompt']='Inspect the test failures and report the cause.'
        visible={'type':'text','timestamp':1700000003000,'part':{'id':'visible','type':'text','text':'The failure comes from the parser.'}}
        prompt={'type':'message','role':'user','timestamp':1700000000000,'content':[{'type':'text','text':'Fix the parser without changing the API.'}]}
        self.write([prompt,e,visible]);snap=self.mod.observatory_snapshot(self.owner());events=snap['traces'][0]['events']
        self.assertTrue(snap['capabilities']['owner_evidence']);self.assertIn('Fix the parser',events[0]['evidence']['prompt']['text'])
        evidence=events[1]['evidence'];self.assertEqual(evidence['prompt']['text'],'Inspect the test failures and report the cause.')
        self.assertIn('echo SUPER_PRIVATE_SENTINEL',evidence['arguments']['text']);self.assertNotIn('PRIVATE_SENTINEL_123456789',evidence['arguments']['text'])
        self.assertTrue(evidence['arguments']['redacted']);self.assertEqual(evidence['response']['text'],'TOP_SECRET_OUTPUT')
        self.assertEqual(events[2]['message_role'],'assistant');self.assertIn('failure comes from the parser',events[2]['summary'])
        self.assertIsNone(events[2]['evidence']['arguments']);self.assertIsNone(events[2]['duration_ms'])
    def test_owner_and_editor_cache_cannot_disclose_across_actor_changes(self):
        self.write([self.event()]);owner=self.mod.observatory_snapshot(self.owner())
        for actor in (None,{'role':'editor','level':2},{'role':'admin','level':3},{'role':'editor','level':4},{'role':'owner','level':2}):
            snapshot=self.mod.observatory_snapshot(actor)
            self.assertIsNone(snapshot['traces'][0]['events'][0]['evidence']);self.assertNotIn('SUPER_PRIVATE_SENTINEL',json.dumps(snapshot))
            self.assertFalse(snapshot['capabilities']['owner_evidence']);self.assertIsNot(snapshot,owner)
        self.assertIs(self.mod.observatory_snapshot(self.owner()),owner)
    def test_http_authorizes_before_read_and_uses_verified_actor(self):
        self.write([self.event()]);handler=object.__new__(self.mod.SafeHandler);handler.path='/api/observatory?owner=true'
        handler.sendj=lambda value,code=200:(code,value)
        handler.need=lambda level:None
        with mock.patch.object(self.mod,'observatory_snapshot',side_effect=AssertionError('must authorize before reading')):
            self.assertIsNone(handler.do_GET())
        handler.need=lambda level:{'role':'editor','level':2}
        code,editor=handler.do_GET();self.assertEqual(code,200);self.assertFalse(editor['capabilities']['owner_evidence'])
        handler.need=lambda level:self.owner()
        code,owner=handler.do_GET();self.assertEqual(code,200);self.assertTrue(owner['capabilities']['owner_evidence'])
    def test_credential_redaction_structured_headers_urls_and_echoed_values(self):
        e=self.event();e['part']['state']['input']={'command':'curl -H "Authorization: Bearer ABCD12345678" https://user:myurlpass@example.test/?api_key=query-secret-value',
             'apiKey':'ordinary-secret-value','nested':{'Password':'another-sensitive-value'},'private_key':'-----BEGIN PRIVATE KEY-----\nPRIVATE_KEY_SENTINEL\n-----END PRIVATE KEY-----'}
        e['part']['state']['output']='Read complete. ordinary-secret-value another-sensitive-value\nCookie: login=COOKIE_SENTINEL\npassword="a password with spaces"\nTOKEN=ENV_SENTINEL\n'+ 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signaturehere'
        self.write([e]);raw=json.dumps(self.mod.observatory_snapshot(self.owner()))
        for secret in ('ABCD12345678','myurlpass','query-secret-value','ordinary-secret-value','another-sensitive-value','PRIVATE_KEY_SENTINEL','COOKIE_SENTINEL','a password with spaces','ENV_SENTINEL','signaturehere'):
            self.assertNotIn(secret,raw,secret)
        self.assertIn('Read complete.',raw)
    def test_cookie_assignments_and_curl_cookie_user_options_are_redacted(self):
        commands=[
            'COOKIE=COOKIE_SENTINEL_456 curl https://example.test',
            'HTTP_COOKIE="session=COOKIE_QUOTED_SENTINEL; remember=COOKIE_SECOND_SENTINEL" curl https://example.test',
            "curl --cookie 'session=COOKIE_LONG_SENTINEL; other=COOKIE_LONG_SECOND' https://example.test",
            'curl --cookie="session=COOKIE_EQUALS_SENTINEL; other=COOKIE_EQUALS_SECOND" https://example.test',
            "curl -b 'session=COOKIE_SHORT_SENTINEL; other=COOKIE_SHORT_SECOND' https://example.test",
            "curl -b'session=COOKIE_ATTACHED_SENTINEL' https://example.test",
            "curl -b'session=COOKIE_ATTACHED_SENTINEL; other=COOKIE_ATTACHED_SECOND_SENTINEL' https://example.test",
            'curl -b=session=COOKIE_SHORT_EQUALS_SENTINEL https://example.test',
            'curl --user joe:USER_SENTINEL_456 https://example.test',
            'curl --user="joe:USER_EQUALS_SENTINEL" https://example.test',
            "curl -u 'joe:USER_SHORT_SENTINEL with spaces' https://example.test",
            "curl -ujoe:USER_ATTACHED_SENTINEL https://example.test",
            "curl -u'joe:USER_ATTACHED_SENTINEL with USER_ATTACHED_SECOND_SENTINEL' https://example.test",
            'curl -u=joe:USER_SHORT_EQUALS_SENTINEL https://example.test',
            "curl --user 'joe':\"USER_SEGMENT_SENTINEL\" https://example.test",
            'curl --user joe:USER_ESCAPED_SENTINEL\\ with\\ spaces https://example.test',
            "curl '--cookie' 'session=COOKIE_QUOTED_OPTION_SENTINEL' https://example.test",
            'curl --proxy-user=joe:USER_PROXY_SENTINEL https://example.test',
            'curl -Ujoe:USER_PROXY_SHORT_SENTINEL https://example.test',
        ]
        events=[]
        for index,command in enumerate(commands):
            with self.subTest(command=index):
                redacted=self.mod._obs_redact_text(command)
                self.assertNotIn('SENTINEL',redacted);self.assertIn('https://example.test',redacted)
                event=self.event('credentials-'+str(index));event['part']['state']['input']={'command':command};event['part']['state']['output']=command;events.append(event)
        self.write(events);snapshot=self.mod.observatory_snapshot(self.owner())
        self.assertEqual(snapshot['traces'][0]['event_count'],len(commands))
        raw=json.dumps(snapshot);self.assertNotIn('SENTINEL',raw);self.assertIn('https://example.test',raw)
    def test_long_cookie_user_values_are_redacted_before_evidence_bounds(self):
        command='curl --cookie="session='+('LONG_COOKIE_SENTINEL'*1200)+'" -ujoe:'+('LONG_USER_SENTINEL'*1200)+' https://example.test'
        e=self.event();e['part']['state']['input']={'command':command};e['part']['state']['output']='COOKIE='+('LONG_ENV_SENTINEL'*1200)
        self.write([e]);snapshot=self.mod.observatory_snapshot(self.owner());raw=json.dumps(snapshot)
        self.assertNotIn('SENTINEL',raw);self.assertIn('https://example.test',raw)
        self.assertLess(len(raw),5000)
        evidence=snapshot['traces'][0]['events'][0]['evidence'];self.assertTrue(evidence['arguments']['redacted']);self.assertTrue(evidence['response']['redacted'])
    def test_hidden_reasoning_events_and_content_blocks_are_never_visible(self):
        items=[{'type':'reasoning','text':'HIDDEN_REASONING'}, {'type':'text','channel':'analysis','part':{'text':'HIDDEN_CHANNEL'}},
               {'type':'text','part':{'type':'reasoning','text':'HIDDEN_PART'}}, {'type':'message','message':{'role':'assistant','channel':'analysis','content':'HIDDEN_MESSAGE'}},
               {'type':'text','visibility':'internal','text':'HIDDEN_INTERNAL'},
               {'type':'message','role':'assistant','content':[{'type':'reasoning','text':'HIDDEN_BLOCK'},{'type':'output_text','text':'Visible result.'}]}]
        e=self.event();e['part']['state']['output']={'type':'reasoning','text':'HIDDEN_TOOL_BLOCK'};items.append(e)
        self.write(items);snap=self.mod.observatory_snapshot(self.owner());raw=json.dumps(snap)
        self.assertEqual(snap['traces'][0]['event_count'],2);self.assertNotIn('HIDDEN_',raw);self.assertIn('Visible result.',raw)
    def test_hidden_phases_and_nested_message_depth_are_excluded(self):
        items=[{'type':'text','phase':phase,'text':'HIDDEN_PHASE'} for phase in ('analysis','reasoning','thinking')]
        items.extend([{'type':'message','message':{'phase':'analysis','content':'HIDDEN_NESTED_PHASE'}},
                      {'type':'message','role':'assistant','message':{'message':{'content':'HIDDEN_NESTED_DEPTH'}}},
                      {'type':'message','role':'assistant','message':{'role':'developer','content':'HIDDEN_NESTED_ROLE'}},
                      {'type':'text','phase':'final','text':'Visible final result.'},
                      {'type':'message','content':[{'type':'text','phase':'analysis','text':'HIDDEN_PHASE_BLOCK'},{'type':'text','text':'Visible block.'}]}])
        tool=self.event();tool['part']['state']['output']={'type':'text','phase':'thinking','text':'HIDDEN_TOOL_PHASE'};items.append(tool)
        self.write(items);raw=json.dumps(self.mod.observatory_snapshot(self.owner()))
        self.assertNotIn('HIDDEN_',raw);self.assertIn('Visible final result.',raw)
        self.assertEqual(self.mod._obs_visible_text({'message':{'phase':'thinking','content':'HIDDEN_DIRECT'}},{}),(None,''))
    def test_recorded_counts_are_separate_and_duration_needs_both_recorded_endpoints(self):
        e=self.event();e['part']['state']['time'].pop('start')
        step={'type':'step_finish','timestamp':1700000003000,'part':{'id':'step-1','tokens':{'input':300,'output':20,'reasoning':5,'cache':{'read':100,'write':0}}}}
        self.write([e,step]);trace=self.mod.observatory_snapshot(self.owner())['traces'][0]
        self.assertIsNone(trace['events'][0]['duration_ms']);self.assertIsNone(trace['tokens'])
        tokens=trace['events'][1]['tokens'];self.assertIsNone(tokens['total']);self.assertEqual(tokens['input'],300);self.assertEqual(tokens['cache_read'],100);self.assertEqual(tokens['cache_write'],0)
    def test_payload_limits_are_explicit_and_do_not_expose_secret_suffixes(self):
        e=self.event();e['part']['state']['input']={'prompt':'Useful prompt '*1000,'command':'Read the file'}
        e['part']['state']['output']='x'*8190+' Bearer '+('CREDENTIAL_SENTINEL'*1000)
        self.write([e]);evidence=self.mod.observatory_snapshot(self.owner())['traces'][0]['events'][0]['evidence']
        fields=[v for v in evidence.values() if v]
        self.assertTrue(evidence['prompt']['truncated']);self.assertTrue(evidence['response']['truncated'])
        self.assertTrue(all(len(v['text'])<=self.mod.OBS_EVIDENCE_FIELD_CHARS for v in fields))
        self.assertLessEqual(sum(len(v['text']) for v in fields),self.mod.OBS_EVIDENCE_EVENT_CHARS)
        self.assertGreater(evidence['prompt']['recorded_chars'],len(evidence['prompt']['text']));self.assertNotIn('CREDENTIAL_SENTINEL',json.dumps(evidence))
        self.assertLess(len(json.dumps(evidence)),self.mod.OBS_EVIDENCE_EVENT_CHARS+3000)
    def test_owner_reads_only_existing_allowlist_and_never_executes_or_changes_files(self):
        self.write([self.event()]);before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        original_open=self.mod.os.open
        def guarded_open(path,*args,**kwargs):
            self.assertIn(Path(path).name,{'processed.json','issue-7.log'})
            return original_open(path,*args,**kwargs)
        with mock.patch.object(self.mod.os,'open',side_effect=guarded_open), mock.patch.object(self.mod.subprocess,'run',side_effect=AssertionError('read only')):
            snapshot=self.mod.observatory_snapshot(self.owner())
        self.assertEqual(len(snapshot['traces']),1)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
if __name__=='__main__':unittest.main(verbosity=2)
