#!/usr/bin/env python3
import json, os, sqlite3, threading, time, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(ROOT,'kanban.db')
LOCK=threading.Lock()
SECRET_FILE=os.path.join(ROOT,'admin.secret')

SEED=[
('Make every Studio tool functional','StickDeath Infinity','Active','Critical','Complete timeline, drawing, animation, upload, export, and all visible controls.'),
('Finish Spatter AI production workflow','StickDeath Infinity','Active','Critical','AI must understand the app, assist creation, and build/export video workflows.'),
('Stabilize Studio layout and interactions','StickDeath Infinity','Review','High','Preserve the approved studio structure while fixing non-functional controls.'),
('Complete REM workbook ingestion','DASH_BYTE / VITROS','Active','Critical','Parse authoritative workbook content independent of filename and update tracker records with audit history.'),
('Complete DHR consumption integration','DASH_BYTE / VITROS','Active','Critical','Consume parts from DHR data and reflect updates in stock summary and archive.'),
('Repair incoming stock intake AI workflow','DASH_BYTE / VITROS','Backlog','High','Ensure incoming-stock image workflow is separated from DHR scanning logic.'),
('Enterprise dashboard hardening','DASH_BYTE / VITROS','Backlog','High','Fix synchronized table headers, tracker completeness, auditability, and enterprise controls.'),
('Finish dynamic G2 dashboard','BRAIN_BYTE','Active','Critical','Contextual dashboard that changes with the current task instead of copying Iris.'),
('R1 ring navigation across app','BRAIN_BYTE','Backlog','High','Use ring input to navigate primary app workflows and contextual G2 views.'),
('Background execution behavior','BRAIN_BYTE','Review','High','Support continued operation where platform permits and require explicit close intent.'),
('Unify local AI orchestration framework','JoeOS','Active','Critical','Create a stable personal OS layer for assistants, coding agents, routing, and local compute.'),
('Harden remote app-building workflow','JoeOS','Backlog','High','Reliable iPhone-to-VPS-to-Mac development path with OpenAI and local models.'),
('Resolve Go AI pass and capture rules','GO_BYTE','Blocked','Critical','AI must pass legally, never permit user to act for AI, and enforce Go capture/suicide rules correctly.'),
('Fix 19x19 board selection','GO_BYTE','Backlog','High','Selected board size must always match actual rendered and played board.'),
('Repair transient controller behavior','RETRO_BYTE','Blocked','High','Controller must remain mounted and all prototype controls must remain functional.'),
('Harden Even Hub connection layer','PRFKT_BYTE','Review','High','Keep provider routing, setup, security, agents, and G2 connection workflows reliable.'),
('Prepare Nintendo x Even execution package','Nintendo × Even G2','Active','High','Maintain partnership strategy, testing framework, stakeholder plan, and technical POC readiness.'),
('Define mirrored G2 + audio architecture','Nintendo × Even G2','Backlog','High','Specify display mirroring, R1 controls, Bluetooth audio, performance constraints, and acceptance gates.')]

STATUSES=['Backlog','Active','Blocked','Review','Done']
PRIORITIES=['Critical','High','Medium','Low']

def con():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def admin_secret():
    if not os.path.exists(SECRET_FILE):
        secret=uuid.uuid4().hex+uuid.uuid4().hex[:8]
        fd=os.open(SECRET_FILE,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f: f.write(secret)
    with open(SECRET_FILE) as f: return f.read().strip()

def init_db():
    with con() as c:
        c.execute('''create table if not exists tasks (
            id text primary key,title text not null,project text not null,status text not null,
            priority text not null,note text not null default '',created_at integer not null,updated_at integer not null)''')
        n=c.execute('select count(*) from tasks').fetchone()[0]
        if n==0:
            now=int(time.time())
            c.executemany('insert into tasks values (?,?,?,?,?,?,?,?)',[(str(uuid.uuid4()),*x,now,now) for x in SEED])

def rows():
    with con() as c:
        return [dict(r) for r in c.execute('select * from tasks order by created_at,id')]

def read_json(h):
    try:
        n=int(h.headers.get('Content-Length','0'))
        if n<=0 or n>65536: return None
        return json.loads(h.rfile.read(n))
    except Exception: return None

class H(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=ROOT,**kw)
    def log_message(self, fmt, *args): print('%s - %s'%(self.address_string(),fmt%args))
    def sendj(self,obj,code=200):
        b=json.dumps(obj,separators=(',',':')).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=urlparse(self.path).path
        if p=='/healthz': return self.sendj({'ok':True})
        if p=='/api/tasks': return self.sendj({'tasks':rows()})
        if p=='/': self.path='/index.html'
        return super().do_GET()
    def authorized(self):
        supplied=self.headers.get('X-Admin-Key','')
        try: return bool(supplied) and __import__('hmac').compare_digest(supplied,admin_secret())
        except Exception: return False
    def do_POST(self):
        path=urlparse(self.path).path
        if path=='/api/auth':
            return self.sendj({'ok':self.authorized()},200 if self.authorized() else 401)
        if path!='/api/tasks': return self.sendj({'error':'not found'},404)
        if not self.authorized(): return self.sendj({'error':'owner key required'},401)
        d=read_json(self)
        if not isinstance(d,dict): return self.sendj({'error':'invalid json'},400)
        title=str(d.get('title','')).strip()[:120]; project=str(d.get('project','')).strip()[:80]
        status=d.get('status','Backlog'); priority=d.get('priority','Medium'); note=str(d.get('note','')).strip()[:500]
        if not title or not project or status not in STATUSES or priority not in PRIORITIES: return self.sendj({'error':'invalid fields'},400)
        tid=str(uuid.uuid4()); now=int(time.time())
        with LOCK, con() as c: c.execute('insert into tasks values (?,?,?,?,?,?,?,?)',(tid,title,project,status,priority,note,now,now))
        return self.sendj({'ok':True,'id':tid},201)
    def do_PATCH(self):
        p=urlparse(self.path).path
        if not p.startswith('/api/tasks/'): return self.sendj({'error':'not found'},404)
        if not self.authorized(): return self.sendj({'error':'owner key required'},401)
        tid=p.split('/')[-1]; d=read_json(self)
        if not isinstance(d,dict): return self.sendj({'error':'invalid json'},400)
        allowed={}
        for k,lim in [('title',120),('project',80),('note',500)]:
            if k in d: allowed[k]=str(d[k]).strip()[:lim]
        if 'status' in d:
            if d['status'] not in STATUSES:return self.sendj({'error':'invalid status'},400)
            allowed['status']=d['status']
        if 'priority' in d:
            if d['priority'] not in PRIORITIES:return self.sendj({'error':'invalid priority'},400)
            allowed['priority']=d['priority']
        if not allowed:return self.sendj({'error':'nothing to update'},400)
        allowed['updated_at']=int(time.time()); vals=list(allowed.values())+[tid]
        sql='update tasks set '+','.join(f'{k}=?' for k in allowed)+' where id=?'
        with LOCK, con() as c:
            cur=c.execute(sql,vals)
            if cur.rowcount==0:return self.sendj({'error':'not found'},404)
        return self.sendj({'ok':True})
    def do_DELETE(self):
        p=urlparse(self.path).path
        if not p.startswith('/api/tasks/'): return self.sendj({'error':'not found'},404)
        if not self.authorized(): return self.sendj({'error':'owner key required'},401)
        tid=p.split('/')[-1]
        with LOCK, con() as c:
            cur=c.execute('delete from tasks where id=?',(tid,))
            if cur.rowcount==0:return self.sendj({'error':'not found'},404)
        return self.sendj({'ok':True})

if __name__=='__main__':
    init_db(); admin_secret()
    host=os.getenv('KANBAN_HOST','127.0.0.1'); port=int(os.getenv('KANBAN_PORT','8094'))
    print(f'PROJECT_BYTE listening on http://{host}:{port}')
    ThreadingHTTPServer((host,port),H).serve_forever()
