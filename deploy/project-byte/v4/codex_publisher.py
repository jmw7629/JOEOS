"""Trusted, one-shot publication of an owner-reviewed frozen snapshot.

The worker never receives this module's private files, Git metadata, or GitHub
credentials. Preparation performs local plumbing only. Publication requires the
controller's exact approval and never resumes an uncertain external operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import threading
import time
import uuid

from codex_sandbox import _process


REPOSITORY = 'jmw7629/JOEOS'
REPOSITORY_NAME = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}\Z')
ID = re.compile(r'[0-9a-f]{32}\Z')
OBJECT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
MAX_FILE = 16 * 1024 * 1024
MAX_PATCH = 8 * 1024 * 1024
MAX_TREE = 128 * 1024 * 1024
MAX_REVIEW = 2 * 1024 * 1024
MAX_FILES = 20000


class PublisherError(RuntimeError):
    """A bounded error suitable for the controller, without command output."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private_directory(path, create=False):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise PublisherError('Publication storage must be a private absolute directory')
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PublisherError('Publication storage must be owner-private')
    return path.resolve(strict=True)


def read_private(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > limit):
            raise PublisherError('Frozen publication data is unavailable')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise PublisherError('Frozen publication data exceeds its limit')
        return data


def atomic(path, data):
    temporary = path.with_name('.write-' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value.encode()) > 2048 or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ('', '.', '..', '.git') or len(part.encode()) > 255 for part in value.split('/'))
            or value.split('/')[0] in ('.sandbox-home', '.sandbox-tmp')):
        raise PublisherError('The snapshot contains an invalid publication path')
    return value


class Publisher:
    def __init__(self, state, trusted_repo, repo=REPOSITORY, *, base_branch, registered_repo=None):
        # Registration is trusted application configuration, never a tool or
        # request argument. Legacy callers remain limited to the original repo.
        allowed = REPOSITORY if registered_repo is None else registered_repo
        if (not isinstance(repo, str) or not isinstance(allowed, str)
                or not REPOSITORY_NAME.fullmatch(repo) or not REPOSITORY_NAME.fullmatch(allowed)
                or repo.split('/')[1] in ('.', '..') or repo != allowed):
            raise PublisherError('Only the exact registered GitHub repository can be published')
        if (not isinstance(base_branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,159}', base_branch)
                or '..' in base_branch or any(part in ('', '.') or part.endswith('.lock') for part in base_branch.split('/'))
                or base_branch.endswith('.')):
            raise PublisherError('A fixed valid base branch is required')
        self.state = private_directory(state, create=True)
        source = Path(trusted_repo)
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            raise PublisherError('A trusted source checkout is required')
        self.source = source.resolve(strict=True)
        if self.state == self.source or self.state.is_relative_to(self.source):
            raise PublisherError('Publication metadata must remain outside the source checkout')
        self.repo, self.base_branch = repo, base_branch
        self.git = shutil.which('git')
        self.gh = shutil.which('gh')
        if not self.git:
            raise PublisherError('The trusted Git executable is unavailable')
        self.git = str(Path(self.git).resolve(strict=True))
        if self.gh:
            self.gh = str(Path(self.gh).resolve(strict=True))
        self.auth_config = os.environ.get('GH_CONFIG_DIR') or str(Path.home() / '.config' / 'gh')
        self.lock = threading.RLock()
        self.stop_context = ContextVar('publication_stop', default=None)
        self.home = private_directory(self.state / 'home', create=True)
        self.template = private_directory(self.state / 'empty-template', create=True)

    @contextmanager
    def _locked(self):
        with self.lock:
            fd = os.open(self.state / 'publish.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                    raise PublisherError('Publication lock is unavailable')
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _environment(self, auth=False, *, git_protocols=''):
        if git_protocols not in ('', 'file', 'https'):
            raise PublisherError('Unsupported trusted Git transport')
        env = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':str(self.home), 'LANG':'C.UTF-8', 'LC_ALL':'C',
               'GIT_CONFIG_GLOBAL':os.devnull, 'GIT_CONFIG_SYSTEM':os.devnull, 'GIT_CONFIG_NOSYSTEM':'1',
               'GIT_NO_REPLACE_OBJECTS':'1', 'GIT_NO_LAZY_FETCH':'1', 'GIT_ALLOW_PROTOCOL':git_protocols,
               'GIT_TERMINAL_PROMPT':'0', 'GIT_ASKPASS':'/usr/bin/false', 'SSH_ASKPASS':'/usr/bin/false',
               'GIT_AUTHOR_NAME':'PRFKT_PROJECT', 'GIT_COMMITTER_NAME':'PRFKT_PROJECT',
               'GIT_AUTHOR_EMAIL':'prfkt-project@users.noreply.github.com',
               'GIT_COMMITTER_EMAIL':'prfkt-project@users.noreply.github.com',
               'GH_HOST':'github.com', 'GH_PROMPT_DISABLED':'1', 'GH_NO_UPDATE_NOTIFIER':'1',
               'GH_CONFIG_DIR':self.auth_config if auth else str(self.home), 'NO_COLOR':'1'}
        # This environment is used only by trusted publisher subprocesses.
        if auth:
            for key in ('GH_TOKEN', 'GITHUB_TOKEN'):
                if os.environ.get(key):
                    env[key] = os.environ[key]
        return env

    def _execute(self, args, *, auth=False, stop_event=None, output_limit=MAX_PATCH, git_protocols=''):
        stop_event = stop_event if stop_event is not None else self.stop_context.get()
        if stop_event is not None and stop_event.is_set():
            raise PublisherError('Publication was stopped before the next command')
        result = _process(args, cwd=self.state, env=self._environment(auth, git_protocols=git_protocols), timeout=120,
                          output_limit=output_limit, stop_event=stop_event)
        if (result['exit_code'] != 0 or result['timed_out'] or result['stopped'] or result['truncated']):
            raise PublisherError('A trusted publication command did not complete successfully')
        return result['stdout']

    def _git(self, arguments, folder=None, *, auth=False, stop_event=None, output_limit=MAX_PATCH, transport=None):
        args = [self.git, '-c', 'core.hooksPath='+os.devnull, '-c', 'core.fsmonitor=false',
                '-c', 'core.attributesFile='+os.devnull, '-c', 'commit.gpgSign=false',
                '-c', 'tag.gpgSign=false', '-c', 'protocol.ext.allow=never',
                '-c', 'credential.helper=']
        if auth:
            args += ['-c', 'credential.https://github.com.helper=!'+shlex.quote(self.gh)+' auth git-credential']
        args += ['--git-dir='+str(folder / 'git')] if folder else ['-C', str(self.source)]
        # All inspection denies transports, including source-local overrides.
        # Only the explicit fixed-source fetch and fixed-target push opt in.
        return self._execute(args + arguments, auth=auth, stop_event=stop_event, output_limit=output_limit,
                             git_protocols=transport if transport is not None else '')

    def _base(self):
        value = self._git(['rev-parse', '--verify', 'refs/heads/'+self.base_branch+'^{commit}']).decode().strip()
        if not OBJECT.fullmatch(value):
            raise PublisherError('The configured base commit is unavailable')
        return value

    def _source_remote(self):
        # Resolve fetch and push URLs without contacting a remote. This also
        # catches source-local insteadOf/pushInsteadOf rewrites and pushurl
        # overrides; publication itself always uses our fixed HTTPS target.
        allowed = {prefix + self.repo + suffix for prefix in
                   ('https://github.com/', 'git@github.com:', 'ssh://git@github.com/')
                   for suffix in ('', '.git')}
        for direction in ([], ['--push']):
            raw = self._git(['remote', 'get-url', *direction, '--all', 'origin'], output_limit=4096)
            try:
                urls = raw.decode('utf-8').splitlines()
            except UnicodeError:
                raise PublisherError('The source origin does not match the registered repository') from None
            if len(urls) != 1 or urls[0] not in allowed:
                raise PublisherError('The source origin does not match the registered repository')

    def _github_url(self, value, kind):
        return isinstance(value, str) and bool(re.fullmatch(
            r'https://github\.com/' + re.escape(self.repo) + '/' + kind + r'/[1-9][0-9]*', value))

    def _result(self, result):
        if (not isinstance(result, dict) or set(result) != {'issue_url', 'pull_request_url'}
                or not self._github_url(result['issue_url'], 'issues')
                or not self._github_url(result['pull_request_url'], 'pull')):
            raise PublisherError('The saved publication result does not match the registered repository')
        return result

    def _index(self, folder, base):
        entries = {}
        for raw in self._git(['ls-tree', '-rz', '--full-tree', base], folder).split(b'\0'):
            if not raw:
                continue
            metadata, name = raw.split(b'\t', 1)
            mode, kind, object_id = metadata.decode('ascii').split(' ')
            path = relative_path(name.decode('utf-8'))
            if mode not in ('100644', '100755') or kind != 'blob' or not OBJECT.fullmatch(object_id):
                raise PublisherError('The source contains unsupported links or special entries')
            entries[path] = (mode, object_id)
            if len(entries) > MAX_FILES:
                raise PublisherError('The source contains too many publication files')
        return entries

    def prepare(self, workspace, run, args, stop_event=None):
        token = self.stop_context.set(stop_event)
        try:
            return self._prepare(workspace,run,args)
        finally:
            self.stop_context.reset(token)

    def _check_stop(self):
        stop = self.stop_context.get()
        if stop is not None and stop.is_set():
            raise PublisherError('Publication preparation was stopped')

    def _prepare(self, workspace, run, args):
        self._check_stop()
        if (not isinstance(run, str) or not ID.fullmatch(run) or not isinstance(args, dict)
                or set(args) != {'title', 'body'} or not isinstance(args['title'], str)
                or not 0 < len(args['title'].strip()) <= 160 or any(ord(c) < 32 for c in args['title'])
                or not isinstance(args['body'], str) or not 0 < len(args['body']) <= 12000 or '\0' in args['body']):
            raise PublisherError('A bounded title, description and valid run are required')
        with self._locked(), workspace.lock:
            folder = self.state / run
            if folder.exists() or folder.is_symlink():
                raise PublisherError('This run already has a frozen publication; use its existing review')
            self._source_remote()
            delta, inventory = workspace.diff(), workspace.snapshot()
            self._check_stop()
            base = self._base()
            if (delta.get('run_id') != inventory.get('run_id') or inventory.get('run_id') != workspace.run_id
                    or not ID.fullmatch(str(workspace.run_id))
                    or delta.get('base_commit') != base or inventory.get('base_commit') != base):
                raise PublisherError('The workspace no longer matches the configured base branch')
            changes = delta.get('changes')
            if not isinstance(changes, list) or len(changes) > MAX_FILES:
                raise PublisherError('The changed-file inventory is invalid')
            if not changes:
                raise PublisherError('There are no changes to publish')
            frozen_files, seen, total = {}, set(), 0
            for change in changes:
                self._check_stop()
                path = relative_path(change.get('path'))
                if path in seen or change.get('status') not in ('added','modified','deleted'):
                    raise PublisherError('The changed-file inventory is inconsistent')
                seen.add(path)
                if change['status'] == 'deleted':
                    if path in inventory['files'] or change.get('after_sha256') is not None:
                        raise PublisherError('A deleted file is still present in the snapshot')
                    continue
                entry = inventory['files'].get(path)
                data = workspace.read(path)
                if (not isinstance(data, bytes) or len(data) > MAX_FILE or not entry
                        or entry.get('sha256') != sha(data) or change.get('after_sha256') != sha(data)
                        or type(change.get('after_executable')) is not bool
                        or entry.get('executable') != change['after_executable']):
                    raise PublisherError('A workspace file changed while freezing the review')
                frozen_files[path] = data
                total += len(data)
                if total > MAX_TREE:
                    raise PublisherError('The frozen files exceed the publication size limit')
            if workspace.snapshot() != inventory:
                raise PublisherError('The workspace changed while freezing the review')
            folder.mkdir(mode=0o700)
            try:
                objects = private_directory(folder / 'files', create=True)
                empty_worktree = private_directory(folder / 'empty-worktree', create=True)
                object_format = self._git(['rev-parse','--show-object-format']).decode().strip()
                if object_format not in ('sha1','sha256'):
                    raise PublisherError('The source uses an unsupported Git object format')
                self._execute([self.git, 'init', '--bare', '--object-format='+object_format,
                               '--template='+str(self.template), str(folder / 'git')])
                # Only the fixed trusted local checkout can supply base objects.
                self._git(['-c','protocol.file.allow=always','fetch','--no-tags','--no-recurse-submodules',
                           '--no-write-fetch-head',str(self.source),base], folder, transport='file')
                self._git(['read-tree',base],folder)
                before = self._index(folder,base)
                for change in changes:
                    self._check_stop()
                    path = change['path']; prior = before.get(path)
                    if change['status'] == 'added':
                        if prior or change.get('before_sha256') is not None:
                            raise PublisherError('An added file conflicts with the trusted base')
                    elif (not prior or change.get('before_executable') != (prior[0] == '100755')
                          or sha(self._git(['cat-file','blob',prior[1]],folder,output_limit=MAX_FILE)) != change.get('before_sha256')):
                        raise PublisherError('The reviewed file baseline does not match Git')
                    if change['status'] == 'deleted':
                        # Git's named-path deletion asks for a worktree even
                        # though --force-remove only changes the index. Bind an
                        # empty private directory; no source file is checked out.
                        self._git(['--work-tree='+str(empty_worktree),'update-index','--force-remove','--',path],folder)
                    else:
                        data = frozen_files[path]; file = objects / sha(data)
                        if not file.exists():
                            atomic(file,data)
                        object_id = self._git(['hash-object','--no-filters','-w','--',str(file)],folder).decode().strip()
                        if not OBJECT.fullmatch(object_id):
                            raise PublisherError('A frozen file could not be recorded in Git')
                        mode = '100755' if change['after_executable'] else '100644'
                        self._git(['update-index','--add','--cacheinfo',mode+','+object_id+','+path],folder)
                tree = self._git(['write-tree'],folder).decode().strip()
                patch = self._git(['diff','--cached','--binary','--full-index','--no-ext-diff','--no-textconv',base,'--'],folder)
                if not patch or not OBJECT.fullmatch(tree):
                    raise PublisherError('There are no reviewable Git changes')
                public = {'repo':self.repo,'base':self.base_branch,'base_commit':base,'branch':'prfkt/task-'+run,
                          'patch_sha256':sha(patch),'changed_files':changes,'title':args['title'].strip(),'body':args['body'],
                          'run_id':run,'workspace_id':workspace.run_id,'automatic_merge':False}
                review = ('GitHub publication review\n'+json.dumps(public,indent=2,ensure_ascii=False)+
                          '\n\nComplete frozen Git patch (including binary and mode changes):\n\n'+patch.decode('utf-8'))
                if len(review.encode()) > MAX_REVIEW:
                    raise PublisherError('The complete review exceeds 2 MiB; split this publication into smaller tasks')
                manifest = {'public':public,'tree':tree,'review':review}
                raw = encoded(manifest)
                atomic(folder / 'patch',patch); atomic(folder / 'manifest.json',raw)
                atomic(folder / 'ledger.json',encoded({'state':'prepared','step':'review','updated_at':time.time()}))
                return {'id':run,'manifest_sha256':sha(raw),'public':public,'review':review}
            except BaseException:
                shutil.rmtree(folder)
                raise

    def _frozen(self, frozen):
        if not isinstance(frozen,dict) or set(frozen) != {'id','manifest_sha256','public','review'} or not ID.fullmatch(str(frozen.get('id',''))):
            raise PublisherError('Invalid frozen publication')
        folder = private_directory(self.state / frozen['id'])
        raw = read_private(folder / 'manifest.json', MAX_PATCH*3)
        manifest = json.loads(raw)
        if (sha(raw) != frozen['manifest_sha256'] or manifest['public'] != frozen['public']
                or manifest['review'] != frozen['review'] or manifest['public']['repo'] != self.repo
                or manifest['public']['base'] != self.base_branch
                or manifest['public']['branch'] != 'prfkt/task-'+frozen['id']):
            raise PublisherError('The frozen publication changed after review')
        return folder,manifest

    def publish(self, frozen, stop_event=None):
        with self._locked():
            folder,manifest = self._frozen(frozen)
            ledger = json.loads(read_private(folder / 'ledger.json', 16384))
            if ledger['state'] == 'completed':
                return self._result(ledger['result'])
            if ledger['state'] != 'prepared':
                ledger.update(state='unknown',updated_at=time.time()); atomic(folder / 'ledger.json',encoded(ledger))
                raise PublisherError('Publication may have partially completed. Inspect GitHub; this request will not run again')
            if not self.gh:
                raise PublisherError('The trusted GitHub CLI is unavailable')
            if stop_event is not None and stop_event.is_set():
                raise PublisherError('Publication stopped before any GitHub action')
            self._source_remote()
            public = manifest['public']; patch = read_private(folder / 'patch', MAX_PATCH)
            current = self._git(['diff','--cached','--binary','--full-index','--no-ext-diff','--no-textconv',public['base_commit'],'--'],folder)
            if (sha(patch) != public['patch_sha256'] or current != patch
                    or self._git(['write-tree'],folder).decode().strip() != manifest['tree']
                    or self._base() != public['base_commit']):
                raise PublisherError('The exact reviewed snapshot or configured base changed')
            issue_body = folder / 'issue-body.txt'; pr_body = folder / 'pr-body.txt'; commit_body = folder / 'commit-message.txt'
            provenance = '\n\nPRFKT_PROJECT native run: '+public['run_id']+'\nFrozen patch SHA-256: '+public['patch_sha256']
            atomic(issue_body,(public['body']+provenance+'\n\nThis issue records the prepared native workspace change.').encode())
            atomic(commit_body,(public['title']+'\n').encode())
            def record(step, **values):
                ledger.update(state='dispatching',step=step,updated_at=time.time(),**values)
                atomic(folder / 'ledger.json',encoded(ledger))
            try:
                # Durable intent precedes the first external write. Any uncertainty
                # from this point is terminal; even a restart cannot replay it.
                record('create_issue')
                issue = self._execute([self.gh,'issue','create','--repo',self.repo,'--title','[OC] '+public['title'],
                                       '--body-file',str(issue_body)],auth=True,stop_event=stop_event).decode().strip()
                if not self._github_url(issue, 'issues'):
                    raise PublisherError('GitHub issue creation did not return the expected repository URL')
                record('create_commit',issue_url=issue)
                commit = self._git(['commit-tree',manifest['tree'],'-p',public['base_commit'],'-F',str(commit_body)],folder,stop_event=stop_event).decode().strip()
                if not OBJECT.fullmatch(commit):
                    raise PublisherError('The reviewed commit could not be created')
                record('push_branch',commit=commit)
                # An empty expected value is a create-only lease: an existing
                # remote branch is rejected, even when a fast-forward is possible.
                self._git(['push','--porcelain','--no-verify','--force-with-lease=refs/heads/'+public['branch']+':',
                          'https://github.com/'+self.repo+'.git',commit+':refs/heads/'+public['branch']],
                          folder,auth=True,stop_event=stop_event,transport='https')
                record('create_pr')
                atomic(pr_body,(public['body']+'\n\nRelated issue: '+issue+provenance).encode())
                pr = self._execute([self.gh,'pr','create','--repo',self.repo,'--base',self.base_branch,'--head',public['branch'],
                                    '--title',public['title'],'--body-file',str(pr_body)],auth=True,stop_event=stop_event).decode().strip()
                if not self._github_url(pr, 'pull'):
                    raise PublisherError('GitHub PR creation did not return the expected repository URL')
                result = {'issue_url':issue,'pull_request_url':pr}
                ledger.update(state='completed',step='complete',result=result,updated_at=time.time())
                atomic(folder / 'ledger.json',encoded(ledger))
                return result
            except BaseException:
                ledger.update(state='unknown',updated_at=time.time())
                atomic(folder / 'ledger.json',encoded(ledger))
                raise PublisherError('Publication did not finish with a verified result. Inspect GitHub before starting new work') from None
