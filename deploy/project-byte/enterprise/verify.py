#!/usr/bin/env python3
"""Verify new workspace isolation before optionally publishing its own port."""
import argparse, http.client, json, os, socket, subprocess, time
from pathlib import Path
from urllib.parse import urlsplit

BASE=Path('/var/lib/prfkt-enterprise')
ORIGIN='https://mcso9tqzb9-1.tailb9395f.ts.net:10000'
PERSONAL=('project-byte.service','project-byte-public-gateway.service','prfkt-codex-sandbox.service')

def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=30).stdout

def states(units):
    result={}
    for unit in units:
        result[unit]=dict(line.split('=',1) for line in run('systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','ControlGroup','-p','User').strip().splitlines())
    return result

def call(method,path,body=None,cookie='',csrf=''):
    connection=http.client.HTTPConnection('127.0.0.1',8100,timeout=10)
    headers={'Host':urlsplit(ORIGIN).netloc,'X-Forwarded-For':'203.0.113.24','X-Forwarded-Proto':'https','Origin':ORIGIN,'Content-Type':'application/json','X-PRFKT-Portal':'1'}
    if cookie:headers['Cookie']=cookie
    if csrf:headers['X-Access-Key']=csrf
    connection.request(method,path,None if body is None else json.dumps(body),headers);response=connection.getresponse();raw=response.read();status=response.status;headers=dict(response.getheaders());connection.close()
    try:body=json.loads(raw)
    except ValueError:body=raw.decode()
    return status,body,headers

def isolation(unit,state):
    import pwd
    account=pwd.getpwnam(state['User']);pid=int(state['MainPID']);group=state['ControlGroup']
    assert group=='/system.slice/'+unit and pid>1
    probe=r'''
import os,socket,json,http.client
missing=['/home/joevps','/run/prfkt-codex-sandbox.sock','/var/lib/prfkt-enterprise/workspaces','/sockets/slot02','/var/run/docker.sock']
assert all(not os.path.exists(p) for p in missing)
assert os.path.isfile('/workspace/state/kanban.db')
blocked=[]
for address,port in [('127.0.0.1',8094),('100.99.71.65',22),('64.202.186.3',22)]:
 try:
  s=socket.create_connection((address,port),timeout=2);s.close();raise AssertionError('Private network connection was allowed')
 except PermissionError:blocked.append(address)
connection=http.client.HTTPSConnection('api.openai.com',timeout=8)
connection.request('GET','/v1/models');response=connection.getresponse();status=response.status;response.read(1024);connection.close()
assert status==401,status
print(json.dumps({'uid':os.getuid(),'private_paths_hidden':True,'private_network_denied':blocked,'public_https':status}))
'''
    # Only this short-lived probe joins the existing service cgroup. The app's
    # PID, ownership, namespace and worker state are not changed.
    wrapper="import os\nopen("+repr('/sys/fs/cgroup'+group+'/cgroup.procs')+",'w').write(str(os.getpid()))\nos.execv('/usr/bin/nsenter',"+repr(['/usr/bin/nsenter','--target',str(pid),'--mount','--root','--wd','--setuid',str(account.pw_uid),'--setgid',str(account.pw_gid),'--','/usr/bin/python3','-I','-c',probe])+")\n"
    return json.loads(run('/usr/bin/python3','-I','-c',wrapper))

def verify(publish=False,before=None):
    if os.geteuid()!=0:raise RuntimeError('Authorized root verification required')
    receipt=json.loads((BASE/'deployment.json').read_text());units=receipt['units']
    for _ in range(15):
        observed=states(units)
        if all(v['ActiveState']=='active' and int(v['MainPID'])>0 for v in observed.values()):break
        time.sleep(1)
    assert all(v['ActiveState']=='active' and int(v['MainPID'])>0 for v in observed.values()),observed
    sys_before=before or states(PERSONAL)
    checks={u:isolation(u,s) for u,s in observed.items() if 'slot' in u}
    # Read every private socket directly with only that instance's credential.
    import importlib.util
    spec=importlib.util.spec_from_file_location('enterprise_portal',Path(__file__).with_name('portal.py'));portal=importlib.util.module_from_spec(spec);spec.loader.exec_module(portal)
    inventories={}
    for i in range(1,receipt['capacity']+1):
        slot=f'slot{i:02}';key=(BASE/'workspaces'/slot/'private/owner.key').read_text();counts={}
        for collection in ('tasks','projects','models'):
            c=portal.UnixConnection('/run/prfkt-enterprise-'+slot+'/app.sock');c.request('GET','/api/'+collection,headers={'X-Access-Key':key});r=c.getresponse();data=json.loads(r.read());assert r.status==200;c.close();counts[collection]=len(data[collection])
        inventories[slot]=counts
        # A fresh provisioning has no guest data or inherited connections.
        assert all(value==0 for value in counts.values()),counts
    assert call('GET','/api/tasks')[0]==401
    assert call('GET','/signin')[0]==200
    credential=json.loads(Path('/home/joevps/.local/share/prfkt-enterprise-access/admin.json').read_text())
    status,_,headers=call('POST','/_enterprise/login',{'username':credential['username'],'password':credential['password']});assert status==200
    cookie=headers['Set-Cookie'].split(';')[0]
    status,me,_=call('GET','/_enterprise/me',cookie=cookie);assert status==200 and me['role']=='admin'
    status,snapshot,_=call('GET','/_enterprise/admin',cookie=cookie);assert status==200 and snapshot['available']==receipt['capacity']
    assert call('POST','/_enterprise/logout',{},cookie,me['csrf'])[0]==200
    assert states(PERSONAL)==sys_before,'Personal service state changed'
    result={'state':'verified-private','isolation':checks,'empty_workspaces':inventories,'admin_signin':True,'anonymous_denied':True,'personal_services_unchanged':True}
    if publish:
        prior=json.loads(run('tailscale','serve','status','--json'))
        assert '10000' not in prior.get('TCP',{}),'Port 10000 is already configured'
        run('tailscale','funnel','--bg','--https=10000','--yes','http://127.0.0.1:8100')
        after=json.loads(run('tailscale','serve','status','--json'))
        for key,value in prior.get('Web',{}).items():assert after['Web'].get(key)==value
        for key,value in prior.get('TCP',{}).items():assert after['TCP'].get(key)==value
        for key,value in prior.get('AllowFunnel',{}).items():assert after['AllowFunnel'].get(key)==value
        assert after['AllowFunnel'].get(urlsplit(ORIGIN).netloc) is True
        result.update(state='verified-published',url=ORIGIN+'/signin')
    path=BASE/('verification-'+str(int(time.time()))+'.json');path.write_text(json.dumps(result,indent=2)+'\n');os.chmod(path,0o600)
    print(json.dumps(result))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--publish',action='store_true');args=parser.parse_args();verify(args.publish)
