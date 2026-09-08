#!/usr/bin/env python3
"""Stage the Codex task overlay onto the verified renamed live app, code only.

Does not read credentials/data, start workers, touch Git, or deploy. The exact
live baseline is required; inherited feature-branch runtime/UI are never copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from stage_ai_overlay import _directory, _read_code

HERE = Path(__file__).resolve().parent
BASE = {
    'server.py': '31fc8434a0dd06757545362e8904ba336405ba12d3508724208e818c118b4b7a',
    'index.html': 'f9c0880bc2dcbfa4ab73509d19631d313940af7d72a7260e9f5edf9d0b52ba64',
    'ai_runtime.py': '682326d44e1e9949346a2f49942253e799b5308d2c25fd8192bd6f7f540657b5',
}
FILES = ('codex_tasks.py', 'codex_tasks_runtime.py', 'codex_task_rpc.py',
         'codex_sandbox.py', 'codex_publisher.py', 'codex-workspace.js', 'ai_runtime.py')
HOOK = b'from codex_tasks_runtime import install as install_codex_workspace\n\nSafeHandler = install_codex_workspace(app, SafeHandler, PUBLIC_FILES)\n\n\n'
SCRIPT = b'<script src="/codex-workspace.js"></script>'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def stage(source, destination, features=HERE):
    source, destination, features = map(Path, (source, destination, features))
    if destination.exists() or destination.is_symlink():
        raise ValueError('Staging destination must be new')
    destination = destination.parent.resolve(strict=True) / destination.name
    source_real = source.resolve(strict=True)
    if destination == source_real or source_real in destination.parents:
        raise ValueError('Staging must be outside the live app')
    fd = _directory(source)
    try:
        originals = {name: _read_code(fd, name) for name in BASE}
        if any(digest(originals[name]) != expected for name, expected in BASE.items()):
            raise ValueError('Live app baseline differs; review before staging')
        if any(name in os.listdir(fd) for name in FILES if name != 'ai_runtime.py'):
            raise ValueError('A Codex task overlay is already present')
    finally:
        os.close(fd)
    fd = _directory(features)
    try:
        output = {name: _read_code(fd, name) for name in FILES}
    finally:
        os.close(fd)
    for name, marker, insertion in (('server.py', b'if __name__ == "__main__":\n', HOOK),
                                    ('index.html', b'</body>', SCRIPT)):
        original = originals[name]
        if original.count(marker) != 1 or insertion in original:
            raise ValueError('Unexpected live overlay marker')
        output[name] = original.replace(marker, insertion + marker, 1)
        assert output[name].replace(insertion, b'', 1) == original
    for name, data in output.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    manifest = {'operation': 'stage-codex-code-only', 'baseline_sha256': BASE,
                'output_sha256': {name: digest(data) for name, data in output.items()}}
    output['codex-overlay-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    destination.mkdir(mode=0o700)
    for name, data in output.items():
        fd = os.open(destination / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.source_app, args.output), sort_keys=True))
