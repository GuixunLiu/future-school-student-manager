"""Security regression scenarios shared by the disposable PostgreSQL fixture."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from business.security import password_hash
from business.store import Problem
from management.runtime_permissions import provision_first_owner

ADMIN = 'wxu_'+'a'*43
TEACHER = 'wxu_'+'u'*43
PASSWORD = 'Fiction permission alignment password 2026!'


class PermissionAlignmentChecks:
    def test_permission_concurrent_approval_and_new_request_do_not_deadlock_or_double_credit(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        data = dict(units=1200, expected_revision=1, reason='虚构并发调整', command_ref='pending-first')
        request_id = self.store.submit_balance_request(admin, 'student-a', data)['id']
        def new_request():
            try:
                return self.store.submit_balance_request(admin, 'student-a', dict(data, command_ref='pending-second'))
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(self.store.decide_balance_request, ADMIN, request_id,
                                   dict(action='approved', expected_version=1, reason='虚构批准'))
            submitted = pool.submit(new_request)
            self.assertEqual('approved', approval.result(timeout=15)['status'])
            self.assertIn(submitted.result(timeout=15), {'balance_changed', 'balance_request_pending'})
        self.assertEqual(1200, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_permission_migration_is_additive_and_repeatable(self):
        from management.runtime_schema import upgrade_staff_schema, runtime_readiness
        with psycopg.connect(self.conninfo) as db:
            before = db.execute('SELECT units FROM student_balances ORDER BY student_id').fetchall()
            db.execute('DROP TABLE balance_adjustment_requests')
            db.execute('ALTER TABLE renewal_records DROP COLUMN campus_id CASCADE')
            db.execute("DELETE FROM schema_migrations WHERE version='runtime-permissions-v2'")
            self.assertEqual('schema_incomplete', runtime_readiness(db)['status'])
            upgrade_staff_schema(db)
            upgrade_staff_schema(db)
            self.assertEqual('ready', runtime_readiness(db)['status'])
            self.assertEqual(before, db.execute('SELECT units FROM student_balances ORDER BY student_id').fetchall())
            self.assertEqual(1, db.execute("SELECT count(*) FROM schema_migrations WHERE version='runtime-permissions-v2'").fetchone()[0])

    def test_permission_owner_chooses_campus_for_shared_course_and_report_uses_transaction_campus(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        with psycopg.connect(self.conninfo) as db:
            db.execute("INSERT INTO classes(id,org_id,name,course_id,created_at,campus_id) VALUES('class-c','org-fiction','虚构跨校区班','course-a',1,%s)", (south,))
            db.execute("INSERT INTO student_class_enrollments VALUES('shared-course','org-fiction','student-a','class-c',1,NULL,1)")
        data = self.renewal_data()
        self.assert_problem(409, 'renewal_campus_required', self.store.admin_renewal, ADMIN, data)
        self.assert_problem(403, 'shared_student', self.store.admin_renewal, admin, dict(data, campus_id=north))
        self.store.admin_renewal(ADMIN, dict(data, campus_id=south))
        self.assertEqual(0, self.store.admin_monthly_report(admin)['overall']['renewal_events'])
        self.assertEqual(1, self.store.admin_monthly_report(other)['overall']['renewal_events'])
        with psycopg.connect(self.conninfo) as db:
            db.execute("UPDATE student_class_enrollments SET left_at=2 WHERE id='shared-course'")
        self.assertEqual(1, self.store.admin_monthly_report(other)['overall']['renewal_events'])

    def permission_fixture(self):
        north = self.store.admin_create_campus(ADMIN, '虚构北校区')['id']
        south = self.store.admin_create_campus(ADMIN, '虚构南校区')['id']
        hashed = password_hash(PASSWORD)
        with psycopg.connect(self.conninfo, row_factory=dict_row) as db:
            for user in ('admin-a', 'teacher-a', 'teacher-b'):
                db.execute("INSERT INTO user_credentials VALUES('org-fiction',%s,%s,1)", (user, hashed))
            provision_first_owner(db, 'fiction', 'fiction-admin-a')
            db.execute("INSERT INTO teacher_campuses VALUES('org-fiction','teacher-a',%s),('org-fiction','teacher-b',%s)", (north, south))
        for classroom, campus in (('class-a', north), ('class-b', south)):
            self.store.assign_class_campus(ADMIN, classroom, {
                'campus_id': campus, 'include_history': True, 'reason': '虚构权限归属'})
        tokens = []
        for login, role, campus in (('north-admin', 'campus_admin', north),
                                    ('south-admin', 'campus_admin', south),
                                    ('fee-reviewer', 'performance_reviewer', None)):
            self.store.set_staff_access(ADMIN, dict(login=login, role=role, campus_id=campus,
                display_name=login, password=PASSWORD, reason='虚构角色分配'))
            tokens.append(self.store.login('', login, PASSWORD, login)['access_token'])
        return north, south, *tokens

    def test_permission_campus_renewal_checks_scope_on_duplicate_and_shared_students(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        data = self.renewal_data(campus_id=north)
        result = self.store.admin_renewal(admin, data)
        self.assertEqual(north, result['campus_id'])
        self.assertTrue(self.store.admin_renewal(admin, data)['duplicate'])
        self.assert_problem(404, 'campus_scope', self.store.admin_renewal, other, data)
        self.assert_problem(403, 'admin_required', self.store.admin_renewal, reviewer, data)
        with psycopg.connect(self.conninfo) as db:
            db.execute("UPDATE organization_staff_access SET campus_id=%s WHERE user_id=(SELECT id FROM users WHERE login='north-admin')", (south,))
        self.assert_problem(404, 'campus_scope', self.store.admin_renewal, admin, data)
        with psycopg.connect(self.conninfo) as db:
            db.execute("UPDATE organization_staff_access SET campus_id=%s WHERE user_id=(SELECT id FROM users WHERE login='north-admin')", (north,))
            db.execute("INSERT INTO student_class_enrollments VALUES('shared-permission','org-fiction','student-a','class-b',0,NULL,0)")
        self.assert_problem(403, 'shared_student', self.store.admin_renewal, admin, data)
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM renewal_records')['n'])

    def test_permission_monthly_and_weekly_reports_keep_campuses_separate(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        self.store.admin_renewal(admin, self.renewal_data(campus_id=north))
        self.store.admin_renewal(other, self.renewal_data(command='south-renewal', source='south-ref',
            student_id='student-b', course_id='course-b', campus_id=south))
        self.assertEqual(1, self.store.admin_monthly_report(admin, '1970-01')['overall']['renewal_events'])
        self.assertEqual(2, self.store.admin_monthly_report(ADMIN, '1970-01')['overall']['renewal_events'])
        report = self.store.admin_monthly_report(admin, '1970-01')
        self.assertEqual(['student-a'], [row['student_id'] for row in report['renewals']])
        self.assertEqual(['class-a'], [row['class_id'] for row in self.store.admin_weekly_report(admin)['classes']])
        self.assert_problem(403, 'admin_required', self.store.admin_report_runs, admin, 'monthly', '1970-01')
        self.assert_problem(403, 'admin_required', self.store.admin_report_run, admin, 'any-run-id')
        self.assert_problem(403, 'admin_required', self.store.admin_monthly_report, reviewer)
        # A student's later campus transfer does not reattribute past income.
        with psycopg.connect(self.conninfo) as db:
            db.execute("UPDATE student_class_enrollments SET left_at=900 WHERE student_id='student-a'")
            db.execute("INSERT INTO student_class_enrollments VALUES('moved','org-fiction','student-a','class-b',900,NULL,900)")
        self.assertEqual(1, self.store.admin_monthly_report(admin, '1970-01')['overall']['renewal_events'])

    def test_permission_teacher_can_gain_and_lose_management_without_losing_teaching(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        original = self.read("SELECT password_hash FROM user_credentials WHERE user_id='teacher-a'")['password_hash']
        assigned = self.store.set_staff_access(ADMIN, dict(login='fiction-teacher-a', role='campus_admin',
            campus_id=south, display_name=None, password=None, reason='虚构教师兼任'))
        self.assertEqual('teacher-a', assigned['user_id'])
        self.assert_problem(401, 'unauthorized', self.store.me, TEACHER)
        token = self.store.login('', 'fiction-teacher-a', PASSWORD, 'dual-permission')['access_token']
        me = self.store.me(token)
        self.assertEqual(['campus_admin', 'teacher'], me['roles'])
        self.assertEqual(south, me['campus_id'])
        self.assertEqual('虚构北校区', me['teacher_campus_name'])
        self.assertEqual(['class-b'], [row['id'] for row in self.store.admin_classes(token)['items']])
        self.assertEqual(['lesson-a'], [row['id'] for row in self.store.teacher_lessons(token)['items']])
        self.store.set_staff_access(ADMIN, dict(login='fiction-teacher-a', role=None, campus_id=None,
            display_name=None, password=None, reason='虚构撤销兼任'))
        self.assert_problem(401, 'unauthorized', self.store.me, token)
        token = self.store.login('', 'fiction-teacher-a', PASSWORD, 'dual-revoked')['access_token']
        self.assertEqual(['teacher'], self.store.me(token)['roles'])
        self.assertEqual(original, self.read("SELECT password_hash FROM user_credentials WHERE user_id='teacher-a'")['password_hash'])
        self.assertEqual('lesson-a', self.store.teacher_lessons(token)['items'][0]['id'])
        self.assert_problem(403, 'admin_required', self.store.admin_classes, token)

    def test_permission_campus_account_changes_cannot_take_over_privileged_or_other_campus_users(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        data = self.account_password_data(expected_version=0)
        self.assert_problem(404, 'teacher_account_missing', self.store.admin_reset_account_password, admin, 'teacher-b', data)
        self.assertTrue(self.store.admin_reset_account_password(admin, 'teacher-a', data)['password_changed'])
        self.store.set_staff_access(ADMIN, dict(login='fiction-teacher-a', role='campus_admin',
            campus_id=south, display_name=None, password=None, reason='虚构兼任保护'))
        self.assert_problem(403, 'staff_account_protected', self.store.admin_reset_account_password,
                            admin, 'teacher-a', dict(data, command_ref='protected-reset', expected_version=1))
        self.assert_problem(403, 'staff_account_protected', self.store.admin_set_account_status,
                            admin, 'teacher-a', self.account_status_data())
        self.assertFalse(next(row for row in self.store.admin_accounts(admin)['items'] if row['id']=='teacher-a')['editable'])

    def test_permission_balance_request_is_scoped_and_approval_is_atomic(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        data = dict(units=1200, expected_revision=1, reason='虚构待审核调整', command_ref='request-1')
        self.assert_problem(403, 'owner_required', self.store.owner_adjust_balance, TEACHER, 'student-a', data)
        self.assert_problem(403, 'owner_required', self.store.owner_adjust_balance, admin, 'student-a', data)
        self.assert_problem(403, 'campus_admin_required', self.store.submit_balance_request, TEACHER, 'student-a', data)
        self.assert_problem(404, 'campus_scope', self.store.submit_balance_request, other, 'student-a', data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.submit_balance_request(admin, 'student-a', data), range(2)))
        self.assertEqual([False, True], sorted(row['duplicate'] for row in results))
        request_id = results[0]['id']
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual([], self.store.balance_requests(other)['items'])
        self.assert_problem(403, 'balance_request_required', self.store.balance_requests, reviewer)
        self.assert_problem(403, 'owner_required', self.store.decide_balance_request, admin, request_id,
                            dict(action='approved', expected_version=1, reason='虚构越权审批'))
        decision = dict(action='approved', expected_version=1, reason='虚构老板核对')
        with patch.object(self.store, '_audit', side_effect=RuntimeError('approval rollback')):
            with self.assertRaises(RuntimeError):
                self.store.decide_balance_request(ADMIN, request_id, decision)
        self.assertEqual('pending', self.store.balance_requests(admin)['items'][0]['status'])
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.store.decide_balance_request(ADMIN, request_id, decision)
        self.assertEqual(1200, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assert_problem(409, 'balance_request_changed', self.store.decide_balance_request, ADMIN, request_id, decision)
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_permission_stale_balance_request_does_not_overwrite_renewal(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        data = dict(units=1200, expected_revision=1, reason='虚构旧余额申请', command_ref='stale-request')
        request_id = self.store.submit_balance_request(admin, 'student-a', data)['id']
        self.store.admin_renewal(admin, self.renewal_data(campus_id=north))
        current = self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units']
        self.assert_problem(409, 'balance_changed', self.store.decide_balance_request, ADMIN, request_id,
                            dict(action='approved', expected_version=1, reason='虚构审批'))
        self.store.decide_balance_request(ADMIN, request_id, dict(action='rejected', expected_version=1, reason='余额已变化'))
        self.assertEqual(current, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_permission_owner_can_review_fees_but_campus_admin_cannot(self):
        north, south, admin, other, reviewer = self.permission_fixture()
        with psycopg.connect(self.conninfo) as db:
            db.execute("UPDATE lessons SET ends_at=5500 WHERE id='lesson-a'")
            db.execute('UPDATE auth_sessions SET expires_at=9000000')
        self.store.clock = lambda: 3000000
        request_id = self.store.submit_fee_review(admin, dict(campus_id=north, period='month', date='1970-01-01', reason='虚构月度审核'))['id']
        approved = self.store.decide_fee_review(ADMIN, request_id, dict(action='approved', expected_version=1, reason='老板复核'))
        self.assertEqual('approved', approved['status'])
        self.assert_problem(403, 'review_role_required', self.store.decide_fee_review, admin, request_id,
                            dict(action='returned', expected_version=2, reason='越权退回'))
        self.assertEqual('returned', self.store.decide_fee_review(ADMIN, request_id,
            dict(action='returned', expected_version=2, reason='老板退回'))['status'])

    def test_permission_http_closes_teacher_balance_and_enforces_owner_and_request_roles(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        north, south, admin, other, reviewer = self.permission_fixture()
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        data = dict(units=900, expected_revision=1, reason='虚构接口核对', command_ref='http-balance')
        self.assertEqual(404, client.put('/v1/teacher/students/student-a/balance', json=data,
                         headers={'Authorization':'Bearer '+TEACHER}).status_code)
        self.assertEqual(403, client.put('/v1/owner/students/student-a/balance', json=data,
                         headers={'Authorization':'Bearer '+TEACHER}).status_code)
        created = client.post('/v1/admin/students/student-a/balance-request', json=data,
                              headers={'Authorization':'Bearer '+admin})
        self.assertEqual(201, created.status_code, created.text)
        repeated = client.post('/v1/admin/students/student-a/balance-request', json=data,
                               headers={'Authorization':'Bearer '+admin})
        self.assertEqual(200, repeated.status_code)
        self.assertEqual(created.json()['id'], repeated.json()['id'])
        self.assertEqual(403, client.get('/v1/balance-adjustment-requests', headers={'Authorization':'Bearer '+reviewer}).status_code)
        self.assertEqual(1, len(client.get('/v1/balance-adjustment-requests', headers={'Authorization':'Bearer '+ADMIN}).json()['items']))
