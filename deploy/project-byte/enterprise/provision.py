#!/usr/bin/env python3
"""Provision a new invite-only pilot. Root only; refuses existing deployments.

No existing workspace, service, worker, repository or Tailscale route is modified.
Public routing is a separate, explicitly verified final operation.
"""
import argparse, hashlib, importlib.util, json, os, pwd, secrets, shutil, subprocess, sys
from pathlib import Path

BASE=Path('/var/lib/prfkt-enterprise')
CODE=Path('/opt/prfkt-enterprise')
UNITS=Path('/etc/systemd/system')
ORIGIN='https://mcso9tqzb9-1.tailb9395f.ts.net:10000'

def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout

def write(path,value,mode=0o600,uid=0,gid=0):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'w') as f:f.write(value)
    os.chown(path,uid,gid)

def user(name):
    try:pwd.getpwnam(name);raise RuntimeError('Refusing an existing service account: '+name)
    except KeyError:pass
    run('useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin',name)
    return pwd.getpwnam(name)

def rootfs(path,account):
    for sub in ('usr','etc/ssl/certs','workspace','socket','code','config','state','sockets','proc','sys','dev','run','tmp'):(path/sub).mkdir(parents=True,exist_ok=True)
    for directory in (path,*[p for p in path.rglob('*') if p.is_dir()]):os.chmod(directory,0o755)
    (path/'lib').symlink_to('usr/lib');(path/'lib64').symlink_to('usr/lib64');os.chmod(path/'tmp',0o1777)
    write(path/'etc/resolv.conf','nameserver 1.1.1.1\nnameserver 8.8.8.8\n',0o644)
    write(path/'etc/hosts','127.0.0.1 localhost\n::1 localhost\n',0o644)
    write(path/'etc/nsswitch.conf','passwd: files\ngroup: files\nhosts: files dns\n',0o644)
    write(path/'etc/passwd',f'root:x:0:0:root:/nonexistent:/usr/sbin/nologin\n{account.pw_name}:x:{account.pw_uid}:{account.pw_gid}::/nonexistent:/usr/sbin/nologin\n',0o644)
    write(path/'etc/group',f'{account.pw_name}:x:{account.pw_gid}:\n',0o644)

COMMON='''NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
UMask=0077
TasksMax=96
MemoryMax=384M
CPUQuota=60%
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
'''
NETWORK='''IPAddressDeny=127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 0.0.0.0/8 224.0.0.0/4 240.0.0.0/4 ::1/128 fc00::/7 fe80::/10 ff00::/8 64.202.186.3/32
'''

def provision(source,count,publish=False):
    if os.geteuid()!=0:raise RuntimeError('Authorized root invocation required')
    if BASE.exists() or CODE.exists():raise RuntimeError('Deployment already exists; use a reviewed versioned update')
    if not 1<=count<=5:raise ValueError('Pilot capacity must be 1–5')
    os.umask(0o077);BASE.mkdir(mode=0o711);CODE.mkdir(mode=0o755)
    sys.path.insert(0,str(source.resolve()/'enterprise'))
    import verify
    personal_before=verify.states(verify.PERSONAL)
    portal_user=user('prfkt-portal')
    source=source.resolve()
    for directory in ('enterprise','public-access'):
        shutil.copytree(source/directory,CODE/directory)
    for p in CODE.rglob('*'):os.chmod(p,0o755 if p.is_dir() else 0o644)
    (BASE/'workspaces').mkdir(mode=0o711);(BASE/'roots').mkdir(mode=0o711)
    state=BASE/'portal-state';state.mkdir(mode=0o700);os.chown(state,portal_user.pw_uid,portal_user.pw_gid)
    configdir=BASE/'portal-config';configdir.mkdir(mode=0o750);os.chown(configdir,0,portal_user.pw_gid)
    spec=importlib.util.spec_from_file_location('private_prepare',source/'private-install/prepare.py');prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
    slots=[];groups=[];units=[]
    for i in range(1,count+1):
        slot=f'slot{i:02}';account=user('prfkt-'+slot);groups.append(account.pw_name)
        workspace=BASE/'workspaces'/slot;prepare.prepare(workspace,'Your workspace','Owner',hosted=True)
        write(workspace/'.runtime.lock','')
        for p in [workspace,*workspace.rglob('*')]:os.chown(p,account.pw_uid,account.pw_gid)
        jail=BASE/'roots'/slot;rootfs(jail,account)
        unit=f'''[Unit]
Description=PRFKT private workspace {slot}
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User={account.pw_name}
Group={account.pw_name}
RootDirectory={jail}
MountAPIVFS=yes
BindReadOnlyPaths=/usr /etc/ssl/certs
BindPaths={workspace}:/workspace
RuntimeDirectory=prfkt-enterprise-{slot}
RuntimeDirectoryMode=0750
BindPaths=/run/prfkt-enterprise-{slot}:/socket
ReadWritePaths=/workspace/state /workspace/private /workspace/.runtime.lock /socket
WorkingDirectory=/workspace
ExecStart=/usr/bin/python3 -I /workspace/runtime.py --socket /socket/app.sock
{COMMON}{NETWORK}
[Install]
WantedBy=multi-user.target
'''
        unitname=f'prfkt-enterprise-{slot}.service';write(UNITS/unitname,unit,0o644);units.append(unitname)
        slots.append({'id':slot,'socket':'/sockets/'+slot+'/app.sock','key':(workspace/'private/owner.key').read_text()})
    jail=BASE/'roots/portal';rootfs(jail,portal_user)
    for s in slots:(jail/'sockets'/s['id']).mkdir(mode=0o755)
    config={'origin':ORIGIN,'port':8100,'state':'/state','gateway':'/code/public-access','slots':slots}
    write(configdir/'config.json',json.dumps(config,indent=2)+'\n',0o640,0,portal_user.pw_gid)
    # Initialization uses host paths once; runtime sees only its private root.
    sys.path.insert(0,str(CODE/'enterprise'));import portal
    password=secrets.token_urlsafe(30)
    store=portal.Store(state,{'slots':slots});store.initialize_admin(password)
    for p in state.iterdir():os.chown(p,portal_user.pw_uid,portal_user.pw_gid);os.chmod(p,0o600)
    joe=pwd.getpwnam('joevps');delivery=Path(joe.pw_dir)/'.local/share/prfkt-enterprise-access';delivery.mkdir(mode=0o700);os.chown(delivery,joe.pw_uid,joe.pw_gid)
    write(delivery/'admin.json',json.dumps({'url':ORIGIN+'/admin','username':'owner','password':password},indent=2)+'\n',0o600,joe.pw_uid,joe.pw_gid)
    sockets='\n'.join('BindPaths=/run/prfkt-enterprise-'+s['id']+':/sockets/'+s['id'] for s in slots)
    unit=f'''[Unit]
Description=PRFKT private workspace invitations
After=network-online.target {' '.join(units)}
Wants=network-online.target {' '.join(units)}
[Service]
Type=simple
User=prfkt-portal
Group=prfkt-portal
SupplementaryGroups={' '.join(groups)}
RootDirectory={jail}
MountAPIVFS=yes
BindReadOnlyPaths=/usr /etc/ssl/certs {CODE}:/code {configdir}:/config
BindPaths={state}:/state
{sockets}
ReadWritePaths=/state
ExecStart=/usr/bin/python3 -I /code/enterprise/portal.py --config /config/config.json
{COMMON}IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=384M
[Install]
WantedBy=multi-user.target
'''
    write(UNITS/'prfkt-enterprise-portal.service',unit,0o644);units.append('prfkt-enterprise-portal.service')
    run('systemd-analyze','verify',*[str(UNITS/u) for u in units])
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now',*units)
    receipt={'state':'private-services-started-not-public','origin':ORIGIN,'capacity':count,'units':units,'admin_file':str(delivery/'admin.json'),'source_sha256':{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for d in ('enterprise','public-access','private-install') for p in (source/d).glob('*') if p.is_file()}}
    write(BASE/'deployment.json',json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'}))
    verify.verify(publish=publish,before=personal_before)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--capacity',type=int,default=5);parser.add_argument('--publish',action='store_true');args=parser.parse_args();provision(args.source,args.capacity,args.publish)
