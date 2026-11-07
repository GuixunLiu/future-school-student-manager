"""Class teacher deployment adds schema without replacing live business data."""
import base64
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('class_teacher_upgrade',Path(__file__).parents[1]/'ops/upgrade-canonical-class-teachers.py')
upgrade=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(upgrade)


class ClassTeacherUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
        self.release=self.work/'release';self.release.mkdir();self.db=self.work/'business.sqlite'
        with closing(sqlite3.connect(self.db)) as db,db:
            db.executescript("CREATE TABLE users(org_id TEXT,id TEXT,PRIMARY KEY(org_id,id));INSERT INTO users VALUES('fiction','owner');CREATE TABLE classes(org_id TEXT,id TEXT,PRIMARY KEY(org_id,id));CREATE TABLE devices(org_id TEXT,id TEXT,PRIMARY KEY(org_id,id));CREATE TABLE business(value TEXT);INSERT INTO business VALUES('original balance and history');")
        self.payload=dict(format='future-school-class-teacher-upgrade-v1',files={});self.old={}
        for name in upgrade.FILES:
            raw=(Path(__file__).parents[1]/name).read_bytes() if name=='management/class_teachers.py' else b'# reviewed fixture\n' if name.endswith('.py') else b'/* reviewed fixture */'
            prior=None
            if name not in upgrade.NEW:
                p=self.release/name;p.parent.mkdir(parents=True,exist_ok=True);old=b'# original\n' if name.endswith('.py') else b'/* original */';p.write_bytes(old);self.old[name]=old;prior=upgrade.sha(old)
            self.payload['files'][name]=dict(previous_sha256=prior,sha256=upgrade.sha(raw),base64=base64.b64encode(raw).decode())
        files={n:upgrade.sha(raw) for n,raw in self.old.items()};self.payload['previous_runtime_sha256']=upgrade.sha(upgrade.encoded(files))
        (self.work/'runtime.private.json').write_bytes(upgrade.encoded(dict(database=str(self.db),files=files)))
        (self.release/'MANIFEST.json').write_bytes(upgrade.encoded(dict(contains_data=False,files=files)))
        tls=self.work/'tls';tls.mkdir();cert=tls/'server.crt';key=tls/'server.key'
        cert.write_bytes(b'fictional certificate');key.write_bytes(b'fictional private key')
        self.gateway=dict(origin='http://47.97.226.219:18083',expires_at=1794391683,gateway_sha256=files['management/gateway.py'],certificate=str(cert),private_key=str(key))
        (self.work/'gateway.private.json').write_bytes(upgrade.encoded(self.gateway))
        self.calls=[]
        def service(action,name):self.calls.append((action,name));return 'active' if action=='is-active' else ''
        for p in [patch.object(upgrade,'WORK',self.work),patch.object(upgrade,'private'),patch.object(upgrade,'CERTIFICATE_SHA256',upgrade.sha(cert.read_bytes())),patch.object(upgrade,'service',side_effect=service),
                  patch.object(upgrade,'health',return_value={'mode':'management_active','personnel_api_enabled':False}),
                  patch.object(upgrade,'wait_ready',return_value={}),patch.object(upgrade.subprocess,'run',return_value=type('R',(),{'returncode':0})())]:
            p.start();self.addCleanup(p.stop)

    def test_additive_upgrade_preserves_all_original_rows_and_tls_expiry(self):
        result=upgrade.install(self.payload)
        self.assertTrue(result['updated']);self.assertTrue(result['old_business_unchanged_during_check']);self.assertFalse(result['database_restored']);self.assertEqual(result['accounts_created'],0)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT value FROM business').fetchone()[0],'original balance and history')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
            self.assertTrue(upgrade.OBJECTS<=set(upgrade.objects(db)))
        actual=json.loads((self.work/'gateway.private.json').read_bytes())
        for key in ('origin','expires_at','certificate','private_key'):self.assertEqual(actual[key],self.gateway[key])
        self.assertEqual(len(json.loads((self.work/'runtime.private.json').read_bytes())['files']),7)

    def test_changed_runtime_and_existing_new_module_refuse_before_stop(self):
        bad=dict(self.payload,previous_runtime_sha256='0'*64)
        with self.assertRaises(ValueError):upgrade.install(bad)
        p=self.release/'management/class_teachers.py';p.write_text('# unexpected')
        with self.assertRaises(ValueError):upgrade.install(self.payload)
        self.assertEqual(self.calls,[])

    def test_schema_outside_exact_class_tables_and_triggers_refused(self):
        raw=b"SCHEMA='CREATE TABLE surprise(value TEXT);'"
        self.payload['files']['management/class_teachers.py'].update(base64=base64.b64encode(raw).decode(),sha256=upgrade.sha(raw))
        with self.assertRaises(ValueError):upgrade.install(self.payload)
        self.assertEqual(self.calls,[])

    def test_failed_restart_restores_code_only_retaining_new_business_and_schema(self):
        original_gateway=(self.work/'gateway.private.json').read_bytes()
        def ready(*args,**kwargs):
            if kwargs.get('class_teachers'):
                with closing(sqlite3.connect(self.db)) as db,db:db.execute("INSERT INTO business VALUES('real work after upgrade')")
                raise ValueError('fictional readiness failure')
            return {}
        with patch.object(upgrade,'wait_ready',side_effect=ready):
            with self.assertRaises(ValueError):upgrade.install(self.payload)
        for name,raw in self.old.items():self.assertEqual((self.release/name).read_bytes(),raw)
        for name in upgrade.NEW:self.assertFalse((self.release/name).exists())
        self.assertEqual((self.work/'gateway.private.json').read_bytes(),original_gateway)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM business').fetchone()[0],2)
            self.assertTrue(upgrade.OBJECTS<=set(upgrade.objects(db)))


if __name__=='__main__':unittest.main()
