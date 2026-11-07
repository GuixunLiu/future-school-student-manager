"""Fictional full SQLite copies, NOT reconstructed real production credentials."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from business.store import Store
from business_next.archive import initialize as archive_schema
from teacher_web.mirror_store import initialize as mirror_schema, receive
from management import initialization as init
from management.api import create_app


class InitializationTests(unittest.TestCase):
    def node(self,script,value):
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        if not node:self.fail('Node is required for cross-language reviewed inputs')
        r=subprocess.run([node,'-e',script],input=json.dumps(value),cwd=Path(__file__).parents[2],capture_output=True,encoding='utf-8')
        if r.returncode:self.fail(r.stderr)
        return json.loads(r.stdout)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'source.sqlite';self.target=self.root/'isolated'
        self.now=1793505600  # 2026-11-01, fictional clock only
        store=Store.bootstrap(self.source,'fictional','owner','虚构管理员','Fictional initialization test password 2026!')
        archive_schema(self.source)
        self.raw=dict(classes=[dict(id=11,name='虚构班',courseName='虚构课程',teacher='虚构老师',scheduleWeekday=6,scheduleStart='14:00',scheduleEnd='15:30')],
            students=[dict(id=21,name='已有学生',studentNo='001',className='虚构班',hours=9),
                      dict(id=22,name='新增基础身份',studentNo='002',className='虚构班',hours=10),
                      dict(id=23,name='已停课学生',studentNo='003',className='',hours=15,longTermAbsent=True,pausedClassName='旧班'),
                      dict(id=24,name='已归档学生',studentNo='004',className='',hours=12.25,longTermAbsent=True,pausedClassName='已删除班')],
            attendance=[dict(id=100,studentId=21,classId=99,className='已删除班',date='2026-10-24',time='14:00',method='manual')],
            transactions=[dict(id=101,studentId=21,classId=99,className='已删除班',date='2026-10-24',amount=-1,attendanceId=100)],settings={})
        initial=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));const h=require('./server/ops/build-legacy-history-archive.cjs'),raw=JSON.stringify(v);console.log(JSON.stringify({raw,archive:h.encoded(h.build(raw).artifact),data:require('./server/ops/build-teacher-web-data.cjs').build(v,{asOfDate:'2026-11-01',sourceSha256:'f'.repeat(64),sourceCapturedAt:'Fictional source',mirror:true})}));",self.raw)
        self.source_raw=initial['raw']
        with store.transaction() as db:
            self.org=db.execute('SELECT id FROM organizations').fetchone()[0]
            for key,name in [('d'*32,'虚构老师'),('e'*32,'旧任课老师')]:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,1,0)',(key,self.org,'fixture-'+key,name,'fictional hash, must be preserved'))
                db.execute("INSERT INTO roles VALUES(?,?,'teacher')",(self.org,key))
            for key,name in [('c'*32,'虚构班'),('f'*32,'服务器独有班')]:
                db.execute('INSERT INTO classes VALUES(?,?,?,?,0)',(key,self.org,name,'虚构课程'))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(self.org,'e'*32,'c'*32))
            for key,ref,name in [('a'*32,'001','已有学生'),('8'*32,'003','已停课学生'),('7'*32,'SERVER','服务器独有学生')]:
                db.execute('INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active) VALUES(?,?,?,?,?,9999,1)',(key,self.org,'c'*32,name,ref))
            db.execute('INSERT INTO archived_students VALUES(?,?,?,?,?,?,0)',('b'*32,self.org,'已归档学生','004',1225,'已删除班'))
            db.execute('INSERT INTO roster_imports VALUES(?,?,?,?,?,?,?)',(self.org,'b'*64,'a'*64,'2026-10-24',0,'c'*64,'{}'))
            for kind,local,key in [('class',11,'c'*32),('student',21,'a'*32),('student',23,'8'*32),('student',24,'b'*32)]:
                db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(self.org,kind,local,key,'b'*64))
            db.execute('CREATE TABLE unknown_private_table(id TEXT PRIMARY KEY,payload BLOB)')
            db.execute('INSERT INTO unknown_private_table VALUES(?,?)',('opaque',b'\x00\xffPRIVATE'))
        data=initial['data'];mirror_schema(self.source)
        token='wxd_'+'A'*43
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('CREATE TABLE web_dataset(id INTEGER PRIMARY KEY,data_json TEXT NOT NULL,source_sha256 TEXT NOT NULL,imported_at INTEGER NOT NULL)')
            db.execute('CREATE TABLE web_class_links(local_id TEXT PRIMARY KEY,remote_id TEXT NOT NULL)')
            db.execute('CREATE TABLE web_makeup_requests(id TEXT PRIMARY KEY,org_id TEXT,lesson_key TEXT,student_local_id TEXT,requested_by TEXT,request_ref TEXT,payload_digest TEXT,reason TEXT,status TEXT,source_sha256 TEXT,created_at INTEGER)')
            db.execute('INSERT INTO web_dataset VALUES(1,?,?,?)',(json.dumps(data),'f'*64,self.now))
            db.execute('INSERT INTO web_class_links VALUES(?,?)',('11','c'*32))
            db.execute('INSERT INTO web_mirror_devices VALUES(?,?,?,?,?,0,NULL,NULL)',('6'*32,self.org,init.sha(token.encode()),1,self.now+86400))
            db.execute('INSERT INTO web_makeup_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',('old-request',self.org,'11:2026-10-31','21','d'*32,'old-ref','digest','Preserve old pending request','pending','a'*64,0))
        data.pop('source_sha256',None)
        receive(self.source,token,json.dumps(dict(protocol=1,device_id='6'*32,revision=1,request_ref='5'*32,data=data)).encode(),self.now)
        self.helper=init.export_helper()
        self.privacy=patch.object(self.helper,'private_path');self.privacy.start();self.addCleanup(self.privacy.stop)
        review=self.helper.snapshot(self.source,'fictional');self.review_raw=json.dumps(review,separators=(',',':')).encode()
        self.archive_raw=initial['archive'].encode()
        plan=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));const h=require('./server/ops/build-legacy-history-archive.cjs');console.log(JSON.stringify(h.encoded(require('./server/ops/build-management-initialization-plan.cjs').build(v.raw,v.review,{organization:'fictional',newId:()=> '9'.repeat(32)}).plan)));",dict(raw=initial['raw'],review=self.review_raw.decode()))
        self.plan_raw=plan.encode();self.plan=json.loads(plan)
        with closing(sqlite3.connect(self.source)) as db:
            db.row_factory=sqlite3.Row;self.before=init.fingerprints(db)
        self.bytes_before=self.source.read_bytes()

    def run_copy(self,**changes):
        args=dict(plan_raw=self.plan_raw,plan_sha256=init.sha(self.plan_raw),archive_raw=self.archive_raw,review_raw=self.review_raw,now=self.now)|changes
        with patch.object(init,'export_helper',return_value=self.helper):
            return init.rehearse(self.source,self.target,**args)

    def open_copy(self):
        db=sqlite3.connect(self.target/'rehearsal.sqlite');db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db

    def test_complete_copy_preserves_accounts_requests_archives_and_opens_without_replay(self):
        receipt=self.run_copy();self.assertTrue(receipt['rehearsal_only']);self.assertTrue(receipt['teacher_grants_aligned']);self.assertEqual(receipt['events_replayed'],0)
        self.assertEqual(self.source.read_bytes(),self.bytes_before)
        with closing(self.open_copy()) as db:
            after=init.fingerprints(db)
            for table,value in self.before.items():
                if table not in {'students','teacher_classes'}:self.assertEqual(after[table],value,table)
            self.assertEqual(db.execute('SELECT units FROM management_balances WHERE student_id=?',('a'*32,)).fetchone()[0],900)
            self.assertEqual(db.execute('SELECT SUM(units) FROM management_openings').fetchone()[0],1900)
            self.assertEqual(db.execute('SELECT active FROM students WHERE id=?',('8'*32,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT remaining_units FROM students WHERE id=?',('7'*32,)).fetchone()[0],9999)
            self.assertEqual(db.execute('SELECT count(*) FROM management_source_links').fetchone()[0],5)
            self.assertIsNone(db.execute('PRAGMA foreign_key_check').fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM management_outbox').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM management_device_access').fetchone()[0],0)
            stored=db.execute('SELECT archive_json FROM management_initializations').fetchone()[0]
            self.assertEqual(stored,self.archive_raw.decode())

    def test_sealed_rows_openings_and_marker_are_immutable_and_rehearsal_cannot_serve(self):
        self.run_copy()
        with closing(self.open_copy()) as db:
            for table in ('management_initializations','management_legacy_rows','management_openings','management_source_links','management_initialization_seals'):
                with self.assertRaisesRegex(sqlite3.IntegrityError,'immutable'):db.execute('DELETE FROM '+table)
                db.rollback()
            key=db.execute('SELECT id FROM management_initializations').fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError,'sealed'):db.execute('INSERT INTO management_legacy_rows VALUES(?,?,?,?)',(key,'attendance',999,'{}'))
            db.rollback()
            self.assertEqual(json.loads(db.execute("SELECT payload FROM management_legacy_rows WHERE section='attendance'").fetchone()[0]),self.raw['attendance'][0])
        with self.assertRaisesRegex(ValueError,'Offline rehearsal'):create_app(self.target/'rehearsal.sqlite')

    def test_changed_current_balance_teacher_mirror_or_account_refuses_before_population(self):
        for sql in ("UPDATE students SET remaining_units=123 WHERE external_ref='001'","UPDATE users SET display_name='Changed' WHERE id='"+'d'*32+"'","UPDATE web_mirror_status SET revision=2"):
            with self.subTest(sql=sql):
                with closing(sqlite3.connect(self.source)) as db,db:db.execute(sql)
                with self.assertRaisesRegex(ValueError,'differs from reviewed'):self.run_copy()
                self.assertFalse(self.target.exists())
                with closing(sqlite3.connect(self.source)) as db:
                    db.executescript("UPDATE students SET remaining_units=9999 WHERE external_ref='001'; UPDATE users SET display_name='虚构老师' WHERE id='"+'d'*32+"'; UPDATE web_mirror_status SET revision=1;");db.commit()

    def test_tampered_inputs_and_wrong_opening_or_dropped_archive_stop_without_source_write(self):
        with self.assertRaises(ValueError):self.run_copy(archive_raw=self.archive_raw+b' ')
        for change in (lambda p:p['canonical_openings'].pop(),lambda p:p['students'][0].update(opening_balance_units=800),lambda p:p.update(preserve_archive_ids=[]),lambda p:p.update(activation_authorized=True)):
            p=copy.deepcopy(self.plan);change(p);raw=json.dumps(p).encode()
            with self.assertRaises(ValueError):self.run_copy(plan_raw=raw,plan_sha256=init.sha(raw))
            self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(),self.bytes_before)

    def test_existing_output_and_git_directory_are_never_overwritten(self):
        self.target.mkdir();marker=self.target/'keep';marker.write_text('untouched')
        with self.assertRaises(ValueError):self.run_copy()
        self.assertEqual(marker.read_text(),'untouched');marker.unlink();self.target.rmdir()
        (self.root/'.git').mkdir()
        with self.assertRaisesRegex(ValueError,'outside Git'):self.run_copy()
        self.assertFalse(self.target.exists())

    def test_trigger_side_effect_rolls_back_schema_and_copy_is_cleaned(self):
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute("CREATE TRIGGER break_private AFTER UPDATE ON students BEGIN DELETE FROM unknown_private_table; END")
        with self.assertRaisesRegex(ValueError,'other existing table changed'):self.run_copy()
        self.assertFalse(self.target.exists())
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM unknown_private_table').fetchone()[0],1)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='management_initializations'").fetchone())

    def test_raw_history_and_source_ids_preserved_after_restart_and_duplicate_run_refused(self):
        self.run_copy()
        with closing(self.open_copy()) as db:
            self.assertEqual(json.loads(db.execute("SELECT payload FROM management_legacy_rows WHERE section='transactions'").fetchone()[0]),self.raw['transactions'][0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM roster_source_links').fetchone()[0],4)
        with self.assertRaises(ValueError):self.run_copy()
        self.assertEqual(self.source.read_bytes(),self.bytes_before)

    def test_event_injection_cannot_leave_partial_candidate(self):
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute("CREATE TRIGGER inject_event AFTER UPDATE ON students BEGIN INSERT INTO management_audit(org_id,actor_id,workspace_role,action,entity_id,detail,created_at) VALUES('fake','fake','admin','fake','fake','{}',0); END")
        with self.assertRaisesRegex(ValueError,'Unexpected event'):self.run_copy()
        self.assertFalse(self.target.exists())
        with closing(sqlite3.connect(self.source)) as db:self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='management_audit'").fetchone())

    def test_trigger_cannot_change_reserved_student_identity_or_balance(self):
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute("CREATE TRIGGER change_reserved AFTER INSERT ON students BEGIN UPDATE students SET remaining_units=333 WHERE id=NEW.id; END")
        with self.assertRaisesRegex(ValueError,'Reserved student insert differs'):self.run_copy()
        self.assertFalse(self.target.exists())
        with closing(sqlite3.connect(self.source)) as db:
            self.assertFalse(db.execute('SELECT 1 FROM students WHERE id=?',('9'*32,)).fetchone())

    def test_whole_school_366_active_and_43_classes_copy_without_replaying_history(self):
        # Grow the fictional fixture to the real scope size, without borrowing
        # any real account hashes, roster IDs or student records.
        with closing(sqlite3.connect(self.source)) as db,db:
            for i in range(42):
                local=100+i;key=format(4000+i,'032x');name='虚构分班'+str(i)
                self.raw['classes'].append(dict(id=local,name=name,courseName='虚构课程',teacher='虚构老师',scheduleWeekday=6,scheduleStart='14:00',scheduleEnd='15:30'))
                db.execute('INSERT INTO classes VALUES(?,?,?,?,0)',(key,self.org,name,'虚构课程'))
                db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(self.org,'e'*32,key))
                db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(self.org,'class',local,key,'b'*64))
            for i in range(364):
                local=1000+i;key=format(10000+i,'032x');c=self.raw['classes'][i%43]
                student=dict(id=local,name='虚构学生'+str(i),studentNo='TEST'+str(i),className=c['name'],hours=8.5)
                self.raw['students'].append(student)
                class_key='c'*32 if c['id']==11 else format(4000+c['id']-100,'032x')
                db.execute('INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,999)',(key,self.org,class_key,student['name'],student['studentNo']))
                db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(self.org,'student',local,key,'b'*64))
        projected=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8')),h=require('./server/ops/build-legacy-history-archive.cjs'),raw=JSON.stringify(v);console.log(JSON.stringify({raw,archive:h.encoded(h.build(raw).artifact),data:require('./server/ops/build-teacher-web-data.cjs').build(v,{asOfDate:'2026-11-01',sourceSha256:'f'.repeat(64),sourceCapturedAt:'Fictional full scope',mirror:true})}));",self.raw)
        data=projected['data'];data.pop('source_sha256',None)
        receive(self.source,'wxd_'+'A'*43,json.dumps(dict(protocol=1,device_id='6'*32,revision=2,request_ref='4'*32,data=data)).encode(),self.now)
        self.review_raw=json.dumps(self.helper.snapshot(self.source,'fictional'),separators=(',',':')).encode()
        self.archive_raw=projected['archive'].encode()
        plan=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8')),h=require('./server/ops/build-legacy-history-archive.cjs');console.log(JSON.stringify(h.encoded(require('./server/ops/build-management-initialization-plan.cjs').build(v.raw,v.review,{organization:'fictional',newId:()=> '9'.repeat(32)}).plan)));",dict(raw=projected['raw'],review=self.review_raw.decode()))
        self.plan_raw=plan.encode();self.plan=json.loads(plan);self.source_raw=projected['raw'];source_before=self.source.read_bytes()
        result=self.run_copy()
        self.assertEqual(result['scope']['active_students'],366);self.assertEqual(result['scope']['current_classes'],43)
        with closing(self.open_copy()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_balances').fetchone()[0],366)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_openings').fetchone()[0],366)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_attendance').fetchone()[0],0)
        self.assertEqual(self.source.read_bytes(),source_before)


if __name__=='__main__':unittest.main()
