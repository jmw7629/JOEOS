"""Verify AI release files are pinned, packaged, and rollback covered."""
import hashlib
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
installer = (HERE.parent / 'install.sh').read_text()
files = {'AI_RUNTIME': 'ai_runtime.py', 'AI_CONNECTIONS': 'ai_connections.py',
         'CODEX_CONNECTION': 'codex_connection.py', 'AI_UI': 'ai-connections.js'}
for key, name in files.items():
    digest = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
    assert f'EXPECTED_{key}="{digest}"' in installer, f'Stale {name} checksum'
    assert f'"$V4/{name}" -o "$TMP/{name}"' in installer, f'Missing {name} download'
    assert f'[ "${key}_SHA" = "$EXPECTED_{key}" ]' in installer, f'Missing {name} verification'
    assert f'install -m 0644 "$TMP/{name}" "$DEST/{name}"' in installer, f'Missing {name} install'
    assert f'cp -p "$DEST/backups/{name}-${{STAMP}}" "$DEST/{name}"' in installer, f'Missing {name} rollback'
html = ''.join(p.read_text() for p in sorted((HERE / 'index').glob('*.part')))
assert html.count('<script src="/ai-connections.js"></script>') == 1
runtime = (HERE / 'runtime_server.py').read_text()
assert runtime.count('SafeHandler = install_ai_connections(app, SafeHandler, PUBLIC_FILES)') == 1
gateway = (HERE.parent / 'public-access/gateway.py').read_text()
for route in ('/ai-connections.js', '/api/ai-connections', *('/api/ai-connections/' + x for x in ('discover', 'connect', 'default', 'login', 'disconnect'))):
    assert repr(route) in gateway or '"' + route + '"' in gateway, f'Gateway missing {route}'
print('AI_RELEASE_PINS_ASSETS_MODULES_ROUTES_AND_ROLLBACK=PASS')
