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
if __name__=='__main__':unittest.main(verbosity=2)
