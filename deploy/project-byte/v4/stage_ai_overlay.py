#!/usr/bin/env python3
"""Stage only the AI overlay onto the exact protected PROJECT_BYTE app baseline.

This is a code-only operation: no database, credential, user file, service,
worker, gateway, or network operation is copied or invoked. The feature payload
comes from this script's directory; the existing app supplies six pinned files.
An existing output directory or an unexpected AI installation is an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

BASE_COMMIT = 'd95ca223fa1070ef45c6f34ee1aa9e2d358cd127'
BASE_HASHES = {
    'server.py': '16e7488c8aea3774fc08c03b1375535dfc94986f6aa83b514c7621ec3d89568e',
    'index.html': '520056a851bfebb86d71657a759913f9111a9bef239d3a6316c00c735cc41f94',
    'backend.py': '7df2ed5c0e32ebca1bc2efce46ecdc3a393318e833bc4c6ee8754404fc642526',
    'home.js': '7ace191200373446f6d606811c9f512ba7b1e6afc1c7814f9c505d79572a4e5d',
    'home-inspector.js': '9924325cd0434a5b44dc7f49ff0db87642ddfaef1308b68256b5838dc7ec8d1d',
    'health-runtime.js': 'f189f4673a0610671839f9f43f59a404e9001e9a923f83ffe70ee363f61cc12d',
}
FEATURE_FILES = ('ai_connections.py', 'ai_runtime.py', 'codex_connection.py', 'ai-connections.js')
RESULT_HASHES = {
    'server.py': '31fc8434a0dd06757545362e8904ba336405ba12d3508724208e818c118b4b7a',
    'index.html': '146c27b0d5f6014435776d5630f3221a94acc0fa9786c4e03db0fbaad477593a',
}
RUNTIME_MARKER = b'if __name__ == "__main__":\n'
RUNTIME_INSERT = (b'from ai_runtime import install as install_ai_connections\n\n'
                  b'SafeHandler = install_ai_connections(app, SafeHandler, PUBLIC_FILES)\n\n\n')
INDEX_INSERT = b'<script src="/ai-connections.js"></script>'
MAX_CODE_BYTES = 20 * 1024 * 1024
HERE = Path(__file__).resolve().parent


class StageError(ValueError):
    """A bounded, reviewable staging precondition failed."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_code(directory_fd, name):
    """Read only a named, regular, non-symlink code file with a size bound."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CODE_BYTES:
            raise StageError('Expected a bounded regular code file: ' + name)
        data = stream.read(MAX_CODE_BYTES + 1)
    if not data or len(data) > MAX_CODE_BYTES:
        raise StageError('Empty or oversized code file: ' + name)
    return data


def _directory(path):
    if path.is_symlink() or not path.is_dir():
        raise StageError('Expected an existing non-symlink source directory')
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def stage(source_app, output, feature_source=HERE):
    """Validate everything, then create one fresh code-only staging directory."""
    source_app, output, feature_source = map(Path, (source_app, output, feature_source))
    if output.exists() or output.is_symlink():
        raise StageError('Output must not already exist')
    source_real = source_app.resolve(strict=True)
    # Resolve the existing parent so writes never follow a final output symlink.
    output = output.parent.resolve(strict=True) / output.name
    if output == source_real or source_real in output.parents:
        raise StageError('Output must be outside the source app')
    originals = {}
    source_fd = _directory(source_app)
    try:
        for name in os.listdir(source_fd):
            if name.startswith(('ai_', 'ai-')) or name == 'codex_connection.py':
                raise StageError('Unexpected existing AI file: ' + name)
        for name, expected in BASE_HASHES.items():
            data = _read_code(source_fd, name)
            if sha256(data) != expected:
                raise StageError('Protected baseline hash mismatch: ' + name)
            originals[name] = data
    finally:
        os.close(source_fd)
    files = dict(originals)
    feature_fd = _directory(feature_source)
    try:
        for name in FEATURE_FILES:
            files[name] = _read_code(feature_fd, name)
    finally:
        os.close(feature_fd)
    for name, marker, insertion in (('server.py', RUNTIME_MARKER, RUNTIME_INSERT),
                                    ('index.html', b'</body>', INDEX_INSERT)):
        original = originals[name]
        if original.count(marker) != 1 or insertion in original:
            raise StageError('Unexpected overlay marker: ' + name)
        files[name] = original.replace(marker, insertion + marker, 1)
        if files[name].replace(insertion, b'', 1) != original or sha256(files[name]) != RESULT_HASHES[name]:
            raise StageError('Overlay did not preserve the protected baseline: ' + name)
    for name, data in files.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')  # Syntax only; no module imports or startup.
    manifest = {
        'base_commit': BASE_COMMIT,
        'operation': 'stage-code-only',
        'baseline_sha256': dict(BASE_HASHES),
        'feature_sha256': {name: sha256(files[name]) for name in FEATURE_FILES},
        'output_sha256': {name: sha256(data) for name, data in files.items()},
    }
    files['overlay-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    # Never reuse a directory and never remove anything we did not create.
    os.mkdir(output, 0o700)
    made = []
    try:
        for name, data in files.items():
            target = output / name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            made.append(target)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
    except BaseException:
        for target in reversed(made):
            target.unlink(missing_ok=True)
        output.rmdir()
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = stage(args.source_app, args.output)
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, 'Staging failed: ' + str(error) + '\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
