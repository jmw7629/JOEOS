#!/usr/bin/env python3
"""Prepare an empty, isolated installation. Never runs the owner installer."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import uuid

HERE = Path(__file__).resolve().parent
ASSETS = {
    'PERMISSIONS': 'execution_permissions.py', 'SERVER': 'runtime_server.py',
    'HOME': 'home.js', 'INSPECTOR': 'home-inspector.js', 'HEALTH': 'health-runtime.js',
    'AI_RUNTIME': 'ai_runtime.py', 'AI_CONNECTIONS': 'ai_connections.py',
    'CODEX_CONNECTION': 'codex_connection.py', 'AI_UI': 'ai-connections.js',
    'CODEX_TASK_RPC': 'codex_task_rpc.py', 'CODEX_SANDBOX': 'codex_sandbox.py',
    'CODEX_TASKS': 'codex_tasks.py', 'CODEX_TASKS_RUNTIME': 'codex_tasks_runtime.py',
    'CODEX_PROJECTS': 'codex_projects.py', 'CODEX_PUBLISHER': 'codex_publisher.py',
    'CODEX_UI': 'codex-workspace.js',
}

def digest(data): return hashlib.sha256(data).hexdigest()

def schema_only(source: bytes) -> bytes:
    tree = ast.parse(source)
    init = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'init_db')
    block = [n for n in init.body if isinstance(n, ast.With)]
    if len(block) != 1: raise ValueError('Unexpected database initializer')
    body = block[0].body
    cuts = [i for i, n in enumerate(body) if isinstance(n, ast.If) and 'select count(*) from tasks' in ast.unparse(n.test)]
    if len(cuts) != 1 or 'notification_reads' not in ast.unparse(body[cuts[0]-1]):
        raise ValueError('Cannot safely separate schema from owner seed data')
    block[0].body = body[:cuts[0]]
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SEED' for t in node.targets):
            node.value = ast.List(elts=[], ctx=ast.Load())
    data = (ast.unparse(tree) + '\n').encode()
    compile(data, 'backend.py', 'exec')
    return data

def new_target(path: Path) -> Path:
    path = path.absolute()
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink(): raise ValueError('Installation paths cannot contain symlinks')
    if path.exists(): raise FileExistsError('Refusing to overwrite an existing installation')
    if not path.parent.is_dir(): raise ValueError('Installation parent must already exist')
    return path

def write_private(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream: stream.write(data)

def prepare(target: Path, name='PRFKT_PROJECT', owner='Owner', assistant='AI_BYTE', source=None, hosted=False):
    target = new_target(target)
    for value in (name, owner, assistant):
        if not isinstance(value, str) or not value.strip() or len(value) > 48 or any(ord(c) < 32 for c in value):
            raise ValueError('Names must be 1–48 printable characters')
    source = Path(source or HERE.parent / 'v4').resolve()
    if target.is_relative_to(source) or source.is_relative_to(target): raise ValueError('Installation overlaps source')
    pins = dict(re.findall(r'^EXPECTED_([A-Z_]+)="([0-9a-f]{64})"$', (source.parent / 'install.sh').read_text(), re.M))
    payload = {key: (source / name).read_bytes() for key, name in ASSETS.items()}
    payload['BACKEND'] = b''.join((source / 'server' / f'{n:02}.part').read_bytes() for n in range(1, 9))
    payload['INDEX'] = b''.join((source / 'index' / f'{n:02}.part').read_bytes() for n in range(1, 8))
    for key, data in payload.items():
        if digest(data) != pins.get(key): raise ValueError(f'Release checksum mismatch: {key}')
    assets = {ASSETS[key]: data for key, data in payload.items() if key in ASSETS}
    assets['backend.py'] = schema_only(payload['BACKEND'])
    assets['index.html'] = payload['INDEX']
    # This empty install has no inherited owner-specific bridge diagnostics.
    home = assets['home.js'].decode()
    old = "bridgeNode('stickdeath','StickDeath')+bridgeNode('vitros','VITROS builder')+bridgeNode('vitros_verifier','VITROS verifier')+"
    if home.count(old) != 1: raise ValueError('Unexpected Home bridge section')
    home = home.replace(old, "Object.entries(bridges).map(([key,value])=>bridgeNode(key,value.label||key)).join('')+")
    assets['home.js'] = home.encode()
    if hosted:
        # The owner-specific Saxon Shore player is not a default for new users.
        hosted_home = assets['home.js'].decode()
        hosted_home, removed = re.subn(r'// PRFKT_SPOTIFY_PLAYER_BEGIN[^\n]*\n.*?^\}\)\(\);(?:\n|$)', '', hosted_home, count=1, flags=re.S | re.M)
        if removed != 1: raise ValueError('Owner music module boundary changed')
        assets['home.js'] = hosted_home.encode()
        ui = assets['codex-workspace.js'].decode()
        replacements = {
            "ready:mode === 'execute' && configured && connected": "ready:mode === 'chat' && configured && connected",
            "if (state.ready) return 'Connected · agents selected automatically';": "if (state.ready) return 'Connected · private provider chat · execution tools not connected';",
            "Describe the outcome. Codex chooses the agents and tools, and brings progress, permission requests and results here. Pull requests remain open for review; nothing merges automatically.": "Chat uses your connected AI account. Conversations are saved per project. Execution tools, device access and code publication require a separately connected runner.",
            "'Codex run'": "'Provider conversation'",
        }
        for old, new in replacements.items():
            if ui.count(old) != 1: raise ValueError('Hosted chat interface patch no longer matches')
            ui = ui.replace(old, new)
        # The general room in an empty install is independent of Joe's repo key.
        ui = ui.replace("'joeos'", "'general'")
        ui = ui.replace('Choose an outcome; Codex handles the routing.', 'Chat with your connected provider. Execution tools are not connected.')
        ui = ui.replace('const visibleNative = native.filter(m=>m.text &&', 'const visibleNative = native.filter(m=>m.text && !messages.some(saved=>saved.id===m.id) &&')
        assets['codex-workspace.js'] = ui.encode()
        account = "\n(() => { const target=document.querySelector('#settings > .between'); if(target){const link=document.createElement('a');link.className='btn';link.href='/account';link.textContent='Sign-in & password';target.appendChild(link);} })();\n"
        assets['home.js'] += account.encode()
        assets['ai-connections.js'] = assets['ai-connections.js'].replace(
            b'Private owner connections \xc2\xb7 use your existing Codex sign-in, connect a provider, or add your own model and agent.',
            b'Connect your own AI API account or a public HTTPS model endpoint. Subscription runtimes and execution tools are not connected to this pilot.')
        connections = assets['ai-connections.js'].decode()
        connections, labels = re.subn(r'^    const label = codexConnected\(\).*$', "    const label = 'Your AI connections';", connections, flags=re.M)
        connections, descriptions = re.subn(r'^    const description = codexConnected\(\).*$', "    const description = 'Use your own AI API account. Saved conversations stay in this private workspace. Execution tools and subscription runners are not connected.';", connections, flags=re.M)
        if labels != 1 or descriptions != 1: raise ValueError('AI connection summary changed')
        assets['ai-connections.js'] = connections.encode()
    for name_, data in assets.items():
        if name_.endswith('.py'): compile(data, name_, 'exec')
    profile = {'schema_version': 1, 'display_name': name.strip(), 'assistant_name': assistant.strip(), 'owner_shortcuts': []}
    assets['workspace.json'] = (json.dumps(profile, indent=2) + '\n').encode()
    identifier = uuid.uuid4().hex
    target.mkdir(mode=0o700)
    try:
        for relative in ('app', 'state', 'state/uploads', 'private', 'private/home', 'private/config', 'private/cache', 'private/state', 'private/ai-credentials'):
            (target / relative).mkdir(mode=0o700)
        for filename, data in assets.items(): write_private(target / 'app' / filename, data)
        launchers = ['runtime.py', 'recovery.py']
        for filename in launchers:
            write_private(target / filename, (HERE / filename).read_bytes())
        if hosted:
            for filename in ('workspace', 'chat'):
                output = 'enterprise_' + filename + '.py'
                write_private(target / output, (HERE.parent / 'enterprise' / (filename + '.py')).read_bytes())
                launchers.append(output)
        write_private(target / 'private' / 'owner.key', secrets.token_urlsafe(36).encode())
        manifest = {'schema_version': 1, 'installation_id': identifier, 'owner_name': owner.strip(),
                    'profile': profile, 'source_pins': {k: digest(v) for k, v in payload.items()},
                    'assets': {k: digest(v) for k, v in assets.items()},
                    'launchers': {k: digest((target / k).read_bytes()) for k in launchers}, 'hosted': bool(hosted)}
        write_private(target / 'installation.json', (json.dumps(manifest, indent=2) + '\n').encode())
    except BaseException:
        shutil.rmtree(target)
        raise
    return {'installation_id': identifier, 'directory': str(target), 'owner_key_file': str(target / 'private/owner.key'), 'state': 'prepared-not-running'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('--name', default='PRFKT_PROJECT'); parser.add_argument('--owner', default='Owner')
    parser.add_argument('--assistant', default='AI_BYTE')
    parser.add_argument('--hosted', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.target, args.name, args.owner, args.assistant, hosted=args.hosted)))
