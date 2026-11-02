"""Fictional full-copy cutover and fixed unit actions, never real systemctl."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from management import initialization as init
from management.approvals import digest
import test_management_initialization as fixture

OPS=Path(__file__).parents[1]/'ops'
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,OPS/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
broker=module('fictional_cutover_broker','canonical-cutover-broker.py')
package=module('fictional_management_release','package-management-release.py')


class CutoverTests(unittest.TestCase):
    node=fixture.InitializationTests.node
    def setUp(self):
        fixture.InitializationTests.setUp(self)
        with closing(sqlite3.connect(self.source)) as db,db:db.execute("UPDATE organizations SET slug='weilai'")
        self.app=self.root/'app';self.app.mkdir();self.python=self.root/'python';self.python.write_bytes(b'fictional')
        home=self.root/'home';(home/'.config/systemd/user').mkdir(parents=True)
        self.calls=[];self.states={name:'active' for name in broker.OLD}
        def run(*args,allowed=(0,)):
            self.calls.append(args);action=args[0]
            if action=='is-active':return self.states.get(args[1],'inactive')
            if action=='is-enabled':return 'enabled'
            if action=='cat':return '[Unit]\nFictional preserved source'
            if action in ('start','stop'):self.states[args[1]]='active' if action=='start' else 'inactive'
            return ''
        self.service_run=run
        for p in (patch.object(broker,'APP',self.app),patch.object(broker,'SOURCE',self.source),patch.object(broker,'PYTHON',self.python),patch.object(broker,'run',side_effect=run),patch.object(broker.time,'time',return_value=self.now),patch.object(broker.Path,'home',return_value=home),patch.object(init,'export_helper',return_value=self.helper)):
            p.start();self.addCleanup(p.stop)
        self.code,self.manifest=package.build();self.cut=broker.Cutover()
        self.input=dict(organization='weilai',accept_service_cutover=True,source_captured_at=self.now,code_base64=__import__('base64').b64encode(self.code).decode(),code_sha256=broker.sha(self.code))
        self.original=self.source.read_bytes()

    def copied(self):return self.cut.copy(self.input)
    def initialized(self):
        copied=self.copied();review=__import__('base64').b64decode(copied['review_base64']).decode()
        result=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8')),h=require('./server/ops/build-legacy-history-archive.cjs');const p=require('./server/ops/build-management-initialization-plan.cjs').build(v.raw,v.review,{organization:'weilai',newId:()=> '9'.repeat(32)}).plan;console.log(JSON.stringify({plan:h.encoded(p),archive:h.encoded(h.build(v.raw).artifact)}));",dict(raw=self.source_raw,review=review))
        b64=lambda s:__import__('base64').b64encode(s.encode()).decode()
        value=self.cut.initialize(dict(plan_base64=b64(result['plan']),plan_sha256=hashlib.sha256(result['plan'].encode()).hexdigest(),archive_base64=b64(result['archive']),source_captured_at=self.now,web_login='owner',web_password='Fictional initialization test password 2026!'))
        self.schedule=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(require('./server/ops/build-canonical-schedule.cjs').build(v.raw,v.plan,v.boundary,{now:v.now})));",dict(raw=self.source_raw,plan=json.loads(result['plan']),boundary=value['boundary'],now=self.now))
        return value

    def test_copy_stops_both_writers_preserves_source_and_exact_inputs_are_required(self):
        result=self.copied();self.assertEqual(result['phase'],'copied');self.assertFalse(result['source_written']);self.assertEqual(self.original,self.source.read_bytes())
        self.assertTrue(all(self.states[name]=='inactive' for name in broker.OLD))
        self.assertEqual(self.cut.cleanup_failure(),'original_services_restored');self.assertTrue(all(self.states[name]=='active' for name in broker.OLD))
        with self.assertRaises(ValueError):self.cut.copy(self.input)

    def test_stale_capture_or_changed_code_never_pauses_original(self):
        for bad in (self.input|dict(source_captured_at=self.now-900),self.input|dict(code_sha256='f'*64)):
            with self.assertRaises(ValueError):self.cut.copy(bad)
            self.assertTrue(all(self.states[name]=='active' for name in broker.OLD))
        self.assertFalse(any(call[0]=='stop' for call in self.calls))

    def test_full_copy_stages_real_calendar_scope_without_reset_or_debit_and_can_restore_preoffer(self):
        value=self.initialized();staged=self.cut.stage(dict(schedule=self.schedule,nonce='a'*32))
        self.assertEqual(staged['phase'],'staged');self.assertEqual(value['scope']['active_students'],2)
        self.assertEqual(self.original,self.source.read_bytes());self.assertTrue(self.cut.work.joinpath('device-provisioning.private.json').is_file())
        with self.cut.store.transaction() as db:
            self.assertEqual(db.execute('SELECT SUM(units) FROM management_balances').fetchone()[0],1900)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_legacy_rows').fetchone()[0],7)
        self.assertEqual(self.cut.cleanup_failure(),'original_services_restored')

    def test_committed_offer_never_automatically_reopens_old_writer_even_if_python_flag_was_lost(self):
        self.initialized();staged=self.cut.stage(dict(schedule=self.schedule,nonce='a'*32))
        self.cut.store.offer_activation(self.cut.token,'admin',hold_id=self.cut.hold['hold_id'],bootstrap_sha256='b'*64,source_map_sha256=digest(staged['source']),reason='虚构待平板启用',command_ref='fixture-offer')
        self.cut.offered=False
        self.assertEqual(self.cut.cleanup_failure(),'reconciliation_required');self.assertTrue(all(self.states[name]=='inactive' for name in broker.OLD));self.assertEqual(self.original,self.source.read_bytes())

    def test_release_is_only_whitelisted_code_and_broker_has_no_arbitrary_rpc(self):
        self.assertEqual(set(self.manifest['files']),broker.FILES);self.assertFalse(self.manifest['contains_data'])
        with self.assertRaises(ValueError):broker.unpack(self.code,'f'*64,self.root/'never-created')
        source=(OPS/'canonical-cutover-broker.py').read_text(encoding='utf-8');compile(source,'fictional-broker','exec')
        self.assertNotIn('shell=True',source);self.assertIn("{'copy','initialize','stage','publish','finish','diagnose','reset'}",source)

    def tls_fixture(self):
        folder=self.source.parent/'mirror-tls';folder.mkdir();(folder/'server.crt').write_bytes(b'fictional-certificate');(folder/'server.key').write_bytes(b'fictional-key')
        real_sha=broker.sha
        return patch.object(broker,'sha',side_effect=lambda b:'320750ba1113dafa0410bcdbeffd7b04a68f42f64b74744adf01731bad92e8fe' if b==b'fictional-certificate' else real_sha(b))

    def test_publish_writes_only_new_units_and_keeps_old_copy_untouched_after_offer(self):
        self.initialized();staged=self.cut.stage(dict(schedule=self.schedule,nonce='a'*32))
        class Viewer:
            server_address=('127.0.0.1',12345)
            def __init__(self,*a,**kw):pass
            def serve_forever(self):pass
            def shutdown(self):pass
            def server_close(self):pass
        def health(port,*a):return dict(mode='management_staged',runtime_admitted=True) if port==18098 else dict(legacy_mirror_writable=False,gateway_sha256=self.manifest['files']['management/gateway.py'])
        with self.tls_fixture(),patch('management.gateway.Viewer',Viewer),patch.object(broker.ssl,'SSLContext'),patch.object(broker.ssl,'create_default_context'),patch.object(self.cut,'health',side_effect=health):
            result=self.cut.publish(dict(source_map_sha256=digest(staged['source']),bootstrap_sha256='b'*64))
        self.assertEqual(result['status'],'awaiting_device_commit');self.assertFalse(result['original_source_written']);self.assertEqual(self.original,self.source.read_bytes())
        self.assertTrue(all(('enable',name) in self.calls for name in broker.NEW));self.assertTrue(all(('disable',name) in self.calls for name in broker.OLD))
        self.assertEqual(self.cut.cleanup_failure(),'reconciliation_required')
        with self.cut.store.transaction() as db:self.assertEqual(db.execute('SELECT phase FROM management_activations').fetchone()[0],'offered')

    def test_backend_start_failure_before_offer_restores_old_services_and_removes_only_new_units(self):
        self.initialized();staged=self.cut.stage(dict(schedule=self.schedule,nonce='a'*32))
        def failing(*args,**kw):
            if args==('start',broker.NEW[1]):raise ValueError('fictional startup failure')
            return self.service_run(*args,**kw)
        with self.tls_fixture(),patch.object(broker,'run',side_effect=failing):
            with self.assertRaises(ValueError):self.cut.publish(dict(source_map_sha256=digest(staged['source']),bootstrap_sha256='b'*64))
        self.assertEqual(self.cut.cleanup_failure(),'original_services_restored');self.assertTrue(all(self.states[name]=='active' for name in broker.OLD));self.assertEqual(self.original,self.source.read_bytes())

    def test_real_subprocess_can_start_from_the_release_archive_alone_and_preserves_login(self):
        self.initialized();self.cut.stage(dict(schedule=self.schedule,nonce='a'*32))
        code="""import sys,json
from management.api import create_app
from fastapi.testclient import TestClient
v=json.loads(sys.stdin.read());app=create_app(v['database'],lambda:v['now'],allow_test_client=True)
with TestClient(app,base_url='http://127.0.0.1:18098') as c:
 r=c.post('/v1/auth/login',json=dict(organization='weilai',login='owner',password=v['password']))
 assert r.status_code==200,r.text
 token=r.json()['access_token'];h=c.get('/health/ready').json();assert h['mode']=='management_staged',h
 assert c.get('/v1/me',headers={'Authorization':'Bearer '+token}).status_code==200
 assert c.get('/v1/admin/people',headers={'Authorization':'Bearer '+token}).status_code==404
 assert c.post('/v1/auth/logout',json={},headers={'Authorization':'Bearer '+token}).status_code==200
 print('isolated release startup and preserved account authentication passed')
"""
        env=os.environ.copy();env['PYTHONPATH']=str(self.cut.release);env['PYTHONIOENCODING']='utf-8'
        result=subprocess.run([sys.executable,'-c',code],input=json.dumps(dict(database=str(self.cut.database),now=self.now,password='Fictional initialization test password 2026!')),cwd=self.cut.release,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
