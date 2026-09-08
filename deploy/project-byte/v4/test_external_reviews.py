import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
BYTE='jmw7629/stickdeath-byte'
VITROS='jmw7629/vitros-web-dashboard'

class ExternalReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir();self.calls=self.root/'calls'
        stub=types.ModuleType('backend');stub.H=object;stub.ROOT=self.root;stub.UPLOADS=self.root/'uploads'
        stub.sanitize=lambda value: re.sub(r'ghp_[A-Za-z0-9]{10,}','[REDACTED]',value or '')
        stub.con=lambda: (_ for _ in ()).throw(AssertionError('database must not be touched'))
        sys.modules['backend']=stub
        spec=importlib.util.spec_from_file_location('review_runtime',HERE/'runtime_server.py')
        self.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.mod)
        self.old_path=os.environ.get('PATH','');os.environ['PATH']=str(self.bin)+':'+self.old_path
        self.addCleanup(lambda:os.environ.__setitem__('PATH',self.old_path))
        self.mod.EXTERNAL_REVIEW_CACHE={'repositories':{},'at':0.0}

    def fake_gh(self,byte='[]',vitros='[]',fail=''):
        script=f'''#!/usr/bin/env python3
import json,sys
from pathlib import Path
Path({str(self.calls)!r}).open('a').write(' '.join(sys.argv[1:])+'\\n')
repo=sys.argv[sys.argv.index('--repo')+1] if '--repo' in sys.argv else ''
if repo in {fail.split(',')!r}: sys.exit(3)
if repo=={BYTE!r}: sys.stdout.write({byte!r})
elif repo=={VITROS!r}: sys.stdout.write({vitros!r})
else: sys.exit(9)
'''
        p=self.bin/'gh';p.write_text(script);p.chmod(0o700)
        self.mod.EXTERNAL_REVIEW_GH=str(p)

    def test_allowlisted_fresh_items_are_minimal_and_sanitized(self):
        byte=json.dumps([{'number':7,'title':'BYTE ghp_ABCDEFGHIJKL title','isDraft':True,'updatedAt':'2026-09-07T20:00:00Z','reviewDecision':'APPROVED','mergeStateStatus':'CLEAN','statusCheckRollup':[{'status':'COMPLETED','conclusion':'SUCCESS'},{'state':'SUCCESS'}],'body':'TOP SECRET','url':'https://evil.invalid'}])
        vitros=json.dumps([{'number':251,'title':'Header hardening','isDraft':False,'updatedAt':'2026-09-07T20:10:00Z','reviewDecision':'','mergeStateStatus':'CLEAN','statusCheckRollup':[{'status':'COMPLETED','conclusion':'FAILURE'},{'status':'IN_PROGRESS','conclusion':''}]}])
        self.fake_gh(byte,vitros)
        with mock.patch.object(self.mod.time,'time',return_value=1788813000.0): snap=self.mod.external_reviews_snapshot()
        self.assertEqual(snap['state'],'fresh');self.assertEqual(len(snap['items']),2)
        raw=json.dumps(snap);self.assertNotIn('TOP SECRET',raw);self.assertNotIn('evil.invalid',raw);self.assertNotIn('ghp_',raw)
        by={x['repo']:x for x in snap['items']}
        self.assertEqual(by[BYTE]['url'],'https://github.com/jmw7629/stickdeath-byte/pull/7')
        self.assertTrue(by[BYTE]['read_only']);self.assertEqual(by[BYTE]['checks']['passed'],2);self.assertEqual(by[VITROS]['checks']['failed'],1);self.assertEqual(by[VITROS]['checks']['pending'],1)
        calls=self.calls.read_text();self.assertIn('pr list --repo '+BYTE,calls);self.assertIn('pr list --repo '+VITROS,calls)
        for verb in ('create','merge','close','comment','review','edit'):self.assertNotIn(' '+verb+' ', ' '+calls+' ')
        self.assertNotIn('StickDeath-Infinity-',calls)

    def test_r3_is_not_an_observable_target(self):
        repos=[repo for _key,_project,repo in self.mod.EXTERNAL_REVIEW_REPOS]
        self.assertEqual(repos,[BYTE,VITROS]);self.assertNotIn('jmw7629/StickDeath-Infinity-',repos)

    def test_malformed_or_untrusted_output_is_unavailable(self):
        self.fake_gh('{}','not-json')
        snap=self.mod.external_reviews_snapshot();self.assertEqual(snap['state'],'unavailable');self.assertEqual(snap['items'],[])

    def test_empty_or_excessively_nested_json_is_unavailable(self):
        for payload in ['', '   ', '['*1200+'0'+']'*1200, '[{"number":'+'9'*5000+'}]']:
            with self.subTest(payload=payload[:40]):
                self.fake_gh(payload,payload)
                self.mod.EXTERNAL_REVIEW_CACHE={'repositories':{},'at':0.0}
                snap=self.mod.external_reviews_snapshot()
                self.assertEqual(snap['state'],'unavailable')
                self.assertEqual(snap['items'],[])

    def test_partial_failure_is_explicit_not_empty_assumption(self):
        vitros=json.dumps([{'number':235,'title':'Provider boundary','updatedAt':'2026-09-07T20:00:00Z','statusCheckRollup':[]}])
        self.fake_gh('[]',vitros,fail=BYTE)
        snap=self.mod.external_reviews_snapshot();self.assertEqual(snap['state'],'partial');self.assertEqual(snap['repositories']['stickdeath']['state'],'unavailable');self.assertEqual(snap['repositories']['vitros']['state'],'fresh');self.assertEqual(len(snap['items']),1)

    def test_stale_cache_is_labeled_and_expires_fail_closed(self):
        vitros=json.dumps([{'number':251,'title':'Review me','updatedAt':'2026-09-07T20:00:00Z','statusCheckRollup':[]}])
        self.fake_gh('[]',vitros)
        with mock.patch.object(self.mod.time,'time',return_value=1000.0): fresh=self.mod.external_reviews_snapshot()
        self.assertEqual(fresh['state'],'fresh')
        self.fake_gh('[]','[]',fail=BYTE+','+VITROS)
        self.mod.EXTERNAL_REVIEW_CACHE['at']=0
        with mock.patch.object(self.mod.time,'time',return_value=1060.0): stale=self.mod.external_reviews_snapshot()
        self.assertEqual(stale['state'],'stale');self.assertTrue(all(x['evidence_state']=='stale' for x in stale['items']))
        with mock.patch.object(self.mod.time,'time',return_value=1070.0): still_stale=self.mod.external_reviews_snapshot()
        self.assertEqual(still_stale['state'],'stale');self.assertTrue(all(x['evidence_state']=='stale' for x in still_stale['items']))
        self.mod.EXTERNAL_REVIEW_CACHE['at']=0
        self.mod.EXTERNAL_REVIEW_CACHE['repositories']['vitros']['observed_at']=600
        self.mod.EXTERNAL_REVIEW_CACHE['repositories']['stickdeath']['observed_at']=600
        with mock.patch.object(self.mod.time,'time',return_value=1000.0): expired=self.mod.external_reviews_snapshot()
        self.assertEqual(expired['state'],'unavailable');self.assertEqual(expired['items'],[])

    def test_invalid_fields_are_bounded(self):
        data=json.dumps([{'number':3,'title':'   ','updatedAt':'nonsense','reviewDecision':'INJECT','mergeStateStatus':'EVIL','statusCheckRollup':'bad'}])
        self.fake_gh(data,'[]')
        snap=self.mod.external_reviews_snapshot();item=[x for x in snap['items'] if x['repo']==BYTE][0]
        self.assertEqual(item['number'],3);self.assertEqual(item['review_decision'],'');self.assertEqual(item['merge_state'],'UNKNOWN');self.assertEqual(item['checks']['unknown'],1);self.assertIsNone(item['updated_age_seconds'])

    def test_malformed_entries_never_become_fresh_empty_or_wrong_urls(self):
        for number in [None,True,False,'1','bad',1.0,1.7,float('inf'),float('nan'),0,-1,2_147_483_648]:
            with self.subTest(number=number):
                self.fake_gh(json.dumps([{'number':number}]),'[]')
                self.mod.EXTERNAL_REVIEW_CACHE={'repositories':{},'at':0.0}
                snap=self.mod.external_reviews_snapshot()
                self.assertEqual(snap['repositories']['stickdeath']['state'],'unavailable')
                self.assertEqual(snap['items'],[])
        for payload in ['[{}]','[null]','[1]','[{"number":1e309}]',
                        '[{"number":7},{}]','[{"number":7},{"number":7}]',
                        json.dumps([{'number':n+1} for n in range(13)])]:
            with self.subTest(payload=payload):
                self.fake_gh(payload,'[]')
                self.mod.EXTERNAL_REVIEW_CACHE={'repositories':{},'at':0.0}
                snap=self.mod.external_reviews_snapshot()
                self.assertEqual(snap['repositories']['stickdeath']['state'],'unavailable')
                self.assertEqual(snap['items'],[])

    def test_malformed_refresh_preserves_labeled_prior_evidence(self):
        self.fake_gh('[{"number":7,"title":"Prior evidence"}]','[]')
        with mock.patch.object(self.mod.time,'time',return_value=1000.0): fresh=self.mod.external_reviews_snapshot()
        self.assertEqual(fresh['items'][0]['number'],7)
        self.fake_gh('[{}]','[]')
        with mock.patch.object(self.mod.time,'time',return_value=1060.0): stale=self.mod.external_reviews_snapshot()
        self.assertEqual(stale['items'][0]['number'],7)
        self.assertEqual(stale['items'][0]['evidence_state'],'stale')
        with mock.patch.object(self.mod.time,'time',return_value=1400.0): expired=self.mod.external_reviews_snapshot()
        self.assertEqual(expired['repositories']['stickdeath']['state'],'unavailable')
        self.assertEqual(expired['items'],[])

    def test_check_summary_does_not_hide_failures_after_64_checks(self):
        checks=[{'status':'COMPLETED','conclusion':'SUCCESS'} for _ in range(64)]
        checks += [{'status':'COMPLETED','conclusion':'FAILURE'},
                   {'status':'IN_PROGRESS'},None,{'state':'SUCCESS'}]
        self.fake_gh(json.dumps([{'number':7,'statusCheckRollup':checks}]),'[]')
        snap=self.mod.external_reviews_snapshot()
        self.assertEqual(snap['items'][0]['checks'],{'passed':65,'failed':1,'pending':1,'unknown':1})

if __name__=='__main__':unittest.main(verbosity=2)
