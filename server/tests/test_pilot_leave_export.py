import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import test_scoped_pilot as fixture

SOURCE=Path(__file__).resolve().parents[1]/'ops/export-pilot-leaves.py'
spec=importlib.util.spec_from_file_location('leave_export',SOURCE);reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)


class LeaveExportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ScopedPilotTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.stage=self.f.f.root/'read-stage';self.work=self.stage/'teacher-pilot';self.work.mkdir(parents=True)
        self.database=self.work/'business.sqlite';self.database.write_bytes(self.f.database.read_bytes())
        (self.work/'scoped-pilot.py').write_bytes((SOURCE.parent/'scoped-pilot.py').read_bytes())
        digest=hashlib.sha256((self.work/'scoped-pilot.py').read_bytes()).hexdigest()
        cfg={'format':'future-school-scoped-teacher-config','scope':self.f.scope,'files':{'scoped-pilot.py':digest}}
        (self.work/'scoped.private.json').write_text(json.dumps(cfg))
        self.config={'stage_directory':str(self.stage),'runtime_sha256':digest,**{k:self.f.scope[k] for k in ['class_id','lesson_id','student_ids']}}

    def read(self):
        before=self.database.read_bytes()
        with patch.object(reader,'stage_root',return_value=self.stage):result=reader.export(self.config)
        self.assertEqual(before,self.database.read_bytes());self.assertEqual(result['source_access'],'read_only')
        self.assertFalse(result['server_written']);self.assertNotIn('password',json.dumps(result));return result

    def insert_leave(self,device=False):
        # Seed the copied fictional database, never the runtime service.
        db=sqlite3.connect(self.database);org=self.f.scope['organization_id'];actor=fixture.fixture.OWNER;leave='6'*32
        if device:
            actor='7'*32
            db.execute('INSERT INTO devices VALUES(?,?,?,?,?,?,?)',(actor,org,'fictional device','fictional-digest',0,2000000000,1))
        db.execute("INSERT INTO leaves(id,org_id,lesson_id,student_id,status,reason,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",(leave,org,self.f.scope['lesson_id'],fixture.fixture.STUDENT,'approved','private fictional reason',actor,1))
        self.f.api.state.store.audit(db,{'org_id':org,'id':actor},'device_leave_mirrored' if device else 'leave_approved',leave)
        db.commit();db.close();return leave

    def test_empty_export_is_selected_lesson_only_and_source_unchanged(self):
        result=self.read();self.assertEqual(result['leaves'],[]);self.assertEqual(result['arrived_student_ids'],[])
        self.assertEqual(result['student_ids'],self.f.scope['student_ids'])

    def test_teacher_origin_is_audited_but_name_and_reason_not_exported(self):
        leave=self.insert_leave();result=self.read();self.assertEqual(result['leaves'][0]['id'],leave)
        self.assertEqual(result['leaves'][0]['origin'],'teacher');self.assertNotIn('private fictional reason',json.dumps(result))
        self.assertNotIn('name',result['leaves'][0])

    def test_changed_scope_and_unproven_origin_are_refused(self):
        original=self.config['class_id'];self.config['class_id']='5'*32
        with patch.object(reader,'stage_root',return_value=self.stage):
            with self.assertRaises(ValueError):reader.export(self.config)
        self.config['class_id']=original;self.insert_leave()
        db=sqlite3.connect(self.database);db.execute("DELETE FROM audit WHERE action='leave_approved'");db.commit();db.close()
        with patch.object(reader,'stage_root',return_value=self.stage):
            with self.assertRaises(ValueError):reader.export(self.config)

    def test_device_origin_requires_its_audit_record(self):
        self.insert_leave(device=True)
        # This fictional fixture adds a disabled historical device before taking its sealed baseline.
        cfg=json.loads((self.work/'scoped.private.json').read_bytes())
        with sqlite3.connect(self.database) as db:cfg['scope']['master_sha256']=fixture.runtime.seal(db)
        (self.work/'scoped.private.json').write_text(json.dumps(cfg))
        result=self.read()
        self.assertEqual(result['leaves'][0]['origin'],'device')
        self.assertEqual(result['leaves'][0]['recorded_by'],'7'*32)

    def test_changed_balance_cannot_be_reported_as_verified_mapping(self):
        with sqlite3.connect(self.database) as db:db.execute('UPDATE students SET remaining_units=remaining_units+100')
        with patch.object(reader,'stage_root',return_value=self.stage):
            with self.assertRaisesRegex(ValueError,'Protected baseline'):reader.export(self.config)


if __name__=='__main__':unittest.main()
