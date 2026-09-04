#!/usr/bin/env python3
import hmac, json, mimetypes, os, sqlite3, threading, time, uuid
from datetime import date, datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import unquote, urlparse

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(ROOT,'kanban.db')
LOCK=threading.RLock()
SECRET_FILE=os.path.join(ROOT,'admin.secret')
UPLOAD_DIR=os.path.join(ROOT,'uploads')
STATUSES=['Backlog','Active','Blocked','Review','Done']
PRIORITIES=['Critical','High','Medium','Low']
HEALTH=['On track','At risk','Blocked','Paused']
MAX_UPLOAD=10*1024*1024
ALLOWED_UPLOAD_EXT={'.pdf','.txt','.md','.csv','.png','.jpg','.jpeg','.webp','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.zip'}
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

def now(): return int(time.time())
def con():
    c=sqlite3.connect(DB, timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('pragma foreign_keys=on')
    return c

def admin_secret():
    if not os.path.exists(SECRET_FILE):
        secret=uuid.uuid4().hex+uuid.uuid4().hex[:8]
        fd=os.open(SECRET_FILE,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f: f.write(secret)
    with open(SECRET_FILE,encoding='utf-8') as f: return f.read().strip()

def add_col(c,table,name,definition):
    cols={r['name'] for r in c.execute(f'pragma table_info({table})')}
    if name not in cols: c.execute(f'alter table {table} add column {name} {definition}')

def init_db():
    os.makedirs(UPLOAD_DIR,exist_ok=True)
    with con() as c:
        c.execute('''create table if not exists tasks (
            id text primary key,title text not null,project text not null,status text not null,
            priority text not null,note text not null default '',created_at integer not null,updated_at integer not null)''')
        for n,d in [('due_date',"text not null default ''"),('progress','integer not null default 0'),('milestone',"text not null default ''"),('depends_on',"text not null default ''"),('url',"text not null default ''"),('owner',"text not null default ''")]: add_col(c,'tasks',n,d)
        c.execute('''create table if not exists projects (
            name text primary key, health text not null default 'On track', goal text not null default '',
            target_date text not null default '', notes text not null default '', updated_at integer not null)''')
        c.execute('''create table if not exists activity (
            id integer primary key autoincrement,ts integer not null,action text not null,
            task_id text not null default '',project text not null default '',detail text not null default '')''')
        c.execute('''create table if not exists attachments (
            id text primary key,task_id text not null,original_name text not null,stored_name text not null,
            content_type text not null,size integer not null,created_at integer not null,
            foreign key(task_id) references tasks(id) on delete cascade)''')
        if c.execute('select count(*) from tasks').fetchone()[0]==0:
            ts=now()
            c.executemany('''insert into tasks(id,title,project,status,priority,note,created_at,updated_at,due_date,progress,milestone,depends_on,url,owner)
                           values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',[(str(uuid.uuid4()),*x,ts,ts,'',0,'','','','') for x in SEED])
        names=[r[0] for r in c.execute('select distinct project from tasks where project<>""')]
        ts=now(); c.executemany('insert or ignore into projects(name,updated_at) values(?,?)',[(p,ts) for p in names])

def read_json(h):
    try:
        n=int(h.headers.get('Content-Length','0'))
        if n<=0 or n>1024*1024: return None
        return json.loads(h.rfile.read(n))
    except Exception: return None

def safe_text(v,limit): return str(v or '').strip()[:limit]
def valid_date(v):
    v=safe_text(v,10)
    if not v:return ''
    try: datetime.strptime(v,'%Y-%m-%d'); return v
    except ValueError: raise ValueError('invalid date')
def valid_url(v):
    v=safe_text(v,500)
    if not v:return ''
    u=urlparse(v)
    if u.scheme not in ('http','https') or not u.netloc: raise ValueError('invalid url')
    return v

def log(c,action,task_id='',project='',detail=''):
    c.execute('insert into activity(ts,action,task_id,project,detail) values(?,?,?,?,?)',(now(),action,task_id,project,safe_text(detail,500)))

def task_rows():
    with con() as c:
        tasks=[dict(r) for r in c.execute('select * from tasks order by created_at,id')]
        at={}
        for r in c.execute('select * from attachments order by created_at'):
            d=dict(r); d['url']='/uploads/'+d['stored_name']; at.setdefault(d['task_id'],[]).append(d)
        for t in tasks:t['attachments']=at.get(t['id'],[])
        return tasks

def project_rows():
    with con() as c:return [dict(r) for r in c.execute('select * from projects order by name')]
def activity_rows(limit=100):
    limit=max(1,min(int(limit),300))
    with con() as c:return [dict(r) for r in c.execute('select * from activity order by id desc limit ?',(limit,))]

def score_task(t,project_health):
    if t['status']=='Done': return -999
    p={'Critical':50,'High':35,'Medium':20,'Low':10}.get(t['priority'],0)
    s={'Blocked':20,'Active':15,'Review':10,'Backlog':5}.get(t['status'],0)
    health={'Blocked':20,'At risk':10,'Paused':-10,'On track':0}.get(project_health.get(t['project'],'On track'),0)
    due=0
    if t.get('due_date'):
        try:
            days=(datetime.strptime(t['due_date'],'%Y-%m-%d').date()-date.today()).days
            due=35 if days<0 else 30 if days<=2 else 20 if days<=7 else 10 if days<=14 else 0
        except ValueError: pass
    dep=8 if t.get('depends_on') else 0
    progress=max(0,min(int(t.get('progress') or 0),100)); finish=6 if progress>=75 else 3 if progress>=50 else 0
    return p+s+health+due+dep+finish

def intelligence():
    tasks=task_rows();projs=project_rows();ph={p['name']:p['health'] for p in projs};by_id={t['id']:t for t in tasks};ranked=[]
    for t in tasks:
        item=dict(t);item['score']=score_task(t,ph);dep=t.get('depends_on','');item['dependency_title']=by_id.get(dep,{}).get('title','') if dep else '';ranked.append(item)
    ranked=[x for x in ranked if x['score']>-999];ranked.sort(key=lambda x:(-x['score'],x['due_date'] or '9999-12-31',x['title']))
    blocked=sum(1 for t in tasks if t['status']=='Blocked');critical=sum(1 for t in tasks if t['priority']=='Critical' and t['status']!='Done');overdue=sum(1 for t in tasks if t['status']!='Done' and t.get('due_date') and t['due_date']<date.today().isoformat())
    return {'top':ranked[:8],'signals':{'blocked':blocked,'critical_open':critical,'overdue':overdue},'method':'Deterministic priority score: priority + workflow state + project health + due-date urgency + dependency + finishability.'}

class H(SimpleHTTPRequestHandler):
    server_version='PROJECT_BYTE/2.0'
    def __init__(self,*a,**kw): super().__init__(*a,directory=ROOT,**kw)
    def log_message(self,fmt,*args): print('%s - %s'%(self.address_string(),fmt%args))
    def sendj(self,obj,code=200):
        b=json.dumps(obj,separators=(',',':')).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def authorized(self):
        supplied=self.headers.get('X-Admin-Key','')
        try:return bool(supplied) and hmac.compare_digest(supplied,admin_secret())
        except Exception:return False
    def must_auth(self):
        if self.authorized():return True
        self.sendj({'error':'owner key required'},401);return False
    def do_GET(self):
        p=unquote(urlparse(self.path).path)
        if p=='/healthz':return self.sendj({'ok':True,'version':2})
        if p=='/api/tasks':return self.sendj({'tasks':task_rows()})
        if p=='/api/projects':return self.sendj({'projects':project_rows()})
        if p=='/api/activity':return self.sendj({'activity':activity_rows(120)})
        if p=='/api/intelligence':return self.sendj(intelligence())
        if p=='/':self.path='/index.html'
        return super().do_GET()
    def do_POST(self):
        p=urlparse(self.path).path
        if p=='/api/auth':return self.sendj({'ok':self.authorized()},200 if self.authorized() else 401)
        if p=='/api/tasks':
            if not self.must_auth():return
            d=read_json(self)
            if not isinstance(d,dict):return self.sendj({'error':'invalid json'},400)
            try:
                title=safe_text(d.get('title'),120);project=safe_text(d.get('project'),80);status=d.get('status','Backlog');priority=d.get('priority','Medium');note=safe_text(d.get('note'),500);due=valid_date(d.get('due_date',''));progress=max(0,min(int(d.get('progress') or 0),100));milestone=safe_text(d.get('milestone'),120);dep=safe_text(d.get('depends_on'),80);url=valid_url(d.get('url',''));owner=safe_text(d.get('owner'),80)
            except (ValueError,TypeError):return self.sendj({'error':'invalid fields'},400)
            if not title or not project or status not in STATUSES or priority not in PRIORITIES:return self.sendj({'error':'invalid fields'},400)
            tid=str(uuid.uuid4());ts=now()
            with LOCK,con() as c:
                c.execute('insert or ignore into projects(name,updated_at) values(?,?)',(project,ts));c.execute('''insert into tasks(id,title,project,status,priority,note,created_at,updated_at,due_date,progress,milestone,depends_on,url,owner) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(tid,title,project,status,priority,note,ts,ts,due,progress,milestone,dep,url,owner));log(c,'task_created',tid,project,title)
            return self.sendj({'ok':True,'id':tid},201)
        if p=='/api/projects':
            if not self.must_auth():return
            d=read_json(self)
            if not isinstance(d,dict):return self.sendj({'error':'invalid json'},400)
            name=safe_text(d.get('name'),80)
            if not name:return self.sendj({'error':'project name required'},400)
            with LOCK,con() as c:c.execute('insert or ignore into projects(name,updated_at) values(?,?)',(name,now()));log(c,'project_created','',name,name)
            return self.sendj({'ok':True},201)
        if p=='/api/upload':
            if not self.must_auth():return
            task_id=safe_text(self.headers.get('X-Task-ID'),80);filename=os.path.basename(safe_text(self.headers.get('X-Filename'),180))
            try:n=int(self.headers.get('Content-Length','0'))
            except ValueError:n=0
            ext=os.path.splitext(filename)[1].lower()
            if not task_id or not filename or n<=0 or n>MAX_UPLOAD or ext not in ALLOWED_UPLOAD_EXT:return self.sendj({'error':'invalid upload'},400)
            with con() as c:
                task=c.execute('select project,title from tasks where id=?',(task_id,)).fetchone()
                if not task:return self.sendj({'error':'task not found'},404)
            data=self.rfile.read(n);aid=str(uuid.uuid4());stored=aid+ext;dest=os.path.join(UPLOAD_DIR,stored)
            with open(dest,'wb') as f:f.write(data)
            ctype=self.headers.get('Content-Type') or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            with LOCK,con() as c:c.execute('insert into attachments values(?,?,?,?,?,?,?)',(aid,task_id,filename,stored,ctype,n,now()));log(c,'file_attached',task_id,task['project'],filename)
            return self.sendj({'ok':True,'id':aid,'url':'/uploads/'+stored},201)
        return self.sendj({'error':'not found'},404)
    def do_PATCH(self):
        p=urlparse(self.path).path
        if not self.must_auth():return
        if p.startswith('/api/tasks/'):
            tid=p.split('/')[-1];d=read_json(self)
            if not isinstance(d,dict):return self.sendj({'error':'invalid json'},400)
            allowed={}
            try:
                for k,lim in [('title',120),('project',80),('note',500),('milestone',120),('depends_on',80),('owner',80)]:
                    if k in d:allowed[k]=safe_text(d[k],lim)
                if 'url' in d:allowed['url']=valid_url(d['url'])
                if 'due_date' in d:allowed['due_date']=valid_date(d['due_date'])
                if 'progress' in d:allowed['progress']=max(0,min(int(d['progress'] or 0),100))
                if 'status' in d:
                    if d['status'] not in STATUSES:raise ValueError()
                    allowed['status']=d['status']
                if 'priority' in d:
                    if d['priority'] not in PRIORITIES:raise ValueError()
                    allowed['priority']=d['priority']
            except (ValueError,TypeError):return self.sendj({'error':'invalid fields'},400)
            if not allowed:return self.sendj({'error':'nothing to update'},400)
            allowed['updated_at']=now()
            with LOCK,con() as c:
                old=c.execute('select * from tasks where id=?',(tid,)).fetchone()
                if not old:return self.sendj({'error':'not found'},404)
                if 'project' in allowed and allowed['project']:c.execute('insert or ignore into projects(name,updated_at) values(?,?)',(allowed['project'],now()))
                c.execute('update tasks set '+','.join(f'{k}=?' for k in allowed)+' where id=?',list(allowed.values())+[tid]);log(c,'task_updated',tid,allowed.get('project',old['project']),', '.join(k for k in allowed if k!='updated_at'))
            return self.sendj({'ok':True})
        if p.startswith('/api/projects/'):
            name=unquote(p[len('/api/projects/'):]);d=read_json(self)
            if not isinstance(d,dict):return self.sendj({'error':'invalid json'},400)
            fields={}
            try:
                if 'health' in d:
                    if d['health'] not in HEALTH:raise ValueError()
                    fields['health']=d['health']
                if 'goal' in d:fields['goal']=safe_text(d['goal'],500)
                if 'target_date' in d:fields['target_date']=valid_date(d['target_date'])
                if 'notes' in d:fields['notes']=safe_text(d['notes'],1200)
            except ValueError:return self.sendj({'error':'invalid fields'},400)
            if not fields:return self.sendj({'error':'nothing to update'},400)
            fields['updated_at']=now()
            with LOCK,con() as c:
                cur=c.execute('update projects set '+','.join(f'{k}=?' for k in fields)+' where name=?',list(fields.values())+[name])
                if cur.rowcount==0:return self.sendj({'error':'project not found'},404)
                log(c,'project_updated','',name,', '.join(k for k in fields if k!='updated_at'))
            return self.sendj({'ok':True})
        return self.sendj({'error':'not found'},404)
    def do_DELETE(self):
        p=urlparse(self.path).path
        if not self.must_auth():return
        if p.startswith('/api/tasks/'):
            tid=p.split('/')[-1]
            with LOCK,con() as c:
                row=c.execute('select project,title from tasks where id=?',(tid,)).fetchone()
                if not row:return self.sendj({'error':'not found'},404)
                files=[r['stored_name'] for r in c.execute('select stored_name from attachments where task_id=?',(tid,))];c.execute('delete from attachments where task_id=?',(tid,));c.execute('delete from tasks where id=?',(tid,));log(c,'task_deleted',tid,row['project'],row['title'])
            for f in files:
                try:os.remove(os.path.join(UPLOAD_DIR,f))
                except FileNotFoundError:pass
            return self.sendj({'ok':True})
        if p.startswith('/api/attachments/'):
            aid=p.split('/')[-1]
            with LOCK,con() as c:
                row=c.execute('select a.*,t.project from attachments a join tasks t on t.id=a.task_id where a.id=?',(aid,)).fetchone()
                if not row:return self.sendj({'error':'not found'},404)
                c.execute('delete from attachments where id=?',(aid,));log(c,'file_removed',row['task_id'],row['project'],row['original_name'])
            try:os.remove(os.path.join(UPLOAD_DIR,row['stored_name']))
            except FileNotFoundError:pass
            return self.sendj({'ok':True})
        return self.sendj({'error':'not found'},404)

if __name__=='__main__':
    init_db();admin_secret();host=os.getenv('KANBAN_HOST','127.0.0.1');port=int(os.getenv('KANBAN_PORT','8094'));print(f'PROJECT_BYTE v2 listening on http://{host}:{port}');ThreadingHTTPServer((host,port),H).serve_forever()
