from contextlib import closing
import copy
import hashlib
import json
import unittest
from teacher_web.mirror_store import initialize,receive,MirrorError
import test_teacher_web as fixture

TOKEN='wxd_'+'A'*43
DEVICE='d'*32


class WebMirrorTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TeacherWebTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=self.f.db;initialize(self.db)
        with closing(fixture.sqlite3.connect(self.db)) as db,db:db.execute('INSERT INTO web_mirror_devices VALUES(?,?,?,?,?,?,?,?)',(DEVICE,fixture.fixture.fixture.ORG,hashlib.sha256(TOKEN.encode()).hexdigest(),1,self.f.now+86400,0,None,None))
        self.data=copy.deepcopy(self.f.data);self.data.pop('source_sha256')
        self.data['students']=[{'local_id':'22','name':'虚构学生','external_ref':'TEST001','remaining_units':2500}]
        self.data['lessons'][0]['members'][0].update(remaining_units=2500,makeup=False)

    def payload(self,revision=1,data=None,reference=None):
        return json.dumps({'protocol':1,'device_id':DEVICE,'revision':revision,'request_ref':reference or format(revision,'032x'),'data':data or self.data},ensure_ascii=False,separators=(',',':')).encode()

    def test_current_mirror_updates_view_without_replaying_or_changing_accounts_and_balances(self):
        with closing(fixture.sqlite3.connect(self.db)) as db:users=db.execute('SELECT * FROM users').fetchall();roles=db.execute('SELECT * FROM roles').fetchall();students=db.execute('SELECT * FROM students').fetchall()
        raw=self.payload();r=receive(self.db,TOKEN,raw,self.f.now);self.assertFalse(r['hours_deducted']);self.assertEqual(receive(self.db,TOKEN,raw,self.f.now)['duplicate'],True)
        with self.f.client() as c:
            h=self.f.login(c);day=c.get('/v1/web/days',headers=h).json();self.assertEqual(day['lessons'][0]['members'][0]['remaining_units'],2500);self.assertEqual(day['mirror']['revision'],1)
        with closing(fixture.sqlite3.connect(self.db)) as db:
            self.assertEqual(users,db.execute('SELECT * FROM users').fetchall());self.assertEqual(roles,db.execute('SELECT * FROM roles').fetchall());self.assertEqual(students,db.execute('SELECT * FROM students').fetchall())

    def test_teacher_reassignment_changes_web_access_immediately_without_editing_roles(self):
        data=copy.deepcopy(self.data);data['classes'][0]['teacher']='虚构另班老师'
        receive(self.db,TOKEN,self.payload(data=data),self.f.now)
        with self.f.client() as c:
            h=self.f.login(c);c.workspace_role='teacher';self.assertEqual(c.get('/v1/web/days',headers=h).json()['lessons'],[])
            other=self.f.login(c,'other-teacher');self.assertEqual(len(c.get('/v1/web/days',headers=other).json()['lessons']),1)

    def test_unknown_teacher_does_not_auto_create_account_or_retain_old_teacher_access(self):
        data=copy.deepcopy(self.data);data['classes'][0]['teacher']='未开户虚构老师'
        receive(self.db,TOKEN,self.payload(data=data),self.f.now)
        with self.f.client() as c:
            h=self.f.login(c);c.workspace_role='teacher';self.assertEqual(c.get('/v1/web/days',headers=h).json()['lessons'],[])
            c.workspace_role='admin';self.assertEqual(len(c.get('/v1/web/days',headers=h).json()['mirror']['teacher_warnings']),1)

    def test_reused_reference_stale_revision_and_invalid_device_never_overwrite_latest(self):
        raw=self.payload(2);receive(self.db,TOKEN,raw,self.f.now)
        for body,token in [(self.payload(2,data={**self.data,'source_records':2}),TOKEN),(self.payload(1),TOKEN),(self.payload(3),'wxd_'+'B'*43)]:
            with self.assertRaises(MirrorError):receive(self.db,token,body,self.f.now)
        with self.f.client() as c:h=self.f.login(c);self.assertEqual(c.get('/v1/web/sync-status',headers=h).json()['mirror']['revision'],2)

    def test_secret_or_face_fields_are_rejected_and_arrival_resolves_only_pending_request(self):
        bad=copy.deepcopy(self.data);bad['students'][0]['faceImage']='DO_NOT_UPLOAD'
        with self.assertRaises(MirrorError):receive(self.db,TOKEN,self.payload(data=bad),self.f.now)
        with self.f.client() as c:h=self.f.login(c);self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.f.request()).status_code,201)
        data=copy.deepcopy(self.data);data['lessons'][0].update(arrived=1);data['lessons'][0]['members'][0].update(status='arrived',attendance_time='16:00')
        receive(self.db,TOKEN,self.payload(data=data),self.f.now)
        with self.f.client() as c:
            h=self.f.login(c);self.assertEqual(c.get('/v1/web/requests',headers=h).json()['items'][0]['status'],'observed_arrived')

    def test_migration_pause_blocks_new_snapshot_and_identical_retry_database_wide(self):
        raw=self.payload();receive(self.db,TOKEN,raw,self.f.now)
        with closing(fixture.sqlite3.connect(self.db)) as db,db:
            before=db.execute('SELECT * FROM web_dataset').fetchall()
            db.execute('CREATE TABLE management_migration_holds(org_id TEXT,state TEXT)')
            db.execute("INSERT INTO management_migration_holds VALUES('another-org','held')")
        for body in (raw,self.payload(2)):
            with self.assertRaises(MirrorError) as error:receive(self.db,TOKEN,body,self.f.now)
            self.assertEqual(error.exception.code,'migration_in_progress')
        with closing(fixture.sqlite3.connect(self.db)) as db,db:
            self.assertEqual(before,db.execute('SELECT * FROM web_dataset').fetchall());self.assertEqual(db.execute('SELECT revision FROM web_mirror_devices').fetchone()[0],1)
            db.execute("UPDATE management_migration_holds SET state='cancelled'")
        self.assertTrue(receive(self.db,TOKEN,raw,self.f.now)['duplicate'])
        self.assertEqual(receive(self.db,TOKEN,self.payload(2),self.f.now)['revision'],2)

    def test_partial_canonical_schema_without_guard_refuses_legacy_write(self):
        with closing(fixture.sqlite3.connect(self.db)) as db,db:db.execute('CREATE TABLE management_balances(org_id TEXT,units INTEGER)')
        with self.assertRaises(MirrorError) as error:receive(self.db,TOKEN,self.payload(),self.f.now)
        self.assertEqual(error.exception.code,'migration_guard_missing')


if __name__=='__main__':unittest.main()
