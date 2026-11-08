import hashlib,importlib.util,json,sqlite3,unittest
from pathlib import Path
from contextlib import closing
from teacher_web import mirror_store
import test_web_mirror as fixture

spec=importlib.util.spec_from_file_location('current_teacher_ops',Path(__file__).resolve().parents[1]/'ops/add-current-teacher.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)

class CurrentTeacherTests(unittest.TestCase):
    def setUp(self):
        self.w=fixture.WebMirrorTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.db=self.w.db;self.backup=self.w.f.f.f.root/'new-current-teacher'
        with closing(sqlite3.connect(self.db)) as db:
            raw=db.execute('SELECT id,name,course_name FROM classes ORDER BY id LIMIT 1').fetchone()
            password=db.execute('SELECT password_hash FROM users LIMIT 1').fetchone()[0]
        self.plan=dict(format='future-school-current-teacher-v1',confirmed_by_owner=True,plan_id='e'*32,organization='fictional',owner_login='fictional-teacher',
                       person=dict(id='9'*32,login='fictional-new-teacher',display_name='虚构新增老师',password_hash=password),
                       classes=[dict(server_id=raw[0],local_id=self.w.data['classes'][0]['local_id'],name=raw[1],course_name=raw[2])])
        c=self.w.data['classes'][0];c.update(name=raw[1],course_name=raw[2],teacher=self.plan['person']['display_name'])
        self.w.data['lessons'][0].update(class_name=raw[1],teacher=c['teacher'])
        mirror_store.receive(self.db,fixture.TOKEN,self.w.payload(),self.w.f.now)

    def apply(self):return ops.apply(self.db,self.plan,self.backup)
    def test_new_teacher_can_read_mirrored_class_and_business_is_preserved(self):
        with closing(sqlite3.connect(self.db)) as db:before=ops.rows(db,'users');students=ops.rows(db,'students');dataset=ops.rows(db,'web_dataset')
        r=self.apply();self.assertTrue(r['created_account']);self.assertEqual(r['authorized_classes'],1)
        self.assertEqual(hashlib.sha256((self.backup/'before.sqlite').read_bytes()).hexdigest(),r['backup_sha256'])
        with self.w.f.client() as c:
            h=self.w.f.login(c,self.plan['person']['login']);c.workspace_role='teacher'
            self.assertEqual(c.get('/v1/me',headers=h).json()['roles'],['teacher'])
            self.assertEqual(len(c.get('/v1/web/days',headers=h).json()['lessons']),1)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertTrue(set(before).issubset(set(ops.rows(db,'users'))));self.assertEqual(students,ops.rows(db,'students'));self.assertEqual(dataset,ops.rows(db,'web_dataset'))
        self.assertTrue(self.apply()['replayed'])

    def test_username_collision_or_ambiguous_name_never_resets_existing_account(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("UPDATE users SET login=? WHERE login='other-teacher'",(self.plan['person']['login'],));before=ops.rows(db,'users')
        with self.assertRaisesRegex(ValueError,'Existing account'):self.apply()
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(before,ops.rows(db,'users'))
        self.assertFalse(self.backup.exists())

    def test_changed_live_mirror_label_or_base_name_stops_before_backup(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            data=json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0]);data['classes'][0]['teacher']='different'
            db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(data),))
        with self.assertRaisesRegex(ValueError,'mirror teacher'):self.apply()
        self.assertFalse(self.backup.exists())

    def test_existing_exact_teacher_is_reused_without_password_reset_or_other_scope_loss(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("UPDATE users SET login=?,display_name=? WHERE login='other-teacher'",(self.plan['person']['login'],self.plan['person']['display_name']))
            current=db.execute('SELECT id,password_hash FROM users WHERE login=?',(self.plan['person']['login'],)).fetchone()
            old_grants=ops.rows(db,'teacher_classes')
        r=self.apply();self.assertFalse(r['created_account']);self.assertTrue(r['password_preserved']);self.assertEqual(r['user_id'],current[0])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT password_hash FROM users WHERE id=?',(current[0],)).fetchone()[0],current[1])
            scope={x['server_id'] for x in self.plan['classes']}
            self.assertTrue(set(g for g in old_grants if g[2] not in scope).issubset(set(ops.rows(db,'teacher_classes'))))

    def test_mid_transaction_failure_rolls_back_without_restoring_a_database(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("CREATE TRIGGER fail_add BEFORE INSERT ON web_mirror_assignments BEGIN SELECT RAISE(ABORT,'test');END")
            before={t:ops.rows(db,t) for t in ('users','roles','teacher_classes','web_mirror_assignments')}
        with self.assertRaises(sqlite3.IntegrityError):self.apply()
        with closing(sqlite3.connect(self.db)) as db:
            for t,wanted in before.items():self.assertEqual(ops.rows(db,t),wanted)
        self.assertTrue((self.backup/'before.sqlite').exists())

    def test_replay_preserves_subsequently_changed_password_and_refuses_changed_scope(self):
        self.apply()
        with closing(sqlite3.connect(self.db)) as db,db:db.execute("UPDATE users SET password_hash='changed' WHERE login=?",(self.plan['person']['login'],))
        self.assertTrue(self.apply()['password_preserved'])
        with closing(sqlite3.connect(self.db)) as db,db:
            self.assertEqual(db.execute('SELECT password_hash FROM users WHERE login=?',(self.plan['person']['login'],)).fetchone()[0],'changed')
            db.execute('DELETE FROM web_mirror_assignments')
        with self.assertRaisesRegex(ValueError,'Assignments changed'):self.apply()

    def test_migration_pause_and_missing_owner_refuse_without_writing(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('CREATE TABLE management_migration_holds(state TEXT)');db.execute("INSERT INTO management_migration_holds VALUES('held')")
        with self.assertRaisesRegex(ValueError,'Migration is paused'):self.apply()
        self.assertFalse(self.backup.exists())

if __name__=='__main__':unittest.main()
