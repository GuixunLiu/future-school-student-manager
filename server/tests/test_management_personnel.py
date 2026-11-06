"""Personnel authorization, session revocation and retry safety. Fictional only."""
from contextlib import closing
import json
import unittest

from fastapi.testclient import TestClient
from business.security import verify_password
from business.store import Problem
from management.api import create_app
from management.personnel import PersonnelStore
from management.personnel_api import install_personnel_routes
from test_management_rules import ApprovalTests


class PersonnelTests(unittest.TestCase):
    def setUp(self):
        self.f=ApprovalTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        from management.delivery import DeliveryStore
        DeliveryStore(self.f.path,lambda:self.f.now).initialize()
        self.store=PersonnelStore(self.f.path,lambda:self.f.now);self.store.initialize_personnel()

    def create(self,**changes):
        return self.store.staff_save(self.f.tokens['owner'],'admin',**(dict(login='new-person',display_name='虚构新增人员',roles=['teacher','assessor'],active=True,approval_class_ids=['class-a'],password='123456',reason='虚构新增',command_ref='create-person')|changes))

    def person(self,who):return next(v for v in self.store.staff_list(self.f.tokens['owner'],'admin')['items'] if v['id']==who)

    def update(self,who,**changes):
        p=self.person(who)
        return self.store.staff_save(self.f.tokens['owner'],'admin',**(dict(user_id=who,login=p['login'],display_name=p['display_name'],roles=p['roles'],active=p['active'],approval_class_ids=p['approval_class_ids'],expected_revision=p['revision'],password=None,reason='虚构变更',command_ref='edit-'+who)|changes))

    def test_create_dual_identity_short_password_idempotency_and_no_secret_in_list_or_audit(self):
        a=self.create();b=self.create();self.assertEqual(a['id'],b['id']);self.assertTrue(b['duplicate'])
        with self.assertRaises(Problem) as error:self.create(password='654321')
        self.assertEqual(error.exception.code,'idempotency_conflict')
        record=self.person(a['id']);self.assertEqual(record['roles'],['assessor','teacher']);self.assertEqual(record['approval_class_ids'],['class-a'])
        self.assertNotIn('password',json.dumps(record))
        with self.store.transaction() as db:
            user=db.execute('SELECT password_hash FROM users WHERE id=?',(a['id'],)).fetchone()[0];self.assertTrue(verify_password('123456',user))
            audit=db.execute("SELECT detail FROM management_audit WHERE action='staff_created'").fetchone()[0];self.assertNotIn('123456',audit)
            self.assertTrue(db.execute('SELECT password_proof FROM management_staff_commands').fetchone()[0].startswith('pbkdf2_sha256$600000$'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_staff_commands').fetchone()[0],1)

    def test_login_password_role_scope_edit_revokes_sessions_preserves_teaching_and_balances(self):
        before=self.f.state();r=self.update('teacher',login='renamed-teacher',roles=['teacher','assessor'],approval_class_ids=['class-b'],password='123456')
        self.assertTrue(r['sessions_revoked']);self.assertFalse(r['balances_changed']);self.assertEqual(self.f.state(),before)
        with self.assertRaises(Problem):self.store.command(self.f.tokens['teacher'],lambda db,a:None)
        session=self.store.login('fictional','renamed-teacher','123456','personnel-test')['access_token']
        actor=self.store.command(session,lambda db,a:a);self.assertEqual(actor['roles'],{'teacher','assessor'})
        row=self.person('teacher');self.assertEqual(row['teaching_class_ids'],['class-a']);self.assertEqual(row['approval_class_ids'],['class-b']);self.assertEqual(row['revision'],1)

    def test_selected_role_and_all_nonadmins_cannot_read_or_write_personnel(self):
        for who in ('teacher','assessor','principal','other-teacher'):
            with self.assertRaises(Problem):self.store.staff_list(self.f.tokens[who],who if who!='other-teacher' else 'teacher')
            with self.assertRaises(Problem):self.store.staff_list(self.f.tokens[who],'admin')
        with self.store.transaction() as db:db.execute('INSERT INTO roles VALUES(?,?,?)',(self.f.org,self.f.owner,'teacher'))
        with self.assertRaises(Problem):self.store.staff_list(self.f.tokens['owner'],'teacher')

    def test_last_active_admin_is_protected_but_explicit_handoff_can_demote_self(self):
        with self.assertRaises(Problem) as e:self.update(self.f.owner,active=False)
        self.assertEqual(e.exception.code,'last_admin')
        with self.assertRaises(Problem):self.update(self.f.owner,roles=['teacher'])
        self.create(roles=['admin'],approval_class_ids=[],active=False)
        with self.assertRaises(Problem):self.update(self.f.owner,roles=['teacher'])
        person=next(v for v in self.store.staff_list(self.f.tokens['owner'],'admin')['items'] if v['login']=='new-person')
        self.update(person['id'],active=True)
        result=self.update(self.f.owner,roles=['teacher']);self.assertTrue(result['force_relogin'])
        with self.assertRaises(Problem):self.store.staff_list(self.f.tokens['owner'],'admin')

    def test_stale_revision_foreign_scope_or_foreign_person_never_changes_data(self):
        self.update('principal',display_name='虚构校长新名称')
        with self.assertRaises(Problem) as e:self.update('principal',expected_revision=0,command_ref='stale')
        self.assertEqual(e.exception.code,'staff_changed')
        with self.store.transaction() as db:
            db.execute('INSERT INTO organizations VALUES(?,?,?)',('other-org','other-fictional',self.f.now))
            db.execute('INSERT INTO classes VALUES(?,?,?,?,?)',('foreign-class','other-org','外部班','课程',self.f.now))
            proof=db.execute('SELECT password_hash FROM users WHERE id=?',(self.f.owner,)).fetchone()[0]
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',('foreign-person','other-org','foreign-person','外部人员',proof,1,self.f.now))
            db.execute('INSERT INTO roles VALUES(?,?,?)',('other-org','foreign-person','admin'))
        with self.assertRaises(Problem):self.create(approval_class_ids=['foreign-class'])
        with self.assertRaises(Problem):self.store.staff_save(self.f.tokens['owner'],'admin',user_id='foreign-person',login='foreign-person',display_name='不可修改',roles=['admin'],active=True,approval_class_ids=[],expected_revision=0,reason='虚构跨机构',command_ref='foreign')
        self.assertNotIn('外部人员',json.dumps(self.store.staff_list(self.f.tokens['owner'],'admin'),ensure_ascii=False))

    def test_bound_teacher_name_role_and_deactivation_require_real_handoff(self):
        for changes in [dict(active=False),dict(roles=['admin']),dict(display_name='不能单独修改任课姓名')]:
            with self.assertRaises(Problem):self.update('teacher',**changes)
        self.assertEqual(self.person('teacher')['revision'],0)

    def test_parent_identity_and_links_are_preserved_when_staff_roles_change(self):
        with self.store.transaction() as db:
            db.execute('INSERT INTO roles VALUES(?,?,?)',(self.f.org,'principal','parent'))
            db.execute('INSERT INTO test_parent_links VALUES(?,?,?,?)',(self.f.org,'principal','student',self.f.now))
        before=self.f.state();self.update('principal',roles=['admin'])
        with self.store.transaction() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT role FROM roles WHERE user_id=?',('principal',))},{'admin','parent'})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM test_parent_links WHERE user_id=?',('principal',)).fetchone()[0],1)
        self.assertEqual(self.f.state(),before)

    def test_http_routes_require_explicit_installation_and_reject_extra_fields(self):
        app=create_app(self.f.path,lambda:self.f.now,allow_test_client=True);install_personnel_routes(app,self.f.path,lambda:self.f.now)
        with TestClient(app,base_url='http://127.0.0.1:18087') as client:
            path='/v1/management/people?workspace_role=admin';headers={'Authorization':'Bearer '+self.f.tokens['owner']}
            self.assertEqual(client.get(path).status_code,401)
            self.assertEqual(client.get(path,headers={'Authorization':'Bearer '+self.f.tokens['teacher']}).status_code,403)
            data=client.get(path+'&limit=1',headers=headers);self.assertEqual(data.status_code,200);self.assertIsNotNone(data.json()['next_before'])
            self.assertEqual(client.post('/v1/management/people',headers=headers,json={'forged':True}).status_code,422)
            body=dict(workspace_role='admin',login='api-person',display_name='虚构HTTP人员',roles=['admin'],active=True,password='123456',reason='虚构HTTP新增',command_ref='http-create')
            created=client.post('/v1/management/people',headers=headers,json=body);self.assertEqual(created.status_code,201,created.text)
            for p in ('/people','/personnel.js'):self.assertEqual(client.get(p).status_code,200)
            self.assertIn("script-src 'self'",client.get('/people').headers['content-security-policy'])
            self.assertEqual(client.get(path,headers=headers).headers['content-security-policy'],"default-src 'none'; frame-ancestors 'none'")

    def test_routes_do_not_create_personnel_schema_implicitly(self):
        with self.store.transaction() as db:
            db.execute('DROP TABLE management_staff_commands');db.execute('DROP TABLE management_staff_versions')
        app=create_app(self.f.path,lambda:self.f.now,allow_test_client=True)
        with self.assertRaises(ValueError):install_personnel_routes(app,self.f.path,lambda:self.f.now)
        with self.store.transaction() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_schema WHERE name='management_staff_commands'").fetchone())
            self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],1000)


if __name__=='__main__':unittest.main()
