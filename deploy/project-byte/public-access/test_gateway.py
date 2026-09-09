import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from gateway import Access, COOKIE, handler_for, digest, api_allowed


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.key=secrets.token_hex(20);self.viewer=secrets.token_hex(20)
        secret=self.root/'admin.secret';secret.write_text(self.key);secret.chmod(0o600)
        with sqlite3.connect(self.root/'kanban.db') as db:
            db.execute('CREATE TABLE collaborators(id TEXT,name TEXT,role TEXT,token_hash TEXT,active INT)')
            db.execute('INSERT INTO collaborators VALUES (?,?,?,?,?)',('viewer','Viewer','viewer',digest(self.viewer),1))
            db.execute('CREATE TABLE user_settings(subject TEXT,data TEXT)')
            db.execute('INSERT INTO user_settings VALUES (?,?)',('owner','{"security":{"session_minutes":60}}'))
        self.calls=[];outer=self
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def serve(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                outer.calls.append({'path':self.path,'method':self.command,'key':self.headers.get('X-Access-Key'),'body':body,'task':self.headers.get('X-Task-ID'),'filename':self.headers.get('X-Filename'),'origin':self.headers.get('Origin')})
                if self.path=='/api/chat' and self.command=='POST':time.sleep(.5)
                raw=b'<html><head></head><body>PRIVATE WORKSPACE</body></html>' if self.path=='/' else b'{"fixture":"PRIVATE DATA"}'
                self.send_response(200);self.send_header('Content-Type','text/html' if self.path=='/' or self.path.startswith('/uploads/') else 'application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
                if self.command!='HEAD':self.wfile.write(raw)
            do_GET=serve;do_HEAD=serve;do_POST=serve;do_PATCH=serve;do_DELETE=serve
        self.upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream);self.start(self.upstream)
        self.now=1000;self.access=Access(self.root,clock=lambda:self.now)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.server.RequestHandlerClass=handler_for(self.access,self.origin,('127.0.0.1',self.upstream.server_port))
        self.start(self.server)
    def start(self,server):
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
    def request(self,path='/',method='GET',body=None,token='',key='',headers=None):
        h={'Host':self.origin.removeprefix('http://')}
        if token:h['Cookie']=COOKIE+'='+token
        if key:
            session=self.access.sessions.get(digest(token),{})
            h['X-Access-Key']=session.get('csrf',key) if session.get('key')==key else key
        if method not in ('GET','HEAD'):h.update({'Origin':self.origin,'Content-Type':'application/json','X-Project-Byte-Gateway':'1'})
        h.update(headers or {})
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        conn.request(method,path,json.dumps(body).encode() if body is not None else (b'' if method not in ('GET','HEAD') else None),h)
        response=conn.getresponse();raw=response.read();result=(response.status,dict(response.getheaders()),raw);conn.close();return result
    def test_spotify_return_does_not_expose_workspace_or_weaken_session(self):
        status, headers, body = self.request('/spotify-callback?code=TEST_CALLBACK_SECRET&state=TEST_STATE')
        self.assertEqual(status, 200)
        self.assertNotIn(b'TEST_CALLBACK_SECRET', body)
        self.assertNotIn(b'PRIVATE WORKSPACE', body)
        self.assertNotIn('Set-Cookie', headers)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertIn(b"history.replaceState", body)
        self.assertIn(b"sessionStorage.setItem('prfkt.spotify.callback'", body)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request('/')[0], 303)
        self.assertEqual(self.request('/api/tasks')[0], 401)
        self.assertEqual(self.request('/spotify-callback', 'POST', {})[0], 303)
        token = self.login()
        status, headers, body = self.request('/', token=token)
        self.assertEqual(status, 200)
        policy = headers['Content-Security-Policy']
        self.assertIn('https://sdk.scdn.co', policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertNotIn('https:;', policy)

    def login(self,key=None):
        status,headers,raw=self.request('/_gateway/login','POST',{'key':key or self.key});self.assertEqual(status,200,raw)
        cookie=headers['Set-Cookie'];self.assertIn('Secure; HttpOnly; SameSite=Strict',cookie)
        return cookie.split(';')[0].split('=',1)[1]

    def enable_public_owner(self,key=None):
        self.public_key=key or secrets.token_urlsafe(32)
        self.public_file=self.root/'public-owner.secret'
        self.public_file.write_text(self.public_key+'\n');self.public_file.chmod(0o600)
        self.access=Access(self.root,clock=lambda:self.now,public_owner_key_file=self.public_file)
        self.server.RequestHandlerClass=handler_for(self.access,self.origin,('127.0.0.1',self.upstream.server_port))

    def test_public_alias_accepts_strong_key_and_forwards_existing_short_owner_key(self):
        self.key='1234';(self.root/'admin.secret').write_text(self.key)
        self.enable_public_owner()
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],401)
        token=self.login(self.public_key)
        status,_,raw=self.request('/api/session',token=token,key=self.key)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['role'],'owner')
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.key)
        record=self.access.sessions[digest(token)]
        self.assertEqual(record['fingerprint'],digest(self.key))
        self.assertEqual(record['public_owner_fingerprint'],digest(self.public_key))
        self.assertNotIn(self.public_key,repr(record))
        self.assertNotIn(self.public_key,self.request('/',token=token)[2].decode())

    def test_public_alias_rejects_strong_private_owner_key_and_preserves_collaborator(self):
        self.enable_public_owner()
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],401)
        token=self.login(self.viewer)
        self.assertEqual(json.loads(self.request('/api/session',token=token,key=self.viewer)[2])['role'],'viewer')
        self.assertEqual(self.request('/api/tasks',token=token,key=self.viewer)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.viewer)
        self.assertIsNone(self.access.sessions[digest(token)]['public_owner_fingerprint'])
        # Only an exact, explicitly configured public alias can also equal the private key.
        (self.root/'admin.secret').write_text(self.public_key)
        token=self.login(self.public_key)
        self.assertEqual(json.loads(self.request('/api/session',token=token,key=self.public_key)[2])['role'],'owner')
        self.assertEqual(self.access.sessions[digest(token)]['public_owner_fingerprint'],digest(self.public_key))

    def test_explicit_numeric_public_pin_forwards_owner_key_and_rejects_other_short_keys(self):
        self.enable_public_owner('9876')
        token=self.login(self.public_key)
        self.assertEqual(json.loads(self.request('/api/session',token=token,key=self.key)[2])['role'],'owner')
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.key)
        record=self.access.sessions[digest(token)]
        self.assertEqual(record['public_owner_fingerprint'],digest(self.public_key))
        self.assertNotIn(self.public_key,record.values())
        viewer_token=self.login(self.viewer)
        self.assertEqual(json.loads(self.request('/api/session',token=viewer_token,key=self.viewer)[2])['role'],'viewer')
        self.assertEqual(self.request('/api/tasks',token=viewer_token,key=self.viewer)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.viewer)
        self.assertIsNone(self.access.sessions[digest(viewer_token)]['public_owner_fingerprint'])
        before=len(self.calls)
        for invalid in ('9875','abcd','987','98765'):
            self.assertEqual(self.request('/_gateway/login','POST',{'key':invalid})[0],401)
        self.assertEqual(len(self.calls),before)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],401)
        (self.root/'admin.secret').write_text(self.public_key)
        token=self.login(self.public_key)
        self.assertEqual(json.loads(self.request('/api/session',token=token,key=self.public_key)[2])['role'],'owner')

    def test_short_credentials_require_explicit_owner_alias_not_collaborator_or_private_key(self):
        short_key='9876'
        with sqlite3.connect(self.root/'kanban.db') as db:
            db.execute('INSERT INTO collaborators VALUES (?,?,?,?,?)',('short','Short fixture','editor',digest(short_key),1))
        self.assertEqual(self.request('/_gateway/login','POST',{'key':short_key})[0],401)
        (self.root/'admin.secret').write_text(short_key)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':short_key})[0],401)
        self.enable_public_owner()
        self.assertEqual(self.request('/_gateway/login','POST',{'key':short_key})[0],401)
        self.assertEqual(self.calls,[])

    def test_numeric_public_pin_failures_are_rate_limited(self):
        self.enable_public_owner('9876')
        for _ in range(10):
            self.assertEqual(self.request('/_gateway/login','POST',{'key':'9875'})[0],401)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.public_key})[0],429)
        self.assertEqual(self.calls,[])
        self.now+=61
        self.login(self.public_key)

    def test_public_alias_rotation_to_pin_and_back_revokes_credentials_and_sessions(self):
        self.enable_public_owner()
        original_alias=self.public_key;original_token=self.login(original_alias)
        pin='9876';self.public_file.write_text(pin+'\n')
        self.assertEqual(self.request('/',token=original_token)[0],303)
        self.assertNotIn(digest(original_token),self.access.sessions)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':original_alias})[0],401)
        pin_token=self.login(pin)
        replacement=secrets.token_urlsafe(32);self.public_file.write_text(replacement)
        self.assertEqual(self.request('/',token=pin_token)[0],303)
        self.assertNotIn(digest(pin_token),self.access.sessions)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':pin})[0],401)
        replacement_token=self.login(replacement)
        self.assertEqual(self.request('/api/tasks',token=replacement_token,key=self.key)[0],200)

    def test_public_alias_and_private_owner_rotation_revoke_old_sessions(self):
        self.enable_public_owner();token=self.login(self.public_key)
        replacement=secrets.token_urlsafe(32);self.public_file.write_text(replacement)
        self.assertEqual(self.request('/',token=token)[0],303)
        self.assertNotIn(digest(token),self.access.sessions)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.public_key})[0],401)
        token=self.login(replacement)
        self.key=secrets.token_hex(20);(self.root/'admin.secret').write_text(self.key)
        self.assertEqual(self.request('/',token=token)[0],303)
        token=self.login(replacement)
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.key)

    def test_public_alias_never_crosses_owner_collaborator_identity_boundary(self):
        self.enable_public_owner();owner_token=self.login(self.public_key);viewer_token=self.login(self.viewer)
        with sqlite3.connect(self.root/'kanban.db') as db:
            db.execute('INSERT INTO collaborators VALUES (?,?,?,?,?)',('old-owner','Old owner','admin',digest(self.key),1))
        (self.root/'admin.secret').write_text(self.viewer)
        self.assertEqual(self.request('/',token=owner_token)[0],303,'old owner must not become a collaborator')
        self.assertEqual(self.request('/',token=viewer_token)[0],303,'collaborator must not become owner')
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.viewer})[0],401)
        self.assertEqual(self.calls,[])

    def test_public_alias_file_requires_private_regular_valid_credential(self):
        self.enable_public_owner()
        for malformed in ['', 'abcd', '98a6', '987', '98765', '\u0669\u0668\u0667\u0666', '9876\n\n', 'x'*42, 'x'*44, 'x'*42+'!', 'x'*43+'\n\n']:
            self.public_file.write_text(malformed)
            with self.subTest(value_length=len(malformed)),self.assertRaises(ValueError):
                Access(self.root,public_owner_key_file=self.public_file)
        self.public_file.write_text(self.public_key)
        for mode in (0o640,0o644,0o400):
            self.public_file.chmod(mode)
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                Access(self.root,public_owner_key_file=self.public_file)
        self.public_file.chmod(0o600);self.public_file.unlink()
        with self.assertRaises(OSError):Access(self.root,public_owner_key_file=self.public_file)
        self.public_file.symlink_to(self.root/'admin.secret')
        with self.assertRaises(OSError):Access(self.root,public_owner_key_file=self.public_file)

    def test_unavailable_public_alias_fails_closed_during_session(self):
        self.enable_public_owner();token=self.login(self.public_key)
        self.public_file.unlink()
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],503)
        self.public_file.write_text('invalid');self.public_file.chmod(0o600)
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],400)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.viewer})[0],400)
        self.assertEqual(self.calls,[])

    def test_anonymous_never_reaches_private_content_or_head(self):
        for route in ['/','/home.js','/codex-workspace.js','/api/codex-workspace','/api/codex-workspace/artifacts/'+'a'*32,'/api/tasks','/api/projects','/api/activity','/api/intelligence','/uploads/private.html','/admin.secret','/kanban.db']:
            for method in ('GET','HEAD'):
                status,_,raw=self.request(route,method);self.assertIn(status,(303,401));self.assertNotIn(b'PRIVATE',raw)
        self.assertEqual(self.calls,[])
    def test_existing_owner_signin_and_matching_key(self):
        token=self.login();status,h,raw=self.request('/',token=token)
        self.assertEqual(status,200);self.assertIn(b'/_gateway/session.js',raw)
        self.assertNotIn(self.key,raw.decode())
        self.assertNotIn(token,repr(self.access.sessions))
        status,_,raw=self.request('/api/session',token=token,key=self.key)
        self.assertEqual(json.loads(raw)['role'],'owner');self.assertEqual(status,200)
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
        self.assertEqual(self.calls[-1]['key'],self.key)
    def test_cookie_alone_and_key_alone_cannot_read_api(self):
        token=self.login();self.assertEqual(self.request('/api/tasks',token=token)[0],401)
        self.assertEqual(self.request('/api/tasks',key=self.key)[0],401);self.assertEqual(self.calls,[])
    def test_key_mismatch_does_not_revoke_other_tab_session(self):
        token=self.login();self.assertEqual(self.request('/api/tasks',token=token,key=self.viewer)[0],401)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
    def test_bad_keys_do_not_reach_backend_and_are_rate_limited(self):
        for _ in range(10):self.assertEqual(self.request('/_gateway/login','POST',{'key':'wrong-credential-value'} )[0],401)
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],429)
        self.assertEqual(self.calls,[])
        self.now+=61;self.login()
    def test_revocation_role_changes_owner_rotation_and_expiry(self):
        token=self.login(self.viewer)
        with sqlite3.connect(self.root/'kanban.db') as db:db.execute("UPDATE collaborators SET role='editor'")
        self.assertEqual(json.loads(self.request('/api/session',token=token,key=self.viewer)[2])['level'],2)
        with sqlite3.connect(self.root/'kanban.db') as db:db.execute('UPDATE collaborators SET active=0')
        self.assertEqual(self.request('/',token=token)[0],303)
        token=self.login();self.now+=3601;self.assertEqual(self.request('/',token=token)[0],303)
        token=self.login();(self.root/'admin.secret').write_text(secrets.token_hex(20));self.assertEqual(self.request('/',token=token)[0],303)
    def test_explicit_logout_revokes_but_signin_page_preserves_other_tabs(self):
        token=self.login();self.assertEqual(self.request('/_gateway/logout','POST',{},token=token,key=self.key)[0],200)
        self.assertEqual(self.request('/uploads/file.txt',token=token)[0],303)
        token=self.login();self.assertEqual(self.request('/signin',token=token)[0],200)
        self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
    def test_cross_origin_mutations_and_host_spoofing(self):
        token=self.login()
        for origin in ['https://evil.test',self.origin.replace('http:','https:'),'null','']:
            self.assertEqual(self.request('/api/tasks','POST',{},token,self.key,{'Origin':origin})[0],403)
        self.assertEqual(self.request('/',token=token,headers={'Host':'evil.test'})[0],421)
        self.assertEqual(self.calls,[])
    def test_upload_headers_and_safe_authenticated_download(self):
        token=self.login()
        self.assertEqual(self.request('/api/upload','POST',{},token,self.key,{'X-Task-ID':'task-1','X-Filename':'report.txt'})[0],200)
        self.assertEqual(self.calls[-1]['task'],'task-1');self.assertEqual(self.calls[-1]['filename'],'report.txt')
        status,headers,_=self.request('/uploads/file.html',token=token)
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertIn('attachment;',headers['Content-Disposition']);self.assertIn('sandbox',headers['Content-Security-Policy'])
        self.assertIsNone(self.calls[-1]['key'],'download never borrows an owner API key')
    def test_unknown_routes_and_traversal_never_forward(self):
        token=self.login()
        for path in ['/api/arbitrary','/server.py','/execution-permissions.json','/.execution-permissions/audit.sqlite3','/api/../admin.secret','/uploads/%2e%2e/admin.secret','//evil.test','/api/tasks/%5csecret']:
            self.assertIn(self.request(path,token=token,key=self.key)[0],(400,404))
        self.assertEqual(self.calls,[])
    def test_all_observed_dynamic_routes(self):
        for method,path in [('POST','/api/approvals/review:run-id/decision'),('GET','/api/runs/run-id/terminal'),('PATCH','/api/projects/Revenue / 100% Growth'),('PATCH','/api/tasks/task-id'),('DELETE','/api/tasks/task-id')]:
            self.assertTrue(api_allowed(method,path))
        self.assertFalse(api_allowed('DELETE','/api/projects/X'))

    def test_codex_exact_owner_routes_and_authenticated_asset(self):
        token=self.login();ident='a'*32;prefix='/api/codex-workspace'
        routes=[('GET',prefix),('GET',prefix+'/conversations/'+ident),
                ('GET',prefix+'/runs/'+ident+'/events'),('GET',prefix+'/runs/'+ident+'/events?after=0'),
                ('GET',prefix+'/runs/'+ident+'/events?after=197'),('GET',prefix+'/artifacts/'+ident),
                ('POST',prefix+'/message'),('POST',prefix+'/runs/'+ident+'/stop'),
                ('POST',prefix+'/permissions/'+ident+'/decision')]
        for method,route in routes:
            with self.subTest(method=method,route=route):
                self.assertEqual(self.request(route,method,{} if method=='POST' else None,token,self.key)[0],200)
                self.assertEqual(self.calls[-1]['path'],route)
                self.assertEqual(self.calls[-1]['key'],self.key)
                self.assertEqual(self.calls[-1]['origin'],'http://127.0.0.1:8094' if method=='POST' else None)
        status,headers,_=self.request(prefix+'/artifacts/'+ident,token=token,key=self.key)
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'application/json')
        self.assertNotIn('Content-Disposition',headers,'text artifact content remains authenticated JSON for the UI download')
        for method in ('GET','HEAD'):
            self.assertEqual(self.request('/codex-workspace.js',method,token=token)[0],200)
            self.assertIsNone(self.calls[-1]['key'],'script asset does not forward a private API key')

    def test_codex_cookie_nonce_owner_role_and_origin_are_all_required(self):
        owner=self.login();viewer=self.login(self.viewer);ident='a'*32;prefix='/api/codex-workspace'
        routes=[('GET',prefix),('GET',prefix+'/conversations/'+ident),('GET',prefix+'/artifacts/'+ident),
                ('GET',prefix+'/runs/'+ident+'/events?after=0'),('POST',prefix+'/message'),
                ('POST',prefix+'/runs/'+ident+'/stop'),('POST',prefix+'/permissions/'+ident+'/decision')]
        for method,route in routes:
            body={} if method=='POST' else None
            for token,key,status in [('',self.key,401),(owner,'',401),(owner,'stale-nonce',401),(viewer,self.viewer,403)]:
                with self.subTest(route=route,token_type='owner' if token==owner else 'other',key_present=bool(key)):
                    self.assertEqual(self.request(route,method,body,token,key)[0],status)
            if method=='POST':
                self.assertEqual(self.request(route,method,body,owner,self.key,{'Origin':'https://elsewhere.example'})[0],403)
        for role in ('editor','admin'):
            with sqlite3.connect(self.root/'kanban.db') as db:db.execute('UPDATE collaborators SET role=?',(role,))
            self.assertEqual(self.request(prefix,token=viewer,key=self.viewer)[0],403)
            self.assertEqual(self.request(prefix+'/message','POST',{},viewer,self.viewer)[0],403)
        self.assertEqual(self.calls,[])

    def test_codex_dynamic_paths_and_queries_are_canonical_and_bounded(self):
        token=self.login();ident='a'*32;prefix='/api/codex-workspace';events=prefix+'/runs/'+ident+'/events'
        invalid=[prefix+'/',prefix+'?url=http://elsewhere.example',prefix+'?',
                 prefix+'/conversations/'+ident+'?after=0',prefix+'/artifacts/'+ident+'?download=1',
                 prefix+'/artifacts/../admin.secret',prefix+'/runs/'+ident+'/events/extra',
                 events+'?after=-1',events+'?after=01',events+'?after=1.2',events+'?after=1&after=2',
                 events+'?after=0&url=http://elsewhere.example',events+'?after='+('9'*20),
                 events+'?after=%31',events+'?after=',events+'?',
                 prefix+'/conversations/'+('a'*31),prefix+'/conversations/'+('a'*33),
                 prefix+'/conversations/'+('A'*32),prefix+'/conversations/00000000-0000-0000-0000-000000000000',
                 prefix+'/conversations/%61'+('a'*31),'/api/%63odex-workspace',
                 '/api/codex-workspace%2fconversations/'+ident,'/codex-workspace.js?version=1','/codex-workspace.js?',
                 '/%63odex-workspace.js','/codex-workspace.js/extra']
        for route in invalid:
            with self.subTest(route=route):self.assertIn(self.request(route,token=token,key=self.key)[0],(400,404))
        for method,route in [('POST',events),('GET',prefix+'/message'),('HEAD',prefix),
                             ('DELETE',prefix+'/conversations/'+ident),('PATCH',prefix+'/message'),
                             ('POST',prefix+'/message?after=0'),('POST',prefix+'/runs/'+ident+'/stop?after=0')]:
            with self.subTest(method=method,route=route):self.assertEqual(self.request(route,method,{},token,self.key)[0],404)
        self.assertEqual(self.calls,[])

    def test_codex_request_body_limit_is_64_kib(self):
        token=self.login();path='/api/codex-workspace/message'
        self.assertEqual(self.request(path,'POST',{'message':'x'*64000},token,self.key)[0],200)
        before=len(self.calls)
        self.assertEqual(self.request(path,'POST',{'message':'x'*65536},token,self.key)[0],400)
        self.assertEqual(len(self.calls),before)
    def test_parallel_chat_does_not_block_reads(self):
        token=self.login();result=[]
        t=threading.Thread(target=lambda:result.append(self.request('/api/chat','POST',{},token,self.key)[0]));t.start()
        deadline=time.monotonic()+1
        while not self.calls and time.monotonic()<deadline:time.sleep(.01)
        start=time.monotonic();self.assertEqual(self.request('/api/tasks',token=token,key=self.key)[0],200)
        self.assertLess(time.monotonic()-start,.3);t.join();self.assertEqual(result,[200])
    def test_persistent_unique_sources_never_reassigned(self):
        access=Access(self.root,state_dir=self.root/'gateway-state')
        a=access.source(digest(self.key));b=access.source(digest(self.viewer));self.assertNotEqual(a,b)
        restarted=Access(self.root,state_dir=self.root/'gateway-state')
        self.assertEqual(restarted.source(digest(self.viewer)),b);self.assertEqual(restarted.source(digest(self.key)),a)
        self.assertNotIn(self.key,(self.root/'gateway-state/sources.sqlite3').read_bytes().decode(errors='ignore'))
        self.assertEqual((self.root/'gateway-state/sources.sqlite3').stat().st_mode&0o777,0o600)
    def test_trusted_proxy_headers_are_single_valid_values(self):
        self.server.RequestHandlerClass=handler_for(self.access,self.origin,('127.0.0.1',self.upstream.server_port),trusted_serve=True)
        for forwarded in ['1.2.3.4, 5.6.7.8','not-an-ip','']:
            self.assertEqual(self.request('/signin',headers={'X-Forwarded-For':forwarded,'X-Forwarded-Proto':'https'})[0],400)
        self.assertEqual(self.request('/signin',headers={'X-Forwarded-For':'203.0.113.2','X-Forwarded-Proto':'https'})[0],200)
        self.assertEqual(self.request('/signin',headers={'X-Forwarded-For':'203.0.113.2','X-Forwarded-Proto':'http'})[0],400)
    def test_database_absence_fails_closed_without_creating_it(self):
        (self.root/'kanban.db').unlink()
        self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],503)
        self.assertFalse((self.root/'kanban.db').exists());self.assertEqual(self.calls,[])

    def test_stale_responses_never_expire_new_browser_cookie(self):
        token=self.login()
        for response in [self.request('/_gateway/login','POST',{'key':'invalid-key-123456789'},token),
                         self.request('/api/tasks',token=token,key='old-nonce'),
                         self.request('/api/tasks',token='unknown-cookie'),
                         self.request('/_gateway/logout','POST',{},token,self.key)]:
            self.assertNotIn('Set-Cookie',response[1])

    def test_slow_login_has_hard_deadline_and_per_client_limit(self):
        original=self.server.RequestHandlerClass
        class QuickDeadline(original):
            def read_deadline(self,seconds):super().read_deadline(min(seconds,.5))
        self.server.RequestHandlerClass=QuickDeadline
        peers=[]
        try:
            for _ in range(2):
                peer=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=2);peers.append(peer)
                peer.sendall(("POST /_gateway/login HTTP/1.1\r\nHost: "+self.origin.removeprefix('http://')+"\r\nOrigin: "+self.origin+"\r\nX-Project-Byte-Gateway: 1\r\nContent-Type: application/json\r\nContent-Length: 2000\r\n\r\n{").encode())
            deadline=time.monotonic()+.3
            while self.access.login_clients['local-fixture']<2 and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(self.request('/_gateway/login','POST',{'key':self.key})[0],429)
            started=time.monotonic()
            for peer in peers:
                self.assertEqual(peer.recv(1),b'')
            self.assertLess(time.monotonic()-started,1)
        finally:
            for peer in peers:peer.close()


if __name__=='__main__':unittest.main(verbosity=2)
