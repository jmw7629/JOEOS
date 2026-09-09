"""Verify AI release files are pinned, packaged, and rollback covered."""
import hashlib
from pathlib import Path
import re
import runpy
import sys

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
installer = (HERE.parent / 'install.sh').read_text()
files = {'AI_RUNTIME': 'ai_runtime.py', 'AI_CONNECTIONS': 'ai_connections.py',
         'CODEX_CONNECTION': 'codex_connection.py', 'AI_UI': 'ai-connections.js',
         'CODEX_TASK_RPC': 'codex_task_rpc.py', 'CODEX_SANDBOX': 'codex_sandbox.py',
         'CODEX_TASKS': 'codex_tasks.py', 'CODEX_TASKS_RUNTIME': 'codex_tasks_runtime.py',
         'CODEX_PROJECTS': 'codex_projects.py', 'CODEX_PUBLISHER': 'codex_publisher.py', 'CODEX_UI': 'codex-workspace.js'}
for key, name in files.items():
    digest = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
    assert f'EXPECTED_{key}="{digest}"' in installer, f'Stale {name} checksum'
    for marker, label in (
        (f'"$V4/{name}" -o "$TMP/{name}"', 'download'),
        (f'[ "${key}_SHA" = "$EXPECTED_{key}" ]', 'verification'),
        (f'cp -p "$DEST/{name}" "$DEST/backups/{name}-${{STAMP}}"', 'backup'),
        (f'install -m 0644 "$TMP/{name}" "$DEST/{name}"', 'install'),
        (f'cp -p "$DEST/backups/{name}-${{STAMP}}" "$DEST/{name}"', 'rollback'),
        (f'else rm -f "$DEST/{name}"; fi', 'first-install rollback cleanup'),
    ):
        assert installer.count(marker) == 1, f'Missing or repeated {name} {label}'
    if name.endswith('.py'):
        syntax = next(line for line in installer.splitlines() if line.startswith('python3 -m py_compile '))
        assert syntax.count(f'"$TMP/{name}"') == 1, f'Missing {name} syntax check'
    else:
        assert installer.count(f'node --check "$TMP/{name}"') == 1, f'Missing {name} syntax check'

# The actual runtime and reconstructed index changed along with their new imports.
core = {'PERMISSIONS':'execution_permissions.py','SERVER':'runtime_server.py','HOME':'home.js',
        'INSPECTOR':'home-inspector.js','HEALTH':'health-runtime.js'}
core_bytes = {key:(HERE/name).read_bytes() for key,name in core.items()}
for key, directory in (('BACKEND','server'),('INDEX','index')):
    core_bytes[key] = b''.join(path.read_bytes() for path in sorted((HERE/directory).glob('*.part')))
for key, data in core_bytes.items():
    assert f'EXPECTED_{key}="{hashlib.sha256(data).hexdigest()}"' in installer, f'Stale {key} release pin'

html = ''.join(p.read_text() for p in sorted((HERE / 'index').glob('*.part')))
order = ['home.js','home-inspector.js','health-runtime.js','ai-connections.js','codex-workspace.js']
positions = []
for name in order:
    tag = f'<script src="/{name}"></script>'
    assert html.count(tag) == 1, f'Missing or repeated {name} script hook'
    positions.append(html.index(tag))
assert positions == sorted(positions), 'Codex workspace must load after Home and AI connections'
runtime = (HERE / 'runtime_server.py').read_text()
assert runtime.count('SafeHandler = install_ai_connections(app, SafeHandler, PUBLIC_FILES)') == 1
assert runtime.count('from codex_tasks_runtime import install as install_codex_workspace') == 1
assert runtime.count('SafeHandler = install_codex_workspace(app, SafeHandler, PUBLIC_FILES)') == 1
assert runtime.index('SafeHandler = install_ai_connections(') < runtime.index('SafeHandler = install_codex_workspace(')
tasks_runtime = (HERE/'codex_tasks_runtime.py').read_text()
assert tasks_runtime.count("public_files['/codex-workspace.js']") == 1
assert "os.getenv('PRFKT_CODEX_STATE')" in tasks_runtime and "os.getenv('PRFKT_CODEX_REPO')" in tasks_runtime
assert "os.getenv('PRFKT_CODEX_PUBLISH') == '1'" in tasks_runtime
assert not re.search(r'(?m)^\s*(?:export\s+|Environment=)?PRFKT_CODEX_(?:STATE|REPO|PUBLISH)=',installer), 'Installing code must not enable native execution'

gateway_path = HERE.parent / 'public-access/gateway.py'
gateway = gateway_path.read_text()
for route in ('/ai-connections.js', '/api/ai-connections', *('/api/ai-connections/' + x for x in ('discover', 'connect', 'default', 'login', 'disconnect'))):
    assert repr(route) in gateway or '"' + route + '"' in gateway, f'Gateway missing {route}'
routes = runpy.run_path(str(gateway_path))
assert '/codex-workspace.js' in routes['ASSETS']
prefix, identifier = '/api/codex-workspace', 'a'*32
for method, path, query in (
    ('GET',prefix,''), ('GET',prefix+'/conversations/'+identifier,''),
    ('GET',prefix+'/runs/'+identifier+'/events','after=0'), ('GET',prefix+'/artifacts/'+identifier,''),
    ('POST',prefix+'/message',''), ('POST',prefix+'/runs/'+identifier+'/stop',''),
    ('POST',prefix+'/permissions/'+identifier+'/decision',''),
):
    assert routes['api_allowed'](method,path,query), f'Gateway missing {method} {path}'
assert not routes['api_allowed']('POST',prefix+'/runs/'+identifier+'/stop','after=0')
assert not routes['api_allowed']('GET',prefix+'/runs/'+identifier+'/events','after=-1')

version = re.search(r"PINNED_VERSION = '([^']+)'",(HERE/'codex_connection.py').read_text())[1]
workflow = (HERE.parents[2]/'.github/workflows/project-byte-ai-connections-verify.yml').read_text()
assert '@openai/codex@'+version in workflow, 'CI native runtime does not match the transport pin'
for name in ('test_codex_task_rpc.py','test_codex_sandbox.py','test_codex_tasks.py',
             'test_codex_tasks_runtime.py','test_codex_projects.py','test_codex_publisher.py','test_codex_workspace_native.py',
             'test_codex_workspace_browser.mjs','test_stage_codex_overlay.py'):
    assert name in workflow, f'Missing native workspace CI gate: {name}'
print('AI_RELEASE_PINS_ASSETS_MODULES_ROUTES_AND_ROLLBACK=PASS')
print('CODEX_WORKSPACE_PACKAGE_ORDER_OPTIONAL_EXECUTION_AND_NATIVE_CI_GATES=PASS')
