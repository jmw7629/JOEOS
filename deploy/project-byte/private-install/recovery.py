#!/usr/bin/env python3
"""Offline, coordinated backup and recovery for one private installation."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tarfile
import tempfile

MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 10000

def digest(data): return hashlib.sha256(data).hexdigest()

def backup(root: Path, destination: Path):
    root = root.resolve(strict=True)
    if destination.exists() or destination.is_symlink(): raise FileExistsError('Backup destination exists')
    if destination.resolve().is_relative_to(root): raise ValueError('Store backups outside the installation')
    manifest = json.loads((root / 'installation.json').read_text())
    lock = os.open(root / '.runtime.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Backup requires this installation to be offline; no worker was stopped')
        files = {}
        for path in sorted(root.rglob('*')):
            if path.is_symlink(): raise ValueError('Cannot back up symlinks')
            relative = path.relative_to(root).as_posix()
            if path.is_dir(): continue
            if not path.is_file(): raise ValueError('Cannot back up special files')
            if relative == '.runtime.lock' or relative.startswith('__pycache__/') or '/__pycache__/' in relative or relative in ('state/kanban.db', 'state/kanban.db-wal', 'state/kanban.db-shm'): continue
            files[relative] = path.read_bytes()
            if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_BYTES: raise ValueError('Backup size limit exceeded')
        database = root / 'state/kanban.db'
        if database.exists():
            with tempfile.TemporaryDirectory() as tmp:
                snapshot = Path(tmp) / 'database.sqlite'
                source = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
                dest = sqlite3.connect(snapshot)
                try:
                    source.backup(dest)
                    if dest.execute('pragma integrity_check').fetchone()[0] != 'ok': raise ValueError('Database integrity check failed')
                finally: source.close(); dest.close()
                files['state/kanban.db'] = snapshot.read_bytes()
        index = {'schema_version': 1, 'installation_id': manifest['installation_id'], 'files': {name: digest(data) for name, data in files.items()}}
        files['snapshot.json'] = json.dumps(index, sort_keys=True).encode()
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as out, tarfile.open(fileobj=out, mode='w:gz') as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o600
                    archive.addfile(member, io.BytesIO(data))
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return {'state': 'backed-up', 'installation_id': index['installation_id'], 'file': str(destination), 'sha256': digest(destination.read_bytes())}
    finally: os.close(lock)

def restore(archive_path: Path, target: Path, expected_id: str):
    target = target.absolute()
    if target.exists() or target.is_symlink(): raise FileExistsError('Restore requires a new target')
    for path in target.parents:
        if path.is_symlink(): raise ValueError('Restore paths cannot contain symlinks')
    if not target.parent.is_dir(): raise ValueError('Restore parent does not exist')
    files = {}; total = 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or '..' in path.parts or member.name != str(path) or member.name in files:
                raise ValueError('Unsafe or duplicate archive entry')
            if path.parts[0] not in ('app', 'state', 'private', 'installation.json', 'runtime.py', 'recovery.py', 'snapshot.json'):
                raise ValueError('Unexpected archive entry')
            total += member.size
            if total > MAX_BYTES or len(files) >= MAX_FILES: raise ValueError('Archive limits exceeded')
            files[member.name] = archive.extractfile(member).read()
    index = json.loads(files.pop('snapshot.json'))
    if index.get('schema_version') != 1 or index.get('installation_id') != expected_id:
        raise ValueError('Backup belongs to another installation')
    if index.get('files') != {name: digest(data) for name, data in files.items()}: raise ValueError('Backup checksum mismatch')
    manifest = json.loads(files['installation.json'])
    if manifest['installation_id'] != expected_id or not files.get('private/owner.key'):
        raise ValueError('Backup identity is incomplete')
    for group, prefix in (('assets', 'app/'), ('launchers', '')):
        for name, expected in manifest[group].items():
            if digest(files[prefix + name]) != expected: raise ValueError('Code checksum mismatch')
    target.mkdir(mode=0o700)
    try:
        def private_directory(path):
            current = target
            for part in path.relative_to(target).parts:
                current = current / part
                current.mkdir(mode=0o700, exist_ok=True)
        for directory in ('app', 'state/uploads', 'private/home', 'private/config', 'private/cache', 'private/state', 'private/ai-credentials'):
            private_directory(target / directory)
        for name, data in files.items():
            path = target / name
            private_directory(path.parent)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(data)
        database = target / 'state/kanban.db'
        if database.exists():
            with sqlite3.connect(database) as db:
                if db.execute('pragma integrity_check').fetchone()[0] != 'ok': raise ValueError('Recovered database is invalid')
    except BaseException:
        shutil.rmtree(target)
        raise
    return {'state': 'restored-not-running', 'installation_id': expected_id, 'directory': str(target)}

if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('backup'); make.add_argument('root', type=Path); make.add_argument('destination', type=Path)
    recover = sub.add_parser('restore'); recover.add_argument('archive', type=Path); recover.add_argument('target', type=Path); recover.add_argument('--installation-id', required=True)
    args = parser.parse_args()
    print(json.dumps(backup(args.root, args.destination) if args.command == 'backup' else restore(args.archive, args.target, args.installation_id)))
