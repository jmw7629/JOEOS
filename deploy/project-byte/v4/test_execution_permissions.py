"""Failure-path tests use a synthetic protocol peer; real runner proof is separate."""
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
import importlib.util
import sys
import types
from email.message import Message

from execution_permissions import Gateway, PermissionError


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.now = 1000
        self.posts = []
        self.version = '1.18.29'
        self.session = {'id':'ses_fixture', 'directory':str(self.workspace), 'projectID':'global', 'time':{'created':123}}
        self.pending = [self.item('per_one')]
        self.response_mode = 'ok'
        outer = self
        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                path = self.path.split('?')[0]
                data = {'/global/health':{'healthy':True,'version':outer.version},
                        '/session/ses_fixture':outer.session, '/permission':outer.pending}.get(path)
                self.respond(data)
            def respond(self,data):
                raw=json.dumps(data).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.posts.append((self.path,body))
                if outer.response_mode=='drop':
                    self.connection.shutdown(2);self.connection.close();return
                if outer.response_mode=='false':self.respond(False);return
                if body['reply']=='reject':outer.pending=[]
                else:outer.pending=[p for p in outer.pending if p['id'] not in self.path]
                self.respond(True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Peer)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.secret=secrets.token_urlsafe(32)
        self.patch=mock.patch.dict(os.environ,{'PB_PERMISSION_TEST_SECRET':self.secret})
        self.patch.start();self.addCleanup(self.patch.stop)
        self.cfg={'schema_version':1,'endpoint':f'http://127.0.0.1:{self.server.server_port}',
                  'directory':str(self.workspace),'session_id':'ses_fixture','session_created':123,'project_id':'global',
                  'repository':'jmw7629/JOEOS','generation':'isolated-test-generation','password_env':'PB_PERMISSION_TEST_SECRET','ttl_seconds':120}
        self.save()

    def item(self, ident):
        return {'id':ident,'sessionID':'ses_fixture','permission':'bash','patterns':['printf hello'],
                'always':['printf *'],'metadata':{'command':'printf hello'},'tool':{'messageID':'msg_one','callID':'call_'+ident}}

    def save(self):
        p=self.root/'execution-permissions.json';p.write_text(json.dumps(self.cfg));p.chmod(0o600)
    def gateway(self):return Gateway(self.root,clock=lambda:self.now)
    def decision(self,g=None,kind='approve_once'):
        row=next(r for r in (g or self.gateway()).snapshot()['requests'] if r['state']=='pending')
        return {'handle':row['handle'],'review_token':row['review_token'],'decision':kind,'message':'owner note'}
    def approve(self,value,g=None):return (g or self.gateway()).decide(value,{'subject':'owner','name':'Joe','role':'owner'})
    def rejects(self,call,status):
        with self.assertRaises(PermissionError) as cm:call()
        self.assertEqual(cm.exception.status,status)

    def test_default_disconnected_has_no_network_or_storage(self):
        (self.root/'execution-permissions.json').unlink()
        with mock.patch.object(Gateway,'call',side_effect=AssertionError('network')):
            self.assertEqual(self.gateway().snapshot()['state'],'not_connected')
        self.assertFalse((self.root/'.execution-permissions').exists())

    def test_once_exact_tool_audit_and_replay(self):
        decision=self.decision()
        result=self.approve(decision)
        self.assertEqual(result['state'],'accepted');self.assertFalse(result['tool_completion_verified'])
        self.assertEqual(self.posts[0][1],{'reply':'once'})
        self.rejects(lambda:self.approve(decision),409)
        self.assertEqual(len(self.posts),1)
        history=self.gateway().snapshot()['history']
        self.assertEqual(history[0]['note'],'owner note')
        self.assertEqual(json.loads(history[0]['actor'])['subject'],'owner')
        self.assertNotIn(self.secret,json.dumps(history))
        self.assertEqual((self.root/'.execution-permissions/audit.sqlite3').stat().st_mode&0o777,0o600)

    def test_deny_siblings_and_feedback(self):
        self.pending.append(self.item('per_two'))
        decision=self.decision(kind='deny_session')
        self.assertEqual(self.approve(decision)['state'],'rejected')
        self.assertEqual(self.posts[0][1],{'reply':'reject','message':'owner note'})
        self.assertEqual({r['state'] for r in self.gateway().snapshot()['requests']},{'rejected'})

    def test_new_sibling_requires_review(self):
        decision=self.decision();self.pending.append(self.item('per_two'))
        self.rejects(lambda:self.approve(decision),409);self.assertEqual(self.posts,[])

    def test_expiry_survives_restart_and_clock_rollback(self):
        decision=self.decision();self.now+=121
        self.rejects(lambda:self.approve(decision),409)
        self.now=1000
        self.rejects(lambda:self.approve(decision),409);self.assertEqual(self.posts,[])

    def test_clock_rollback_expires(self):
        decision=self.decision();self.now=900
        self.rejects(lambda:self.approve(decision),409)

    def test_changed_payload_is_never_rebound(self):
        decision=self.decision();self.pending[0]['metadata']['command']='different'
        self.rejects(lambda:self.approve(decision),409)
        self.assertEqual(self.gateway().snapshot()['requests'][0]['state'],'changed')
        self.pending[0]['metadata']['command']='printf hello'
        self.rejects(lambda:self.approve(decision),409)

    def test_wrong_session_directory_version_fail_closed(self):
        decision=self.decision()
        for field,value in [('id','ses_other'),('directory','/wrong'),('projectID','other'),('time',{'created':124})]:
            before=self.session[field];self.session[field]=value
            self.rejects(lambda:self.approve(decision),503);self.session[field]=before
        self.version='1.1.48';self.rejects(lambda:self.approve(decision),503)
        self.assertEqual(self.posts,[])

    def test_ambiguous_reply_never_retried(self):
        for mode in ['drop','false']:
            self.pending=[self.item('per_'+mode)];self.response_mode=mode
            decision=self.decision()
            self.assertEqual(self.approve(decision)['state'],'unknown')
            count=len(self.posts)
            self.rejects(lambda:self.approve(decision),409)
            self.assertEqual(len(self.posts),count)

    def test_interrupted_dispatch_becomes_unknown(self):
        decision=self.decision()
        with sqlite3.connect(self.root/'.execution-permissions/audit.sqlite3') as db:
            db.execute("UPDATE requests SET state='dispatching'")
        self.rejects(lambda:self.approve(decision),409)
        self.assertEqual(self.gateway().snapshot()['requests'][0]['state'],'unknown');self.assertEqual(self.posts,[])

    def test_concurrent_decisions_send_once(self):
        decision=self.decision();outcomes=[]
        def run():
            try:outcomes.append(self.approve(decision)['state'])
            except PermissionError as e:outcomes.append(e.status)
        threads=[threading.Thread(target=run) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(outcomes.count('accepted'),1);self.assertEqual(outcomes.count(409),3);self.assertEqual(len(self.posts),1)

    def test_owner_hold_stops_decision(self):
        decision=self.decision();(self.root/'PERMISSIONS_STOPPED_BY_OWNER').touch()
        self.rejects(lambda:self.approve(decision),423);self.assertEqual(self.posts,[])
        (self.root/'PERMISSIONS_STOPPED_BY_OWNER').unlink()
        g=Gateway(self.root,hold=lambda repo:{'held':True})
        self.rejects(g.snapshot,423)

    def test_registration_rotation_cannot_reuse_decision(self):
        decision=self.decision();self.cfg['generation']='new-isolated-generation';self.save()
        self.rejects(lambda:self.approve(decision),409)
        self.assertEqual(self.posts,[])

    def test_invalid_configs_and_excluded_workers(self):
        before=dict(self.cfg)
        for field,value in [('endpoint','https://example.com'),('endpoint','http://localhost:80'),('endpoint','http://127.0.0.1:80/path'),
                            ('endpoint','http://user@127.0.0.1:80'),('directory','relative'),('ttl_seconds',True),('ttl_seconds',601),
                            ('repository','jmw7629/StickDeath-Infinity-'),('repository','jmw7629/stickdeath-byte'),('repository','jmw7629/vitros-web-dashboard'),
                            ('password_env','HOME'),('session_created',True)]:
            with self.subTest(field=field,value=value):
                self.cfg=dict(before);self.cfg[field]=value;self.save()
                self.rejects(self.gateway,503)
        self.assertFalse((self.root/'.execution-permissions').exists())

    def test_queue_invalid_or_overflow_never_partial(self):
        for pending in [[self.item('per_one')]*2,[self.item('per_'+str(n)) for n in range(65)],{},[{'id':'per_one','sessionID':'ses_fixture'}]]:
            self.pending=pending;self.rejects(lambda:self.gateway().snapshot(),503)
        self.assertEqual(self.posts,[])

    def test_non_tool_and_other_session_not_actionable(self):
        self.pending[0]['sessionID']='ses_other';self.assertEqual(self.gateway().snapshot()['requests'],[])
        self.pending=[self.item('per_one')];self.pending[0].pop('tool')
        self.rejects(lambda:self.gateway().snapshot(),503)

    def test_body_cannot_set_upstream_or_always(self):
        decision=self.decision()
        for extra in [{'endpoint':'http://evil'},{'decision':'always'},{'message':'x'*1001},{'handle':'../x'}]:
            self.rejects(lambda:self.approve({**decision,**extra}),400)
        self.assertEqual(self.posts,[])

    def test_dangling_stop_marker_is_a_hold(self):
        decision=self.decision()
        (self.root/'PERMISSIONS_STOPPED_BY_OWNER').symlink_to(self.root/'absent')
        self.rejects(lambda:self.approve(decision),423);self.assertEqual(self.posts,[])

    def test_registration_and_audit_special_files_fail_closed(self):
        registration=self.root/'execution-permissions.json'
        registration.chmod(0o644);self.rejects(self.gateway,503)
        registration.unlink();os.mkfifo(registration,0o600);self.rejects(self.gateway,503)
        registration.unlink();registration.symlink_to(self.root/'absent')
        self.rejects(self.gateway,503)
        registration.unlink();self.save()
        decision=self.decision()
        db=self.root/'.execution-permissions/audit.sqlite3';db.chmod(0o644)
        self.rejects(lambda:self.approve(decision),503);self.assertEqual(self.posts,[])

    def test_audit_survives_runner_outage(self):
        decision=self.decision();self.response_mode='drop';self.approve(decision)
        self.version='unavailable'
        with self.assertRaises(PermissionError) as cm:self.gateway().snapshot()
        self.assertEqual(cm.exception.history[0]['result'],'unknown')

    def test_rotation_cannot_revive_an_unknown_request(self):
        decision=self.decision();self.response_mode='drop';self.approve(decision)
        self.cfg['generation']='another-new-generation';self.save()
        self.assertEqual(self.gateway().snapshot()['requests'],[])
        self.rejects(lambda:self.approve(decision),409);self.assertEqual(len(self.posts),1)


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        backend=types.ModuleType('backend');backend.H=object
        with mock.patch.dict(sys.modules,{'backend':backend}):
            spec=importlib.util.spec_from_file_location('permission_runtime',Path(__file__).with_name('runtime_server.py'))
            self.runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.runtime)
        self.handler=object.__new__(self.runtime.SafeHandler)
        self.handler.path='/api/execution-permissions/decision'
        self.handler.sendj=lambda value,status=200:(status,value)
        self.handler.need=lambda level:{'subject':'owner','level':4} if level==4 else None
        self.handler.headers=Message()
        for k,v in [('Host','127.0.0.1:8094'),('Content-Type','application/json'),('Content-Length','2')]:self.handler.headers[k]=v
        self.handler.rfile=io.BytesIO(b'{}')

    def test_auth_before_gateway_for_get_and_post(self):
        self.handler.need=lambda level:None
        with mock.patch.object(self.runtime,'permission_gateway',side_effect=AssertionError('unauthorized I/O')):
            self.assertIsNone(self.handler.do_POST())
            self.handler.path='/api/execution-permissions';self.assertIsNone(self.handler.do_GET())

    def test_reject_cross_origin_transfer_and_duplicate_lengths(self):
        for key,value in [('Origin','https://evil.test'),('Transfer-Encoding','chunked'),('Content-Length','2')]:
            self.handler.headers[key]=value
            self.assertEqual(self.handler.do_POST()[0],400)
            del self.handler.headers[key]
            if key=='Content-Length':self.handler.headers[key]='2'

    def test_reject_duplicate_json_and_nonfinite(self):
        for body in [b'{"a":1,"a":2}',b'{"a":NaN}',b'null']:
            del self.handler.headers['Content-Length'];self.handler.headers['Content-Length']=str(len(body))
            self.handler.rfile=io.BytesIO(body)
            self.assertEqual(self.handler.do_POST()[0],400)


if __name__=='__main__':unittest.main(verbosity=2)
