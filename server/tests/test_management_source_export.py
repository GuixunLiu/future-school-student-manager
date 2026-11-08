from contextlib import closing
import hashlib,importlib.util,json,sqlite3,unittest
import os,shutil,subprocess
from pathlib import Path
from unittest.mock import patch
import test_web_mirror as fixture

spec=importlib.util.spec_from_file_location('management_export',Path(__file__).resolve().parents[1]/'ops/export-management-review.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)

class ManagementExportTests(unittest.TestCase):
    def setUp(self):
        self.w=fixture.WebMirrorTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.db=Path(self.w.db);self.org=fixture.fixture.fixture.fixture.ORG
        self.paths=patch.object(ops,'private_path');self.paths.start();self.addCleanup(self.paths.stop)

    def read(self):return ops.snapshot(self.db,'fictional')
    def test_absent_mirror_is_distinct_from_an_imported_old_dataset(self):
        d=self.read();self.assertFalse(d['mirror']['present']);self.assertEqual(d['mirror']['lessons'],[])
        self.assertTrue(d['archive_available']);self.assertTrue(d['source_links_available']);self.assertEqual(d['source_access'],'read_only')

    def test_archive_identity_and_live_mirror_balances_are_separate_from_old_base(self):
        fixture.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('INSERT INTO archived_students VALUES(?,?,?,?,?,?,?)',('9'*32,self.org,'虚构归档','ARCH001',2500,'',self.w.f.now))
        before=hashlib.sha256(self.db.read_bytes()).hexdigest();d=self.read()
        self.assertEqual(d['archived_students'][0]['external_ref'],'ARCH001');self.assertEqual(d['archived_students'][0]['previous_class_name'],'')
        self.assertEqual(d['mirror']['lessons'][0]['members'][0]['remaining_units'],2500);self.assertEqual(d['mirror']['revision'],1)
        self.assertEqual(len(d['mirror']['covered_weeks']),2);self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)
        self.assertNotIn('password_hash',d['staff'][0]);self.assertTrue(all(isinstance(r['source_id'],str) for r in d['source_links']))

    def test_private_fields_never_leave_whitelist_even_if_present_in_stored_json(self):
        fixture.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)
        marker='SECRET_FACE_PHONE_TOKEN_PASSWORD_MUST_NOT_EXPORT'
        with closing(sqlite3.connect(self.db)) as db,db:
            data=json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0]);data['password']=marker;data['classes'][0]['token']=marker;data['lessons'][0]['members'][0]['faceImage']=marker
            db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(data),));db.execute('UPDATE users SET password_hash=?',(marker,))
        self.assertNotIn(marker,json.dumps(self.read()));self.assertNotIn('token_digest',json.dumps(self.read()))

    def test_wrong_mirror_ownership_or_source_hash_never_exports_partial_data(self):
        fixture.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)
        with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE web_mirror_status SET source_sha256='different'")
        with self.assertRaisesRegex(ValueError,'source differs'):self.read()

    def test_unknown_organization_and_partial_mirror_schema_fail_closed(self):
        with self.assertRaises(ValueError):ops.snapshot(self.db,'other-school')
        fixture.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('DROP TABLE web_mirror_assignments')
        with self.assertRaises(sqlite3.Error):self.read()

    def test_real_python_projection_passes_node_whitelist(self):
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        if not node:self.skipTest('Node runtime required')
        fixture.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)
        script="const fs=require('fs');const d=JSON.parse(fs.readFileSync(0,'utf8'));require('./server/ops/read-management-review.cjs').validateSource(d,'fictional');console.log('ok');"
        r=subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[2],input=json.dumps(self.read()),encoding='utf8',capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),'ok')

if __name__=='__main__':unittest.main()
