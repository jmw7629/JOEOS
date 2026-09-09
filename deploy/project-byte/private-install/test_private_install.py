from __future__ import annotations
import io
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from urllib import request, error
from prepare import prepare
from recovery import backup, restore

class PrivateInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.processes = []
    def tearDown(self):
        for process in self.processes:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
            if process.stdout: process.stdout.close()
            if process.stderr: process.stderr.close()
        self.temp.cleanup()
    def start(self, root):
        process = subprocess.Popen([sys.executable, str(root / 'runtime.py'), '--port', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, 'OPENAI_API_KEY': 'test-must-not-inherit', 'PRFKT_CODEX_STATE': '/bad/inherited/state', 'PRFKT_CODEX_REPO': '/bad/inherited/repo'})
        self.processes.append(process)
        readable, _, _ = select.select([process.stdout], [], [], 15)
        if not readable: self.fail('Runtime did not start')
        line = process.stdout.readline()
        if not line: self.fail(process.stderr.read())
        return process, json.loads(line)['url'].rstrip('/')
    def call(self, url, path, key='', method='GET', body=None, headers=None):
        hdr = {'X-Access-Key': key, **(headers or {})}
        data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body).encode())
        if data: hdr.setdefault('Content-Type', 'application/json')
        req = request.Request(url + path, data=data, method=method, headers=hdr)
        try: response = request.urlopen(req, timeout=5)
        except error.HTTPError as e: response = e
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if 'json' in response.headers.get('Content-Type','') and raw else raw
    def test_two_installations_are_empty_isolated_and_recoverable(self):
        a, b = self.root/'a', self.root/'b'
        ma = prepare(a, 'Workspace A', 'Alice'); mb = prepare(b, 'Workspace B', 'Bob')
        ka = (a/'private/owner.key').read_text(); kb = (b/'private/owner.key').read_text()
        self.assertNotEqual(ka, kb); self.assertNotEqual(ma['installation_id'], mb['installation_id'])
        pa, ua = self.start(a); pb, ub = self.start(b)
        for url, key, other, name in [(ua,ka,kb,'Alice'),(ub,kb,ka,'Bob')]:
            self.assertEqual(self.call(url,'/api/session')[0],401)
            self.assertEqual(self.call(url,'/api/session',other)[0],401)
            self.assertEqual(self.call(url,'/api/session',key)[1]['name'],name)
            for collection in ('tasks','projects','models','agents','runs','team','chat'):
                code, data = self.call(url,'/api/'+collection,key)
                self.assertEqual(code,200); self.assertFalse(next(iter(data.values())))
            self.assertFalse(self.call(url,'/healthz')[1]['ai_ops'])
            self.assertEqual(self.call(url,'/api/codex-workspace',key)[1]['configured'],False)
            self.assertEqual(self.call(url,'/api/codex-workspace/message',key,'POST',{'message':'test'})[0],503)
            self.assertEqual(self.call(url,'/uploads/example.txt')[0],401)
        code, _ = self.call(ua,'/api/settings',ka,'POST',{'general':{'density':'compact'}})
        self.assertEqual(code,200)
        self.assertEqual(self.call(ub,'/api/settings',kb)[1]['settings']['general']['density'],'comfortable')
        self.assertEqual(self.call(ua,'/api/settings',ka,'POST',{}, {'Origin':'https://different.invalid'})[0],403)
        code, task = self.call(ua,'/api/tasks',ka,'POST',{'title':'Private A task','project':'Only A','status':'Backlog','priority':'High'})
        self.assertEqual(code,201)
        code, attachment = self.call(ua,'/api/upload',ka,'POST',b'private A attachment',{'Content-Type':'text/plain','X-Task-ID':task['id'],'X-Filename':'evidence.txt'})
        self.assertEqual(code,201)
        self.assertEqual(self.call(ua,attachment['url'])[0],401)
        self.assertEqual(self.call(ub,attachment['url'],kb)[0],404)
        self.assertEqual(self.call(ua,attachment['url'],ka)[1],b'private A attachment')
        self.assertEqual(self.call(ub,'/api/tasks',kb)[1]['tasks'],[])
        with self.assertRaises(RuntimeError): backup(a,self.root/'busy.tgz')
        pa.terminate(); pa.wait(timeout=10)
        # Data-bearing recovery fixture stays inside this disposable installation.
        (a/'state/uploads/evidence.txt').write_text('installation A evidence')
        os.chmod(a/'state/uploads/evidence.txt',0o600)
        with sqlite3.connect(a/'state/kanban.db') as db:
            for table in ('tasks','projects','models','agents','collaborators','runs','chat_messages','notifications'):
                self.assertEqual(db.execute('select count(*) from '+table).fetchone()[0],1 if table in ('tasks','projects') else 0)
        archive = self.root/'a.tgz'; result = backup(a,archive)
        with self.assertRaises(ValueError): restore(archive,self.root/'wrong',mb['installation_id'])
        recovered = self.root/'recovered'; restore(archive,recovered,ma['installation_id'])
        self.assertEqual((recovered/'private/owner.key').read_text(),ka)
        self.assertEqual((recovered/'state/uploads/evidence.txt').read_text(),'installation A evidence')
        _, url = self.start(recovered)
        self.assertEqual(self.call(url,'/api/settings',ka)[1]['settings']['general']['density'],'compact')
        self.assertEqual(self.call(url,'/api/tasks',ka)[1]['tasks'][0]['title'],'Private A task')
        self.assertEqual(self.call(url,attachment['url'],ka)[1],b'private A attachment')
        pb.terminate(); pb.wait(timeout=10)
        _, blank = self.start(b)
        self.assertEqual(self.call(blank,'/api/tasks',kb)[1]['tasks'],[])
        self.assertEqual(self.call(blank,'/api/projects',kb)[1]['projects'],[])
        self.assertEqual(self.call(url,'/api/session',kb)[0],401)
        self.assertEqual(self.call(url,'/app/backend.py',ka)[0],404)
        self.assertEqual(self.call(url,'/private/owner.key',ka)[0],404)
    def test_existing_targets_symlinks_tampering_and_missing_keys_fail_closed(self):
        target = self.root/'a'; prepare(target)
        with self.assertRaises(FileExistsError): prepare(target)
        link = self.root/'link'; link.symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError): prepare(link/'child')
        (target/'app/home.js').write_text('tampered')
        p = subprocess.run([sys.executable,str(target/'runtime.py')],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0); self.assertIn('checksum mismatch',p.stderr)
        other = self.root/'other'; prepare(other); (other/'private/owner.key').unlink()
        p = subprocess.run([sys.executable,str(other/'runtime.py')],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0); self.assertFalse((other/'private/owner.key').exists())
        bad = self.root/'bad.tgz'
        with tarfile.open(bad,'w:gz') as tar:
            item=tarfile.TarInfo('../escape'); item.size=1; tar.addfile(item,io.BytesIO(b'x'))
        with self.assertRaises(ValueError): restore(bad,self.root/'bad-restore','x'*32)
        self.assertFalse((self.root/'escape').exists())

if __name__ == '__main__': unittest.main()
