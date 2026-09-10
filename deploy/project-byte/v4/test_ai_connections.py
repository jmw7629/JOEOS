import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ai_connections import (Manager, ConnectionError, REGISTRY, SecretStore, AUTO_CODEX_MODEL, AUTO_CODEX_KEY,
                            http_json, validate_endpoint, _address_allowed, MAX_RESPONSE)

OWNER={'ok':True,'role':'owner','level':4,'subject':'owner','name':'Fixture owner'}
VIEWER={'ok':True,'role':'viewer','level':1,'subject':'collab:fixture','name':'Fixture viewer'}
KEY='fixture-api-secret-never-in-model-records'


class App:
    def __init__(self, root):
        self.ROOT=root;root.mkdir();self.database=root/'fixture.db'
        with self.con() as db:
            db.executescript('''
              CREATE TABLE models(model_key TEXT PRIMARY KEY,display_name TEXT,provider TEXT,endpoint TEXT,
               model_name TEXT,secret_env TEXT DEFAULT '',capabilities TEXT,enabled INTEGER,local INTEGER,notes TEXT,
               last_status TEXT DEFAULT '',last_latency_ms INTEGER DEFAULT 0,success_count INTEGER DEFAULT 0,
               failure_count INTEGER DEFAULT 0,updated_at INTEGER);
              CREATE TABLE agents(agent_key TEXT PRIMARY KEY,name TEXT,role TEXT,description TEXT,system_prompt TEXT,
               preferred_model TEXT,fallback_models TEXT,capabilities TEXT,opencode_agent TEXT,memory_enabled INTEGER,
               learning_enabled INTEGER,enabled INTEGER,updated_at INTEGER);
              CREATE TABLE user_settings(subject TEXT PRIMARY KEY,data TEXT,updated_at INTEGER);
              CREATE TABLE activity(action TEXT,detail TEXT,actor TEXT);
            ''')
            db.execute('INSERT INTO models(model_key,display_name,provider,endpoint,model_name,enabled) VALUES(?,?,?,?,?,?)',
                       ('legacy-custom','Keep my model','ollama','http://127.0.0.1:11434','kept',1))
            db.execute('INSERT INTO agents(agent_key,name,preferred_model) VALUES(?,?,?)',('custom-agent','Keep my agent','legacy-custom'))
            db.execute('INSERT INTO user_settings VALUES(?,?,?)',('owner',json.dumps({'general':{'density':'compact'},'ai':{'default_agent':'custom-agent','default_model':'legacy-custom'},'custom':['retain']}),1))
            db.execute('INSERT INTO user_settings VALUES(?,?,?)',('collab:fixture',json.dumps({'ai':{'default_model':'legacy-custom'}}),1))
    def con(self):
        c=sqlite3.connect(self.database);c.row_factory=sqlite3.Row;return c
    def now(self):return int(time.time())
    def audit(self,c,action,task='',project='',detail='',actor='system'):
        c.execute('INSERT INTO activity VALUES(?,?,?)',(action,detail,actor))


class Codex:
    connected=True
    calls=[]
    def status(self):return {'available':True,'connected':self.connected}
    def models(self):return [{'id':'codex-fixture','name':'Codex fixture'}]
    def complete(self,messages,model=''):
        self.calls.append((messages,model));return 'CODEX FIXTURE',5


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.app=App(self.root/'app');self.codex=Codex();self.calls=[]
        self.reply={'data':[{'id':'fixture-model'}]}
        def transport(endpoint,path,provider,headers,payload=None,timeout=None):
            self.calls.append({'endpoint':endpoint,'path':path,'provider':provider,'headers':headers,'payload':payload,'timeout':timeout})
            return self.reply
        self.manager=Manager(self.app,self.codex,secret_dir=self.root/'private',transport=transport)
    def body(self,provider='openai-responses',**extra):
        base={'provider':provider,'model_name':'fixture-model','display_name':'Fixture connection'}
        if provider not in ('ollama','openai-compatible','codex-chatgpt'):base['api_key']=KEY
        if provider=='openai-compatible':base['endpoint']='http://127.0.0.1:9999/v1'
        return {**base,**extra}
    def connect(self,provider='openai-responses',**extra):return self.manager.connect(self.body(provider,**extra),OWNER)

    def test_catalog_is_dynamic_provider_registry_and_honest_discovery(self):
        catalog=self.manager.catalog();self.assertEqual(len(catalog['providers']),18)
        self.assertEqual(REGISTRY['xai']['protocol'],'responses')
        for provider in ('fireworks','perplexity'):self.assertFalse(REGISTRY[provider]['discovery'])
        for provider in ('openclaw','hermes'):self.assertEqual(REGISTRY[provider]['kind'],'agent')
        self.assertEqual(REGISTRY['codex-chatgpt']['auth_modes'],['subscription'])
        for runtime in catalog['subscription_runtimes']:
            self.assertFalse(runtime['adapter_available']);self.assertFalse(runtime['connectable'])
            self.assertNotIn(runtime['id'],REGISTRY)
        catalog['providers'][0]['id']='changed';self.assertIn('codex-chatgpt',REGISTRY)

    def auto_fixture(self, default=''):
        with self.app.con() as db:
            settings=json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0])
            settings['ai']['default_model']=default
            db.execute("UPDATE user_settings SET data=? WHERE subject='owner'",(json.dumps(settings),))
        self.codex.models=lambda:[{'id':AUTO_CODEX_MODEL}]
        return settings

    def test_auto_codex_registers_verified_signin_once_without_credentials_or_generation(self):
        before=self.auto_fixture()
        with self.app.con() as db:
            other=tuple(db.execute("SELECT * FROM user_settings WHERE subject='collab:fixture'").fetchone())
            agents=[tuple(r) for r in db.execute('SELECT * FROM agents')]
        result=self.manager.autoconnect_codex(OWNER)
        self.assertEqual(result,{'state':'registered','model_key':AUTO_CODEX_KEY,'created':True,'default_set':True})
        self.assertEqual(self.manager.autoconnect_codex(OWNER),{**result,'created':False,'default_set':False})
        with self.app.con() as db:
            after=json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0])
            before['ai']['default_model']=AUTO_CODEX_KEY
            self.assertEqual(after,before)
            self.assertEqual(tuple(db.execute("SELECT * FROM user_settings WHERE subject='collab:fixture'").fetchone()),other)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM agents')],agents)
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM activity').fetchone()[0],2)
            model=dict(db.execute('SELECT * FROM models WHERE model_key=?',(AUTO_CODEX_KEY,)).fetchone())
        self.assertEqual(model['last_status'],'not-tested')
        self.assertEqual(model['secret_env'],'');self.assertFalse((self.root/'private').exists());self.assertEqual(self.calls,[])

    def test_auto_codex_preserves_existing_explicit_defaults_and_reuses_owner_model(self):
        before=self.auto_fixture('missing-explicit-default')
        self.connect('codex-chatgpt',model_key='connection/codex-chatgpt/owner',model_name=AUTO_CODEX_MODEL)
        result=self.manager.autoconnect_codex(OWNER)
        self.assertEqual(result['model_key'],'connection/codex-chatgpt/owner')
        self.assertFalse(result['created']);self.assertFalse(result['default_set'])
        with self.app.con() as db:
            self.assertEqual(json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0]),before)
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],1)
        self.auto_fixture()
        self.assertTrue(self.manager.autoconnect_codex(OWNER)['default_set'])

    def test_auto_codex_never_revives_disconnected_connection_even_after_restart(self):
        self.auto_fixture();result=self.manager.autoconnect_codex(OWNER)
        self.manager.disconnect(result['model_key'],OWNER)
        self.auto_fixture()
        fresh=Manager(self.app,self.codex,secret_dir=self.root/'private')
        self.assertEqual(fresh.autoconnect_codex(OWNER),{'state':'disabled','model_key':AUTO_CODEX_KEY})
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],1)
            self.assertEqual(json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0])['ai']['default_model'],'')

    def test_auto_codex_absent_auth_or_model_leaves_all_configuration_unchanged(self):
        self.auto_fixture()
        for connected,models,state in ((False,[{'id':AUTO_CODEX_MODEL}],'sign_in_required'),(True,[],'model_unavailable')):
            self.codex.connected=connected;self.codex.models=lambda:models
            self.assertEqual(self.manager.autoconnect_codex(OWNER),{'state':state})
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM activity').fetchone()[0],0)
        self.assertFalse((self.root/'private').exists())

    def test_auto_codex_invalid_settings_and_key_collision_cannot_overwrite_configuration(self):
        self.auto_fixture()
        with self.app.con() as db:db.execute("UPDATE user_settings SET data='invalid' WHERE subject='owner'")
        with self.assertRaises(ConnectionError):self.manager.autoconnect_codex(OWNER)
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],0)
            db.execute("UPDATE user_settings SET data='{}' WHERE subject='owner'")
            db.execute('UPDATE models SET model_key=? WHERE model_key=?',(AUTO_CODEX_KEY,'legacy-custom'))
        with self.assertRaises(ConnectionError):self.manager.autoconnect_codex(OWNER)
        with self.app.con() as db:
            self.assertEqual(db.execute('SELECT model_name FROM models WHERE model_key=?',(AUTO_CODEX_KEY,)).fetchone()[0],'kept')
            self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],0)

    def test_auto_codex_concurrent_registrations_share_one_connection_and_default(self):
        self.auto_fixture();barrier=threading.Barrier(2)
        def models():
            barrier.wait(timeout=3)
            return [{'id':AUTO_CODEX_MODEL}]
        self.codex.models=models
        other=Manager(self.app,self.codex,secret_dir=self.root/'private')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda manager:manager.autoconnect_codex(OWNER),(self.manager,other)))
        self.assertEqual(sum(result['created'] for result in results),1)
        self.assertEqual(sum(result['default_set'] for result in results),1)
        self.assertEqual({result['model_key'] for result in results},{AUTO_CODEX_KEY})

    def test_auto_codex_rechecks_owner_choice_after_slow_discovery(self):
        self.auto_fixture()
        def models():
            self.manager.set_default('legacy-custom',OWNER)
            return [{'id':AUTO_CODEX_MODEL}]
        self.codex.models=models
        result=self.manager.autoconnect_codex(OWNER)
        self.assertTrue(result['created']);self.assertFalse(result['default_set'])
        with self.app.con() as db:
            self.assertEqual(json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0])['ai']['default_model'],'legacy-custom')

    def test_malformed_json_field_types_are_controlled_errors_without_side_effects(self):
        for field in ('provider','endpoint','model_name','display_name','model_key','api_key','secret_env','auth_mode','agent_template'):
            for bad in ([],{},True,12,None):
                with self.subTest(field=field,bad=bad),self.assertRaises(ConnectionError) as raised:
                    self.manager.connect(self.body(**{field:bad}),OWNER)
                self.assertEqual(raised.exception.http_status,400)
        for body in ({'provider':'ollama','unrecognized':True},{'provider':'ollama','set_default':'yes'}):
            with self.assertRaises(ConnectionError):self.manager.discover(body,OWNER)
        self.assertEqual(self.calls,[])
        with self.app.con() as db:self.assertEqual(db.execute('SELECT count(*) FROM ai_connections').fetchone()[0],0)

    def test_all_new_configuration_and_calls_enforce_owner_without_http(self):
        connected=self.connect();key=connected['model_key']
        for actor in (None,VIEWER,{**OWNER,'subject':'collab:fake'},{**OWNER,'role':'admin'}):
            for operation in (lambda:self.manager.connect(self.body(),actor),lambda:self.manager.discover(self.body(),actor),
                              lambda:self.manager.autoconnect_codex(actor),
                              lambda:self.manager.set_default(key,actor),lambda:self.manager.connections(actor),
                              lambda:self.manager.disconnect(key,actor),lambda:self.manager.call(key,[{'role':'user','content':'hello'}],actor)):
                with self.assertRaises(ConnectionError) as raised:operation()
                self.assertEqual(raised.exception.http_status,403)
        self.assertEqual(self.calls,[])

    def test_keys_are_private_server_files_and_never_serialized_or_audited(self):
        connected=self.connect();key=connected['model_key']
        public=json.dumps(connected)+json.dumps(self.manager.connections(OWNER))
        with self.app.con() as db:
            public+=json.dumps([dict(r) for r in db.execute('SELECT * FROM models')])
            public+=json.dumps([dict(r) for r in db.execute('SELECT * FROM ai_connections')])
            public+=json.dumps([dict(r) for r in db.execute('SELECT * FROM activity')])
        self.assertNotIn(KEY,public);self.assertNotIn(KEY,self.app.database.read_bytes().decode(errors='ignore'))
        metadata=self.manager._metadata(key);secret=self.root/'private'/metadata['secret_ref']
        self.assertEqual(secret.read_text(),KEY);self.assertEqual(secret.stat().st_mode&0o777,0o600)
        self.assertEqual(secret.parent.stat().st_mode&0o777,0o700)
        self.reply={'output':[{'content':[{'type':'output_text','text':'OK'}]}]}
        self.assertEqual(self.manager.call(key,[{'role':'user','content':'hello'}],OWNER)[0],'OK')
        self.assertEqual(self.calls[-1]['headers']['Authorization'],'Bearer '+KEY)

    def test_collision_does_not_replace_existing_model_or_leave_secrets(self):
        with self.assertRaises(ConnectionError) as raised:self.connect(model_key='legacy-custom')
        self.assertEqual(raised.exception.http_status,409)
        self.assertEqual(self.manager._row('legacy-custom')['display_name'],'Keep my model')
        self.assertFalse((self.root/'private').exists())

    def test_owner_default_preserves_custom_settings_other_users_and_agents(self):
        connected=self.connect(set_default=True);key=connected['model_key']
        with self.app.con() as db:
            owner=json.loads(db.execute("SELECT data FROM user_settings WHERE subject='owner'").fetchone()[0])
            viewer=json.loads(db.execute("SELECT data FROM user_settings WHERE subject='collab:fixture'").fetchone()[0])
            agent=db.execute("SELECT preferred_model FROM agents WHERE agent_key='custom-agent'").fetchone()[0]
        self.assertEqual(owner['ai'],{'default_agent':'custom-agent','default_model':key})
        self.assertEqual(owner['general'],{'density':'compact'});self.assertEqual(owner['custom'],['retain'])
        self.assertEqual(viewer['ai']['default_model'],'legacy-custom');self.assertEqual(agent,'legacy-custom')
        self.manager.set_default('legacy-custom',OWNER)
        with self.assertRaises(ConnectionError):self.manager.set_default('missing',OWNER)

    def test_malformed_existing_settings_abort_connection_and_secret_transaction(self):
        with self.app.con() as db:db.execute("UPDATE user_settings SET data='not-json' WHERE subject='owner'")
        with self.assertRaises(ConnectionError):self.connect(model_key='must-not-exist',set_default=True)
        self.assertFalse(self.manager.managed('must-not-exist'))
        self.assertEqual(list((self.root/'private').iterdir()),[])

    def test_external_agent_templates_are_bound_without_starting_workers(self):
        for provider in ('hermes','openclaw'):
            connected=self.connect(provider,agent_template=provider)
            with self.app.con() as db:agent=dict(db.execute('SELECT * FROM agents WHERE agent_key=?',(connected['agent_key'],)).fetchone())
            self.assertEqual(agent['preferred_model'],connected['model_key']);self.assertEqual(agent['opencode_agent'],'')
            self.assertEqual(agent['memory_enabled'],0);self.assertEqual(agent['learning_enabled'],0)
            self.assertEqual(self.calls,[])
        with self.assertRaises(ConnectionError):self.connect('ollama',agent_template='hermes')

    def test_disconnect_only_managed_disables_and_removes_its_saved_key(self):
        connected=self.connect('hermes',agent_template='hermes');key=connected['model_key']
        secret=self.root/'private'/self.manager._metadata(key)['secret_ref']
        self.manager.disconnect(key,OWNER)
        self.assertFalse(secret.exists());self.assertEqual(self.manager._row(key)['enabled'],0)
        with self.app.con() as db:self.assertEqual(db.execute('SELECT enabled FROM agents WHERE agent_key=?',(connected['agent_key'],)).fetchone()[0],0)
        with self.assertRaises(ConnectionError):self.manager.set_default(key,OWNER)
        with self.assertRaises(ConnectionError):self.manager.disconnect('legacy-custom',OWNER)
        self.assertEqual(self.manager._row('legacy-custom')['enabled'],1)

    def test_codex_uses_only_injected_official_runtime_and_requires_login(self):
        self.assertEqual(self.manager.discover({'provider':'codex-chatgpt'},OWNER)['models'][0]['id'],'codex-fixture')
        connected=self.connect('codex-chatgpt',model_name='codex-fixture')
        self.assertEqual(self.manager.call(connected['model_key'],[{'role':'user','content':'hello'}],OWNER),('CODEX FIXTURE',5))
        self.assertFalse((self.root/'private').exists());self.assertEqual(self.calls,[])
        with self.assertRaises(ConnectionError):self.connect('codex-chatgpt',api_key=KEY)
        self.codex.connected=False
        with self.assertRaises(ConnectionError):self.manager.discover({'provider':'codex-chatgpt'},OWNER)
        with self.assertRaises(ConnectionError):self.manager.call(connected['model_key'],[{'role':'user','content':'hello'}],OWNER)

    def test_codex_connection_rejects_unavailable_catalog_models_before_mutation(self):
        with self.app.con() as db:
            before={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)]
                    for table in ('models','agents','user_settings','activity','ai_connections')}
        for catalog,status in (([{'id':'codex-fixture'}],409),([],409),({'id':'unknown'},502),([{'id':[]}],502)):
            with self.subTest(catalog=catalog),patch.object(self.codex,'models',return_value=catalog):
                with self.assertRaises(ConnectionError) as raised:
                    self.connect('codex-chatgpt',model_name='unavailable-codex',model_key='must-not-save',set_default=True)
                self.assertEqual(raised.exception.http_status,status)
        with self.app.con() as db:
            after={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in before}
        self.assertEqual(after,before)
        self.assertFalse((self.root/'private').exists());self.assertEqual(self.calls,[])

    def test_server_environment_reference_is_restricted_and_never_exported(self):
        with patch.dict(os.environ,{'PROJECT_BYTE_AI_FIXTURE_API_KEY':KEY,'UNRELATED_SECRET':KEY}):
            body=self.body();body.pop('api_key');body['secret_env']='UNRELATED_SECRET'
            with self.assertRaises(ConnectionError):self.manager.connect(body,OWNER)
            body['secret_env']='PROJECT_BYTE_AI_FIXTURE_API_KEY';connected=self.manager.connect(body,OWNER)
            self.reply={'output_text':'OK'};self.manager.call(connected['model_key'],[{'role':'user','content':'hello'}],OWNER)
            self.assertEqual(self.calls[-1]['headers']['Authorization'],'Bearer '+KEY)
            self.assertNotIn(KEY,json.dumps(self.manager.connections(OWNER)))
        with self.assertRaises(ConnectionError):self.manager.call(connected['model_key'],[{'role':'user','content':'hello'}],OWNER)

    def test_discovery_protocols_empty_invalid_and_pagination_are_honest(self):
        examples=[('ollama',{'models':[{'name':'llama:fixture'}]},'/api/tags'),
                  ('anthropic',{'data':[{'id':'claude-fixture','display_name':'Claude fixture'}],'has_more':True},'/models?limit=500'),
                  ('google-gemini',{'models':[{'name':'models/gemini-fixture','displayName':'Gemini','supportedGenerationMethods':['generateContent']}]},'/models?pageSize=500'),
                  ('cohere',{'models':[{'name':'command-fixture','endpoints':['chat']}]},'/v1/models'),
                  ('openai-responses',{'data':[{'id':'gpt-fixture'}]},'/models'),
                  ('hermes',{'data':[{'id':'agent-profile'}]},'/models')]
        for provider,reply,path in examples:
            self.reply=reply;result=self.manager.discover(self.body(provider),OWNER)
            self.assertEqual(len(result['models']),1);self.assertEqual(self.calls[-1]['path'],path)
            self.assertFalse(result['empty'])
        self.reply={'models':[]};self.assertTrue(self.manager.discover({'provider':'ollama'},OWNER)['empty'])
        for bad in ({'error':KEY},{'models':'invalid'},{'models':[{'name':'bad\nmodel'}]}):
            self.reply=bad
            with self.assertRaises(ConnectionError):self.manager.discover({'provider':'ollama'},OWNER)
        for provider in ('fireworks','perplexity'):
            with self.assertRaises(ConnectionError):self.manager.discover(self.body(provider),OWNER)

    def test_wire_contracts_for_native_protocols_and_compatible_presets(self):
        messages=[{'role':'system','content':'Be concise'},{'role':'user','content':'hello'},{'role':'assistant','content':'previous'}]
        cases=[('openai-responses',{'output':[{'content':[{'type':'output_text','text':'OK'}]}]},'/responses'),
               ('xai',{'output_text':'OK'},'/responses'),
               ('anthropic',{'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':'OK'}]},'/messages'),
               ('google-gemini',{'candidates':[{'content':{'parts':[{'text':'private','thought':True},{'text':'OK'}]}}]},'/models/fixture-model:generateContent'),
               ('ollama',{'message':{'content':'OK'}},'/api/chat'),
               ('cohere',{'message':{'content':[{'type':'text','text':'OK'}]}},'/v2/chat')]
        cases += [(p,{'choices':[{'message':{'content':'OK'}}]},'/chat/completions') for p in REGISTRY if REGISTRY[p]['protocol']=='chat-completions']
        for provider,reply,path in cases:
            with self.subTest(provider=provider):
                connected=self.connect(provider);self.reply=reply
                answer,latency=self.manager.call(connected['model_key'],messages,OWNER)
                self.assertEqual(answer,'OK');self.assertGreaterEqual(latency,0)
                call=self.calls[-1];self.assertEqual(call['path'],path)
                if provider=='anthropic':
                    self.assertEqual(call['headers']['x-api-key'],KEY);self.assertEqual(call['headers']['anthropic-version'],'2023-06-01')
                    self.assertEqual(call['payload']['system'],'Be concise');self.assertEqual(call['payload']['max_tokens'],4096)
                    self.assertEqual([m['role'] for m in call['payload']['messages']],['user','assistant'])
                if provider=='google-gemini':
                    self.assertEqual(call['headers']['x-goog-api-key'],KEY)
                    self.assertEqual([m['role'] for m in call['payload']['contents']],['user','model'])
                    self.assertEqual(call['payload']['systemInstruction'],{'parts':[{'text':'Be concise'}]})
                if provider in ('openai-responses','xai'):self.assertFalse(call['payload']['store'])

    def test_empty_or_invalid_completions_and_messages_fail_explicitly(self):
        connected=self.connect('ollama');key=connected['model_key']
        for reply in ({'message':{'content':''}},{'message':None},{'error':KEY}):
            self.reply=reply
            with self.assertRaises(ConnectionError) as raised:self.manager.call(key,[{'role':'user','content':'hello'}],OWNER)
            self.assertNotIn(KEY,str(raised.exception))
        for messages in ([],[{'role':'tool','content':'x'}],[{'role':'user','content':'x'*64001}]):
            with self.assertRaises(ConnectionError):self.manager.call(key,messages,OWNER)

    def test_legacy_ollama_remains_available_without_rewriting_existing_records(self):
        self.reply={'message':{'content':'LEGACY'}}
        self.assertEqual(self.manager.call('legacy-custom',[{'role':'user','content':'hello'}],VIEWER)[0],'LEGACY')
        self.assertFalse(self.manager.managed('legacy-custom'))

    def test_private_credential_store_rejects_public_location_symlink_and_permissions(self):
        with self.assertRaises(ConnectionError):SecretStore(self.app.ROOT/'secrets',self.app.ROOT)
        store=self.manager.secrets;ref=store.put(KEY);target=self.root/'private'/ref
        target.chmod(0o644)
        with self.assertRaises(ConnectionError):store.get(ref)
        target.unlink();outside=self.root/'outside';outside.write_text(KEY);outside.chmod(0o600);target.symlink_to(outside)
        with self.assertRaises(ConnectionError):store.get(ref)


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.mode='ok';self.calls=[];outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                outer.calls.append({'path':self.path,'auth':self.headers.get('Authorization')})
                mode=outer.mode
                status=302 if mode=='redirect' else 500 if mode=='error' else 200
                body=(b'x'*(MAX_RESPONSE+1)) if mode=='large' else KEY.encode() if mode in ('invalid','error') else b'{"data":[{"id":"fixture"}]}'
                self.send_response(status);self.send_header('Content-Type','text/html' if mode=='html' else 'application/json')
                if mode=='redirect':self.send_header('Location',outer.endpoint+'/stolen')
                self.send_header('Content-Length',str(len(body)));self.end_headers()
                if mode=='slow':time.sleep(.6)
                try:self.wfile.write(body)
                except OSError:pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.endpoint='http://127.0.0.1:'+str(self.server.server_port)

    def test_real_local_wire_does_not_follow_redirects_or_disclose_error_body(self):
        self.assertEqual(http_json(self.endpoint,'/models','openai-compatible',{'Authorization':'Bearer '+KEY})['data'][0]['id'],'fixture')
        self.mode='redirect'
        with self.assertRaises(ConnectionError):http_json(self.endpoint,'/models','openai-compatible',{'Authorization':'Bearer '+KEY})
        self.assertFalse(any(c['path']=='/stolen' for c in self.calls))
        self.mode='error'
        with self.assertRaises(ConnectionError) as raised:http_json(self.endpoint,'/models','openai-compatible',{'Authorization':'Bearer '+KEY})
        self.assertNotIn(KEY,str(raised.exception));self.assertIn('HTTP 500',str(raised.exception))

    def test_bounded_json_response_and_hard_request_timeout(self):
        for mode in ('large','html','invalid'):
            self.mode=mode
            with self.subTest(mode=mode),self.assertRaises(ConnectionError):http_json(self.endpoint,'/models','openai-compatible',{})
        self.mode='slow';started=time.monotonic()
        with self.assertRaises(ConnectionError):http_json(self.endpoint,'/models','openai-compatible',{},timeout=.1)
        self.assertLess(time.monotonic()-started,.5)

    def test_metadata_unsafe_urls_and_dns_rebinding_fail_before_sending_keys(self):
        urls=['file:///etc/passwd','http://user:pass@127.0.0.1/v1','http://127.0.0.1/v1?key=secret',
              'http://169.254.169.254','http://metadata.google.internal','http://100.100.100.200',
              'http://[fd00:ec2::254]','http://127.0.0.1/%2e%2e/admin','http://0.0.0.0']
        for url in urls:
            with self.subTest(url=url),self.assertRaises(ConnectionError):validate_endpoint(url,'openai-compatible')
        for ips in (['169.254.169.254'],['127.0.0.1'],['8.8.8.8','10.0.0.1']):
            with self.subTest(ips=ips),self.assertRaises(ConnectionError):
                http_json('https://api.example.test/v1','/models','openai-responses',{'Authorization':'Bearer '+KEY},resolver=lambda h,p:ips)
        self.assertEqual(self.calls,[])
        self.assertTrue(_address_allowed('100.99.71.65',True));self.assertTrue(_address_allowed('::1',True))
        self.assertFalse(_address_allowed('fe80::1',True));self.assertFalse(_address_allowed('127.0.0.1',False))

    def test_global_deadline_includes_dns_and_stalled_tls_handshake(self):
        original=socket.getaddrinfo
        def slow_dns(*args,**kwargs):
            time.sleep(.3);return original(*args,**kwargs)
        started=time.monotonic()
        with patch('ai_connections.socket.getaddrinfo',side_effect=slow_dns),self.assertRaises(ConnectionError):
            http_json(self.endpoint,'/models','openai-compatible',{},timeout=.05)
        self.assertLess(time.monotonic()-started,.2);self.assertEqual(self.calls,[])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(1)
        self.addCleanup(listener.close);hello=threading.Event();finished=threading.Event()
        def stall_tls():
            try:
                peer,_=listener.accept()
                with peer:
                    peer.settimeout(1)
                    if peer.recv(4096):hello.set()
                    finished.wait(.6)
            except OSError:pass
        thread=threading.Thread(target=stall_tls,daemon=True);thread.start()
        started=time.monotonic()
        try:
            with self.assertRaises(ConnectionError):
                http_json('https://127.0.0.1:'+str(listener.getsockname()[1]),'/models','openai-compatible',{},timeout=.12)
            self.assertTrue(hello.is_set(),'real TLS ClientHello reached the stalled peer')
            self.assertLess(time.monotonic()-started,.4)
        finally:finished.set();thread.join(1)


if __name__=='__main__':unittest.main(verbosity=2)
