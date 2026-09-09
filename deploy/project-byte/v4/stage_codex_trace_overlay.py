#!/usr/bin/env python3
"""Stage two reviewed native trace modules over the verified project registry release.

No runtime/HTML, gateway, database, settings, registry, credential or worker change.
The operator separately verifies and activates the new private staging directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from stage_ai_overlay import _directory, _read_code

HERE = Path(__file__).resolve().parent
BASE = {
    'codex_tasks.py': '3cf18aac1624675e7ce23d0d9edd93361e64a96349fa019e95ab9e0bd8f9c17f',
    'codex-workspace.js': '411e89a68c31ec58d2a021d3ede30e37440279be0a941a188f2e34ac72f37abc',
}


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
        for name, expected in BASE.items():
            if hashlib.sha256(_read_code(fd, name)).hexdigest() != expected:
                raise ValueError('Live native workspace differs; review before staging')
    finally:
        os.close(fd)
    fd = _directory(features)
    try:
        output = {name: _read_code(fd, name) for name in BASE}
    finally:
        os.close(fd)
    compile(output['codex_tasks.py'], 'codex_tasks.py', 'exec')
    manifest = {'operation': 'stage-native-trace-code-only', 'baseline_sha256': BASE,
                'output_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in output.items()}}
    output['codex-trace-manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
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
