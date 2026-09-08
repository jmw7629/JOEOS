"""Disposable local Git and fake-GitHub tests. Never contact any remote service."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from codex_publisher import Publisher, PublisherError, REPOSITORY
from codex_sandbox import SandboxWorkspace, SandboxError


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.source = self.root / 'source'; self.source.mkdir()
        self.git = shutil.which('git')
        self.git_env = {**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull,
                        'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid',
                        'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid'}
        self.command(['init','-q','-b','main'])
        (self.source/'README.md').write_text('Original\n')
        (self.source/'delete.txt').write_text('Delete me\n')
        (self.source/'script.sh').write_text('echo fixture\n')
        (self.source/'.gitattributes').write_text('*.md filter=untrusted\n')
        self.command(['add','.']);self.command(['commit','-qm','Fixture baseline'])
        self.base = self.command(['rev-parse','HEAD']).decode().strip()
        self.command(['config','filter.untrusted.clean','touch '+str(self.root/'FILTER_RAN')])
        self.command(['config','filter.untrusted.smudge','touch '+str(self.root/'FILTER_RAN')])
        (self.source/'.env').write_text('UNTRACKED_HOST_SECRET')
        self.factory = SandboxWorkspace(self.root/'runs',self.source)
        self.run = uuid.uuid4().hex; self.workspace = self.factory.create(self.run)
        self.publisher = Publisher(self.root/'publications',self.source,base_branch='main')
        self.publisher.gh = '/fixture/gh'
        self.calls = []; self.failure = None; self.stop_on_issue = None
        self.real_execute = self.publisher._execute
        self.addCleanup(patch.stopall)
        patch.object(self.publisher,'_execute',side_effect=self.fake_execute).start()

    def command(self,args,folder=None):
        result = subprocess.run([self.git,'-c','core.hooksPath='+os.devnull,*(['--git-dir='+str(folder/'git')] if folder else ['-C',str(self.source)]),*args],env=self.git_env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return result.stdout

    def fake_execute(self,args,**options):
        self.calls.append((list(args),dict(options)))
        if args[0] == '/fixture/gh':
            if self.failure == 'issue' and args[1:3] == ['issue','create']:
                raise PublisherError('Synthetic uncertain issue response')
            body = Path(args[args.index('--body-file')+1]).read_text()
            self.assertIn('Frozen patch SHA-256:',body)
            self.assertNotIn('PROJECT_BYTE_AGENT_HINT:',body)
            self.assertEqual(args[args.index('--repo')+1],REPOSITORY)
            if args[1:3] == ['issue','create']:
                if self.stop_on_issue:
                    self.stop_on_issue.set()
                return b'https://github.com/jmw7629/JOEOS/issues/71\n'
            self.assertEqual(args[1:3],['pr','create'])
            return b'https://github.com/jmw7629/JOEOS/pull/72\n' if self.failure != 'url' else b'https://github.com/someone/else/pull/72\n'
        if 'push' in args:
            if self.failure == 'push':
                raise PublisherError('Synthetic uncertain push response')
            self.assertIn('https://github.com/jmw7629/JOEOS.git',args)
            self.assertIn('--force-with-lease=refs/heads/prfkt/task-'+self.run+':',args)
            return b''
        return self.real_execute(args,**options)

    def changed(self):
        self.workspace.write('README.md','Reviewed change\n')
        self.workspace.write('binary.bin',b'\x00\xff\x01binary-fixture')
        self.workspace.write('script.sh','echo fixture\n',executable=True)
        (self.workspace.path/'delete.txt').unlink()

    def prepare(self):
        return self.publisher.prepare(self.workspace,self.run,{'title':'Publish bounded fixture','body':'Implement the reviewed fixture.\n\nValidation: local fixture only.'})

    def external(self):
        return [args for args,options in self.calls if options.get('auth')]

    def test_frozen_binary_modes_deletions_no_filters_hooks_or_host_untracked_files(self):
        self.changed(); frozen=self.prepare()
        self.assertEqual(self.external(),[],'preparation cannot make an authenticated external call')
        self.assertIn('GIT binary patch',frozen['review']);self.assertIn('+Reviewed change',frozen['review'])
        self.assertIn('old mode 100644',frozen['review']);self.assertIn('new mode 100755',frozen['review'])
        self.assertIn('deleted file mode',frozen['review']);self.assertNotIn('UNTRACKED_HOST_SECRET',frozen['review'])
        self.assertEqual(frozen['public']['base_commit'],self.base)
        self.assertFalse((self.root/'FILTER_RAN').exists())
        self.assertFalse((self.workspace.path/'.git').exists())
        self.assertNotIn('.env',[change['path'] for change in frozen['public']['changed_files']])
        self.workspace.write('README.md','UNREVIEWED LATER CHANGE\n')
        result=self.publisher.publish(frozen)
        self.assertEqual(result,{'issue_url':'https://github.com/jmw7629/JOEOS/issues/71','pull_request_url':'https://github.com/jmw7629/JOEOS/pull/72'})
        folder=self.publisher.state/self.run;ledger=json.loads((folder/'ledger.json').read_text());commit=ledger['commit']
        self.assertEqual(self.command(['show',commit+':README.md'],folder),b'Reviewed change\n')
        self.assertEqual(self.command(['show',commit+':binary.bin'],folder),b'\x00\xff\x01binary-fixture')
        files=self.command(['ls-tree','-r',commit],folder).decode()
        self.assertNotIn('delete.txt',files);self.assertNotIn('.env',files);self.assertIn('100755 blob',files)
        self.assertEqual(self.command(['rev-parse','HEAD']).decode().strip(),self.base)
        actions=self.external();self.assertEqual(len(actions),3)
        self.assertEqual(actions[0][1:3],['issue','create']);self.assertIn('push',actions[1]);self.assertEqual(actions[2][1:3],['pr','create'])
        self.assertTrue(actions[0][actions[0].index('--title')+1].startswith('[OC] '))
        self.assertFalse(any('merge' in args or '--force' in args for args in actions))
        before=len(self.calls);self.assertEqual(self.publisher.publish(frozen),result);self.assertEqual(len(self.calls),before,'completed publication returns its durable result without replay')
        self.assertFalse((self.root/'FILTER_RAN').exists())

    def test_review_and_patch_tampering_fail_before_any_external_call(self):
        self.changed();frozen=self.prepare();tampered=copy.deepcopy(frozen);tampered['public']['title']='Unreviewed title'
        with self.assertRaises(PublisherError):self.publisher.publish(tampered)
        self.assertEqual(self.external(),[])
        (self.publisher.state/self.run/'patch').write_bytes(b'Unreviewed patch')
        with self.assertRaises(PublisherError):self.publisher.publish(frozen)
        self.assertEqual(self.external(),[])

    def test_advanced_configured_base_refuses_stale_publication(self):
        self.changed();frozen=self.prepare();self.command(['commit','--allow-empty','-qm','New base'])
        with self.assertRaises(PublisherError):self.publisher.publish(frozen)
        self.assertEqual(self.external(),[])

    def test_no_changes_and_wrong_base_create_no_review_or_issue(self):
        with self.assertRaisesRegex(PublisherError,'no changes'):self.prepare()
        self.assertFalse((self.publisher.state/self.run).exists())
        self.workspace.write('README.md','Change\n');self.command(['commit','--allow-empty','-qm','New base'])
        with self.assertRaisesRegex(PublisherError,'configured base'):self.prepare()
        self.assertEqual(self.external(),[])

    def test_persistent_conversation_workspace_binds_distinct_execution_run(self):
        workspace_id=self.workspace.run_id;self.run=uuid.uuid4().hex;self.changed();frozen=self.prepare()
        self.assertEqual(frozen['public']['workspace_id'],workspace_id)
        self.assertEqual(frozen['public']['run_id'],self.run)
        self.assertEqual(frozen['public']['branch'],'prfkt/task-'+self.run)

    def test_create_only_lease_cannot_overwrite_an_existing_remote_branch(self):
        self.changed();frozen=self.prepare();folder=self.publisher.state/self.run
        manifest=json.loads((folder/'manifest.json').read_text())
        commit=self.command(['commit-tree',manifest['tree'],'-p',self.base,'-m','Local lease fixture'],folder).decode().strip()
        remote=self.root/'remote.git'
        subprocess.run([self.git,'init','--bare','--template='+str(self.publisher.template),str(remote)],env=self.git_env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        branch='refs/heads/'+frozen['public']['branch']
        args=[self.git,'--git-dir='+str(folder/'git'),'push','--force-with-lease='+branch+':',str(remote),commit+':'+branch]
        first=subprocess.run(args,env=self.git_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(first.returncode,0,'create-only lease permits a new local remote branch')
        subprocess.run([self.git,'--git-dir='+str(remote),'update-ref',branch,self.base],env=self.git_env,check=True)
        denied=subprocess.run(args,env=self.git_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertNotEqual(denied.returncode,0,'even a possible fast-forward is rejected when the branch already exists')
        preserved=subprocess.run([self.git,'--git-dir='+str(remote),'rev-parse',branch],env=self.git_env,check=True,stdout=subprocess.PIPE)
        self.assertEqual(preserved.stdout.decode().strip(),self.base)
        self.assertEqual(self.external(),[],'the remote in this proof is a disposable local Git directory')

    def test_complete_review_limit_fails_before_owner_approval_or_external_calls(self):
        self.workspace.write('large-review.txt','Review line\n'*(220000))
        with self.assertRaisesRegex(PublisherError,'review exceeds 2 MiB'):self.prepare()
        self.assertFalse((self.publisher.state/self.run).exists());self.assertEqual(self.external(),[])

    def test_partial_or_uncertain_publication_is_terminal_even_after_restart(self):
        for failure in ('issue','push','url'):
            with self.subTest(failure=failure):
                if failure != 'issue':
                    self.run=uuid.uuid4().hex;self.workspace=self.factory.create(self.run)
                self.changed();frozen=self.prepare();self.failure=failure
                with self.assertRaises(PublisherError):self.publisher.publish(frozen)
                ledger=json.loads((self.publisher.state/self.run/'ledger.json').read_text());self.assertEqual(ledger['state'],'unknown')
                before=len(self.calls)
                with self.assertRaises(PublisherError):self.publisher.publish(frozen)
                self.assertEqual(len(self.calls),before)
                restarted=Publisher(self.publisher.state,self.source,base_branch='main');restarted.gh='/fixture/gh'
                with patch.object(restarted,'_execute',side_effect=AssertionError('Unknown work must never be retried')):
                    with self.assertRaises(PublisherError):restarted.publish(frozen)

    def test_crash_dispatch_intent_is_never_replayed(self):
        self.changed();frozen=self.prepare();ledger=self.publisher.state/self.run/'ledger.json'
        ledger.write_text(json.dumps({'state':'dispatching','step':'create_issue'}))
        before=len(self.calls)
        with self.assertRaises(PublisherError):self.publisher.publish(frozen)
        self.assertEqual(len(self.calls),before);self.assertEqual(json.loads(ledger.read_text())['state'],'unknown')

    def test_stop_before_first_write_and_after_issue(self):
        self.changed();frozen=self.prepare();stop=threading.Event();stop.set()
        with self.assertRaisesRegex(PublisherError,'before any GitHub'):self.publisher.publish(frozen,stop)
        self.assertEqual(self.external(),[])
        stop.clear();self.stop_on_issue=stop
        with self.assertRaises(PublisherError):self.publisher.publish(frozen,stop)
        self.assertEqual(len(self.external()),1,'a stop after issue creation prevents branch and PR creation')
        self.assertEqual(json.loads((self.publisher.state/self.run/'ledger.json').read_text())['state'],'unknown')

    def test_rejects_links_traversal_bad_repo_and_arbitrary_args(self):
        (self.workspace.path/'link').symlink_to(self.source/'.env')
        with self.assertRaises((SandboxError,PublisherError)):self.prepare()
        (self.workspace.path/'link').unlink();self.changed()
        with patch.object(self.workspace,'diff',return_value={'run_id':self.run,'base_commit':self.base,'changes':[{'path':'../secret','status':'added'}]}):
            with self.assertRaises(PublisherError):self.prepare()
        with self.assertRaises(PublisherError):Publisher(self.root/'else',self.source,repo='attacker/repo',base_branch='main')
        with self.assertRaises(PublisherError):Publisher(self.root/'else',self.source,base_branch='--upload-pack=bad')
        with self.assertRaises(PublisherError):self.publisher.prepare(self.workspace,self.run,{'title':'Test','body':'Test','repo':'attacker/repo'})
        self.assertEqual(self.external(),[])

    def test_credentials_exist_only_in_trusted_auth_environment(self):
        with patch.dict(os.environ,{'GH_TOKEN':'FIXTURE_SECRET','GITHUB_TOKEN':'SECOND_FIXTURE','GIT_CONFIG_GLOBAL':'/untrusted/config','LD_PRELOAD':'/untrusted/library','GIT_SSH_COMMAND':'untrusted-command'}):
            local=self.publisher._environment();auth=self.publisher._environment(True)
        self.assertNotIn('GH_TOKEN',local);self.assertNotIn('GITHUB_TOKEN',local)
        self.assertEqual(auth['GH_TOKEN'],'FIXTURE_SECRET');self.assertEqual(auth['GITHUB_TOKEN'],'SECOND_FIXTURE')
        for env in (local,auth):
            self.assertEqual(env['GIT_CONFIG_GLOBAL'],os.devnull);self.assertEqual(env['GH_HOST'],'github.com')
            self.assertNotIn('LD_PRELOAD',env);self.assertNotIn('GIT_SSH_COMMAND',env)
        self.changed();frozen=self.prepare();self.assertNotIn('FIXTURE_SECRET',frozen['review'])

    def test_local_source_uploadpack_hook_cannot_execute_during_freeze(self):
        marker=self.root/'UPLOADPACK_HOOK_RAN';hook=self.root/'uploadpack-hook.sh'
        hook.write_text('#!/bin/sh\nprintf hook > "'+str(marker)+'"\nexit 37\n');hook.chmod(0o700)
        self.command(['config','uploadpack.packObjectsHook',str(hook)])
        self.changed();self.prepare()
        self.assertFalse(marker.exists(),'source-local unprotected uploadpack hook is never executed')
        self.assertEqual(self.external(),[])

    def test_cancelled_preparation_never_invokes_git_or_creates_frozen_files(self):
        self.changed();stop=threading.Event();stop.set();before=len(self.calls)
        with self.assertRaisesRegex(PublisherError,'stopped'):
            self.publisher.prepare(self.workspace,self.run,{'title':'Cancelled','body':'Fixture'},stop_event=stop)
        self.assertEqual(len(self.calls),before);self.assertFalse((self.publisher.state/self.run).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
