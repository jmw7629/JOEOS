#!/usr/bin/env python3
"""Invite-only control plane. Fixed Unix-socket routing; no provisioning shell."""
from __future__ import annotations
import argparse, collections, hashlib, hmac, http.client, importlib.util, ipaddress, json, os, re, secrets, socket, sqlite3, threading, time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
COOKIE = '__Host-prfkt_enterprise_session'
TTL = 12 * 3600

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def password_hash(value, salt=None):
    if not isinstance(value, str) or not 12 <= len(value) <= 128: raise ValueError('Use a password of 12–128 characters')
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64*1024*1024).hex()
def password_ok(value, encoded):
    try: return hmac.compare_digest(password_hash(value, encoded.split(':')[0]), encoded)
    except (ValueError, TypeError, AttributeError): return False

def name(value, label, maximum=48):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value): raise ValueError('Invalid ' + label)
    return value.strip()

class Store:
    def __init__(self, directory, config, clock=time.time):
        self.directory = Path(directory); self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077: raise ValueError('Private portal directory required')
        self.dbpath = self.directory / 'portal.sqlite3'
        self.slots = {s['id']: s for s in config['slots']}
        if len(self.slots) != len(config['slots']) or not self.slots: raise ValueError('Invalid slots')
        for s in self.slots.values():
            if not re.fullmatch('slot[0-9]{2}', s['id']) or not Path(s['socket']).is_absolute() or len(s['key']) < 32: raise ValueError('Invalid workspace configuration')
        self.clock = clock; self.lock = threading.RLock(); self.attempts = {}; self.login_clients = collections.Counter()
        self.hash_slots = threading.BoundedSemaphore(2)
        self.dummy = password_hash(secrets.token_urlsafe(32))
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, slot TEXT UNIQUE, workspace TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS invites(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL, slot TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS reserved_slot ON invites(slot) WHERE state='pending';
              CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(ts REAL NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL);
            ''')
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=10); db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def initialize_admin(self, password):
        with self.db() as db:
            if db.execute('SELECT 1 FROM users').fetchone(): raise ValueError('Portal already initialized')
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,1)', (secrets.token_hex(16), 'owner', 'Owner', password_hash(password), 'admin', None, 'Administration'))
    def source(self, fingerprint): return None
    def rate(self, client):
        with self.lock:
            now = self.clock()
            self.attempts = {k: [t for t in v if t > now-60] for k,v in self.attempts.items() if v and v[-1] > now-60}
            if len(self.attempts) >= 4096 and client not in self.attempts: return False
            attempts = self.attempts.setdefault(client, [])
            if len(attempts) >= 8: return False
            attempts.append(now); return True
    def _session(self, db, user_id, old):
        now = self.clock(); token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE token_hash=? OR expires<=?', (digest(old), now))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 7)', (user_id,user_id))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (digest(token), user_id, secrets.token_urlsafe(32), now, now+TTL))
        return token
    def login(self, body, old):
        username = body.get('username', '')
        if not isinstance(username,str): return None
        with self.db() as db: row = db.execute('SELECT * FROM users WHERE username=?', (username.lower().strip(),)).fetchone()
        if not self.hash_slots.acquire(False): raise Busy('Sign-in is busy. Try again shortly.')
        try: valid = password_ok(body.get('password'), row['password'] if row else self.dummy)
        finally: self.hash_slots.release()
        if not valid or not row or not row['enabled']: return None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM users WHERE id=? AND enabled=1', (row['id'],)).fetchone()
            if not current or current['password'] != row['password']: return None
            return self._session(db, row['id'], old)
    def check(self, token):
        if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9_-]{43}', token): return None
        with self.db() as db:
            row = db.execute('SELECT s.*,u.id,u.name,u.role,u.slot,u.workspace FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND u.enabled=1', (digest(token),)).fetchone()
        now = self.clock()
        if not row or not row['created'] <= now < row['expires']: return None
        record = dict(row); record['fingerprint'] = row['id']
        record['key'] = self.slots[row['slot']]['key'] if row['slot'] in self.slots else ''
        identity = {'ok':True,'subject':'owner','role':'owner','level':4,'name':row['name']}
        return record, identity
    def logout(self, token):
        with self.db() as db: db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
    def snapshot(self):
        now = self.clock()
        with self.db() as db:
            db.execute("UPDATE invites SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            users = [dict(r) for r in db.execute("SELECT id,username,name,workspace,slot,enabled FROM users WHERE role='member'")]
            invites = [dict(r) for r in db.execute('SELECT id,label,slot,expires,state,created FROM invites ORDER BY created DESC LIMIT 100')]
        used = {u['slot'] for u in users} | {i['slot'] for i in invites if i['state']=='pending'}
        return {'capacity':len(self.slots),'available':len(set(self.slots)-used),'users':users,'invites':invites}
    def invite(self, body):
        label = name(body.get('label'), 'invitation label')
        now = self.clock(); token = secrets.token_urlsafe(32); identifier = secrets.token_hex(16)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE invites SET state='expired' WHERE state='pending' AND expires<=?", (now,))
            used = {r[0] for r in db.execute("SELECT slot FROM users WHERE slot IS NOT NULL UNION SELECT slot FROM invites WHERE state='pending'")}
            free = sorted(set(self.slots)-used)
            if not free: raise ValueError('All workspaces are assigned or reserved. Revoke an unused invitation to free its reservation.')
            db.execute('INSERT INTO invites VALUES(?,?,?,?,?,?,?)', (identifier,digest(token),label,free[0],now+7*86400,'pending',now))
            db.execute('INSERT INTO audit VALUES(?,?,?)',(now,'invite-created',identifier))
        return {'id':identifier,'token':token,'expires':now+7*86400}
    def join(self, body, old):
        token=body.get('invite'); username=body.get('username'); display=name(body.get('name'),'name'); workspace=name(body.get('workspace'),'workspace name')
        if not isinstance(token,str) or not re.fullmatch('[A-Za-z0-9_-]{43}',token): raise ValueError('Invitation is invalid or unavailable')
        if not isinstance(username,str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{2,39}',username): raise ValueError('Username must be 3–40 letters, numbers, dots, dashes or underscores')
        with self.db() as db:
            invite=db.execute("SELECT * FROM invites WHERE token_hash=? AND state='pending' AND expires>?",(digest(token),self.clock())).fetchone()
        if not invite: raise ValueError('Invitation is invalid or unavailable')
        if not self.hash_slots.acquire(False): raise Busy('Sign-up is busy. Try again shortly.')
        try: encoded=password_hash(body.get('password'))
        finally:self.hash_slots.release()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            invite=db.execute("SELECT * FROM invites WHERE token_hash=? AND state='pending' AND expires>?",(digest(token),self.clock())).fetchone()
            if not invite: raise ValueError('Invitation is invalid or unavailable')
            uid=secrets.token_hex(16)
            try:db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,1)',(uid,username.lower(),display,encoded,'member',invite['slot'],workspace))
            except sqlite3.IntegrityError:raise ValueError('That username is unavailable') from None
            db.execute("UPDATE invites SET state='accepted' WHERE id=?",(invite['id'],))
            db.execute('INSERT INTO audit VALUES(?,?,?)',(self.clock(),'workspace-claimed',uid))
            return self._session(db,uid,old)
    def revoke(self, identifier):
        with self.db() as db:
            changed=db.execute("UPDATE invites SET state='revoked' WHERE id=? AND state='pending'",(identifier,)).rowcount
            if not changed:raise ValueError('Invitation is no longer pending')
            db.execute('INSERT INTO audit VALUES(?,?,?)',(self.clock(),'invite-revoked',identifier))
    def enable(self, identifier, enabled):
        if not isinstance(enabled,bool):raise ValueError('Invalid account state')
        with self.db() as db:
            if not db.execute("UPDATE users SET enabled=? WHERE id=? AND role='member'",(int(enabled),identifier)).rowcount:raise ValueError('Account not found')
            if not enabled:db.execute('DELETE FROM sessions WHERE user_id=?',(identifier,))
            db.execute('INSERT INTO audit VALUES(?,?,?)',(self.clock(),'account-enabled' if enabled else 'account-disabled',identifier))
    def password(self, uid, old, new, token):
        with self.db() as db:row=db.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()
        if not self.hash_slots.acquire(False):raise Busy('Password change is busy. Try again shortly.')
        try:
            if not row or not password_ok(old,row[0]):raise ValueError('Current password is incorrect')
            encoded=password_hash(new)
        finally:self.hash_slots.release()
        with self.db() as db:
            if not db.execute('UPDATE users SET password=? WHERE id=? AND password=?',(encoded,uid,row[0])).rowcount:raise ValueError('Account changed. Sign in again.')
            db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?',(uid,digest(token)))

class Busy(Exception):pass
class UnixConnection(http.client.HTTPConnection):
    def __init__(self,path):super().__init__('localhost',timeout=180);self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)

def load_gateway(path):
    spec=importlib.util.spec_from_file_location('enterprise_gateway',Path(path)/'gateway.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.COOKIE=COOKIE
    return module

def handler_for(store, origin, gateway, trusted=True):
    parent=gateway.handler_for(store,origin,trusted_serve=trusted,connection_factory=lambda record:UnixConnection(store.slots[record['slot']]['socket']),authenticate_uploads=True)
    public=(HERE/'portal.html').read_bytes(); parsed=urlsplit(origin)
    class Handler(parent):
        def dispatch(self):
            if self.headers.get_all('Host',[])!=[parsed.netloc]:return self.respond(421,{'error':'Unknown workspace host'})
            client='fixture'
            if trusted:
                forwarded=self.headers.get_all('X-Forwarded-For',[])
                if self.client_address[0]!='127.0.0.1' or len(forwarded)!=1 or self.headers.get_all('X-Forwarded-Proto',[])!=['https']:return self.respond(400,{'error':'Invalid public proxy request'})
                client=str(ipaddress.ip_address(forwarded[0]))
            path=urlsplit(self.path).path
            if self.path!=path and (path.startswith('/_enterprise') or path in ('/signin','/join','/admin','/account')):return self.respond(400,{'error':'Canonical portal path required'})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))>1:return self.respond(400,{'error':'Invalid framing'})
            if self.command not in ('GET','HEAD') and self.headers.get_all('Origin',[])!=[origin]:return self.respond(403,{'error':'Same-origin request required'})
            found=store.check(self.cookie())
            if path in ('/signin','/join','/admin','/account') and self.command in ('GET','HEAD'):
                if path in ('/admin','/account') and not found:return self.respond(303,b'',extra=[('Location','/signin')])
                if path=='/admin' and found[0]['role']!='admin':return self.respond(403,{'error':'Administrator sign-in required'})
                return self.respond(200,public,'text/html; charset=utf-8',extra=[('Referrer-Policy','no-referrer')])
            if path.startswith('/_enterprise/'):
                if self.command=='GET' and path=='/_enterprise/me':
                    if not found:return self.respond(401,{'error':'Sign in to continue'})
                    record=found[0]
                    return self.respond(200,{'name':record['name'],'role':record['role'],'workspace':record['workspace'],'csrf':record['csrf']})
                if self.command=='GET' and path=='/_enterprise/admin' and found and found[0]['role']=='admin':return self.respond(200,store.snapshot())
                if self.command!='POST':return self.respond(404,{'error':'Not found'})
                if self.headers.get_all('Content-Type',[])!=['application/json'] or self.headers.get_all('X-PRFKT-Portal',[])!=['1']:return self.respond(400,{'error':'Invalid portal request'})
                self.read_deadline(8); body=json.loads(self.body(4096))
                if not isinstance(body,dict):raise ValueError('Invalid request')
                if path in ('/_enterprise/login','/_enterprise/join'):
                    if not store.rate(client):return self.respond(429,{'error':'Too many attempts. Try again in a minute.'})
                    token=store.login(body,self.cookie()) if path.endswith('/login') else store.join(body,self.cookie())
                    if not token:return self.respond(401,{'error':'Invalid username or password'})
                    role=store.check(token)[0]['role']
                    return self.respond(200,{'ok':True,'next':'/admin' if role=='admin' else '/#home'},extra=[('Set-Cookie',COOKIE+'='+token+'; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age='+str(TTL))])
                if not found:return self.respond(401,{'error':'Sign in to continue'})
                record=found[0]
                if self.headers.get_all('X-Access-Key',[])!=[record['csrf']]:return self.respond(403,{'error':'Session changed. Reload this page.'})
                if path=='/_enterprise/logout':store.logout(self.cookie());return self.respond(200,{'ok':True})
                if path=='/_enterprise/password':store.password(record['id'],body.get('current'),body.get('password'),self.cookie());return self.respond(200,{'ok':True})
                if record['role']!='admin':return self.respond(403,{'error':'Administrator sign-in required'})
                if path=='/_enterprise/invite':return self.respond(201,store.invite(body))
                if path=='/_enterprise/revoke':store.revoke(body.get('id'));return self.respond(200,{'ok':True})
                if path=='/_enterprise/enable':store.enable(body.get('id'),body.get('enabled'));return self.respond(200,{'ok':True})
                return self.respond(404,{'error':'Not found'})
            # The old shared-key login cannot mint an enterprise session.
            if path=='/_gateway/login':return self.respond(404,{'error':'Use workspace sign-in'})
            if found and found[0]['role']=='admin':return self.respond(303,b'',extra=[('Location','/admin')])
            if found and path=='/api/workspace-profile' and self.command=='GET':
                if self.headers.get_all('X-Access-Key',[])!=[found[0]['csrf']]:return self.deny(changed=True)
                return self.respond(200,{'profile':{'schema_version':1,'display_name':found[0]['workspace'],'assistant_name':'AI_BYTE','owner_shortcuts':[]}})
            # No shared external review feeds or legacy bridge dispatch in hosted installs.
            if path=='/api/external-reviews' and found:return self.respond(200,{'reviews':[]})
            return super().dispatch()
        def safe_dispatch(self):
            try:self.dispatch()
            except Busy as error:self.respond(429,{'error':str(error)})
            except (ValueError,json.JSONDecodeError,UnicodeError) as error:self.respond(400,{'error':str(error) if isinstance(error,ValueError) and len(str(error))<200 else 'Invalid request'})
            except (OSError,sqlite3.Error,http.client.HTTPException,TypeError,KeyError,AttributeError):
                try:self.respond(503,{'error':'Workspace is temporarily unavailable'})
                except OSError:pass
        do_GET=safe_dispatch;do_HEAD=safe_dispatch;do_POST=safe_dispatch;do_PATCH=safe_dispatch;do_DELETE=safe_dispatch;do_PUT=safe_dispatch;do_OPTIONS=safe_dispatch
    return Handler

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--initialize',type=Path);args=parser.parse_args()
    os.umask(0o077)
    config=json.loads(args.config.read_text());store=Store(config['state'],config)
    if args.initialize:store.initialize_admin(args.initialize.read_text().strip())
    else:
        gateway=load_gateway(config['gateway']);server=gateway.BoundedServer(('127.0.0.1',config['port']),handler_for(store,config['origin'],gateway))
        print('PRFKT enterprise invitation service ready',flush=True);server.serve_forever()
