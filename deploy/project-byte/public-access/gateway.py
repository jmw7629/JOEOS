#!/usr/bin/env python3
"""Authenticated public gateway; app source/data/keys remain unchanged."""
from __future__ import annotations
import argparse
import collections
import hashlib
import hmac
import http.client
import ipaddress
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import threading
import time
from urllib.parse import quote, unquote, urlsplit

COOKIE = '__Host-project_byte_session'
ASSETS = {'/', '/index.html', '/home.js', '/home-inspector.js', '/health-runtime.js', '/ai-connections.js', '/codex-workspace.js'}
GET_APIS = {'/healthz','/api/session','/api/tasks','/api/projects','/api/intelligence','/api/activity',
            '/api/models','/api/agents','/api/runs','/api/approvals','/api/settings','/api/notifications',
            '/api/team','/api/memory','/api/chat','/api/help','/api/observatory','/api/external-reviews',
            '/api/workspace-profile','/api/execution-permissions','/api/ai-connections'}
POST_APIS = {'/api/auth','/api/settings','/api/notifications/read','/api/tasks','/api/chat','/api/models/test',
             '/api/models','/api/agents','/api/memory','/api/team','/api/upload','/api/execution-permissions/decision',
             '/api/ai-connections/discover','/api/ai-connections/connect','/api/ai-connections/default',
             '/api/ai-connections/login','/api/ai-connections/disconnect'}
MAX_BODY = 10 * 1024 * 1024
CODEX_MAX_BODY = 64 * 1024
MAX_RESPONSE = 20 * 1024 * 1024
CODEX_PREFIX = '/api/codex-workspace'
CODEX_ID = r'[0-9a-f]{32}'
CODEX_EVENTS = re.compile(CODEX_PREFIX+'/runs/'+CODEX_ID+'/events')
CSP = "default-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def codex_api(path):
    return path == CODEX_PREFIX or path.startswith(CODEX_PREFIX+'/')


def api_allowed(method,path,query=''):
    if codex_api(path):
        if method == 'GET':
            if CODEX_EVENTS.fullmatch(path):
                return not query or bool(re.fullmatch(r'after=(?:0|[1-9][0-9]{0,18})',query))
            return not query and (path == CODEX_PREFIX or bool(re.fullmatch(
                CODEX_PREFIX+'/(conversations|artifacts)/'+CODEX_ID,path)))
        return method == 'POST' and not query and (path == CODEX_PREFIX+'/message' or bool(re.fullmatch(
            CODEX_PREFIX+'/(runs/'+CODEX_ID+'/stop|permissions/'+CODEX_ID+'/decision)',path)))
    ident=r'[A-Za-z0-9:_-]{1,160}'
    if method=='GET':return path in GET_APIS or bool(re.fullmatch('/api/runs/'+ident+'/terminal',path))
    if method=='POST':return path in POST_APIS or bool(re.fullmatch('/api/(approvals/'+ident+'/decision|tasks/'+ident+'/queue)',path))
    if method=='PATCH':return bool(re.fullmatch('/api/(tasks|team)/'+ident,path)) or path.startswith('/api/projects/') and len(path)>14
    if method=='DELETE':return bool(re.fullmatch('/api/tasks/'+ident,path))
    return False


class Access:
    def __init__(self, app_root, clock=time.time, state_dir=None, public_owner_key_file=None):
        self.root = Path(app_root).resolve(strict=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.attempts = {}
        self.login_clients = collections.Counter()
        # Omission is supported only by disposable compatibility fixtures. The
        # Production requires an explicitly configured public owner credential.
        self.public_owner_key_file = Path(public_owner_key_file) if public_owner_key_file else None
        self.public_owner_key()
        self.state_dir = Path(state_dir) if state_dir else None
        if self.state_dir:
            self.state_dir.mkdir(mode=0o700, exist_ok=True)
            info=self.state_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=os.getuid():
                raise ValueError('Private gateway state directory required')
            fd=os.open(self.state_dir/'sources.sqlite3',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            info=os.fstat(fd);os.close(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=os.getuid():
                raise ValueError('Private gateway state file required')
            with sqlite3.connect(self.state_dir/'sources.sqlite3') as db:
                db.execute('CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL)')

    def source(self, fingerprint):
        if not self.state_dir:
            return None  # Disposable cross-platform fixtures only.
        with self.lock, sqlite3.connect(self.state_dir/'sources.sqlite3') as db:
            row=db.execute('SELECT id FROM sources WHERE fingerprint=?',(fingerprint,)).fetchone()
            if not row:
                current=db.execute('SELECT coalesce(max(id),0) FROM sources').fetchone()[0]
                if current>=65534:raise ValueError('Gateway source allocation is full')
                db.execute('INSERT INTO sources VALUES (?,?)',(current+1,fingerprint))
                row=(current+1,)
            ident=row[0]
        # Stable, distinct loopback sources keep the app's per-IP failure counter
        # from turning one revoked collaborator's requests into an owner lockout.
        return ('127.77.'+str(ident//256)+'.'+str(ident%256),0)

    def public_owner_key(self):
        if self.public_owner_key_file is None:
            return None
        fd = os.open(self.public_owner_key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError('Public owner credential is unavailable')
            raw = stream.read(45)
        # A four-digit owner PIN is an explicit administrator configuration;
        # collaborator keys and automatically generated owner keys stay long.
        if not re.fullmatch(rb'(?:[A-Za-z0-9_-]{43}|[0-9]{4})\n?', raw):
            raise ValueError('Public owner credential is unavailable')
        return raw.decode().rstrip('\n')

    def owner_key(self):
        fd = os.open(self.root / 'admin.secret', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError('Owner credential is unavailable')
            raw = stream.read(1025)
        if len(raw) > 1024:
            raise ValueError('Owner credential is unavailable')
        return raw.decode().strip()

    def identity(self, fingerprint):
        # Never import the app: import/init routines can mutate its database.
        owner_hash = digest(self.owner_key())
        with sqlite3.connect('file:' + quote(str(self.root / 'kanban.db')) + '?mode=ro', uri=True, timeout=3) as db:
            db.execute('PRAGMA query_only=ON')
            if hmac.compare_digest(fingerprint, owner_hash):
                identity = {'ok':True, 'subject':'owner', 'name':'Joe', 'role':'owner', 'level':4}
            else:
                row = db.execute('SELECT id,name,role,active FROM collaborators WHERE token_hash=?', (fingerprint,)).fetchone()
                if not row or not row[3] or row[2] not in ('viewer','editor','admin'):
                    return None
                identity = {'ok':True, 'subject':'collab:'+row[0], 'name':row[1], 'role':row[2],
                            'level':{'viewer':1,'editor':2,'admin':3}[row[2]]}
            row = db.execute('SELECT data FROM user_settings WHERE subject=?', (identity['subject'],)).fetchone()
            minutes = 480
            if row:
                settings = json.loads(row[0])
                minutes = max(60, min(1440, int(settings.get('security', {}).get('session_minutes', 480))))
            return identity, minutes * 60

    def login(self, key, old_token, client='local-fixture'):
        if not isinstance(key, str) or not 4 <= len(key) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            return None
        with self.lock:
            now = self.clock()
            self.attempts={k:v for k,v in self.attempts.items() if v and v[-1]>now-60}
            attempts=self.attempts.setdefault(client,collections.deque())
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10 or len(self.attempts)>4096:
                return 'limited'
            attempts.append(now)
        fingerprint = digest(key)
        public_owner_key = self.public_owner_key()
        public_owner_fingerprint = None
        if public_owner_key is not None:
            owner_key = self.owner_key()
            alias_fingerprint = digest(public_owner_key)
            if hmac.compare_digest(fingerprint, alias_fingerprint):
                key = owner_key
                fingerprint = digest(owner_key)
                public_owner_fingerprint = alias_fingerprint
            elif len(key) < 16 or hmac.compare_digest(fingerprint, digest(owner_key)):
                # Private credentials stay private unless the owner explicitly
                # selected that same value as their public credential.
                return None
        elif len(key) < 16:
            return None
        found = self.identity(fingerprint)
        if not found:
            return None
        if public_owner_key is not None and (found[0]['role'] == 'owner') != (public_owner_fingerprint is not None):
            return None
        with self.lock:
            self.sessions = {k:v for k,v in self.sessions.items() if now < v['expires'] and now >= v['created']}
            if len(self.sessions) >= 256:
                return 'limited'
            self.sessions.pop(digest(old_token), None)
            token = secrets.token_urlsafe(32)
            self.sessions[digest(token)] = {'fingerprint':fingerprint, 'key':key, 'csrf':secrets.token_urlsafe(32),
                                           'created':now, 'expires':now+found[1],
                                           'public_owner_fingerprint':public_owner_fingerprint}
            return token, found[1]

    def check(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        token_hash = digest(token)
        with self.lock:
            record = self.sessions.get(token_hash)
            if record is None:
                return None
            record = dict(record)
        found = self.identity(record['fingerprint'])
        public_owner_key = self.public_owner_key()
        alias_fingerprint = record.get('public_owner_fingerprint')
        if public_owner_key is not None and found and (found[0]['role'] == 'owner') != (alias_fingerprint is not None):
            self.logout(token)
            return None
        if alias_fingerprint is not None and (public_owner_key is None or
                not hmac.compare_digest(alias_fingerprint, digest(public_owner_key)) or
                not found or found[0]['role'] != 'owner'):
            self.logout(token)
            return None
        now = self.clock()
        if not found or now < record['created'] or now >= min(record['expires'], record['created']+found[1]):
            self.logout(token)
            return None
        return record, found[0]

    def logout(self, token):
        with self.lock:
            self.sessions.pop(digest(token), None)


def handler_for(access, origin, upstream=('127.0.0.1',8094), trusted_serve=False):
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid public origin')
    here = Path(__file__).resolve().parent
    login_html = (here / 'signin.html').read_bytes()
    session_js = (here / 'session.js').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'PROJECT_BYTE'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(15)
            self.deadline=None
            self.read_deadline(30)

        def read_deadline(self, seconds):
            if self.deadline:self.deadline.cancel()
            def stop():
                try:self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            self.deadline=threading.Timer(seconds,stop);self.deadline.daemon=True;self.deadline.start()

        def finish(self):
            if self.deadline:self.deadline.cancel()
            super().finish()

        def log_message(self, *_):
            pass  # Never log URLs, credentials, cookies, or bodies.

        def cookie(self):
            raw = self.headers.get_all('Cookie', [])
            if len(raw) != 1 or len(raw[0]) > 4096:
                return ''
            try:
                if raw[0].count(COOKIE+'=') != 1:
                    return ''
                cookie = SimpleCookie();cookie.load(raw[0])
                return cookie[COOKIE].value if COOKIE in cookie else ''
            except Exception:
                return ''

        def respond(self, code, data=b'', kind='application/json; charset=utf-8', extra=(), attachment=False):
            self.deadline.cancel()
            if not isinstance(data, bytes):
                data = json.dumps(data, separators=(',',':')).encode()
            self.send_response(code)
            for key,value in [('Content-Type',kind),('Content-Length',str(len(data))),('Cache-Control','no-store, private'),
                              ('X-Content-Type-Options','nosniff'),('Referrer-Policy','same-origin'),
                              ('Content-Security-Policy',"sandbox; default-src 'none'; frame-ancestors 'none'" if attachment else CSP),
                              ('X-Frame-Options','DENY'),('Permissions-Policy','camera=(), microphone=(), geolocation=()'),
                              ('Strict-Transport-Security','max-age=31536000'),('Connection','close'),*extra]:
                self.send_header(key,value)
            self.end_headers();self.close_connection=True
            if self.command != 'HEAD':
                self.wfile.write(data)

        def deny(self, changed=False):
            if self.path.startswith('/api/') or self.path.startswith('/_gateway/'):
                return self.respond(401, {'error':'Workspace session changed' if changed else 'Sign in to continue'},
                                    extra=[('X-Project-Byte-Session','changed' if changed else 'expired')])
            return self.respond(303, b'', extra=[('Location','/signin')])

        def body(self, limit):
            lengths=self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or int(lengths[0])>limit:
                raise ValueError('Invalid body')
            length=int(lengths[0]);raw=self.rfile.read(length)
            if len(raw)!=length:raise ValueError('Incomplete body')
            return raw

        def dispatch(self):
            # Only the configured public virtual host may use this loopback listener.
            if self.headers.get_all('Host',[]) != [parsed.netloc]:
                return self.respond(421, {'error':'Unknown workspace host'})
            client='local-fixture'
            if trusted_serve:
                forwarded=self.headers.get_all('X-Forwarded-For',[])
                if (self.client_address[0]!='127.0.0.1' or len(forwarded)!=1
                        or self.headers.get_all('X-Forwarded-Proto',[])!=['https']):
                    return self.respond(400,{'error':'Invalid public proxy request'})
                client=str(ipaddress.ip_address(forwarded[0]))
            if len(self.path)>2048 or not self.path.startswith('/') or self.path.startswith('//') or '\\' in self.path or any(ord(c)<32 for c in self.path):
                return self.respond(400, {'error':'Invalid path'})
            target=urlsplit(self.path)
            if target.scheme or target.netloc or target.fragment:
                return self.respond(400, {'error':'Invalid path'})
            path=unquote(target.path)
            if any(part in ('.','..') for part in path.split('/')) or '\\' in path:
                return self.respond(400, {'error':'Invalid path'})
            native_api=codex_api(path)
            if (native_api or path == '/codex-workspace.js') and (target.path != path or '?' in self.path and not target.query):
                return self.respond(400, {'error':'Canonical workspace path required'})
            if path == '/codex-workspace.js' and target.query:
                return self.respond(404, {'error':'Not found'})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))>1:
                return self.respond(400, {'error':'Invalid framing'})
            mutation=self.command not in ('GET','HEAD')
            if mutation and self.headers.get('Origin') != origin:
                return self.respond(403, {'error':'Same-origin request required'})
            if path=='/signin' and self.command in ('GET','HEAD'):
                return self.respond(200,login_html,'text/html; charset=utf-8')
            if path=='/_gateway/login' and self.command=='POST':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json' or self.headers.get('X-Project-Byte-Gateway')!='1':
                    return self.respond(400,{'error':'Invalid sign-in request'})
                with access.lock:
                    if access.login_clients[client]>=2:
                        return self.respond(429,{'error':'A sign-in request is already in progress'})
                    access.login_clients[client]+=1
                try:
                    self.read_deadline(8)
                    body=json.loads(self.body(2048))
                    if not isinstance(body,dict) or set(body)!={'key'}:
                        return self.respond(400,{'error':'Invalid sign-in request'})
                    result=access.login(body['key'],self.cookie(),client)
                finally:
                    with access.lock:
                        access.login_clients[client]-=1
                        if not access.login_clients[client]:del access.login_clients[client]
                if result=='limited':return self.respond(429,{'error':'Too many sign-in attempts. Try again in a minute.'})
                if not result:return self.respond(401,{'error':'Invalid or inactive access key.'})
                token,ttl=result
                return self.respond(200,{'ok':True},extra=[('Set-Cookie',COOKIE+'='+token+'; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age='+str(ttl))])
            if path=='/_gateway/logout' and self.command=='POST':
                if self.headers.get('X-Project-Byte-Gateway')!='1':return self.respond(403,{'error':'Invalid sign-out request'})
                current=access.check(self.cookie())
                if current and not hmac.compare_digest(self.headers.get('X-Access-Key',''),current[0]['csrf']):
                    return self.deny(changed=True)
                access.logout(self.cookie())
                # Revocation is server-side. A delayed cookie-expiry response could
                # erase a newer sign-in from another tab, so never emit one here.
                return self.respond(200,{'ok':True})
            found=access.check(self.cookie())
            if not found:return self.deny()
            record,identity=found
            if path=='/_gateway/session.js' and self.command in ('GET','HEAD'):
                return self.respond(200,session_js,'text/javascript; charset=utf-8')
            api=path.startswith('/api/') or path=='/healthz' or path=='/_gateway/check'
            keys=self.headers.get_all('X-Access-Key',[])
            key=keys[0] if len(keys)==1 and len(keys[0])<=256 else ''
            if api and (not key or not hmac.compare_digest(key,record['csrf'])):
                return self.deny(changed=True)
            if path=='/_gateway/check' and self.command=='GET':
                return self.respond(200,{'ok':True})
            if api and not api_allowed(self.command,path,target.query):
                return self.respond(404,{'error':'Not found'})
            if native_api and (identity.get('role') != 'owner' or identity.get('level') != 4 or identity.get('subject') != 'owner'):
                return self.respond(403,{'error':'Owner sign-in required for the Codex workspace'})
            if path in ('/api/auth','/api/session'):
                if path=='/api/auth' and self.command!='POST' or path=='/api/session' and self.command!='GET':
                    return self.respond(405,{'error':'Method unavailable'})
                if self.command=='POST':self.body(2048)
                return self.respond(200,identity)
            attachment=path.startswith('/uploads/') and bool(re.fullmatch(r'/uploads/[A-Za-z0-9_.-]{1,180}',path))
            if not (path in ASSETS or api or attachment) or (not api and self.command not in ('GET','HEAD')):
                return self.respond(404,{'error':'Not found'})
            if self.command not in ('GET','HEAD','POST','PATCH','DELETE'):
                return self.respond(405,{'error':'Method unavailable'})
            if self.headers.get('Upgrade'):
                return self.respond(400,{'error':'Upgrade unavailable'})
            payload=self.body(CODEX_MAX_BODY if native_api else MAX_BODY) if mutation else None
            self.deadline.cancel()  # Request is complete; long model replies may proceed.
            headers={'Host':'127.0.0.1:8094','Accept-Encoding':'identity'}
            if api:headers['X-Access-Key']=record['key']
            if mutation:headers['Origin']='http://127.0.0.1:8094'
            for name in ('Content-Type','X-Task-ID','X-Filename'):
                values=self.headers.get_all(name,[])
                if len(values)>1:return self.respond(400,{'error':'Duplicate request header'})
                if values:headers[name]=values[0]
            current=access.check(self.cookie())
            if not current:return self.deny()
            connection=http.client.HTTPConnection(upstream[0],upstream[1],timeout=180,source_address=access.source(record['fingerprint']))
            try:
                connection.request(self.command,self.path,body=payload,headers=headers)
                response=connection.getresponse()
                raw=response.read(MAX_RESPONSE+1)
                if len(raw)>MAX_RESPONSE:return self.respond(502,{'error':'Workspace response is too large'})
                kind=response.getheader('Content-Type','application/octet-stream')
                if '\r' in kind or '\n' in kind:kind='application/octet-stream'
                if path in ('/','/index.html') and response.status==200:
                    if raw.count(b'<head>')!=1:return self.respond(502,{'error':'Workspace layout is unavailable'})
                    bootstrap=("<script>sessionStorage.setItem('project_byte_access',"+json.dumps(record['csrf'])+");sessionStorage.setItem('pb_login_at',"+json.dumps(str(int(record['created']*1000)))+");</script>").encode()
                    raw=raw.replace(b'<head>',b'<head>'+bootstrap+b'<script src="/_gateway/session.js"></script>',1)
                extra=[]
                if attachment:
                    kind='application/octet-stream'
                    extra.append(('Content-Disposition','attachment; filename="'+path.rsplit('/',1)[1]+'"'))
                return self.respond(response.status,raw,kind,extra,attachment)
            finally:
                connection.close()

        def safe_dispatch(self):
            try:self.dispatch()
            except (ValueError,json.JSONDecodeError,UnicodeError):
                try:self.respond(400,{'error':'Invalid request or unavailable sign-in configuration'})
                except OSError:pass
            except (OSError,sqlite3.Error,http.client.HTTPException,TypeError,AttributeError,RecursionError):
                try:self.respond(503,{'error':'Workspace is temporarily unavailable'})
                except OSError:pass
        do_GET=safe_dispatch
        do_HEAD=safe_dispatch
        do_POST=safe_dispatch
        do_PATCH=safe_dispatch
        do_DELETE=safe_dispatch
        do_PUT=safe_dispatch
        do_OPTIONS=safe_dispatch
    return Handler


class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(24)
        super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request);return
        try:super().process_request(request,client_address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,*args):
        try:super().process_request_thread(*args)
        finally:self.slots.release()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--app-root',type=Path,required=True)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--port',type=int,default=8097)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--public-owner-key-file',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'https://[a-z0-9-]+\.[a-z0-9]+\.ts\.net',args.origin):parser.error('A canonical HTTPS Tailscale origin is required')
    server=BoundedServer(('127.0.0.1',args.port),handler_for(Access(args.app_root,state_dir=args.state_dir,public_owner_key_file=args.public_owner_key_file),args.origin,trusted_serve=True))
    print('PROJECT_BYTE authenticated public gateway ready',flush=True)
    server.serve_forever()
