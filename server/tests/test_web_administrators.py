from contextlib import closing
import copy
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import test_teacher_web as fixture

spec=importlib.util.spec_from_file_location('add_web_admins',Path(__file__).resolve().parents[1]/'ops/add-web-administrators.py');ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)


class WebAdministratorsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TeacherWebTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=self.f.db;self.backup=self.f.f.f.root/'new-admin-backup'
        with closing(sqlite3.connect(self.db)) as db:encoded=db.execute('SELECT password_hash FROM users WHERE login=?',('fictional-teacher',)).fetchone()[0]
        self.names=('fictional-admin-one','fictional-admin-two','fictional-admin-three')
        self.plan={'organization':'fictional','owner_login':'fictional-teacher','confirmed_add_admins':True,'plan_id':'e'*32,'people':[{'id':n*32,'login':login,'display_name':login,'roles':['admin'],'password_hash':encoded} for n,login in zip('678',self.names)],'teacher_correction':{'display_name':'虚构另班老师','source_login':'other-teacher','new_login':'fictional-renamed-teacher','roles':['teacher'],'expected_password_hash':encoded}}
        self.addCleanup(patch.stopall)
        for key,value in [('WANTED',self.names),('DISPLAY_NAMES',self.names),('ORGANIZATION','fictional'),('OWNER','fictional-teacher'),('TEACHER_NAME','虚构另班老师'),('TEACHER_TARGET','fictional-renamed-teacher')]:patch.object(ops,key,value).start()

    def test_add_three_admins_keep_existing_data_and_allow_admin_auto_entry(self):
        with closing(sqlite3.connect(self.db)) as db:before=ops.rows(db,'users');students=ops.rows(db,'students');grants=ops.rows(db,'teacher_classes')
        r=ops.add(self.db,self.plan,self.backup);self.assertEqual(r['created_accounts'],3);self.assertTrue(r['pending_requests_unchanged'])
        self.assertEqual(hashlib.sha256((self.backup/'before.sqlite').read_bytes()).hexdigest(),r['backup_sha256'])
        with self.f.client() as c:
            h=self.f.login(c,self.names[0]);c.workspace_role=None
            response=c.get('/v1/web/weeks',headers=h);self.assertEqual(response.status_code,200);self.assertEqual(response.json()['workspace_role'],'admin')
            self.assertEqual(c.get('/v1/me',headers=h).json()['roles'],['admin'])
            teacher=self.f.login(c,'fictional-renamed-teacher');self.assertEqual(c.get('/v1/me',headers=teacher).json()['roles'],['teacher'])
            self.assertEqual(c.post('/v1/auth/login',json={'organization':'fictional','login':'other-teacher','password':fixture.fixture.PASSWORD}).status_code,401)
        with closing(sqlite3.connect(self.db)) as db:
            expected=[u[:2]+('fictional-renamed-teacher',)+u[3:] if u[2]=='other-teacher' else u for u in before]
            self.assertTrue(set(expected).issubset(set(ops.rows(db,'users'))));self.assertEqual(students,ops.rows(db,'students'));self.assertEqual(grants,ops.rows(db,'teacher_classes'))
        self.assertEqual(ops.add(self.db,self.plan,self.backup)['created_accounts'],0)

    def test_collision_does_not_overwrite_or_partially_add(self):
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('UPDATE users SET login=? WHERE login=?',(self.names[1],'other-teacher'))
        with closing(sqlite3.connect(self.db)) as db:before=ops.rows(db,'users')
        with self.assertRaisesRegex(ValueError,'already exists'):ops.add(self.db,self.plan,self.backup)
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(before,ops.rows(db,'users'))
        self.assertFalse(self.backup.exists())

    def test_failed_third_insert_rolls_back_first_two_without_database_restore(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("CREATE TRIGGER fictional_failure BEFORE INSERT ON users WHEN NEW.login='fictional-admin-three' BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
            before=ops.rows(db,'users')
        with self.assertRaises(sqlite3.IntegrityError):ops.add(self.db,self.plan,self.backup)
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(before,ops.rows(db,'users'))
        self.assertTrue((self.backup/'before.sqlite').exists())

    def test_replay_never_resets_a_subsequently_changed_account(self):
        ops.add(self.db,self.plan,self.backup)
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('UPDATE users SET active=0 WHERE login=?',(self.names[0],))
        with self.assertRaisesRegex(ValueError,'changed'):ops.add(self.db,self.plan,self.backup)
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT active FROM users WHERE login=?',(self.names[0],)).fetchone()[0],0)


if __name__=='__main__':unittest.main()
