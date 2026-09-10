#!/usr/bin/env python3
"""Repair only the initial enterprise install's masked public file modes.

No provisioning, data replacement, ownership changes or workspace restarts.
The private root and credential modes remain restrictive.
"""
import argparse, hashlib, json, os, pwd, stat, sys, time
from pathlib import Path
import verify

BASE=Path('/var/lib/prfkt-enterprise')
CODE=Path('/opt/prfkt-enterprise')
UNITS=Path('/etc/systemd/system')
PUBLIC_ETC=('resolv.conf','hosts','nsswitch.conf','passwd','group')

def permission_plan(capacity,portal_gid):
    plan=[(BASE,0o711,True,0),(CODE,0o755,True,0),
          (BASE/'workspaces',0o711,True,0),(BASE/'roots',0o711,True,0),
          (BASE/'portal-config',0o750,True,portal_gid),
          (BASE/'portal-config/config.json',0o640,False,portal_gid)]
    for slot in [f'slot{i:02}' for i in range(1,capacity+1)]+['portal']:
        plan.extend((BASE/'roots'/slot/'etc'/name,0o644,False,0) for name in PUBLIC_ETC)
        plan.append((UNITS/f'prfkt-enterprise-{slot}.service',0o644,False,0))
    return plan

def validate_plan(plan):
    # Inspect every target before the first chmod. Refuse symlinks, wrong types,
    # unexpected owners/groups and modes other than the masked/original pair.
    for path,mode,directory,gid in plan:
        for part in (path,*path.parents):
            if part.is_symlink():raise RuntimeError('Refusing symlink in repair path: '+str(path))
        info=path.lstat()
        valid_type=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid_type or info.st_uid!=0 or info.st_gid!=gid or stat.S_IMODE(info.st_mode) not in (mode,mode&~0o077):
            raise RuntimeError('Unexpected enterprise file identity or mode: '+str(path))

def repair(publish=False):
    if os.geteuid()!=0:raise RuntimeError('Run this repair from the authorized VPS terminal')
    os.umask(0o077)
    receipt=json.loads((BASE/'deployment.json').read_text())
    capacity=receipt.get('capacity')
    if not isinstance(capacity,int) or not 1<=capacity<=5:raise RuntimeError('Invalid installation receipt')
    expected=[f'prfkt-enterprise-slot{i:02}.service' for i in range(1,capacity+1)]+['prfkt-enterprise-portal.service']
    if receipt.get('units')!=expected or receipt.get('origin')!=verify.ORIGIN:raise RuntimeError('Installation identity differs from this repair')
    plan=permission_plan(capacity,pwd.getpwnam('prfkt-portal').pw_gid);validate_plan(plan)
    before=verify.states(verify.PERSONAL)
    workspace_before=verify.states(expected[:-1])
    # Credential, database and user-content bytes are never opened or rewritten.
    original=[{'path':str(path),'before':oct(stat.S_IMODE(path.stat().st_mode)),'after':oct(mode)} for path,mode,_,_ in plan]
    repairs=BASE/'repairs';repairs.mkdir(mode=0o700,exist_ok=True)
    record=repairs/('permissions-'+str(time.time_ns())+'.json')
    record.write_text(json.dumps({'state':'prepared','modes':original,'personal_services':before,'workspace_services':workspace_before},indent=2)+'\n')
    for path,mode,_,_ in plan:path.chmod(mode)
    # A healthy portal is not restarted; reset a failed startup and start it.
    verify.run('systemctl','reset-failed','prfkt-enterprise-portal.service')
    verify.run('systemctl','start','prfkt-enterprise-portal.service')
    if verify.states(expected[:-1])!=workspace_before:raise RuntimeError('Workspace service identity changed; publication stopped')
    print(json.dumps({'state':'enterprise-permissions-repaired','paths':len(plan),'workspace_services_unchanged':True,'receipt':str(record)}),flush=True)
    result=verify.verify(publish=publish,before=before)
    data=json.loads(record.read_text());data.update(state='verified',verification=result);record.write_text(json.dumps(data,indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--publish',action='store_true');args=parser.parse_args()
    try:repair(args.publish)
    except verify.VerificationError as error:
        print(str(error),file=sys.stderr);raise SystemExit(error.returncode if 1<=error.returncode<=255 else 1)
