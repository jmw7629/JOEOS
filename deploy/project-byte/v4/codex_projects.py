"""Private, administrator-configured project identities for the Codex workspace.

The browser chooses an opaque key; repository paths and publication authority
are loaded once from owner-private configuration, never from a task request.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat


KEY = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z')
LIMIT = 256 * 1024


class ProjectError(ValueError):
    pass


def _text(value, limit, *, multiline=False):
    if (not isinstance(value, str) or len(value.encode()) > limit
            or any((ord(c) < 32 and not (multiline and c in '\n\t')) or ord(c) == 127 for c in value)):
        raise ProjectError('Project text is invalid')
    return value


def _branch(value):
    if value is None:
        return
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,159}', value)
            or '..' in value or value.endswith('.')
            or any(p in ('', '.') or p.endswith('.lock') for p in value.split('/'))):
        raise ProjectError('Project base branch is invalid')


@dataclass(frozen=True)
class Project:
    key: str
    label: str
    repo: str | None
    source: Path | None
    base_branch: str | None
    mode: str
    publication: bool
    detail: str
    context: str = ''

    def __post_init__(self):
        if not isinstance(self.key, str) or not KEY.fullmatch(self.key):
            raise ProjectError('Project key is invalid')
        if not _text(self.label, 120).strip():
            raise ProjectError('Project label is required')
        _text(self.detail, 1200)
        _text(self.context, 20000, multiline=True)
        if self.repo is not None and (not isinstance(self.repo, str) or not REPO.fullmatch(self.repo)
                                     or self.repo.split('/')[1] in ('.', '..')):
            raise ProjectError('Project repository is invalid')
        _branch(self.base_branch)
        if self.mode not in ('execute', 'observe', 'blocked') or type(self.publication) is not bool:
            raise ProjectError('Project execution mode is invalid')
        if self.source is not None:
            path = Path(self.source)
            if not path.is_absolute() or path.is_symlink() or path.resolve() != path or not path.is_dir():
                raise ProjectError('Project source must be an existing canonical absolute directory')
            object.__setattr__(self, 'source', path)
        if self.mode == 'execute' and self.source is None:
            raise ProjectError('Executable projects require a verified source')
        if self.publication and (self.mode != 'execute' or not self.repo or not self.base_branch):
            raise ProjectError('Publication requires an executable registered repository and base branch')

    @property
    def fingerprint(self):
        identity = [self.key, self.repo, str(self.source) if self.source else None, self.base_branch]
        return hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()


def default_project(source, *, publication=False, base_branch='project-byte-deploy'):
    return Project('joeos', 'PRFKT_PROJECT', 'jmw7629/JOEOS', Path(source).resolve(),
                   base_branch, 'execute', publication, '')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProjectError('Duplicate project configuration field')
        result[key] = value
    return result


def load_projects(path, default, *, state):
    """Read a private immutable registry, preserving the existing JO EOS identity."""
    if not path:
        return (default,)
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ProjectError('Project registry must have a canonical absolute path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > LIMIT):
            raise ProjectError('Project registry must be an owner-private regular file')
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ProjectError('Project registry is too large')
    try:
        data = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError('Project registry JSON is invalid') from error
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'projects'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['projects'], list) or not 1 <= len(data['projects']) <= 32):
        raise ProjectError('Project registry schema is invalid')
    projects, keys, sources, repositories = [], set(), set(), set()
    fields = {'key', 'label', 'repo', 'source', 'base_branch', 'mode', 'publication', 'detail', 'context'}
    workspace_root = Path(state).resolve() / 'workspaces'
    for value in data['projects']:
        if not isinstance(value, dict) or set(value) != fields:
            raise ProjectError('Project configuration fields are invalid')
        project = Project(**value)
        if project.key in keys:
            raise ProjectError('Project keys must be unique')
        keys.add(project.key)
        if project.source:
            if (project.source == workspace_root or workspace_root in project.source.parents
                    or project.source == Path(state).resolve()):
                raise ProjectError('Project source cannot be task execution storage')
            if project.source in sources:
                raise ProjectError('A source cannot have competing project identities')
            sources.add(project.source)
        if project.repo:
            identity = project.repo.lower()
            if identity in repositories:
                raise ProjectError('A repository cannot have competing project identities')
            repositories.add(identity)
        if project.key == 'joeos' and project.fingerprint != default.fingerprint:
            raise ProjectError('The existing JO EOS project identity must be preserved')
        projects.append(project)
    if 'joeos' not in keys:
        raise ProjectError('The existing JO EOS project is required')
    return tuple(projects)
