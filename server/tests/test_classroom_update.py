"""Code update recovery must never restore a live database."""
from contextlib import closing
import base64
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('classroom_update',Path(__file__).parents[1]/'ops/update-canonical-classroom.py')
update=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(update)


class ClassroomUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
        self.release=self.work/'release';self.release.mkdir();self.db=self.work/'business.sqlite'
        with closing(sqlite3.connect(self.db)) as db,db:
            db.executescript("CREATE TABLE management_activations(phase TEXT);INSERT INTO management_activations VALUES('active');CREATE TABLE business(value TEXT);INSERT INTO business VALUES('must remain');")
        self.old={};self.payload={'format':'future-school-classroom-update-v1','files':{}}
        for name in update.FILES:
            p=self.release/name;p.parent.mkdir(parents=True,exist_ok=True);old=b'# old code\n' if name.endswith('.py') else b'/* old code */';new=old+b'\n';p.write_bytes(old);self.old[name]=old
            self.payload['files'][name]=dict(previous_sha256=update.sha(old),sha256=update.sha(new),base64=base64.b64encode(new).decode())
        files={n:update.sha(b) for n,b in self.old.items()}
        self.payload['previous_runtime_sha256']=update.sha(json.dumps(files,sort_keys=True,separators=(',',':')).encode())
        (self.work/'runtime.private.json').write_text(json.dumps(dict(database=str(self.db),files=files)))
        (self.release/'MANIFEST.json').write_text(json.dumps(dict(contains_data=False,files=files)))
        self.calls=[]
        def service(action):self.calls.append(action);return 'active' if action=='is-active' else ''
        for p in [patch.object(update,'WORK',self.work),patch.object(update,'private'),patch.object(update,'service',side_effect=service),
                  patch.object(update,'health',return_value={'mode':'management_active','tablet_delivery_enabled':True,'wechat_sending_enabled':False}),
                  patch.object(update,'wait_ready',return_value={'mode':'management_active'}),
                  patch.object(update.subprocess,'run',return_value=type('R',(),{'returncode':0})())]:p.start();self.addCleanup(p.stop)

    def test_exact_four_file_update_backs_up_db_and_never_resets_accounts_or_gateway(self):
        r=update.install(self.payload);self.assertTrue(r['updated']);self.assertTrue(r['business_unchanged_during_check']);self.assertFalse(r['database_restored']);self.assertFalse(r['gateway_changed'])
        self.assertEqual(self.calls,['is-active','stop','start'])
        self.assertTrue((Path(r['backup_directory'])/'business.sqlite').exists())
        for name,item in self.payload['files'].items():self.assertEqual(update.sha((self.release/name).read_bytes()),item['sha256'])

    def test_changed_source_or_added_file_is_refused_before_stopping(self):
        name=next(iter(update.FILES));(self.release/name).write_bytes(b'changed by someone else')
        with self.assertRaises(ValueError):update.install(self.payload)
        self.assertEqual(self.calls,[])
        self.payload['files']['business/passwords.json']={}
        with self.assertRaises(ValueError):update.install(self.payload)
        self.assertEqual(self.calls,[])

    def test_first_production_layout_without_manifest_uses_exact_full_runtime_fingerprint(self):
        (self.release/'MANIFEST.json').unlink()
        bad=dict(self.payload,previous_runtime_sha256='0'*64)
        with self.assertRaises(ValueError):update.install(bad)
        self.assertEqual(self.calls,[])
        result=update.install(self.payload);self.assertTrue(result['updated']);self.assertTrue((self.release/'MANIFEST.json').exists())

    def test_poststart_failure_restores_only_code_and_keeps_new_business(self):
        def start(action):
            self.calls.append(action)
            if action=='start' and self.calls.count('start')==1:
                with closing(sqlite3.connect(self.db)) as db,db:db.execute("INSERT INTO business VALUES('new real business must remain')")
            return 'active' if action=='is-active' else ''
        with patch.object(update,'service',side_effect=start),patch.object(update,'wait_ready',side_effect=[ValueError('fictional failure'),{'mode':'management_active'}]):
            with self.assertRaises(ValueError):update.install(self.payload)
        for name,raw in self.old.items():self.assertEqual((self.release/name).read_bytes(),raw)
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM business').fetchone()[0],2)


if __name__=='__main__':unittest.main()
