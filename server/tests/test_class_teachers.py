"""Class permissions, historical ownership and ordered tablet metadata. Fictional only."""
import json
import unittest
from fastapi.testclient import TestClient
from business.store import Problem
from management.api import create_app
from management.class_teachers import ClassTeacherStore
from test_management_delivery import DeliveryTests


class ClassTeacherTests(unittest.TestCase):
    def setUp(self):
        self.f=DeliveryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.base=self.f.f;self.store=ClassTeacherStore(self.base.path,lambda:self.base.now);self.store.initialize_class_teachers()
        with self.store.transaction() as db:
            db.execute("CREATE TABLE management_source_links(org_id TEXT,kind TEXT,local_id INTEGER,server_id TEXT)")
            db.execute('INSERT INTO management_source_links VALUES(?,?,?,?)',(self.base.org,'class',2,'class-a'))
            db.execute("UPDATE users SET display_name='原任教师' WHERE id='teacher'")
            db.execute("UPDATE users SET display_name='新任教师' WHERE id='other-teacher'")
        self.client=TestClient(create_app(self.base.path,lambda:self.base.now,allow_test_client=True),base_url='http://127.0.0.1:18087');self.addCleanup(self.client.close)

    def ready(self):return self.store.metadata_ready(self.f.token,'fixture-generation')
    def change(self,**changes):
        return self.store.assign_teacher(self.base.tokens['owner'],'admin',**(dict(class_id='class-a',teacher_id='other-teacher',expected_revision=0,reason='虚构任课交接',command_ref='handoff-one')|changes))
    def ack(self,item,**changes):
        event=json.loads(item['event_json'])
        return self.store.metadata_ack(self.f.token,'fixture-generation',**(dict(class_id=event['class_id'],revision=event['revision'],sha256=item['sha256'],receipt_token=item['receipt_token'],status='applied')|changes))
    def poll(self):return self.store.metadata_next(self.f.token,'fixture-generation')['item']

    def test_old_tablet_blocks_handoff_and_read_scopes_are_explicit(self):
        before=self.base.state()
        with self.assertRaises(Problem) as e:self.change()
        self.assertEqual(e.exception.code,'tablet_update_required');self.assertEqual(self.base.state(),before)
        data=self.store.class_list(self.base.tokens['owner'],'admin',q='虚构班甲');self.assertEqual(len(data['items']),1);self.assertFalse(data['items'][0]['tablet_ready'])
        self.assertTrue(self.store.class_list(self.base.tokens['principal'],'principal')['read_only'])
        for who in ('teacher','assessor','other-teacher'):
            with self.assertRaises(Problem):self.store.class_list(self.base.tokens[who],'admin')
        with self.assertRaises(Problem):self.store.assign_teacher(self.base.tokens['principal'],'principal',class_id='class-a',teacher_id='teacher',expected_revision=0,reason='无权限',command_ref='no')

    def test_permission_changes_once_without_touching_students_balances_or_attendance(self):
        self.ready();before=self.base.state();a=self.change();self.assertEqual(a['pending_deliveries'],1);self.assertTrue(self.change()['duplicate']);self.assertEqual(self.base.state(),before)
        with self.store.transaction() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT user_id FROM teacher_classes WHERE class_id='class-a'")],['other-teacher'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_class_teacher_events').fetchone()[0],1)
            self.assertEqual(json.loads(db.execute('SELECT teachers_json FROM management_lesson_teacher_snapshots').fetchone()[0])[0]['display_name'],'原任教师')
        with self.assertRaises(Problem) as e:self.change(reason='同编号换内容')
        self.assertEqual(e.exception.code,'idempotency_conflict')
        with self.assertRaises(Problem):self.base.submit()
        fresh=self.store.submit(self.base.tokens['other-teacher'],'teacher',lesson_id='lesson',student_id='student',kind='leave',reason='新教师申请',request_ref='new-teacher',expected_revision=0)
        self.assertEqual(fresh['status'],'pending')

    def test_stale_revision_foreign_class_and_inactive_teacher_are_rejected(self):
        self.ready();self.change()
        for changes in (dict(command_ref='stale'),dict(class_id='foreign'),dict(teacher_id='principal',expected_revision=1,command_ref='invalid-teacher')):
            with self.assertRaises(Problem):self.change(**changes)
        self.assertEqual(self.base.state()[1],(1000,0))

    def test_ordered_durable_delivery_lost_ack_and_no_second_business_change(self):
        self.ready();self.change();first=self.poll();self.assertEqual(first,self.poll())
        self.change(teacher_id='teacher',expected_revision=1,command_ref='handoff-back')
        with self.store.transaction() as db:second=dict(db.execute('SELECT * FROM management_class_teacher_events ORDER BY seq DESC LIMIT 1').fetchone())
        with self.assertRaises(Problem):self.ack(dict(event_json=second['payload'],sha256=second['sha256'],receipt_token=second['receipt_token']))
        before=self.base.state();self.assertFalse(self.ack(first)['duplicate']);self.assertTrue(self.ack(first)['duplicate']);self.assertNotEqual(self.poll()['event_json'],first['event_json'])
        self.ack(self.poll());self.assertIsNone(self.poll());self.assertEqual(self.base.state(),before)
        with self.assertRaises(Problem):self.ack(first,status='conflict')

    def test_device_identity_revocation_and_receipt_tamper_are_checked_each_time(self):
        for token,generation in ((self.base.tokens['owner'],'fixture-generation'),(self.f.token,'wrong')):
            with self.assertRaises(Problem):self.store.metadata_ready(token,generation)
        self.ready();self.change();first=self.poll()
        with self.assertRaises(Problem):self.ack(first,sha256='0'*64)
        with self.store.transaction() as db:db.execute('DELETE FROM device_classes')
        with self.assertRaises(Problem):self.ack(first)
        self.assertIsNone(self.poll())

    def test_historical_report_keeps_old_teacher_future_report_uses_new_teacher(self):
        with self.store.transaction() as db:
            db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,?,?,?,?)',(self.base.org,'future','student','class-a',self.base.now+100,1,150,'missing',0))
        self.ready();self.change();report=self.store.weekly_report(self.base.tokens['owner'],'admin',start='1969-12-29')
        rows=report['sections'][0]['report']['teachers'];self.assertEqual({r['name']:r['expected'] for r in rows},{'原任教师':1,'新任教师':1})
        self.change(teacher_id='teacher',expected_revision=1,command_ref='back')
        with self.store.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT teachers_json FROM management_lesson_teacher_snapshots').fetchone()[0])[0]['display_name'],'原任教师')

    def test_http_never_auto_migrates_and_validation_is_strict(self):
        headers={'Authorization':'Bearer '+self.base.tokens['owner']}
        self.assertTrue(self.client.get('/v1/management/workspace?workspace_role=admin',headers=headers).json()['class_teachers_enabled'])
        self.assertEqual(self.client.get('/v1/management/classes?workspace_role=teacher',headers=headers).status_code,422)
        self.assertEqual(self.client.post('/v1/management/classes/class-a/teacher',headers=headers,json=dict(workspace_role='admin',teacher_id='other-teacher',expected_revision=True,reason='测试',command_ref='strict')).status_code,422)
        self.assertEqual(self.client.post('/v1/management/device/class-ready',headers=headers,json={'generation':'fixture-generation'}).status_code,403)

    def test_retained_server_only_classes_are_not_current_or_editable(self):
        with self.store.transaction() as db:
            db.execute('CREATE TABLE management_initializations(org_id TEXT)');db.execute('INSERT INTO management_initializations VALUES(?)',(self.base.org,))
        rows=self.store.class_list(self.base.tokens['owner'],'admin')['items'];self.assertEqual([r['id'] for r in rows],['class-a'])
        with self.assertRaises(Problem) as error:self.change(class_id='class-b')
        self.assertEqual(error.exception.code,'class_not_current')

    def test_without_explicit_tables_startup_and_http_do_not_install_feature(self):
        names=('management_class_versions','management_class_commands','management_lesson_teacher_snapshots','management_metadata_clients','management_class_teacher_events')
        with self.store.transaction() as db:
            for name in names:db.execute('DROP TABLE '+name)
        app=create_app(self.base.path,lambda:self.base.now,allow_test_client=True)
        with TestClient(app,base_url='http://127.0.0.1:18087') as client:
            h={'Authorization':'Bearer '+self.base.tokens['owner']}
            self.assertFalse(client.get('/v1/management/workspace?workspace_role=admin',headers=h).json()['class_teachers_enabled'])
            self.assertEqual(client.get('/v1/management/classes?workspace_role=admin',headers=h).status_code,404)
        with self.store.transaction() as db:self.assertFalse(set(names)&{r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")})

    def test_empty_unbound_class_cannot_silently_change_only_web_copy(self):
        with self.store.transaction() as db:db.execute('DELETE FROM device_classes')
        c=next(c for c in self.store.class_list(self.base.tokens['owner'],'admin')['items'] if c['id']=='class-a')
        self.assertFalse(c['tablet_bound']);self.assertFalse(c['tablet_ready'])
        with self.assertRaises(Problem) as error:self.change()
        self.assertEqual(error.exception.code,'class_device_missing')


if __name__=='__main__':unittest.main()
