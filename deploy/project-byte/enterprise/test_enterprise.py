"""Isolation and real HTTP flows. Every instance and provider is disposable."""
import concurrent.futures, hashlib, http.client, importlib.util, json, os, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));import portal
spec=importlib.util.spec_from_file_location('private_prepare',HERE.parent/'private-install/prepare.py');prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)

class Enterprise(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='prfkt-ent-',dir='/tmp');self.root=Path(self.tmp.name).resolve();os.chmod(self.root,0o700);self.processes=[];self.servers=[]
        slots=[]
        for n in range(2):
            target=self.root/f'workspace{n}';prepare.prepare(target,hosted=True);sock=str(self.root/f'app{n}.sock')
            launch='''import sys,importlib.util,time
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'app'))
import ai_connections
original=ai_connections.Manager.__init__
def init(self,*a,**kw):
 original(self,*a,**kw)
 def transport(endpoint,path,provider,headers,payload=None,**kwargs):
  assert endpoint=='https://api.openai.com/v1'
  if payload is None:return {'data':[{'id':'fixture-model'}]}
  time.sleep(.2)
  return {'output_text':'Fixture response: '+payload['input'][-1]['content'].split('\\n\\nWorkspace reference context')[0]}
 self.transport=transport
ai_connections.Manager.__init__=init
spec=importlib.util.spec_from_file_location('private_runtime',root/'runtime.py');runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime);runtime.serve(0,sys.argv[2])
'''
            log=open(self.root/f'process{n}.log','w');self.addCleanup(log.close)
            p=subprocess.Popen([sys.executable,'-B','-c',launch,str(target),sock],stdout=log,stderr=log);self.processes.append(p)
            slots.append({'id':f'slot{n+1:02}','key':(target/'private/owner.key').read_text(),'socket':sock})
            for _ in range(100):
                if Path(sock).exists():break
                if p.poll() is not None:raise RuntimeError((self.root/f'process{n}.log').read_text())
                time.sleep(.05)
            else:raise RuntimeError('Runtime did not listen')
        self.store=portal.Store(self.root/'portal',{'slots':slots});self.store.initialize_admin('correct-admin-password')
        self.gateway=portal.load_gateway(HERE.parent/'public-access')
        server=self.gateway.BoundedServer(('127.0.0.1',0),self.gateway.BaseHTTPRequestHandler)
        self.port=server.server_port;self.origin=f'http://127.0.0.1:{self.port}';server.RequestHandlerClass=portal.handler_for(self.store,self.origin,self.gateway,trusted=False)
        threading.Thread(target=server.serve_forever,daemon=True).start();self.servers.append(server)
    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        for p in self.processes:p.terminate();p.wait(timeout=8)
        self.tmp.cleanup()
    def request(self,path,body=None,token='',csrf='',method=None,extra=None):
        method=method or ('POST' if body is not None else 'GET');conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'Host':self.origin.split('//')[1]}
        if token:headers['Cookie']=portal.COOKIE+'='+token
        if csrf:headers['X-Access-Key']=csrf
        if body is not None:headers.update({'Origin':self.origin,'Content-Type':'application/json','X-PRFKT-Portal':'1'})
        headers.update(extra or {});conn.request(method,path,json.dumps(body) if body is not None else None,headers);r=conn.getresponse();raw=r.read();status=r.status;head=dict(r.getheaders());conn.close()
        try:data=json.loads(raw)
        except ValueError:data=raw.decode()
        return status,data,head
    def auth(self,username,password):
        status,data,headers=self.request('/_enterprise/login',{'username':username,'password':password});self.assertEqual(status,200,data)
        token=headers['Set-Cookie'].split(';')[0].split('=',1)[1];csrf=self.store.check(token)[0]['csrf'];return token,csrf
    def test_invites_isolation_chat_and_revocation(self):
        self.assertEqual(self.request('/api/tasks')[0],401)
        self.assertEqual(self.request('/_gateway/login',{'key':'correct-admin-password'})[0],404)
        admin,ac=self.auth('owner','correct-admin-password')
        self.assertEqual(self.request('/api/tasks',token=admin,csrf=ac)[0],303)
        self.assertEqual(self.request('/_enterprise/invite',{'label':'bad'},admin,'wrong')[0],403)
        members=[]
        for n in range(2):
            status,inv,_=self.request('/_enterprise/invite',{'label':f'Invite {n}'},admin,ac);self.assertEqual(status,201,inv)
            body={'invite':inv['token'],'username':f'member{n}','password':'correct-member-password','name':f'Member {n}','workspace':f'Workspace {n}'}
            status,data,_=self.request('/_enterprise/join',body);self.assertEqual(status,200,data)
            self.assertEqual(self.request('/_enterprise/join',body)[0],400)
            token,csrf=self.auth(f'member{n}','correct-member-password');members.append((token,csrf))
            self.assertEqual(self.request('/_enterprise/admin',token=token)[0],404)
            self.assertEqual(self.request('/api/tasks',token=token,csrf=csrf)[1]['tasks'],[])
            self.assertEqual(self.request('/api/projects',token=token,csrf=csrf)[1]['projects'],[])
            self.assertEqual(self.request('/api/workspace-profile',token=token,csrf=csrf)[1]['profile']['display_name'],f'Workspace {n}')
        token,csrf=members[0];t2,c2=members[1]
        self.assertEqual(self.request('/api/tasks',token=token,csrf=c2)[0],401)
        task={'title':'Workspace one private task','project':'Project One','status':'Backlog','owner':'Member 0','priority':'High'}
        status,data,_=self.request('/api/tasks',task,token,csrf);self.assertEqual(status,201,data)
        self.assertEqual(len(self.request('/api/tasks',token=token,csrf=csrf)[1]['tasks']),1)
        self.assertEqual(self.request('/api/tasks',token=t2,csrf=c2)[1]['tasks'],[])
        native=self.request('/api/codex-workspace',token=token,csrf=csrf)[1];self.assertFalse(native['connected']);self.assertEqual(native['runtime_kind'],'provider-chat')
        connection={'provider':'openai-responses','endpoint':'https://api.openai.com/v1','model_name':'fixture-model','display_name':'Fixture provider','api_key':'disposable-fake-key','auth_mode':'api_key','set_default':True}
        status,data,_=self.request('/api/ai-connections/connect',connection,token,csrf);self.assertEqual(status,200,data)
        native=self.request('/api/codex-workspace',token=token,csrf=csrf)[1];self.assertTrue(native['connected']);self.assertFalse(native['execution_permissions']['capable'])
        self.assertFalse(self.request('/api/codex-workspace',token=t2,csrf=c2)[1]['connected'])
        payload={'request_id':'a'*32,'project_key':'general','conversation_id':None,'message':'Test durable history'}
        status,run,_=self.request('/api/codex-workspace/message',payload,token,csrf);self.assertEqual(status,202,run)
        self.assertEqual(self.request('/api/codex-workspace/message',payload,token,csrf)[1],run)
        self.assertEqual(self.request('/api/codex-workspace/message',{**payload,'message':'different'},token,csrf)[0],409)
        self.assertEqual(self.request('/api/codex-workspace/conversations/'+run['conversation_id'],token=t2,csrf=c2)[0],404)
        for _ in range(50):
            events=self.request('/api/codex-workspace/runs/'+run['run_id']+'/events',token=token,csrf=csrf)[1]
            if events['run']['status']=='completed':break
            time.sleep(.05)
        self.assertEqual(events['run']['status'],'completed',events)
        history=self.request('/api/codex-workspace/conversations/'+run['conversation_id'],token=token,csrf=csrf)[1]
        self.assertEqual(len(history['messages']),2);self.assertEqual(history['messages'][-1]['text'],'Fixture response: Test durable history')
        self.assertEqual(self.request('/api/codex-workspace/conversations?project=general',token=token,csrf=csrf)[1]['conversations'][0]['id'],run['conversation_id'])
        self.assertEqual(self.request('/api/codex-workspace/conversations',token=t2,csrf=c2)[1]['conversations'],[])
        account=self.store.snapshot()['users'][0]
        self.assertEqual(self.request('/_enterprise/enable',{'id':account['id'],'enabled':False},admin,ac)[0],200)
        self.assertEqual(self.request('/api/tasks',token=token,csrf=csrf)[0],401)
        self.assertEqual(self.request('/api/tasks',token=t2,csrf=c2)[0],200)
    def test_expiry_race_password_and_ssrf(self):
        inv=self.store.invite({'label':'Race'})
        body={'invite':inv['token'],'username':'racer','password':'correct-member-password','name':'Racer','workspace':'Race'}
        def join(_):
            try:return self.store.join(body,'')
            except ValueError:return None
        with concurrent.futures.ThreadPoolExecutor(2) as executor:results=list(executor.map(join,range(2)))
        self.assertEqual(sum(bool(r) for r in results),1)
        token=next(r for r in results if r);record=self.store.check(token)[0]
        token2=self.store.login({'username':'racer','password':'correct-member-password'},'')
        self.store.password(record['id'],'correct-member-password','a-different-new-password',token)
        self.assertIsNone(self.store.check(token2));self.assertIsNotNone(self.store.check(token))
        inv=self.store.invite({'label':'Expire'});self.store.clock=lambda:time.time()+8*86400
        with self.assertRaises(ValueError):self.store.join({**body,'invite':inv['token'],'username':'expired'},'')
        self.assertIsNone(self.store.check(token))
        self.store.clock=time.time
        # Public HTTPS only even for providers that normally allow local servers.
        for endpoint in ['http://127.0.0.1:8094','https://127.0.0.1','https://100.99.71.65','https://64.202.186.3']:
            result=self.request('/api/ai-connections/connect',{'provider':'openai-compatible','endpoint':endpoint,'model_name':'test','api_key':'fake-test-key','auth_mode':'api_key'},token,record['csrf'])
            self.assertEqual(result[0],400,result)

if __name__=='__main__':unittest.main()
