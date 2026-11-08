"""Real PostgreSQL tests for unified runtime transactions and HTTP scopes."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from business.security import password_hash, token_digest, verify_password
from business.store import Problem
from management import runtime_schema
from management.runtime_service import RuntimeStore


USER_TOKEN = 'wxu_' + 'u' * 43
OTHER_USER_TOKEN = 'wxu_' + 'v' * 43
ADMIN_TOKEN = 'wxu_' + 'a' * 43
DEVICE_TOKEN = 'wxd_' + 'd' * 43


def checkin(ref='offline-1'):
    return dict(event_ref=ref, lesson_id='lesson-a', student_id='student-a',
                occurred_at=100, attendance_revision=0, balance_revision=1,
                previous_balance_units=10, consumed_units=1)


def correction(ref='teacher-1'):
    return dict(student_id='student-a', reason='虚构测试原因', event_ref=ref, expected_revision=0)


FACE_KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='


def face_frames(position=0):
    frame = [0.0] * 128
    frame[position] = 1.0
    return [frame.copy(), frame.copy(), frame.copy()]


def face_profile(ref='face-profile-1', frames=None):
    return dict(model_version='sface-int8bq-v1', descriptors=frames or face_frames(),
                consent_ref='fiction-guardian-consent-2026', command_ref=ref)


def face_checkin(ref='web-face-1', frames=None):
    return dict(event_ref=ref, model_version='sface-int8bq-v1',
                descriptors=frames or face_frames())


@unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
class RuntimeBusinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pgserver
        cls.tmp = tempfile.TemporaryDirectory(prefix='runtime-business-pg-')
        cls.server = pgserver.get_server(str(Path(cls.tmp.name) / 'data'), cleanup_mode='stop')
        parsed = urlsplit(cls.server.get_uri())
        cls.base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                 user=parsed.username, password=parsed.password)
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.tmp.cleanup()

    def setUp(self):
        type(self).sequence += 1
        self.name = 'runtime_business_' + str(type(self).sequence)
        with psycopg.connect(self.base, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.addCleanup(self.drop_database)
        self.conninfo = make_conninfo(self.base, dbname=self.name)
        with psycopg.connect(self.conninfo) as connection:
            connection.execute(runtime_schema.full_schema())
            self.seed(connection)
        self.store = RuntimeStore(self.conninfo, lambda: 1000)

    def drop_database(self):
        with psycopg.connect(self.base, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(self.name)))

    @staticmethod
    def seed(connection):
        connection.execute("""
            INSERT INTO organizations VALUES('org-fiction','fiction',1);
            INSERT INTO courses VALUES
                ('course-a','org-fiction','虚构课程','虚构课程内容',12800,TRUE,1),
                ('course-b','org-fiction','虚构课程乙','虚构课程乙内容',19800,TRUE,1);
            INSERT INTO classes VALUES
                ('class-a','org-fiction','虚构甲班','course-a',1),
                ('class-b','org-fiction','虚构乙班','course-b',1);
            INSERT INTO students(id,org_id,name,external_ref,active) VALUES
                ('student-a','org-fiction','虚构学生甲','fiction-a',TRUE),
                ('student-b','org-fiction','虚构学生乙','fiction-b',TRUE);
            INSERT INTO student_class_enrollments VALUES
                ('enrollment-a','org-fiction','student-a','class-a',0,NULL,0),
                ('enrollment-b','org-fiction','student-b','class-b',0,NULL,0);
            INSERT INTO users VALUES
                ('teacher-a','org-fiction','fiction-teacher-a','虚构教师甲',TRUE,1,FALSE),
                ('teacher-b','org-fiction','fiction-teacher-b','虚构教师乙',TRUE,1,FALSE),
                ('admin-a','org-fiction','fiction-admin-a','虚构管理员',TRUE,1,FALSE);
            INSERT INTO roles VALUES
                ('org-fiction','teacher-a','teacher'),('org-fiction','teacher-b','teacher'),
                ('org-fiction','admin-a','admin');
            INSERT INTO teacher_classes VALUES
                ('org-fiction','teacher-a','class-a'),('org-fiction','teacher-b','class-b');
            INSERT INTO devices VALUES('device-a','org-fiction','虚构平板',TRUE,1,FALSE);
            INSERT INTO device_classes VALUES('org-fiction','device-a','class-a');
            INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                ('lesson-a','org-fiction','class-a',100,200,'scheduled',1),
                ('lesson-b','org-fiction','class-b',100,200,'scheduled',1);
            INSERT INTO lesson_teachers VALUES
                ('org-fiction','lesson-a','teacher-a','虚构教师甲'),
                ('org-fiction','lesson-b','teacher-b','虚构教师乙');
            INSERT INTO lesson_students VALUES
                ('org-fiction','class-a','lesson-a','student-a',TRUE,1),
                ('org-fiction','class-b','lesson-b','student-b',TRUE,1);
            INSERT INTO opening_balances VALUES
                ('org-fiction','student-a',10,'manual_review','fiction-review'),
                ('org-fiction','student-b',10,'manual_review','fiction-review');
            INSERT INTO student_balances VALUES
                ('org-fiction','student-a',10,1,1),('org-fiction','student-b',10,1,1);
            INSERT INTO balance_ledger VALUES
                ('ledger-a','org-fiction','student-a','opening','opening-a',0,10,10,0,1,'system',NULL,'虚构期初',1),
                ('ledger-b','org-fiction','student-b','opening','opening-b',0,10,10,0,1,'system',NULL,'虚构期初',1);
            INSERT INTO attendance_records VALUES
                ('org-fiction','lesson-a','student-a','missing',0,NULL,1),
                ('org-fiction','lesson-b','student-b','missing',0,NULL,1);
        """)
        connection.execute("INSERT INTO device_credentials VALUES('org-fiction','device-a','generation-a',%s,1,100000,NULL)",
                           (token_digest(DEVICE_TOKEN),))
        connection.execute("""INSERT INTO auth_sessions VALUES
            ('session-a','org-fiction','teacher-a',%s,1,100000,NULL),
            ('session-b','org-fiction','teacher-b',%s,1,100000,NULL),
            ('session-admin','org-fiction','admin-a',%s,1,100000,NULL)""",
            (token_digest(USER_TOKEN), token_digest(OTHER_USER_TOKEN), token_digest(ADMIN_TOKEN)))

    def read(self, query, params=()):
        with psycopg.connect(self.conninfo, row_factory=dict_row) as connection:
            return connection.execute(query, params).fetchone()

    def assert_problem(self, status, code, function, *args):
        with self.assertRaises(Problem) as caught:
            function(*args)
        self.assertEqual((status, code), (caught.exception.status, caught.exception.code))

    def test_teacher_campus_login_and_substitute_fees(self):
        campus = self.store.admin_create_campus(ADMIN_TOKEN, '虚构北校区')
        password = 'fictional-password-for-fee-test'
        account = self.store.admin_create_account(ADMIN_TOKEN, {
            'login': 'fictional.substitute', 'display_name': '虚构代课老师', 'password': password,
            'campus_id': campus['id'], 'reason': '虚构测试', 'command_ref': 'campus-account'})
        substitute = self.store.login('', account['login'], password, 'staff-feature-test')['access_token']
        self.assertEqual(campus['name'], self.store.me(substitute)['campus_name'])
        self.assert_problem(403, 'admin_required', self.store.admin_campuses, substitute)
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE lessons SET starts_at=2000,ends_at=7400 WHERE id='lesson-a'")
            connection.execute("UPDATE lessons SET starts_at=8000,ends_at=15200 WHERE id='lesson-b'")
        payload = {'teacher_id': account['user_id'], 'expected_version': 0,
                   'reason': '虚构代课', 'command_ref': 'substitute-once'}
        result = self.store.admin_substitute_lesson(ADMIN_TOKEN, 'lesson-a', payload)
        self.assertEqual(1, result['version'])
        self.assertTrue(self.store.admin_substitute_lesson(ADMIN_TOKEN, 'lesson-a', payload)['duplicate'])
        self.assertEqual([], self.store.teacher_lessons(USER_TOKEN)['items'])
        self.assertTrue(self.store.teacher_lessons(substitute)['items'][0]['is_substitute'])
        self.assert_problem(403, 'lesson_scope', self.store.teacher_lesson, USER_TOKEN, 'lesson-a')
        self.assert_problem(403, 'lesson_scope', self.store.teacher_lesson, substitute, 'lesson-b')
        self.assertEqual('student-a', self.store.teacher_lesson(substitute, 'lesson-a')['students'][0]['student_id'])
        self.assertEqual(0, self.store.teaching_fees(substitute, 'week')['total_cents'])
        self.store.clock = lambda: 16000
        self.store.teacher_correction(substitute, 'lesson-a', 'makeup', correction('fee-makeup'))
        self.store.teacher_correction(OTHER_USER_TOKEN, 'lesson-b', 'makeup',
                                      dict(correction('fee-other-makeup'), student_id='student-b'))
        own = self.store.teaching_fees(substitute, 'week')
        self.assertEqual(300, own['total_cents'])
        self.assertEqual([account['user_id']], [row['id'] for row in own['items']])
        self.assertEqual(0, self.store.teaching_fees(USER_TOKEN, 'week')['total_cents'])
        self.assertEqual(750, self.store.teaching_fees(ADMIN_TOKEN, 'month', administrator=True)['total_cents'])

    def test_campus_permissions_and_monthly_review_are_isolated(self):
        from management.runtime_permissions import provision_first_owner
        north = self.store.admin_create_campus(ADMIN_TOKEN, '虚构北校区')['id']
        south = self.store.admin_create_campus(ADMIN_TOKEN, '虚构南校区')['id']
        password = 'fictional-staff-test-password'
        with psycopg.connect(self.conninfo, row_factory=dict_row) as connection:
            connection.execute("INSERT INTO user_credentials VALUES('org-fiction','admin-a',%s,1)",
                               (password_hash(password),))
            provision_first_owner(connection, 'fiction', 'fiction-admin-a')
            connection.execute("UPDATE lessons SET ends_at=5500 WHERE id='lesson-a'")
        self.assertIn('owner', self.store.me(ADMIN_TOKEN)['roles'])
        for class_id, campus_id in (('class-a', north), ('class-b', south)):
            self.store.assign_class_campus(ADMIN_TOKEN, class_id, {
                'campus_id': campus_id, 'include_history': True, 'reason': '虚构校区归属'})
        for login, role, campus_id in (('north-admin', 'campus_admin', north),
                                       ('fee-reviewer', 'performance_reviewer', None)):
            self.store.set_staff_access(ADMIN_TOKEN, {'login': login, 'role': role, 'campus_id': campus_id,
                'display_name': login, 'password': password, 'reason': '虚构授权'})
        admin = self.store.login('', 'north-admin', password, 'test-staff')['access_token']
        reviewer = self.store.login('', 'fee-reviewer', password, 'test-staff')['access_token']
        self.assertEqual(['class-a'], [row['id'] for row in self.store.admin_classes(admin)['items']])
        self.assertEqual(['student-a'], [row['id'] for row in self.store.admin_students(admin)['items']])
        self.assertEqual(['lesson-a'], [row['id'] for row in self.store.admin_lessons(admin)['items']])
        self.assertEqual(['device-a'], [row['id'] for row in self.store.admin_devices(admin)['items']])
        self.assert_problem(404, 'campus_scope', self.store.admin_lesson, admin, 'lesson-b')
        self.assert_problem(403, 'admin_required', self.store.admin_students, reviewer)
        self.assert_problem(403, 'admin_required', self.store.admin_monthly_report, admin)
        self.assert_problem(404, 'campus_scope', self.store.preview_fee_review, admin, south, 'month', '1970-01-01')
        rate = {'rate_cents': 600, 'expected_version': 0, 'reason': '虚构费率调整', 'command_ref': 'campus-rate'}
        self.assert_problem(404, 'course_missing', self.store.submit_course_fee_proposal, admin, 'course-b', rate)
        self.assert_problem(403, 'admin_required', self.store.admin_set_course_teaching_fee, admin, 'course-a', rate)
        proposal = self.store.submit_course_fee_proposal(admin, 'course-a', rate)
        approval = {'action': 'approved', 'reason': '虚构批准'}
        self.assert_problem(403, 'owner_required', self.store.decide_course_fee_proposal, reviewer, proposal['id'], approval)
        self.assertEqual('approved', self.store.decide_course_fee_proposal(ADMIN_TOKEN, proposal['id'], approval)['status'])
        self.assertEqual(600, next(row for row in self.store.admin_courses(admin)['items']
                                   if row['id'] == 'course-a')['teaching_fee_cents'])
        self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction('historical-review-attendance'))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute('UPDATE auth_sessions SET expires_at=9000000')
        self.store.clock = lambda: 3000000
        submitted = self.store.submit_fee_review(admin, {'campus_id': north, 'period': 'month',
            'date': '1970-01-01', 'reason': '虚构月度审核'})
        entries = self.store.fee_reviews(reviewer)
        self.assertEqual({north, south}, {row['id'] for row in entries['campuses']})
        snapshot = entries['items'][0]['snapshot']
        self.assertEqual(300, snapshot['total_cents'])
        self.assertEqual(1, snapshot['items'][0]['lessons'][0]['arrived_count'])
        self.assertNotIn('虚构学生甲', json.dumps(snapshot, ensure_ascii=False))
        self.assertNotIn('student_id', json.dumps(snapshot))
        approve = {'action': 'approved', 'expected_version': 1, 'reason': '虚构核对'}
        self.assert_problem(403, 'review_role_required', self.store.decide_fee_review, admin, submitted['id'], approve)
        self.assertEqual('approved', self.store.decide_fee_review(reviewer, submitted['id'], approve)['status'])
        settle = {'action': 'settled', 'expected_version': 2, 'reason': '虚构结算'}
        self.assert_problem(403, 'review_role_required', self.store.decide_fee_review, reviewer, submitted['id'], settle)
        self.assertEqual('settled', self.store.decide_fee_review(ADMIN_TOKEN, submitted['id'], settle)['status'])
        self.assertEqual(snapshot, self.store.fee_reviews(ADMIN_TOKEN)['items'][0]['snapshot'])

    def test_course_teaching_fee_changes_only_apply_to_later_lessons(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE lessons SET ends_at=5500 WHERE id='lesson-a'")
        fee = {'rate_cents': 650, 'expected_version': 0, 'reason': '虚构费率', 'command_ref': 'fee-rate'}
        self.assert_problem(403, 'admin_required', self.store.admin_set_course_teaching_fee, USER_TOKEN, 'course-a', fee)
        saved = self.store.admin_set_course_teaching_fee(ADMIN_TOKEN, 'course-a', fee)
        self.assertEqual(1, saved['version'])
        self.assertTrue(self.store.admin_set_course_teaching_fee(ADMIN_TOKEN, 'course-a', fee)['duplicate'])
        self.assertEqual(650, next(item for item in self.store.admin_courses(ADMIN_TOKEN)['items']
                                   if item['id'] == 'course-a')['teaching_fee_cents'])
        scheduled = self.store.admin_create_lesson(ADMIN_TOKEN, {
            'class_id': 'class-a', 'teacher_id': 'teacher-a', 'starts_at': 6000, 'ends_at': 11400,
            'charge_units': 1, 'reason': '虚构课次', 'command_ref': 'fee-lesson'})
        self.store.clock = lambda: 12000
        self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction('fee-old-attendance'))
        self.store.teacher_correction(USER_TOKEN, scheduled['lesson_id'], 'makeup', correction('fee-new-attendance'))
        self.store.admin_set_course_teaching_fee(ADMIN_TOKEN, 'course-a', dict(
            fee, rate_cents=None, expected_version=1, command_ref='fee-restore'))
        self.store.admin_update_class(ADMIN_TOKEN, 'class-a', {
            'course_id': 'course-b', 'name': '虚构甲班', 'expected_version': 0,
            'reason': '虚构转课', 'command_ref': 'fee-class-change'})
        report = self.store.teaching_fees(USER_TOKEN, 'week')
        self.assertEqual(950, report['total_cents'])
        self.assertEqual([300, 650], [row['rate_cents'] for row in report['items'][0]['lessons']])

    def test_tablet_bootstrap_contains_only_authorized_classes_and_versions(self):
        result = self.store.tablet_bootstrap(DEVICE_TOKEN)
        self.assertEqual(['class-a'], [row['id'] for row in result['classes']])
        self.assertEqual(['lesson-a'], [row['id'] for row in result['lessons']])
        student = result['lessons'][0]['students'][0]
        self.assertEqual(('student-a', 'missing', 0, 10, 1),
                         (student['student_id'], student['status'], student['attendance_revision'],
                          student['balance_units'], student['balance_revision']))
        self.assert_problem(403, 'device_required', self.store.tablet_bootstrap, USER_TOKEN)

    def test_tablet_checkin_commits_attendance_balance_ledger_audit_and_receipt(self):
        result = self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        self.assertEqual((False, 'arrived', 1, 2, 9),
                         (result['duplicate'], result['status'], result['attendance_revision'],
                          result['balance_revision'], result['balance_units']))
        self.assertEqual((9, 2), tuple(self.read("SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(('arrived', 1), tuple(self.read("SELECT status,revision FROM attendance_records WHERE lesson_id='lesson-a'").values()))
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM audit_log')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_duplicate_returns_same_result_without_double_debit_and_changed_body_conflicts(self):
        first = self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        second = self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        changed = checkin()
        changed['consumed_units'] = 2
        self.assert_problem(409, 'idempotency_conflict', self.store.tablet_checkin, DEVICE_TOKEN, changed)
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_concurrent_identical_retries_create_one_business_event(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.tablet_checkin(DEVICE_TOKEN, checkin()), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_stale_revisions_do_not_leave_events_or_audit(self):
        stale = checkin()
        stale['balance_revision'] = 0
        self.assert_problem(409, 'balance_changed', self.store.tablet_checkin, DEVICE_TOKEN, stale)
        stale = checkin()
        stale['attendance_revision'] = 2
        self.assert_problem(409, 'attendance_changed', self.store.tablet_checkin, DEVICE_TOKEN, stale)
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM audit_log')['n'])
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_device_scope_token_type_and_lesson_day_are_enforced(self):
        outside = checkin()
        outside.update(lesson_id='lesson-b', student_id='student-b')
        self.assert_problem(403, 'checkin_scope', self.store.tablet_checkin, DEVICE_TOKEN, outside)
        self.assert_problem(403, 'device_required', self.store.tablet_checkin, USER_TOKEN, checkin())
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE lessons SET starts_at=100000,ends_at=100100 WHERE id='lesson-a'")
        wrong_day = checkin()
        self.assert_problem(409, 'lesson_day', self.store.tablet_checkin, DEVICE_TOKEN, wrong_day)
        future = checkin()
        future['occurred_at'] = 1121
        self.assert_problem(409, 'device_clock', self.store.tablet_checkin, DEVICE_TOKEN, future)
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_tablet_resolution_waits_for_web_makeup_then_is_idempotent_and_non_mutating(self):
        self.assert_problem(409, 'resolution_pending', self.store.tablet_resolve_checkin,
                            DEVICE_TOKEN, checkin())
        corrected = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction())
        first = self.store.tablet_resolve_checkin(DEVICE_TOKEN, checkin())
        self.assertEqual((True, True, False, 'arrived', corrected['event_id'], 1, 2, 9, False),
                         tuple(first[key] for key in ('accepted', 'resolved', 'duplicate', 'status',
                               'event_id', 'attendance_revision', 'balance_revision', 'balance_units',
                               'apply_local_debit')))
        second = self.store.tablet_resolve_checkin(DEVICE_TOKEN, checkin())
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['event_id'], second['event_id'])
        self.assertEqual(('arrived', 1, corrected['event_id']), tuple(self.read(
            "SELECT status,revision,last_event_id FROM attendance_records WHERE lesson_id='lesson-a'").values()))
        self.assertEqual((9, 2), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM audit_log')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM api_commands')['n'])
        changed = checkin()
        changed['consumed_units'] = 2
        self.assert_problem(409, 'idempotency_conflict', self.store.tablet_resolve_checkin,
                            DEVICE_TOKEN, changed)
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_tablet_resolution_archives_web_leave_without_debit(self):
        corrected = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'leave', correction())
        result = self.store.tablet_resolve_checkin(DEVICE_TOKEN, checkin())
        self.assertEqual(('leave', corrected['event_id'], 1, 1, 10),
                         tuple(result[key] for key in ('status', 'event_id', 'attendance_revision',
                               'balance_revision', 'balance_units')))
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='tablet_checkin_resolved'")['n'])

    def test_tablet_resolution_enforces_token_device_scope_and_existing_roster(self):
        self.assert_problem(403, 'device_required', self.store.tablet_resolve_checkin,
                            USER_TOKEN, checkin())
        outside = checkin()
        outside.update(lesson_id='lesson-b', student_id='student-b')
        self.assert_problem(403, 'checkin_scope', self.store.tablet_resolve_checkin,
                            DEVICE_TOKEN, outside)
        self.assert_problem(404, 'lesson_student_missing', self.store.tablet_resolve_checkin,
                            DEVICE_TOKEN, checkin('missing').copy() | {'lesson_id': 'missing'})
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE lessons SET state='cancelled' WHERE id='lesson-a'")
        self.assert_problem(409, 'lesson_unavailable', self.store.teacher_correction,
                            USER_TOKEN, 'lesson-a', 'makeup', correction())
        self.assert_problem(409, 'resolution_pending', self.store.tablet_resolve_checkin,
                            DEVICE_TOKEN, checkin())
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_tablet_resolution_enforces_auth_validation_and_never_double_debits(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        path = '/v1/tablet/checkins/resolve'
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        self.assertEqual(401, client.post(path, json=checkin()).status_code)
        self.assertEqual(403, client.post(path, headers=teacher, json=checkin()).status_code)
        extra = checkin() | {'unexpected': True}
        self.assertEqual(422, client.post(path, headers=device, json=extra).status_code)
        pending = client.post(path, headers=device, json=checkin())
        self.assertEqual(409, pending.status_code, pending.text)
        self.assertEqual('resolution_pending', pending.json()['error']['code'])
        # Existing deployments may have recorded a makeup before the web route was retired.
        corrected = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction())
        resolved = client.post(path, headers=device, json=checkin())
        self.assertEqual(200, resolved.status_code, resolved.text)
        self.assertEqual(('arrived', corrected['event_id'], 9),
                         tuple(resolved.json()[key] for key in ('status', 'event_id', 'balance_units')))
        repeated = client.post(path, headers=device, json=checkin())
        self.assertEqual(200, repeated.status_code, repeated.text)
        self.assertTrue(repeated.json()['duplicate'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_teacher_lesson_list_is_date_and_scope_limited(self):
        result = self.store.teacher_lessons(USER_TOKEN, '1970-01-01')
        self.assertEqual('1970-01-01', result['date'])
        self.assertEqual(['lesson-a'], [row['id'] for row in result['items']])
        lesson = result['items'][0]
        self.assertEqual(('虚构甲班', 1, 0, 0, 1),
                         (lesson['class_name'], lesson['expected_count'], lesson['arrived_count'],
                          lesson['leave_count'], lesson['missing_count']))
        self.assertEqual(['lesson-b'], [row['id'] for row in self.store.teacher_lessons(OTHER_USER_TOKEN)['items']])
        self.assertEqual([], self.store.teacher_lessons(USER_TOKEN, '1970-01-02')['items'])
        self.assert_problem(403, 'teacher_required', self.store.teacher_lessons, ADMIN_TOKEN)
        self.assert_problem(403, 'user_required', self.store.teacher_lessons, DEVICE_TOKEN)
        self.assert_problem(422, 'invalid_date', self.store.teacher_lessons, USER_TOKEN, '2026-02-30')

    def test_teacher_weekly_report_is_weighted_scoped_and_excludes_pending(self):
        initial = self.store.teacher_weekly_report(USER_TOKEN)
        self.assertEqual(('1969-12-29', '1970-01-04', 1, 0, 1, 0.0),
                         (initial['week_start'], initial['week_end'], initial['overall']['expected_count'],
                          initial['overall']['arrived_count'], initial['overall']['missing_count'],
                          initial['overall']['rate_percent']))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO teacher_classes VALUES('org-fiction','teacher-a','class-b');
                INSERT INTO lesson_teachers VALUES('org-fiction','lesson-b','teacher-a','虚构教师甲');
                UPDATE attendance_records SET status='arrived' WHERE lesson_id='lesson-a';
                UPDATE attendance_records SET status='leave' WHERE lesson_id='lesson-b';
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('lesson-pending','org-fiction','class-a',500,1100,'scheduled',1),
                    ('lesson-cancelled','org-fiction','class-a',600,700,'cancelled',1);
                INSERT INTO lesson_teachers VALUES
                    ('org-fiction','lesson-pending','teacher-a','虚构教师甲'),
                    ('org-fiction','lesson-cancelled','teacher-a','虚构教师甲');
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-a','lesson-pending','student-a',TRUE,1),
                    ('org-fiction','class-a','lesson-cancelled','student-a',TRUE,1);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','lesson-pending','student-a','missing',0,NULL,1),
                    ('org-fiction','lesson-cancelled','student-a','missing',0,NULL,1);
            """)
        result = self.store.teacher_weekly_report(USER_TOKEN, '1969-12-29')
        self.assertEqual((2, 1, 1, 0, 2, 1, 50.0),
                         tuple(result['overall'][key] for key in ('expected_count', 'arrived_count', 'leave_count',
                               'missing_count', 'completed_lessons', 'pending_lessons', 'rate_percent')))
        classes = {row['class_id']: row for row in result['classes']}
        self.assertEqual((100.0, 1), (classes['class-a']['rate_percent'], classes['class-a']['pending_lessons']))
        self.assertEqual((0.0, 1), (classes['class-b']['rate_percent'], classes['class-b']['leave_count']))
        other = self.store.teacher_weekly_report(OTHER_USER_TOKEN, '1969-12-29')
        self.assertEqual(['class-b'], [row['class_id'] for row in other['classes']])
        self.assertEqual(1, other['overall']['expected_count'])
        self.assert_problem(403, 'teacher_required', self.store.teacher_weekly_report, ADMIN_TOKEN)
        self.assert_problem(403, 'user_required', self.store.teacher_weekly_report, DEVICE_TOKEN)
        self.assert_problem(422, 'invalid_week', self.store.teacher_weekly_report, USER_TOKEN, '1970-01-04')

    def test_http_teacher_weekly_report_reflects_current_attendance_and_scope(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        other = {'Authorization': 'Bearer ' + OTHER_USER_TOKEN}
        response = client.get('/v1/teacher/reports/weekly?week_start=1969-12-29', headers=teacher)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual((1, 0, 0.0), tuple(response.json()['overall'][key] for key in ('expected_count', 'arrived_count', 'rate_percent')))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE attendance_records SET status='arrived' WHERE lesson_id='lesson-a'")
        updated = client.get('/v1/teacher/reports/weekly', headers=teacher)
        self.assertEqual(100.0, updated.json()['overall']['rate_percent'])
        scoped = client.get('/v1/teacher/reports/weekly', headers=other)
        self.assertEqual(['class-b'], [row['class_id'] for row in scoped.json()['classes']])
        self.assertEqual(403, client.get('/v1/teacher/reports/weekly', headers={'Authorization': 'Bearer ' + ADMIN_TOKEN}).status_code)
        self.assertEqual(422, client.get('/v1/teacher/reports/weekly?week_start=1970-01-01', headers=teacher).status_code)
        self.assertEqual(422, client.get('/v1/teacher/reports/weekly?week_start=bad', headers=teacher).status_code)

    def test_admin_weekly_report_is_school_wide_weighted_and_historical(self):
        initial = self.store.admin_weekly_report(ADMIN_TOKEN)
        self.assertEqual((2, 0, 2, 0.0), tuple(initial['overall'][key] for key in (
            'expected_count', 'arrived_count', 'missing_count', 'rate_percent')))
        self.assertEqual(['class-a', 'class-b'], [row['class_id'] for row in initial['classes']])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                UPDATE attendance_records SET status='arrived' WHERE lesson_id='lesson-a';
                UPDATE attendance_records SET status='leave' WHERE lesson_id='lesson-b';
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('lesson-b-extra','org-fiction','class-b',300,400,'scheduled',1),
                    ('lesson-pending','org-fiction','class-a',500,1100,'scheduled',1),
                    ('lesson-cancelled','org-fiction','class-a',600,700,'cancelled',1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-b','lesson-b-extra','student-b',TRUE,1),
                    ('org-fiction','class-a','lesson-pending','student-a',TRUE,1),
                    ('org-fiction','class-a','lesson-cancelled','student-a',TRUE,1);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','lesson-b-extra','student-b','arrived',0,NULL,1),
                    ('org-fiction','lesson-pending','student-a','missing',0,NULL,1),
                    ('org-fiction','lesson-cancelled','student-a','missing',0,NULL,1);
                INSERT INTO organizations VALUES('org-other','other',1);
                INSERT INTO courses VALUES('course-other','org-other','其他课程','其他内容',10000,TRUE,1);
                INSERT INTO classes VALUES('class-other','org-other','其他机构班','course-other',1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES('lesson-other','org-other','class-other',100,200,'scheduled',1);
            """)
        result = self.store.admin_weekly_report(ADMIN_TOKEN, '1969-12-29')
        self.assertEqual((3, 2, 1, 0, 3, 1, 66.7), tuple(result['overall'][key] for key in (
            'expected_count', 'arrived_count', 'leave_count', 'missing_count',
            'completed_lessons', 'pending_lessons', 'rate_percent')))
        classes = {row['class_id']: row for row in result['classes']}
        self.assertEqual({'class-a', 'class-b'}, set(classes))
        self.assertEqual(100.0, classes['class-a']['rate_percent'])
        self.assertEqual((2, 1, 1, 50.0), tuple(classes['class-b'][key] for key in (
            'expected_count', 'arrived_count', 'leave_count', 'rate_percent')))
        historical = self.store.admin_weekly_report(ADMIN_TOKEN, '1969-12-22')
        self.assertEqual(('1969-12-22', '1969-12-28'), (historical['week_start'], historical['week_end']))
        self.assertIsNone(historical['overall']['rate_percent'])
        self.assertEqual([], historical['classes'])
        self.assert_problem(403, 'admin_required', self.store.admin_weekly_report, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_weekly_report, DEVICE_TOKEN)
        self.assert_problem(422, 'invalid_week', self.store.admin_weekly_report, ADMIN_TOKEN, '1970-01-04')

    def test_http_admin_weekly_report_enforces_scope_and_supports_history(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        response = client.get('/v1/admin/reports/weekly', headers=admin)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual((2, 0, 0.0), tuple(response.json()['overall'][key] for key in (
            'expected_count', 'arrived_count', 'rate_percent')))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE attendance_records SET status='arrived'")
        updated = client.get('/v1/admin/reports/weekly?week_start=1969-12-29', headers=admin)
        self.assertEqual(100.0, updated.json()['overall']['rate_percent'])
        historical = client.get('/v1/admin/reports/weekly?week_start=1969-12-22', headers=admin)
        self.assertEqual(200, historical.status_code, historical.text)
        self.assertIsNone(historical.json()['overall']['rate_percent'])
        self.assertEqual([], historical.json()['classes'])
        self.assertEqual(403, client.get('/v1/admin/reports/weekly', headers=teacher).status_code)
        self.assertEqual(403, client.get('/v1/admin/reports/weekly', headers=device).status_code)
        self.assertEqual(401, client.get('/v1/admin/reports/weekly').status_code)
        self.assertEqual(422, client.get('/v1/admin/reports/weekly?week_start=1970-01-01', headers=admin).status_code)
        self.assertEqual(422, client.get('/v1/admin/reports/weekly?week_start=bad', headers=admin).status_code)
        self.assertEqual(200, client.get('/admin-report.js').status_code)

    def test_admin_monthly_report_uses_business_dates_and_distinct_records(self):
        initial = self.store.admin_monthly_report(ADMIN_TOKEN)
        self.assertEqual((0, 0, 0, 0, 0), tuple(initial['overall'].values()))
        self.assertFalse(initial['final_period'])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO enrollment_records VALUES
                    ('enroll-a','org-fiction','student-a','class-a',100,'enroll-source-a','admin-a',9999),
                    ('enroll-b','org-fiction','student-b','class-b',2678400,'enroll-source-b','admin-a',9999);
                INSERT INTO renewal_records(id,org_id,student_id,course_id,units_added,amount_cents,unit_price_cents,occurred_at,source_ref,actor_id,reason,created_at) VALUES
                    ('renewal-a1','org-fiction','student-a','course-a',20,256000,12800,100,'renewal-source-a1','admin-a','虚构续费一',9999),
                    ('renewal-a2','org-fiction','student-a','course-a',30,384000,12800,200,'renewal-source-a2','admin-a','虚构续费二',9999),
                    ('renewal-b1','org-fiction','student-b','course-b',10,198000,19800,300,'renewal-source-b1','admin-a','虚构续费三',9999),
                    ('renewal-next','org-fiction','student-a','course-a',5,64000,12800,2678400,'renewal-source-next','admin-a','下月续费',9999);
            """)
        result = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-01')
        self.assertEqual(('1970-01-01', '1970-01-31'), (result['month_start'], result['month_end']))
        self.assertEqual((1, 2, 3, 60, 838000), tuple(result['overall'][key] for key in (
            'new_students', 'renewal_students', 'renewal_events', 'renewal_units', 'renewal_amount_cents')))
        self.assertEqual(1, len(result['enrollments']))
        self.assertEqual(3, len(result['renewals']))
        self.assertEqual(['renewal-a1', 'renewal-a2', 'renewal-b1'], [row['id'] for row in result['renewals']])
        self.assertEqual('虚构甲班', result['enrollments'][0]['class_name'])
        self.assertFalse(result['details_truncated'])
        following = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-02')
        self.assertEqual((1, 1, 1, 5, 64000), tuple(following['overall'].values()))
        historical = self.store.admin_monthly_report(ADMIN_TOKEN, '1969-12')
        self.assertEqual((0, 0, 0, 0, 0), tuple(historical['overall'].values()))
        self.assertTrue(historical['final_period'])
        self.assert_problem(403, 'admin_required', self.store.admin_monthly_report, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_monthly_report, DEVICE_TOKEN)
        for invalid in ('1970-13', '1970-1', 'bad'):
            self.assert_problem(422, 'invalid_month', self.store.admin_monthly_report, ADMIN_TOKEN, invalid)

    def test_http_admin_monthly_report_enforces_scope_and_month_validation(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        response = client.get('/v1/admin/reports/monthly', headers=admin)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual((0, 0, 0, 0, 0), tuple(response.json()['overall'].values()))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO enrollment_records VALUES('enroll-http','org-fiction','student-a','class-a',100,'enroll-http-source','admin-a',9999);
                INSERT INTO renewal_records(id,org_id,student_id,course_id,units_added,amount_cents,unit_price_cents,occurred_at,source_ref,actor_id,reason,created_at)
                    VALUES('renewal-http','org-fiction','student-a','course-a',25,320000,12800,100,'renewal-http-source','admin-a','虚构续费',9999);
            """)
        updated = client.get('/v1/admin/reports/monthly?month=1970-01', headers=admin)
        self.assertEqual((1, 1, 1, 25, 320000), tuple(updated.json()['overall'].values()))
        self.assertEqual('虚构学生甲', updated.json()['renewals'][0]['student_name'])
        self.assertEqual(403, client.get('/v1/admin/reports/monthly', headers=teacher).status_code)
        self.assertEqual(403, client.get('/v1/admin/reports/monthly', headers=device).status_code)
        self.assertEqual(401, client.get('/v1/admin/reports/monthly').status_code)
        for invalid in ('1970-13', '1970-1', 'bad'):
            self.assertEqual(422, client.get('/v1/admin/reports/monthly?month=' + invalid, headers=admin).status_code)
        self.assertEqual(200, client.get('/admin-monthly.js').status_code)

    @staticmethod
    def report_data(command='report-command', kind='weekly', period='1969-12-29'):
        return {'kind': kind, 'period_start': period, 'command_ref': command}

    def test_report_snapshots_keep_history_and_manual_duplicates(self):
        data = self.report_data()
        first = self.store.admin_generate_report(ADMIN_TOKEN, data)
        self.assertEqual((1, 'complete', False, False),
                         (first['run']['version'], first['run']['status'],
                          first['run']['final_period'], first['duplicate']))
        self.assertEqual(0, first['payload']['overall']['arrived_count'])
        duplicate = self.store.admin_generate_report(ADMIN_TOKEN, data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['run']['id'], duplicate['run']['id'])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE attendance_records SET status='arrived' WHERE lesson_id='lesson-a'")
        old = self.store.admin_report_run(ADMIN_TOKEN, first['run']['id'])
        self.assertEqual(0, old['payload']['overall']['arrived_count'])
        second = self.store.admin_generate_report(ADMIN_TOKEN, self.report_data('report-command-2'))
        self.assertEqual((2, 1, 50.0), (second['run']['version'],
                         second['payload']['overall']['arrived_count'], second['payload']['overall']['rate_percent']))
        versions = self.store.admin_report_runs(ADMIN_TOKEN, 'weekly', '1969-12-29')['items']
        self.assertEqual([2, 1], [row['version'] for row in versions])
        self.assertEqual(0, self.store.admin_report_run(ADMIN_TOKEN, first['run']['id'])['payload']['overall']['arrived_count'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_report_generation_succeeded'")['n'])
        self.assert_problem(403, 'admin_required', self.store.admin_report_runs, USER_TOKEN, 'weekly', '1969-12-29')
        self.assert_problem(403, 'admin_required', self.store.admin_report_run, DEVICE_TOKEN, first['run']['id'])
        self.assert_problem(404, 'report_missing', self.store.admin_report_run, ADMIN_TOKEN, 'missing-report')
        self.assert_problem(422, 'report_period_future', self.store.admin_generate_report,
                            ADMIN_TOKEN, self.report_data('future-report', period='1970-01-05'))
        self.assert_problem(422, 'invalid_week', self.store.admin_generate_report,
                            ADMIN_TOKEN, self.report_data('bad-week', period='1970-01-01'))

    def test_monthly_snapshot_does_not_change_when_later_renewal_arrives(self):
        first = self.store.admin_generate_report(ADMIN_TOKEN,
            self.report_data('monthly-report-1', 'monthly', '1970-01'))
        self.assertEqual((1, 0, False), (first['run']['version'],
                         first['payload']['overall']['renewal_events'], first['run']['final_period']))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""INSERT INTO renewal_records
                (id,org_id,student_id,course_id,units_added,amount_cents,unit_price_cents,occurred_at,
                 source_ref,actor_id,reason,created_at) VALUES
                ('renewal-snapshot','org-fiction','student-a','course-a',8,102400,12800,100,
                 'renewal-snapshot-source','admin-a','虚构快照续费',1000)""")
        second = self.store.admin_generate_report(ADMIN_TOKEN,
            self.report_data('monthly-report-2', 'monthly', '1970-01'))
        self.assertEqual((2, 1, 102400), (second['run']['version'],
                         second['payload']['overall']['renewal_events'],
                         second['payload']['overall']['renewal_amount_cents']))
        old = self.store.admin_report_run(ADMIN_TOKEN, first['run']['id'])
        self.assertEqual((0, []), (old['payload']['overall']['renewal_events'], old['payload']['renewals']))

    def test_failed_report_is_persisted_and_retry_creates_a_new_version(self):
        data = self.report_data('report-failure')
        with patch.object(self.store, '_report_payload', side_effect=RuntimeError('secret-dsn-and-sql')):
            failed = self.store.admin_generate_report(ADMIN_TOKEN, data)
            duplicate = self.store.admin_generate_report(ADMIN_TOKEN, data)
        self.assertEqual(('failed', 'report_generation_failed', True),
                         (failed['run']['status'], failed['run']['error'], failed['retryable']))
        self.assertIsNone(failed['payload'])
        self.assertNotIn('secret', json.dumps(failed))
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(failed['run']['id'], duplicate['run']['id'])
        retried = self.store.admin_retry_report(ADMIN_TOKEN, failed['run']['id'], {'command_ref': 'report-retry'})
        self.assertEqual((2, 'complete', False), (retried['run']['version'],
                         retried['run']['status'], retried['retryable']))
        old = self.store.admin_report_run(ADMIN_TOKEN, failed['run']['id'])
        self.assertEqual(('failed', 'report_generation_failed', None),
                         (old['run']['status'], old['run']['error'], old['payload']))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_report_generation_failed'")['n'])
        self.assert_problem(409, 'report_retry_conflict', self.store.admin_retry_report,
                            ADMIN_TOKEN, retried['run']['id'], {'command_ref': 'retry-complete'})
        self.assert_problem(404, 'report_missing', self.store.admin_retry_report,
                            ADMIN_TOKEN, 'missing-report', {'command_ref': 'retry-missing'})

    def test_report_worker_only_generates_completed_periods_and_is_repeatable(self):
        from management.runtime_reports import due_periods, run_due_reports
        self.assertEqual((('weekly', '1969-12-22'), ('monthly', '1969-12')), due_periods(1000))
        first = run_due_reports(self.store)
        self.assertEqual((2, 0, 0), (first['generated'], first['failed'], first['skipped']))
        self.assertEqual({('weekly', '1969-12-22'), ('monthly', '1969-12')},
                         {(row['kind'], row['period_start']) for row in first['results']})
        second = run_due_reports(self.store)
        self.assertEqual((0, 0, 2), (second['generated'], second['failed'], second['skipped']))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM report_runs')['n'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM report_runs WHERE period_start IN ('1969-12-29','1970-01')")['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM audit_log WHERE action='report_generation_succeeded'")['n'])
        self.assertTrue(all(row['requested_by'] is None for row in
                            self.store.admin_report_runs(ADMIN_TOKEN, 'weekly', '1969-12-22')['items']))

    def test_report_worker_retries_latest_failure_without_overwriting_it(self):
        from management.runtime_reports import run_due_reports
        with patch.object(self.store, '_report_payload', side_effect=RuntimeError('fictional failure')):
            failed = run_due_reports(self.store)
        self.assertEqual((0, 2, 0), (failed['generated'], failed['failed'], failed['skipped']))
        succeeded = run_due_reports(self.store)
        self.assertEqual((2, 0, 0), (succeeded['generated'], succeeded['failed'], succeeded['skipped']))
        self.assertEqual({2}, {row['version'] for row in succeeded['results']})
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM report_runs WHERE status='failed'")['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM report_runs WHERE status='complete'")['n'])

    def test_report_generation_concurrent_commands_and_idempotent_retry(self):
        data = self.report_data('concurrent-same')
        with ThreadPoolExecutor(max_workers=2) as pool:
            same = list(pool.map(lambda _: self.store.admin_generate_report(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, len({row['run']['id'] for row in same}))
        self.assertEqual({False, True}, {row['duplicate'] for row in same})
        with ThreadPoolExecutor(max_workers=2) as pool:
            commands = [self.report_data('concurrent-a'), self.report_data('concurrent-b')]
            distinct = list(pool.map(lambda item: self.store.admin_generate_report(ADMIN_TOKEN, item), commands))
        self.assertEqual({2, 3}, {row['run']['version'] for row in distinct})
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM report_runs')['n'])

    def test_http_report_snapshot_routes_enforce_validation_scope_and_receipts(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        created = client.post('/v1/admin/report-runs', json=self.report_data(), headers=admin)
        self.assertEqual(201, created.status_code, created.text)
        run_id = created.json()['run']['id']
        duplicate = client.post('/v1/admin/report-runs', json=self.report_data(), headers=admin)
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertEqual(run_id, duplicate.json()['run']['id'])
        listed = client.get('/v1/admin/report-runs?kind=weekly&period_start=1969-12-29', headers=admin)
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual([run_id], [row['id'] for row in listed.json()['items']])
        detail = client.get('/v1/admin/report-runs/' + run_id, headers=admin)
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual('complete', detail.json()['run']['status'])
        self.assertEqual(403, client.post('/v1/admin/report-runs', json=self.report_data('teacher-report'), headers=teacher).status_code)
        self.assertEqual(403, client.get('/v1/admin/report-runs/' + run_id, headers=teacher).status_code)
        self.assertEqual(404, client.get('/v1/admin/report-runs/missing-report', headers=admin).status_code)
        self.assertEqual(409, client.post('/v1/admin/report-runs/' + run_id + '/retry',
                                         json={'command_ref': 'http-retry-complete'}, headers=admin).status_code)
        self.assertEqual(422, client.post('/v1/admin/report-runs',
            json=self.report_data('http-bad-kind', 'daily', '1970-01'), headers=admin).status_code)
        self.assertEqual(422, client.post('/v1/admin/report-runs',
            json=self.report_data('http-bad-week', period='1970-01-01'), headers=admin).status_code)
        other_token = 'wxu_' + 'o' * 43
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""INSERT INTO organizations VALUES('org-other','other',1);
                INSERT INTO users VALUES('admin-other','org-other','fiction-admin-other','虚构其他管理员',TRUE,1,FALSE);
                INSERT INTO roles VALUES('org-other','admin-other','admin');""")
            connection.execute("INSERT INTO auth_sessions VALUES('session-other','org-other','admin-other',%s,1,100000,NULL)",
                               (token_digest(other_token),))
        other = {'Authorization': 'Bearer ' + other_token}
        self.assertEqual(404, client.get('/v1/admin/report-runs/' + run_id, headers=other).status_code)
        self.assertEqual([], client.get('/v1/admin/report-runs?kind=weekly&period_start=1969-12-29',
                                       headers=other).json()['items'])

    @staticmethod
    def enrollment_data(command='enrollment-command', source='enrollment-source', **changes):
        data = dict(class_id='class-a', name='虚构新生', external_ref='fiction-new',
                    opening_units=12, occurred_at=100, source_ref=source,
                    reason='虚构新生入班', command_ref=command)
        data.update(changes)
        return data

    def test_admin_enrollment_commits_student_opening_ledger_and_monthly_record(self):
        result = self.store.admin_create_student(ADMIN_TOKEN, self.enrollment_data())
        self.assertEqual((False, 12, 12, 1, '虚构甲班', False),
                         tuple(result[key] for key in ('duplicate', 'opening_units', 'balance_units',
                               'balance_revision', 'class_name', 'lesson_rosters_changed')))
        student = self.read("SELECT name,external_ref,active FROM students WHERE id=%s", (result['student_id'],))
        self.assertEqual(('虚构新生', 'fiction-new', True), tuple(student.values()))
        self.assertEqual('class-a', self.read("SELECT class_id FROM student_class_enrollments WHERE student_id=%s AND left_at IS NULL",
                                             (result['student_id'],))['class_id'])
        self.assertEqual((12, 1), tuple(self.read("SELECT units,revision FROM student_balances WHERE student_id=%s",
                                                  (result['student_id'],)).values()))
        self.assertEqual((12, 'manual_review', 'enrollment-source'), tuple(self.read(
            "SELECT units,source,review_ref FROM opening_balances WHERE student_id=%s",
            (result['student_id'],)).values()))
        ledger = self.read("SELECT kind,previous_units,units_delta,units,revision,actor_type,actor_id FROM balance_ledger WHERE id=%s",
                           (result['ledger_id'],))
        self.assertEqual(('opening', 0, 12, 12, 1, 'user', 'admin-a'), tuple(ledger.values()))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM enrollment_records WHERE student_id=%s",
                                      (result['student_id'],))['n'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM lesson_students WHERE student_id=%s",
                                      (result['student_id'],))['n'])
        report = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-01')
        self.assertEqual((1, 0, 0, 0, 0), tuple(report['overall'].values()))
        self.assertEqual('虚构新生', report['enrollments'][0]['student_name'])

    def test_admin_enrollment_concurrent_retry_and_unique_conflicts(self):
        data = self.enrollment_data()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.admin_create_student(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual(results[0]['student_id'], results[1]['student_id'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM students')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM enrollment_records')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(opening_units=13))
        self.assert_problem(409, 'external_ref_conflict', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(command='other-command', source='other-source'))
        self.assert_problem(409, 'source_ref_conflict', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(command='third-command', external_ref='fiction-third'))
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM students')['n'])

    def test_admin_enrollment_scope_validation_and_rollback(self):
        self.assert_problem(403, 'admin_required', self.store.admin_create_student,
                            USER_TOKEN, self.enrollment_data())
        self.assert_problem(403, 'admin_required', self.store.admin_create_student,
                            DEVICE_TOKEN, self.enrollment_data())
        self.assert_problem(404, 'class_missing', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(class_id='missing'))
        self.assert_problem(422, 'invalid_student', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(name='   '))
        self.assert_problem(422, 'invalid_source_ref', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(source='   '))
        self.assert_problem(409, 'enrollment_clock', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(occurred_at=1121))
        self.assert_problem(409, 'external_ref_conflict', self.store.admin_create_student,
                            ADMIN_TOKEN, self.enrollment_data(external_ref='fiction-a'))
        with patch.object(self.store, '_audit', side_effect=RuntimeError('deliberate')):
            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                self.store.admin_create_student(ADMIN_TOKEN, self.enrollment_data())
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM students')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM student_balances')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM enrollment_records')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_http_admin_enrollment_enforces_scope_validation_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        created = client.post('/v1/admin/students', headers=admin, json=self.enrollment_data())
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(('虚构新生', 12, 1), tuple(created.json()[key] for key in (
            'name', 'balance_units', 'balance_revision')))
        duplicate = client.post('/v1/admin/students', headers=admin, json=self.enrollment_data())
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(created.json()['student_id'], duplicate.json()['student_id'])
        self.assertEqual(403, client.post('/v1/admin/students', headers=teacher,
                                         json=self.enrollment_data(command='denied-teacher')).status_code)
        self.assertEqual(403, client.post('/v1/admin/students', headers=device,
                                         json=self.enrollment_data(command='denied-device')).status_code)
        self.assertEqual(401, client.post('/v1/admin/students', json=self.enrollment_data()).status_code)
        self.assertEqual(422, client.post('/v1/admin/students', headers=admin,
                                         json=self.enrollment_data(command='negative', opening_units=-1_000_001)).status_code)
        self.assertEqual(422, client.post('/v1/admin/students', headers=admin,
                                         json=self.enrollment_data(command='extra', unexpected=True)).status_code)
        self.assertEqual(404, client.post('/v1/admin/students', headers=admin,
                                         json=self.enrollment_data(command='missing-class', class_id='missing')).status_code)
        self.assertEqual(409, client.post('/v1/admin/students', headers=admin,
                                         json=self.enrollment_data(command='same-external', source='new-source')).status_code)
        self.assertEqual(200, client.get('/admin-enrollment.js').status_code)
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM students')['n'])

    def test_admin_future_roster_preview_and_sync_preserve_charge_rules_and_history(self):
        created = self.store.admin_create_student(ADMIN_TOKEN, self.enrollment_data())
        student_id = created['student_id']
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active)
                    VALUES('student-aux','org-fiction','虚构辅助学生','fiction-aux',TRUE);
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-aux','org-fiction','student-aux','class-a',0,NULL,0);
                INSERT INTO opening_balances VALUES
                    ('org-fiction','student-aux',10,'manual_review','fiction-aux-review');
                INSERT INTO student_balances VALUES('org-fiction','student-aux',10,1,1);
                INSERT INTO balance_ledger VALUES
                    ('ledger-aux','org-fiction','student-aux','opening','opening-aux',0,10,10,0,1,'system',NULL,'虚构期初',1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('roster-ready','org-fiction','class-a',1100,1200,'scheduled',1),
                    ('roster-empty','org-fiction','class-a',1200,1300,'scheduled',1),
                    ('roster-mixed','org-fiction','class-a',1300,1400,'scheduled',1),
                    ('roster-present','org-fiction','class-a',1400,1500,'scheduled',1),
                    ('roster-started','org-fiction','class-a',1000,1100,'scheduled',1),
                    ('roster-cancelled','org-fiction','class-a',1500,1600,'cancelled',1),
                    ('roster-other-class','org-fiction','class-b',1100,1200,'scheduled',1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-a','roster-ready','student-a',TRUE,2),
                    ('org-fiction','class-a','roster-mixed','student-a',TRUE,1),
                    ('org-fiction','class-a','roster-mixed','student-aux',TRUE,2),
                    ('org-fiction','class-a','roster-present','student-a',TRUE,3),
                    ('org-fiction','class-b','roster-other-class','student-b',TRUE,1);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','roster-ready','student-a','missing',0,NULL,1),
                    ('org-fiction','roster-mixed','student-a','missing',0,NULL,1),
                    ('org-fiction','roster-mixed','student-aux','missing',0,NULL,1),
                    ('org-fiction','roster-present','student-a','missing',0,NULL,1),
                    ('org-fiction','roster-other-class','student-b','missing',0,NULL,1);
            """)
            connection.execute("""INSERT INTO lesson_students VALUES
                ('org-fiction','class-a','roster-present',%s,TRUE,3)""", (student_id,))
            connection.execute("""INSERT INTO attendance_records VALUES
                ('org-fiction','roster-present',%s,'missing',0,NULL,1)""", (student_id,))
        preview = self.store.admin_future_lessons(ADMIN_TOKEN, student_id)
        self.assertEqual(['roster-ready'], [row['lesson_id'] for row in preview['ready_lessons']])
        self.assertEqual(2, preview['ready_lessons'][0]['charge_units'])
        self.assertEqual(['roster-empty', 'roster-mixed'],
                         [row['lesson_id'] for row in preview['skipped_lessons']])
        self.assertEqual(['no_charge_rule', 'mixed_charge_rule'],
                         [row['reason'] for row in preview['skipped_lessons']])
        self.assertEqual([1, 2], preview['skipped_lessons'][1]['observed_charge_units'])
        self.assertEqual(['roster-present'],
                         [row['lesson_id'] for row in preview['already_present_lessons']])
        categorized = {row['lesson_id'] for key in
                       ('ready_lessons', 'skipped_lessons', 'already_present_lessons')
                       for row in preview[key]}
        self.assertFalse({'roster-started', 'roster-cancelled', 'roster-other-class'} & categorized)
        result = self.store.admin_sync_future_lessons(
            ADMIN_TOKEN, student_id, {'command_ref': 'roster-sync-command'})
        self.assertEqual((False, 1, 2, 1, True, False, False, True),
                         tuple(result[key] for key in ('duplicate', 'synced_count', 'skipped_count',
                               'already_present_count', 'lesson_rosters_changed', 'balance_changed',
                               'history_changed', 'audited')))
        roster = self.read('''SELECT class_id,expected,charge_units FROM lesson_students
            WHERE lesson_id='roster-ready' AND student_id=%s''', (student_id,))
        self.assertEqual(('class-a', True, 2), tuple(roster.values()))
        attendance = self.read('''SELECT status,revision,last_event_id,updated_at FROM attendance_records
            WHERE lesson_id='roster-ready' AND student_id=%s''', (student_id,))
        self.assertEqual(('missing', 0, None, 1000), tuple(attendance.values()))
        self.assertEqual(3, self.read("SELECT charge_units FROM lesson_students WHERE lesson_id='roster-present' AND student_id=%s",
                                      (student_id,))['charge_units'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM lesson_students WHERE student_id=%s AND lesson_id IN ('roster-empty','roster-mixed')",
                                      (student_id,))['n'])
        self.assertEqual((12, 1), tuple(self.read(
            'SELECT units,revision FROM student_balances WHERE student_id=%s', (student_id,)).values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM balance_ledger WHERE student_id=%s',
                                      (student_id,))['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_student_future_rosters_synced'")['n'])
        report = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-01')
        self.assertEqual((1, 0, 0, 0, 0), tuple(report['overall'].values()))

    def test_admin_future_roster_concurrent_retry_never_duplicates_membership(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active)
                    VALUES('student-aux','org-fiction','虚构辅助学生','fiction-aux',TRUE);
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-aux','org-fiction','student-aux','class-a',0,NULL,0);
                INSERT INTO student_balances VALUES('org-fiction','student-aux',10,1,1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES('roster-future','org-fiction','class-a',1100,1200,'scheduled',1);
                INSERT INTO lesson_students VALUES('org-fiction','class-a','roster-future','student-aux',TRUE,1);
                INSERT INTO attendance_records VALUES('org-fiction','roster-future','student-aux','missing',0,NULL,1);
            """)
        data = {'command_ref': 'roster-concurrent-command'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.admin_sync_future_lessons(
                ADMIN_TOKEN, 'student-a', data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual({1}, {result['synced_count'] for result in results})
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM lesson_students WHERE lesson_id='roster-future'")['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM attendance_records WHERE lesson_id='roster-future'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM api_commands")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_student_future_rosters_synced'")['n'])
        again = self.store.admin_sync_future_lessons(
            ADMIN_TOKEN, 'student-a', {'command_ref': 'roster-new-check-command'})
        self.assertEqual((0, 1, False), (again['synced_count'], again['already_present_count'],
                                        again['lesson_rosters_changed']))
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM lesson_students WHERE lesson_id='roster-future'")['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_student_future_rosters_synced'")['n'])
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM attendance_events")['n'])

    def test_admin_future_roster_scope_missing_and_inactive_student(self):
        data = {'command_ref': 'roster-scope-command'}
        for function in (self.store.admin_future_lessons, self.store.admin_sync_future_lessons):
            args = ('student-a',) if function == self.store.admin_future_lessons else ('student-a', data)
            self.assert_problem(403, 'admin_required', function, USER_TOKEN, *args)
            self.assert_problem(403, 'admin_required', function, DEVICE_TOKEN, *args)
            missing_args = ('missing',) if function == self.store.admin_future_lessons else ('missing', data)
            self.assert_problem(404, 'student_missing', function, ADMIN_TOKEN, *missing_args)
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE students SET active=FALSE WHERE id='student-a'")
        self.assert_problem(409, 'student_inactive', self.store.admin_future_lessons,
                            ADMIN_TOKEN, 'student-a')
        self.assert_problem(409, 'student_inactive', self.store.admin_sync_future_lessons,
                            ADMIN_TOKEN, 'student-a', data)
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM audit_log')['n'])

    def test_http_admin_future_roster_enforces_validation_scope_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active)
                    VALUES('student-aux','org-fiction','虚构辅助学生','fiction-aux',TRUE);
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-aux','org-fiction','student-aux','class-a',0,NULL,0);
                INSERT INTO student_balances VALUES('org-fiction','student-aux',10,1,1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES('roster-http','org-fiction','class-a',1100,1200,'scheduled',1);
                INSERT INTO lesson_students VALUES('org-fiction','class-a','roster-http','student-aux',TRUE,1);
                INSERT INTO attendance_records VALUES('org-fiction','roster-http','student-aux','missing',0,NULL,1);
            """)
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        path = '/v1/admin/students/student-a/future-lessons'
        preview = client.get(path, headers=admin)
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual(['roster-http'], [row['lesson_id'] for row in preview.json()['ready_lessons']])
        self.assertEqual(403, client.get(path, headers=teacher).status_code)
        self.assertEqual(403, client.get(path, headers=device).status_code)
        self.assertEqual(401, client.get(path).status_code)
        created = client.post(path, headers=admin, json={'command_ref': 'roster-http-command'})
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(1, created.json()['synced_count'])
        duplicate = client.post(path, headers=admin, json={'command_ref': 'roster-http-command'})
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(1, duplicate.json()['synced_count'])
        self.assertEqual(422, client.post(path, headers=admin, json={
            'command_ref': 'roster-http-extra', 'unexpected': True}).status_code)
        self.assertEqual(403, client.post(path, headers=teacher,
                                         json={'command_ref': 'roster-http-teacher'}).status_code)
        self.assertEqual(403, client.post(path, headers=device,
                                         json={'command_ref': 'roster-http-device'}).status_code)
        self.assertEqual(404, client.post('/v1/admin/students/missing/future-lessons', headers=admin,
                                         json={'command_ref': 'roster-http-missing'}).status_code)
        self.assertEqual(200, client.get('/admin-roster-sync.js').status_code)
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM lesson_students WHERE lesson_id='roster-http'")['n'])
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))

    @staticmethod
    def charge_rule_data(command='charge-rule-command', units=2, revision=0):
        return {'charge_units': units, 'expected_revision': revision, 'command_ref': command}

    def test_admin_charge_rule_resolves_empty_and_mixed_then_roster_sync_uses_rule(self):
        created = self.store.admin_create_student(ADMIN_TOKEN, self.enrollment_data())
        student_id = created['student_id']
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active)
                    VALUES('student-aux','org-fiction','虚构辅助学生','fiction-aux',TRUE);
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-aux','org-fiction','student-aux','class-a',0,NULL,0);
                INSERT INTO student_balances VALUES('org-fiction','student-aux',10,1,1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('rule-empty','org-fiction','class-a',1100,1200,'scheduled',1),
                    ('rule-mixed','org-fiction','class-a',1200,1300,'scheduled',1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-a','rule-mixed','student-a',TRUE,1),
                    ('org-fiction','class-a','rule-mixed','student-aux',TRUE,2);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','rule-mixed','student-a','missing',0,NULL,1),
                    ('org-fiction','rule-mixed','student-aux','missing',0,NULL,1);
            """)
        before = self.store.admin_future_lessons(ADMIN_TOKEN, student_id)
        self.assertEqual(['rule-empty', 'rule-mixed'],
                         [row['lesson_id'] for row in before['skipped_lessons']])
        empty = self.store.admin_set_lesson_charge_rule(
            ADMIN_TOKEN, 'rule-empty', self.charge_rule_data('rule-empty-command', 2))
        mixed = self.store.admin_set_lesson_charge_rule(
            ADMIN_TOKEN, 'rule-mixed', self.charge_rule_data('rule-mixed-command', 3))
        self.assertEqual((1, 0, [], False, False),
                         tuple(empty[key] for key in ('charge_rule_revision', 'roster_students_updated',
                               'previous_charge_units', 'balance_changed', 'history_changed')))
        self.assertEqual((1, 2, [1, 2]),
                         tuple(mixed[key] for key in ('charge_rule_revision', 'roster_students_updated',
                               'previous_charge_units')))
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM lesson_students WHERE lesson_id='rule-mixed' AND charge_units=3")['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM attendance_records WHERE lesson_id='rule-mixed' AND status='missing' AND revision=0")['n'])
        preview = self.store.admin_future_lessons(ADMIN_TOKEN, student_id)
        self.assertEqual(['rule-empty', 'rule-mixed'],
                         [row['lesson_id'] for row in preview['ready_lessons']])
        self.assertEqual([2, 3], [row['charge_units'] for row in preview['ready_lessons']])
        self.assertEqual([1, 1], [row['charge_rule_revision'] for row in preview['ready_lessons']])
        self.assertEqual(0, preview['skipped_count'])
        synced = self.store.admin_sync_future_lessons(
            ADMIN_TOKEN, student_id, {'command_ref': 'rule-resolved-sync'})
        self.assertEqual(2, synced['synced_count'])
        rows = [self.read('SELECT charge_units FROM lesson_students WHERE lesson_id=%s AND student_id=%s',
                          (lesson_id, student_id))['charge_units']
                for lesson_id in ('rule-empty', 'rule-mixed')]
        self.assertEqual([2, 3], rows)
        self.assertEqual((12, 1), tuple(self.read(
            'SELECT units,revision FROM student_balances WHERE student_id=%s', (student_id,)).values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM balance_ledger WHERE student_id=%s',
                                      (student_id,))['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_charge_rule_changed'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_student_future_rosters_synced'")['n'])

    def test_admin_charge_rule_concurrent_retry_and_stale_revision_are_safe(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES('rule-concurrent','org-fiction','class-a',1100,1200,'scheduled',1)")
        data = self.charge_rule_data('rule-concurrent-command', 2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.admin_set_lesson_charge_rule(
                ADMIN_TOKEN, 'rule-concurrent', data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual({1}, {result['charge_rule_revision'] for result in results})
        self.assertEqual((2, 1), tuple(self.read(
            "SELECT charge_units,revision FROM lesson_charge_rules WHERE lesson_id='rule-concurrent'").values()))
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-concurrent', self.charge_rule_data('rule-concurrent-command', 3))
        self.assert_problem(409, 'charge_rule_changed', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-concurrent', self.charge_rule_data('rule-stale-command', 3, 0))
        changed = self.store.admin_set_lesson_charge_rule(
            ADMIN_TOKEN, 'rule-concurrent', self.charge_rule_data('rule-new-command', 3, 1))
        self.assertEqual((3, 2), (changed['charge_units'], changed['charge_rule_revision']))
        self.assertEqual((3, 2), tuple(self.read(
            "SELECT charge_units,revision FROM lesson_charge_rules WHERE lesson_id='rule-concurrent'").values()))
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_charge_rule_changed'")['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_admin_charge_rule_scope_state_and_attendance_guards(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active)
                    VALUES('student-aux','org-fiction','虚构辅助学生','fiction-aux',TRUE);
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-aux','org-fiction','student-aux','class-a',0,NULL,0);
                INSERT INTO student_balances VALUES('org-fiction','student-aux',10,1,1);
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('rule-consistent','org-fiction','class-a',1100,1200,'scheduled',1),
                    ('rule-history','org-fiction','class-a',1200,1300,'scheduled',1),
                    ('rule-cancelled','org-fiction','class-a',1300,1400,'cancelled',1),
                    ('rule-empty-scope','org-fiction','class-a',1400,1500,'scheduled',1),
                    ('rule-past','org-fiction','class-a',900,950,'scheduled',1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-a','rule-consistent','student-a',TRUE,1),
                    ('org-fiction','class-a','rule-history','student-a',TRUE,1),
                    ('org-fiction','class-a','rule-history','student-aux',TRUE,2);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','rule-consistent','student-a','missing',0,NULL,1),
                    ('org-fiction','rule-history','student-a','arrived',1,NULL,1),
                    ('org-fiction','rule-history','student-aux','missing',0,NULL,1);
            """)
        self.assert_problem(403, 'admin_required', self.store.admin_set_lesson_charge_rule,
                            USER_TOKEN, 'rule-empty-scope', self.charge_rule_data())
        self.assert_problem(403, 'admin_required', self.store.admin_set_lesson_charge_rule,
                            DEVICE_TOKEN, 'rule-empty-scope', self.charge_rule_data())
        self.assert_problem(404, 'lesson_missing', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'missing', self.charge_rule_data())
        self.assert_problem(409, 'lesson_not_editable', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-past', self.charge_rule_data())
        self.assert_problem(409, 'lesson_not_editable', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-cancelled', self.charge_rule_data())
        self.assert_problem(409, 'charge_rule_consistent', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-consistent', self.charge_rule_data())
        self.assert_problem(409, 'attendance_conflict', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-history', self.charge_rule_data())
        self.assert_problem(422, 'invalid_charge_rule', self.store.admin_set_lesson_charge_rule,
                            ADMIN_TOKEN, 'rule-empty-scope', self.charge_rule_data(units=0))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM lesson_charge_rules')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM audit_log')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_http_admin_charge_rule_enforces_validation_scope_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES('rule-http','org-fiction','class-a',1100,1200,'scheduled',1)")
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        path = '/v1/admin/lessons/rule-http/charge-rule'
        created = client.post(path, headers=admin, json=self.charge_rule_data('rule-http-command', 2))
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual((2, 1, 0), tuple(created.json()[key] for key in (
            'charge_units', 'charge_rule_revision', 'roster_students_updated')))
        duplicate = client.post(path, headers=admin, json=self.charge_rule_data('rule-http-command', 2))
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(1, duplicate.json()['charge_rule_revision'])
        self.assertEqual(403, client.post(path, headers=teacher,
                                         json=self.charge_rule_data('rule-http-teacher')).status_code)
        self.assertEqual(403, client.post(path, headers=device,
                                         json=self.charge_rule_data('rule-http-device')).status_code)
        self.assertEqual(401, client.post(path, json=self.charge_rule_data('rule-http-unauthorized')).status_code)
        self.assertEqual(422, client.post(path, headers=admin,
                                         json=self.charge_rule_data('rule-http-zero', 0)).status_code)
        extra = self.charge_rule_data('rule-http-extra')
        extra['unexpected'] = True
        self.assertEqual(422, client.post(path, headers=admin, json=extra).status_code)
        self.assertEqual(409, client.post(path, headers=admin,
                                         json=self.charge_rule_data('rule-http-stale', 3, 0)).status_code)
        self.assertEqual(404, client.post('/v1/admin/lessons/missing/charge-rule', headers=admin,
                                         json=self.charge_rule_data('rule-http-missing')).status_code)
        self.assertEqual((2, 1), tuple(self.read(
            "SELECT charge_units,revision FROM lesson_charge_rules WHERE lesson_id='rule-http'").values()))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_charge_rule_changed'")['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    @staticmethod
    def lesson_data(command='lesson-command', **changes):
        data = dict(class_id='class-a', teacher_id='teacher-a', starts_at=1100,
                    ends_at=1200, charge_units=2, command_ref=command)
        data.update(changes)
        return data

    def test_admin_lesson_create_commits_rule_teacher_roster_and_visible_schedule(self):
        result = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data())
        self.assertEqual((False, '虚构甲班', '虚构教师甲', 2, 1, 1, False, False),
                         tuple(result[key] for key in ('duplicate', 'class_name', 'teacher_name',
                               'charge_units', 'charge_rule_revision', 'roster_count',
                               'balance_changed', 'history_changed')))
        lesson_id = result['lesson_id']
        self.assertEqual(('class-a', 1100, 1200, 'scheduled'), tuple(self.read(
            'SELECT class_id,starts_at,ends_at,state FROM lessons WHERE id=%s', (lesson_id,)).values()))
        self.assertEqual((2, 1), tuple(self.read(
            'SELECT charge_units,revision FROM lesson_charge_rules WHERE lesson_id=%s', (lesson_id,)).values()))
        self.assertEqual(('teacher-a', '虚构教师甲'), tuple(self.read(
            'SELECT user_id,teacher_name FROM lesson_teachers WHERE lesson_id=%s', (lesson_id,)).values()))
        self.assertEqual(('student-a', True, 2), tuple(self.read(
            'SELECT student_id,expected,charge_units FROM lesson_students WHERE lesson_id=%s', (lesson_id,)).values()))
        self.assertEqual(('missing', 0, None), tuple(self.read(
            'SELECT status,revision,last_event_id FROM attendance_records WHERE lesson_id=%s', (lesson_id,)).values()))
        admin_day = self.store.admin_lessons(ADMIN_TOKEN, '1970-01-01')
        created = next(row for row in admin_day['items'] if row['id'] == lesson_id)
        self.assertEqual(('虚构教师甲', 2, 1, 1, 0, 1),
                         tuple(created[key] for key in ('teacher_names', 'charge_units',
                               'charge_rule_revision', 'expected_count', 'arrived_count', 'missing_count')))
        teacher_day = self.store.teacher_lessons(USER_TOKEN, '1970-01-01')
        self.assertIn(lesson_id, [row['id'] for row in teacher_day['items']])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO classes VALUES('class-empty','org-fiction','虚构空班','course-a',1);
                INSERT INTO teacher_classes VALUES('org-fiction','teacher-a','class-empty');
            """)
        empty = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'lesson-empty-command', class_id='class-empty', starts_at=1200, ends_at=1300))
        self.assertEqual((0, 0, 1), (empty['roster_count'], empty['attendance_created'],
                                    empty['charge_rule_revision']))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM lesson_charge_rules WHERE lesson_id=%s',
                                      (empty['lesson_id'],))['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM lesson_students WHERE lesson_id=%s',
                                      (empty['lesson_id'],))['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))

    def test_admin_lesson_concurrent_retry_and_time_conflict_never_duplicate(self):
        data = self.lesson_data()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.admin_create_lesson(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual(1, len({result['lesson_id'] for result in results}))
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM lessons')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM lesson_charge_rules')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM api_commands')['n'])
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(charge_units=3))
        self.assert_problem(409, 'lesson_time_conflict', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data('lesson-conflict-command'))
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM lessons')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_created'")['n'])

    def test_admin_lesson_scope_teacher_and_time_validation(self):
        self.assert_problem(403, 'admin_required', self.store.admin_teachers, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_teachers, DEVICE_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_lessons, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_lessons, DEVICE_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_create_lesson,
                            USER_TOKEN, self.lesson_data())
        self.assert_problem(403, 'admin_required', self.store.admin_create_lesson,
                            DEVICE_TOKEN, self.lesson_data())
        teachers = self.store.admin_teachers(ADMIN_TOKEN)['items']
        self.assertEqual({'teacher-a', 'teacher-b'}, {row['id'] for row in teachers})
        self.assertEqual(['class-a'], next(row for row in teachers if row['id'] == 'teacher-a')['class_ids'])
        self.assert_problem(422, 'invalid_date', self.store.admin_lessons, ADMIN_TOKEN, 'bad')
        self.assert_problem(404, 'class_missing', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(class_id='missing'))
        self.assert_problem(409, 'teacher_scope', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(teacher_id='teacher-b'))
        self.assert_problem(409, 'lesson_clock', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(starts_at=1000))
        self.assert_problem(422, 'invalid_lesson_time', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(ends_at=1100))
        self.assert_problem(422, 'invalid_lesson_time', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(ends_at=1100 + 12 * 3600 + 1))
        self.assert_problem(422, 'invalid_charge_rule', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data(charge_units=0))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE users SET active=FALSE WHERE id='teacher-a'")
        self.assert_problem(409, 'teacher_scope', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data())
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM lessons')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM audit_log')['n'])

    def test_http_admin_lesson_schedule_enforces_scope_validation_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        teachers = client.get('/v1/admin/teachers', headers=admin)
        self.assertEqual(200, teachers.status_code, teachers.text)
        self.assertEqual({'teacher-a', 'teacher-b'}, {row['id'] for row in teachers.json()['items']})
        self.assertEqual(403, client.get('/v1/admin/teachers', headers=teacher).status_code)
        self.assertEqual(200, client.get('/v1/admin/lessons?date=1970-01-01', headers=admin).status_code)
        self.assertEqual(422, client.get('/v1/admin/lessons?date=bad', headers=admin).status_code)
        self.assertEqual(403, client.get('/v1/admin/lessons', headers=device).status_code)
        created = client.post('/v1/admin/lessons', headers=admin, json=self.lesson_data())
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(1, created.json()['roster_count'])
        duplicate = client.post('/v1/admin/lessons', headers=admin, json=self.lesson_data())
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(created.json()['lesson_id'], duplicate.json()['lesson_id'])
        self.assertEqual(403, client.post('/v1/admin/lessons', headers=teacher,
                                         json=self.lesson_data('lesson-http-teacher')).status_code)
        self.assertEqual(403, client.post('/v1/admin/lessons', headers=device,
                                         json=self.lesson_data('lesson-http-device')).status_code)
        self.assertEqual(401, client.post('/v1/admin/lessons', json=self.lesson_data()).status_code)
        self.assertEqual(422, client.post('/v1/admin/lessons', headers=admin,
                                         json=self.lesson_data('lesson-http-zero', charge_units=0)).status_code)
        extra = self.lesson_data('lesson-http-extra')
        extra['unexpected'] = True
        self.assertEqual(422, client.post('/v1/admin/lessons', headers=admin, json=extra).status_code)
        self.assertEqual(200, client.get('/admin-schedule.js').status_code)
        teacher_day = client.get('/v1/teacher/lessons?date=1970-01-01', headers=teacher)
        self.assertIn(created.json()['lesson_id'], [row['id'] for row in teacher_day.json()['items']])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM lessons')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    @staticmethod
    def renewal_data(command='renewal-command', source='renewal-source', **changes):
        data = dict(student_id='student-a', course_id='course-a', units_added=20,
                    amount_cents=256000, unit_price_cents=12800, occurred_at=100,
                    source_ref=source, reason='虚构续费入账', command_ref=command)
        data.update(changes)
        return data

    def test_admin_renewal_commits_balance_ledger_audit_and_parent_tasks(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO wechat_identities VALUES
                    ('org-fiction','fiction-open-a',NULL,1),
                    ('org-fiction','fiction-open-b',NULL,1),
                    ('org-fiction','fiction-open-old',NULL,1);
                INSERT INTO parent_student_bindings VALUES
                    ('binding-a','org-fiction','fiction-open-a','student-a',1,NULL),
                    ('binding-b','org-fiction','fiction-open-b','student-a',1,NULL),
                    ('binding-old','org-fiction','fiction-open-old','student-a',1,2);
            """)
        result = self.store.admin_renewal(ADMIN_TOKEN, self.renewal_data())
        self.assertEqual((False, 10, 30, 2, 2, 'pending', False),
                         tuple(result[key] for key in ('duplicate', 'previous_balance_units',
                               'balance_units', 'balance_revision', 'notification_tasks',
                               'notification_status', 'wechat_sending_enabled')))
        self.assertEqual((30, 2), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        ledger = self.read("SELECT kind,source_event_id,previous_units,units_delta,units,revision FROM balance_ledger WHERE id=%s",
                           (result['ledger_id'],))
        self.assertEqual(('renewal', result['renewal_id'], 10, 20, 30, 2),
                         tuple(ledger[key] for key in ('kind', 'source_event_id', 'previous_units',
                               'units_delta', 'units', 'revision')))
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM notification_outbox WHERE kind='renewal'")['n'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM notification_outbox WHERE binding_id='binding-old'")['n'])
        self.assertEqual('30', self.read("SELECT payload->>'balance_units' AS units FROM notification_outbox WHERE binding_id='binding-a'")['units'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_renewal_recorded'")['n'])
        students = self.store.admin_students(ADMIN_TOKEN)['items']
        self.assertEqual(30, next(row for row in students if row['id'] == 'student-a')['balance_units'])
        report = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-01')
        self.assertEqual((1, 1, 20), tuple(report['overall'][key] for key in (
            'renewal_students', 'renewal_events', 'renewal_units')))

    def test_admin_renewal_concurrent_retry_and_source_conflict_never_double_credit(self):
        data = self.renewal_data()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.admin_renewal(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in results))
        self.assertEqual(1, sum(result['duplicate'] for result in results))
        self.assertEqual(results[0]['renewal_id'], results[1]['renewal_id'])
        self.assertEqual(30, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(units_added=21))
        self.assert_problem(409, 'source_ref_conflict', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(command='renewal-other-command'))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM renewal_records')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_admin_renewal_scope_limits_and_failure_roll_back_everything(self):
        self.assert_problem(403, 'admin_required', self.store.admin_students, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_students, DEVICE_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_renewal, USER_TOKEN, self.renewal_data())
        self.assert_problem(403, 'admin_required', self.store.admin_renewal, DEVICE_TOKEN, self.renewal_data())
        self.assert_problem(404, 'student_missing', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(student_id='missing'))
        self.assert_problem(422, 'invalid_source_ref', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(source='   '))
        self.assert_problem(409, 'renewal_clock', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(occurred_at=1121))
        self.assert_problem(409, 'balance_limit', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data(units_added=10_000_000))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE students SET active=FALSE WHERE id='student-a'")
        self.assert_problem(409, 'student_inactive', self.store.admin_renewal,
                            ADMIN_TOKEN, self.renewal_data())
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE students SET active=TRUE WHERE id='student-a'")
        with patch.object(self.store, '_audit', side_effect=RuntimeError('deliberate')):
            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                self.store.admin_renewal(ADMIN_TOKEN, self.renewal_data())
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM renewal_records')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM notification_outbox')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_admin_renewal_enforces_scope_validation_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        listed = client.get('/v1/admin/students', headers=admin)
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual({'student-a', 'student-b'}, {row['id'] for row in listed.json()['items']})
        self.assertEqual(403, client.get('/v1/admin/students', headers=teacher).status_code)
        self.assertEqual(403, client.get('/v1/admin/students', headers=device).status_code)
        self.assertEqual(401, client.get('/v1/admin/students').status_code)
        created = client.post('/v1/admin/renewals', headers=admin, json=self.renewal_data())
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual((10, 30, 2), tuple(created.json()[key] for key in (
            'previous_balance_units', 'balance_units', 'balance_revision')))
        duplicate = client.post('/v1/admin/renewals', headers=admin, json=self.renewal_data())
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(403, client.post('/v1/admin/renewals', headers=teacher,
                                         json=self.renewal_data(command='denied-teacher')).status_code)
        self.assertEqual(403, client.post('/v1/admin/renewals', headers=device,
                                         json=self.renewal_data(command='denied-device')).status_code)
        self.assertEqual(401, client.post('/v1/admin/renewals', json=self.renewal_data()).status_code)
        self.assertEqual(422, client.post('/v1/admin/renewals', headers=admin,
                                         json=self.renewal_data(command='bad-units', units_added=0)).status_code)
        self.assertEqual(422, client.post('/v1/admin/renewals', headers=admin,
                                         json=self.renewal_data(command='extra', unexpected=True)).status_code)
        self.assertEqual(409, client.post('/v1/admin/renewals', headers=admin,
                                         json=self.renewal_data(command='same-source')).status_code)
        self.assertEqual(404, client.post('/v1/admin/renewals', headers=admin,
                                         json=self.renewal_data(command='missing', student_id='missing')).status_code)
        self.assertEqual(200, client.get('/admin-renewal.js').status_code)
        self.assertEqual(30, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_http_teacher_list_is_scoped_and_web_makeup_routes_are_removed(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        other = {'Authorization': 'Bearer ' + OTHER_USER_TOKEN}
        listed = client.get('/v1/teacher/lessons?date=1970-01-01', headers=teacher)
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual(['lesson-a'], [row['id'] for row in listed.json()['items']])
        self.assertEqual(422, client.get('/v1/teacher/lessons?date=not-a-date', headers=teacher).status_code)
        for role, token in (('teacher', USER_TOKEN), ('admin', ADMIN_TOKEN)):
            response = client.post(f'/v1/{role}/lessons/lesson-a/makeup',
                                   headers={'Authorization': 'Bearer ' + token}, json=correction())
            self.assertEqual(404, response.status_code, response.text)
        denied = client.post('/v1/teacher/lessons/lesson-a/leave', headers=other,
                             json=correction('http-other-scope'))
        self.assertEqual(403, denied.status_code, denied.text)
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_http_teacher_leave_is_idempotent_and_does_not_debit(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        path = '/v1/teacher/lessons/lesson-a/leave'
        created = client.post(path, headers=teacher, json=correction('http-leave'))
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(('leave', 10, 1),
                         (created.json()['status'], created.json()['balance_units'], created.json()['balance_revision']))
        self.assertEqual(200, client.post(path, headers=teacher, json=correction('http-leave')).status_code)
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_teacher_makeup_debits_once_and_duplicate_is_safe(self):
        result = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction())
        self.assertEqual(('arrived', 9, 2), (result['status'], result['balance_units'], result['balance_revision']))
        duplicate = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'makeup', correction())
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_teacher_leave_changes_attendance_without_debit(self):
        result = self.store.teacher_correction(USER_TOKEN, 'lesson-a', 'leave', correction())
        self.assertEqual(('leave', 10, 1), (result['status'], result['balance_units'], result['balance_revision']))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual('leave', self.read("SELECT status FROM attendance_records WHERE lesson_id='lesson-a'")['status'])

    def test_teacher_cannot_read_or_change_another_teachers_lesson(self):
        self.assert_problem(403, 'lesson_scope', self.store.teacher_lesson, USER_TOKEN, 'lesson-b')
        self.assert_problem(403, 'lesson_scope', self.store.teacher_correction,
                            USER_TOKEN, 'lesson-b', 'makeup', dict(correction(), student_id='student-b'))
        self.assert_problem(403, 'student_scope', self.store.teacher_adjust_balance,
                            USER_TOKEN, 'student-b', dict(units=8, reason='虚构越权', expected_revision=1, command_ref='balance-outside'))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_teacher_balance_adjustment_is_scoped_optimistic_and_idempotent(self):
        data = dict(units=12, reason='虚构余额校正', expected_revision=1, command_ref='balance-1')
        result = self.store.teacher_adjust_balance(USER_TOKEN, 'student-a', data)
        self.assertEqual((12, 2, False), (result['units'], result['revision'], result['duplicate']))
        duplicate = self.store.teacher_adjust_balance(USER_TOKEN, 'student-a', data)
        self.assertTrue(duplicate['duplicate'])
        self.assert_problem(409, 'balance_changed', self.store.teacher_adjust_balance,
                            USER_TOKEN, 'student-a', dict(data, command_ref='balance-stale'))
        self.assertEqual(12, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_failure_after_balance_update_rolls_back_everything(self):
        with patch.object(self.store, '_audit', side_effect=RuntimeError('deliberate')):
            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual('missing', self.read("SELECT status FROM attendance_records WHERE lesson_id='lesson-a'")['status'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_duplicate_is_rejected_after_scope_is_revoked(self):
        self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("DELETE FROM device_classes WHERE device_id='device-a' AND class_id='class-a'")
        self.assert_problem(403, 'checkin_scope', self.store.tablet_checkin, DEVICE_TOKEN, checkin())

    def test_logout_revokes_session(self):
        self.assertEqual('teacher-a', self.store.me(USER_TOKEN)['id'])
        self.assertTrue(self.store.logout(USER_TOKEN)['logged_out'])
        self.assert_problem(401, 'unauthorized', self.store.me, USER_TOKEN)

    def test_runtime_login_admin_pages_and_http_activation(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        with psycopg.connect(self.conninfo) as connection:
            connection.execute('INSERT INTO user_credentials VALUES(%s,%s,%s,%s)',
                               ('org-fiction', 'admin-a', password_hash('Fictional admin password 2026!'), 1))
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        login = dict(organization='fiction', login='fiction-admin-a', password='Fictional admin password 2026!')
        self.assertEqual(401, client.post('/v1/auth/login', json=dict(login, password='wrong')).status_code)
        response = client.post('/v1/auth/login', json=login)
        self.assertEqual(200, response.status_code, response.text)
        admin = {'Authorization': 'Bearer ' + response.json()['access_token']}
        self.assertEqual(['admin'], client.get('/v1/me', headers=admin).json()['roles'])
        self.assertEqual(200, client.get('/runtime').status_code)
        self.assertIn("script-src 'self'", client.get('/runtime').headers['content-security-policy'])
        self.assertEqual(2, len(client.get('/v1/admin/classes', headers=admin).json()['items']))
        issued = client.post('/v1/admin/devices', headers=admin, json=dict(name='网页开通平板', class_ids=['class-a'], command_ref='http-enroll'))
        self.assertEqual(201, issued.status_code, issued.text)
        activated = client.post('/v1/tablet/activate', json={'activation_code': issued.json()['activation_code']})
        self.assertEqual(200, activated.status_code, activated.text)
        device = {'Authorization': 'Bearer ' + activated.json()['access_token']}
        self.assertEqual(200, client.get('/v1/tablet/bootstrap', headers=device).status_code)
        devices = client.get('/v1/admin/devices', headers=admin).json()['items']
        self.assertTrue(next(row for row in devices if row['id'] == issued.json()['device_id'])['active'])

    def test_admin_issues_scoped_one_time_activation_and_tablet_can_bootstrap(self):
        data = dict(name='虚构新平板', class_ids=['class-a'], command_ref='enroll-1')
        self.assert_problem(403, 'admin_required', self.store.admin_create_device, USER_TOKEN, data)
        issued = self.store.admin_create_device(ADMIN_TOKEN, data)
        self.assertTrue(issued['activation_required'])
        self.assert_problem(401, 'unauthorized', self.store.tablet_bootstrap, issued['activation_code'])
        activated = self.store.activate_tablet(issued['activation_code'])
        self.assertEqual(issued['device_id'], activated['device_id'])
        bootstrap = self.store.tablet_bootstrap(activated['access_token'])
        self.assertEqual(['class-a'], [row['id'] for row in bootstrap['classes']])
        self.assertEqual(['lesson-a'], [row['id'] for row in bootstrap['lessons']])
        self.assert_problem(401, 'invalid_activation', self.store.activate_tablet, issued['activation_code'])
        self.assertNotIn(issued['activation_code'], json.dumps(self.read("SELECT detail FROM audit_log WHERE action='device_enrollment_issued'"), ensure_ascii=False))

    def test_device_enrollment_duplicate_does_not_reveal_or_retain_one_time_code(self):
        data = dict(name='虚构新平板', class_ids=['class-a'], command_ref='enroll-duplicate')
        first = self.store.admin_create_device(ADMIN_TOKEN, data)
        second = self.store.admin_create_device(ADMIN_TOKEN, data)
        self.assertTrue(second['duplicate'])
        self.assertIsNone(second['activation_code'])
        self.assertFalse(second['code_recoverable'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM devices')['n'])
        receipt = self.read("SELECT response FROM api_commands WHERE command_ref='enroll-duplicate'")
        self.assertNotIn('activation_code', receipt['response'])

    def test_reissue_revokes_old_token_and_preserves_authorized_classes(self):
        data = {'command_ref': 'reissue-service'}
        self.assert_problem(403, 'admin_required', self.store.admin_reissue_device, USER_TOKEN, 'device-a', data)
        issued = self.store.admin_reissue_device(ADMIN_TOKEN, 'device-a', data)
        self.assertTrue(issued['previous_credentials_revoked'])
        self.assert_problem(401, 'unauthorized', self.store.tablet_bootstrap, DEVICE_TOKEN)
        activated = self.store.activate_tablet(issued['activation_code'])
        self.assertNotEqual('generation-a', activated['generation'])
        bootstrap = self.store.tablet_bootstrap(activated['access_token'])
        self.assertEqual(['class-a'], [row['id'] for row in bootstrap['classes']])
        self.assertEqual(['lesson-a'], [row['id'] for row in bootstrap['lessons']])
        duplicate = self.store.admin_reissue_device(ADMIN_TOKEN, 'device-a', data)
        self.assertTrue(duplicate['duplicate'])
        self.assertIsNone(duplicate['activation_code'])
        self.assertFalse(duplicate['code_recoverable'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM device_credentials WHERE device_id='device-a'")['n'])
        receipt = self.read("SELECT response FROM api_commands WHERE command_ref='reissue-service'")
        self.assertNotIn('activation_code', receipt['response'])
        audit = self.read("SELECT detail FROM audit_log WHERE action='device_enrollment_reissued'")
        self.assertNotIn(issued['activation_code'], json.dumps(audit['detail'], ensure_ascii=False))

    def test_disable_revokes_token_and_unused_code_then_disabled_device_can_reactivate(self):
        self.assert_problem(403, 'admin_required', self.store.admin_disable_device,
                            USER_TOKEN, 'device-a', {'command_ref': 'disable-denied'})
        pending = self.store.admin_reissue_device(ADMIN_TOKEN, 'device-a', {'command_ref': 'reissue-before-disable'})
        disabled = self.store.admin_disable_device(ADMIN_TOKEN, 'device-a', {'command_ref': 'disable-service'})
        self.assertFalse(disabled['active'])
        self.assertTrue(disabled['credentials_revoked'])
        self.assert_problem(401, 'unauthorized', self.store.tablet_bootstrap, DEVICE_TOKEN)
        self.assert_problem(401, 'invalid_activation', self.store.activate_tablet, pending['activation_code'])
        duplicate = self.store.admin_disable_device(ADMIN_TOKEN, 'device-a', {'command_ref': 'disable-service'})
        self.assertTrue(duplicate['duplicate'])
        reissued = self.store.admin_reissue_device(ADMIN_TOKEN, 'device-a', {'command_ref': 'reissue-disabled'})
        activated = self.store.activate_tablet(reissued['activation_code'])
        self.assertEqual('device-a', activated['device_id'])
        self.assertEqual(['class-a'], [row['id'] for row in self.store.tablet_bootstrap(activated['access_token'])['classes']])
        self.assertTrue(self.read("SELECT active FROM devices WHERE id='device-a'")['active'])

    def test_http_device_lifecycle_enforces_scope_and_idempotent_receipts(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        old_device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        path = '/v1/admin/devices/device-a/activation'
        self.assertEqual(403, client.post(path, headers=teacher, json={'command_ref': 'http-denied'}).status_code)
        self.assertEqual(403, client.post('/v1/admin/devices/device-a/disable', headers=teacher,
                                          json={'command_ref': 'http-disable-denied'}).status_code)
        issued = client.post(path, headers=admin, json={'command_ref': 'http-reissue'})
        self.assertEqual(200, issued.status_code, issued.text)
        self.assertEqual(401, client.get('/v1/tablet/bootstrap', headers=old_device).status_code)
        duplicate = client.post(path, headers=admin, json={'command_ref': 'http-reissue'})
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertIsNone(duplicate.json()['activation_code'])
        self.assertFalse(duplicate.json()['code_recoverable'])
        activated = client.post('/v1/tablet/activate', json={'activation_code': issued.json()['activation_code']})
        self.assertEqual(200, activated.status_code, activated.text)
        new_device = {'Authorization': 'Bearer ' + activated.json()['access_token']}
        self.assertEqual(200, client.get('/v1/tablet/bootstrap', headers=new_device).status_code)
        disabled = client.post('/v1/admin/devices/device-a/disable', headers=admin,
                               json={'command_ref': 'http-disable'})
        self.assertEqual(200, disabled.status_code, disabled.text)
        self.assertFalse(disabled.json()['active'])
        self.assertEqual(401, client.get('/v1/tablet/bootstrap', headers=new_device).status_code)
        repeated = client.post('/v1/admin/devices/device-a/disable', headers=admin,
                               json={'command_ref': 'http-disable'})
        self.assertEqual(200, repeated.status_code, repeated.text)
        self.assertTrue(repeated.json()['duplicate'])
        self.assertEqual(422, client.post(path, headers=admin,
                                          json={'command_ref': 'http-extra', 'unexpected': True}).status_code)
        self.assertEqual(404, client.post('/v1/admin/devices/missing/disable', headers=admin,
                                          json={'command_ref': 'http-missing'}).status_code)

    def test_expired_activation_cannot_issue_credentials(self):
        issued = self.store.admin_create_device(ADMIN_TOKEN, dict(name='虚构过期平板', class_ids=['class-a'], command_ref='enroll-expired'))
        self.store.clock = lambda: 2000
        self.assert_problem(410, 'activation_expired', self.store.activate_tablet, issued['activation_code'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM device_credentials WHERE device_id=%s", (issued['device_id'],))['n'])

    def test_http_routes_enforce_auth_validation_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        self.assertEqual(200, client.get('/health/ready').json() and 200)
        self.assertEqual(401, client.post('/v1/tablet/checkins', json=checkin()).status_code)
        response = client.post('/v1/tablet/checkins', json=checkin(), headers=device)
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual(200, client.post('/v1/tablet/checkins', json=checkin(), headers=device).status_code)
        self.assertEqual(403, client.post('/v1/tablet/checkins', json=checkin('wrong-kind'), headers=teacher).status_code)
        invalid = dict(checkin('invalid-extra'), unexpected=True)
        self.assertEqual(422, client.post('/v1/tablet/checkins', json=invalid, headers=device).status_code)
        lesson = client.get('/v1/teacher/lessons/lesson-a', headers=teacher)
        self.assertEqual(200, lesson.status_code, lesson.text)
        self.assertEqual('arrived', lesson.json()['students'][0]['status'])
        self.assertEqual(403, client.get('/v1/teacher/lessons/lesson-b', headers=teacher).status_code)

    @staticmethod
    def course_data(command='course-command', **changes):
        data = dict(name='虚构新课程', content='虚构课程内容与教学目标', unit_price_cents=16800,
                    reason='虚构课程资料已确认', command_ref=command)
        data.update(changes)
        return data

    def test_admin_course_create_edit_disable_is_idempotent_audited_and_scoped(self):
        data = self.course_data()
        self.assert_problem(403, 'admin_required', self.store.admin_courses, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_create_course, DEVICE_TOKEN, data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(lambda _: self.store.admin_create_course(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in created))
        self.assertEqual(1, sum(result['duplicate'] for result in created))
        course_id = created[0]['course_id']
        self.assertEqual(created[0]['course_id'], created[1]['course_id'])
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_create_course,
                            ADMIN_TOKEN, self.course_data(name='不同内容'))
        self.assert_problem(409, 'course_name_conflict', self.store.admin_create_course,
                            ADMIN_TOKEN, self.course_data('course-other-command'))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("INSERT INTO organizations VALUES('org-other','other',1)")
            connection.execute("INSERT INTO courses VALUES('course-other','org-other','其他机构课程','其他内容',100,TRUE,1)")
        update = dict(self.course_data('course-edit-command', name='虚构新课程调价',
                                      content='虚构更新后的课程内容', unit_price_cents=18888,
                                      reason='虚构调价并停用'), active=False)
        edited = self.store.admin_update_course(ADMIN_TOKEN, course_id, update)
        self.assertEqual((16800, 18888, True, False, False), tuple(edited[key] for key in (
            'previous_unit_price_cents', 'unit_price_cents', 'previous_active', 'active',
            'historical_renewals_changed')))
        self.assertTrue(self.store.admin_update_course(ADMIN_TOKEN, course_id, update)['duplicate'])
        self.assert_problem(404, 'course_missing', self.store.admin_update_course,
                            ADMIN_TOKEN, 'course-other', dict(update, command_ref='cross-org-course'))
        listed = self.store.admin_courses(ADMIN_TOKEN)['items']
        self.assertEqual(3, len(listed))
        current = next(row for row in listed if row['id'] == course_id)
        self.assertEqual(('虚构新课程调价', 18888, False, 0), tuple(current[key] for key in (
            'name', 'unit_price_cents', 'active', 'class_count')))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_course_created'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_course_updated'")['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_admin_course_validation_and_inactive_business_boundaries(self):
        for changes in ({'name': '   '}, {'content': '  '}, {'unit_price_cents': -1},
                        {'unit_price_cents': True}, {'unit_price_cents': 100000001}, {'reason': ' '}):
            with self.subTest(changes=changes):
                self.assert_problem(422, 'invalid_course' if 'name' in changes or 'content' in changes else
                                    'invalid_course_reason' if 'reason' in changes else 'invalid_course_price',
                                    self.store.admin_create_course, ADMIN_TOKEN,
                                    self.course_data('invalid-' + str(len(changes)) + str(list(changes)), **changes))
        renewal = self.store.admin_renewal(ADMIN_TOKEN, self.renewal_data())
        self.assertEqual(12800, renewal['unit_price_cents'])
        update = dict(self.course_data('course-a-disable', name='虚构课程', content='虚构课程内容',
                                      unit_price_cents=13800, reason='虚构停用课程'), active=False)
        self.store.admin_update_course(ADMIN_TOKEN, 'course-a', update)
        snapshot = self.read("SELECT amount_cents,unit_price_cents FROM renewal_records WHERE id=%s",
                             (renewal['renewal_id'],))
        self.assertEqual((256000, 12800), tuple(snapshot.values()))
        self.assert_problem(409, 'course_scope', self.store.admin_renewal, ADMIN_TOKEN,
                            self.renewal_data('renewal-inactive', 'renewal-inactive-source'))
        self.assert_problem(404, 'course_missing', self.store.admin_create_class, ADMIN_TOKEN,
                            self.class_data('class-inactive-course'))
        self.assert_problem(409, 'course_inactive', self.store.admin_create_lesson, ADMIN_TOKEN,
                            self.lesson_data('lesson-inactive-course'))
        self.assertEqual(30, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM renewal_records')['n'])
        # Existing class metadata may still be corrected without reactivating the course.
        corrected = self.store.admin_update_class(ADMIN_TOKEN, 'class-a', dict(
            self.class_data('class-inactive-name', name='虚构甲班更名'), expected_version=0))
        self.assertEqual('虚构甲班更名', corrected['name'])

    def test_http_admin_course_contract_permissions_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        self.assertEqual(200, client.get('/v1/admin/courses', headers=admin).status_code)
        self.assertEqual(403, client.get('/v1/admin/courses', headers=teacher).status_code)
        self.assertEqual(401, client.get('/v1/admin/courses').status_code)
        created = client.post('/v1/admin/courses', headers=admin, json=self.course_data())
        self.assertEqual(201, created.status_code, created.text)
        repeated = client.post('/v1/admin/courses', headers=admin, json=self.course_data())
        self.assertEqual(200, repeated.status_code, repeated.text)
        self.assertEqual(created.json()['course_id'], repeated.json()['course_id'])
        course_id = created.json()['course_id']
        update = dict(self.course_data('course-http-edit', unit_price_cents=18888), active=False)
        edited = client.put('/v1/admin/courses/' + course_id, headers=admin, json=update)
        self.assertEqual(200, edited.status_code, edited.text)
        self.assertEqual((18888, False), (edited.json()['unit_price_cents'], edited.json()['active']))
        for payload in (self.course_data('bad-price', unit_price_cents=-1),
                        self.course_data('bad-fraction', unit_price_cents=1.5),
                        self.course_data('bad-extra', unexpected=True),
                        self.course_data('bad-content', content='')):
            self.assertEqual(422, client.post('/v1/admin/courses', headers=admin, json=payload).status_code)
        self.assertEqual(403, client.put('/v1/admin/courses/' + course_id, headers=teacher,
                                        json=dict(update, command_ref='teacher-course-edit')).status_code)
        self.assertEqual(404, client.put('/v1/admin/courses/missing', headers=admin,
                                        json=dict(update, command_ref='missing-course-edit')).status_code)
        self.assertEqual(200, client.get('/admin-directory.js').status_code)
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    @staticmethod
    def schedule_data(command='schedule-command', **changes):
        data = dict(class_id='class-a', weekday=1, interval_weeks=1, anchor_date='2026-11-02',
                    start_time='14:00', end_time='15:30', duration_minutes=90, end_day_offset=0,
                    effective_date='2026-11-02', teacher_id='teacher-a',
                    reason='虚构每周排课已确认', command_ref=command)
        data.update(changes)
        return data

    def test_admin_schedule_create_update_is_idempotent_audited_and_scoped(self):
        data = self.schedule_data()
        self.assert_problem(403, 'admin_required', self.store.admin_schedule_templates, USER_TOKEN)
        self.assert_problem(403, 'admin_required', self.store.admin_create_schedule_template, DEVICE_TOKEN, data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(lambda _: self.store.admin_create_schedule_template(ADMIN_TOKEN, data), range(2)))
        self.assertEqual(1, sum(not result['duplicate'] for result in created))
        self.assertEqual(1, sum(result['duplicate'] for result in created))
        schedule_id = created[0]['schedule_id']
        self.assertEqual(created[0]['schedule_id'], created[1]['schedule_id'])
        self.assertEqual((1, '2026-11-02', 90, 0, False), tuple(created[0][key] for key in (
            'weekday', 'anchor_date', 'duration_minutes', 'linked_lesson_count', 'existing_lessons_changed')))
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_create_schedule_template,
                            ADMIN_TOKEN, self.schedule_data(start_time='16:00', end_time='17:30'))
        self.assert_problem(409, 'schedule_conflict', self.store.admin_create_schedule_template,
                            ADMIN_TOKEN, self.schedule_data('schedule-duplicate'))
        update = self.schedule_data('schedule-update', start_time='16:00', end_time='17:00',
                                    duration_minutes=60, effective_date='2026-11-09', reason='虚构调整固定时段')
        update.pop('class_id')
        edited = self.store.admin_update_schedule_template(ADMIN_TOKEN, schedule_id, update)
        self.assertEqual(('14:00', '16:00', '17:00', 60, False),
                         (edited['previous']['start_time'], edited['start_time'], edited['end_time'],
                          edited['duration_minutes'], edited['existing_lessons_changed']))
        self.assertTrue(self.store.admin_update_schedule_template(ADMIN_TOKEN, schedule_id, update)['duplicate'])
        self.assert_problem(404, 'schedule_missing', self.store.admin_update_schedule_template,
                            ADMIN_TOKEN, 'schedule-other-org', dict(update, command_ref='schedule-cross-org'))
        listed = self.store.admin_schedule_templates(ADMIN_TOKEN)['items']
        self.assertEqual(1, len(listed))
        self.assertEqual(('虚构甲班', '虚构课程', '虚构教师甲', '16:00', 0), tuple(listed[0][key] for key in (
            'class_name', 'course_name', 'teacher_name', 'start_time', 'linked_lesson_count')))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_schedule_template_created'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_schedule_template_updated'")['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    def test_admin_schedule_validation_course_teacher_and_handover_boundaries(self):
        cases = [
            ({'weekday': 0}, 'invalid_schedule'), ({'weekday': True}, 'invalid_schedule'),
            ({'interval_weeks': 3}, 'invalid_schedule'), ({'end_day_offset': 2}, 'invalid_schedule'),
            ({'anchor_date': '2026-02-30'}, 'invalid_schedule_date'),
            ({'effective_date': '2026-11-2'}, 'invalid_schedule_date'),
            ({'start_time': '9:00'}, 'invalid_schedule_time'), ({'end_time': '24:00'}, 'invalid_schedule_time'),
            ({'anchor_date': '2026-11-03', 'weekday': 1}, 'schedule_anchor_weekday'),
            ({'duration_minutes': 60}, 'schedule_duration_mismatch'),
            ({'start_time': '23:00', 'end_time': '00:30', 'duration_minutes': 90}, 'schedule_duration_mismatch'),
            ({'start_time': '00:00', 'end_time': '00:00', 'duration_minutes': 1440}, 'schedule_duration_mismatch'),
            ({'reason': '  '}, 'invalid_schedule_reason')]
        for index, (changes, code) in enumerate(cases):
            with self.subTest(changes=changes):
                self.assert_problem(422, code, self.store.admin_create_schedule_template, ADMIN_TOKEN,
                                    self.schedule_data('invalid-schedule-' + str(index), **changes))
        self.assert_problem(404, 'class_missing', self.store.admin_create_schedule_template,
                            ADMIN_TOKEN, self.schedule_data('schedule-missing-class', class_id='class-other'))
        self.assert_problem(409, 'teacher_scope', self.store.admin_create_schedule_template,
                            ADMIN_TOKEN, self.schedule_data('schedule-unauthorized', teacher_id='teacher-b'))
        self.store.admin_update_course(ADMIN_TOKEN, 'course-a', dict(
            self.course_data('schedule-course-disable', name='虚构课程', content='虚构课程内容'), active=False))
        self.assert_problem(409, 'course_inactive', self.store.admin_create_schedule_template,
                            ADMIN_TOKEN, self.schedule_data('schedule-inactive-course'))

    def test_schedule_supports_cross_day_and_alternating_teachers_without_rewriting_lessons(self):
        self.store.admin_set_class_teacher(ADMIN_TOKEN, 'class-a', dict(
            teacher_ids=['teacher-a', 'teacher-b'], expected_version=0,
            reason='虚构双教师隔周轮课授权', command_ref='schedule-two-teacher-auth'))
        first = self.store.admin_create_schedule_template(ADMIN_TOKEN, self.schedule_data(
            'schedule-alternate-a', weekday=6, interval_weeks=2, anchor_date='2026-11-07'))
        second = self.store.admin_create_schedule_template(ADMIN_TOKEN, self.schedule_data(
            'schedule-alternate-b', weekday=6, interval_weeks=2, anchor_date='2026-11-14', teacher_id='teacher-b'))
        cross_day = self.store.admin_create_schedule_template(ADMIN_TOKEN, self.schedule_data(
            'schedule-cross-day', weekday=5, anchor_date='2026-11-06', effective_date='2026-11-06',
            start_time='23:00', end_time='00:30', end_day_offset=1))
        self.assertEqual(('teacher-a', '2026-11-07'), (first['teacher_id'], first['anchor_date']))
        self.assertEqual(('teacher-b', '2026-11-14'), (second['teacher_id'], second['anchor_date']))
        self.assertEqual((90, 1), (cross_day['duration_minutes'], cross_day['end_day_offset']))
        lesson = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'schedule-linked-lesson', schedule_id=first['schedule_id']))
        update = self.schedule_data('schedule-after-lesson', weekday=6, interval_weeks=2,
                                    anchor_date='2026-11-07', start_time='16:00', end_time='17:30',
                                    effective_date='2026-12-01', reason='虚构下月调整')
        update.pop('class_id')
        edited = self.store.admin_update_schedule_template(ADMIN_TOKEN, first['schedule_id'], update)
        self.assertEqual((1, False, False), (edited['linked_lesson_count'], edited['existing_lessons_changed'],
                                             edited['balance_changed']))
        self.assertEqual((1100, 1200, 'teacher-a'), tuple(self.read(
            'SELECT starts_at,ends_at,user_id FROM lessons JOIN lesson_teachers ON lessons.org_id=lesson_teachers.org_id AND lessons.id=lesson_teachers.lesson_id WHERE lessons.id=%s',
            (lesson['lesson_id'],)).values()))
        self.assert_problem(409, 'schedule_in_use', self.store.admin_delete_schedule_template,
                            ADMIN_TOKEN, first['schedule_id'], dict(reason='虚构删除已引用模板', command_ref='delete-linked'))
        self.assert_problem(409, 'schedule_teacher_conflict', self.store.admin_set_class_teacher,
                            ADMIN_TOKEN, 'class-a', dict(teacher_ids=['teacher-b'], expected_version=1,
                            reason='虚构绕过模板交接', command_ref='schedule-handover-conflict'))
        deleted = self.store.admin_delete_schedule_template(ADMIN_TOKEN, cross_day['schedule_id'],
            dict(reason='虚构删除未使用模板', command_ref='delete-unused-schedule'))
        self.assertTrue(deleted['deleted'])
        repeated = self.store.admin_delete_schedule_template(ADMIN_TOKEN, cross_day['schedule_id'],
            dict(reason='虚构删除未使用模板', command_ref='delete-unused-schedule'))
        self.assertTrue(repeated['duplicate'])
        self.assert_problem(422, 'invalid_schedule_reason', self.store.admin_delete_schedule_template,
                            ADMIN_TOKEN, second['schedule_id'], dict(reason=' ', command_ref='delete-blank-reason'))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM class_schedule_templates')['n'])

    def test_http_admin_schedule_contract_validation_permissions_and_duplicate_status(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        self.assertEqual(200, client.get('/v1/admin/schedule-templates', headers=admin).status_code)
        self.assertEqual(403, client.get('/v1/admin/schedule-templates', headers=teacher).status_code)
        self.assertEqual(401, client.get('/v1/admin/schedule-templates').status_code)
        created = client.post('/v1/admin/schedule-templates', headers=admin, json=self.schedule_data())
        self.assertEqual(201, created.status_code, created.text)
        repeated = client.post('/v1/admin/schedule-templates', headers=admin, json=self.schedule_data())
        self.assertEqual(200, repeated.status_code, repeated.text)
        self.assertEqual(created.json()['schedule_id'], repeated.json()['schedule_id'])
        schedule_id = created.json()['schedule_id']
        update = self.schedule_data('schedule-http-update', start_time='16:00', end_time='17:00', duration_minutes=60)
        update.pop('class_id')
        edited = client.put('/v1/admin/schedule-templates/' + schedule_id, headers=admin, json=update)
        self.assertEqual(200, edited.status_code, edited.text)
        self.assertEqual('16:00', edited.json()['start_time'])
        for payload in (self.schedule_data('schedule-http-extra', unexpected=True),
                        self.schedule_data('schedule-http-time', start_time='bad'),
                        self.schedule_data('schedule-http-duration', duration_minutes=721),
                        self.schedule_data('schedule-http-bool', weekday=True)):
            self.assertEqual(422, client.post('/v1/admin/schedule-templates', headers=admin, json=payload).status_code)
        self.assertEqual(422, client.put('/v1/admin/schedule-templates/' + schedule_id, headers=admin,
                                        json=dict(update, class_id='class-b')).status_code)
        self.assertEqual(403, client.put('/v1/admin/schedule-templates/' + schedule_id, headers=teacher,
                                        json=dict(update, command_ref='schedule-http-forbidden')).status_code)
        self.assertEqual(404, client.put('/v1/admin/schedule-templates/missing', headers=admin,
                                        json=dict(update, command_ref='schedule-http-missing')).status_code)
        self.assertEqual(409, client.post('/v1/admin/schedule-templates', headers=admin,
                                         json=self.schedule_data('schedule-http-conflict', start_time='16:00',
                                                                 end_time='17:00', duration_minutes=60)).status_code)
        deleted = client.post('/v1/admin/schedule-templates/' + schedule_id + '/delete', headers=admin,
                              json=dict(reason='虚构网页删除模板', command_ref='schedule-http-delete'))
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertTrue(deleted.json()['deleted'])
        self.assertEqual(200, client.post('/v1/admin/schedule-templates/' + schedule_id + '/delete', headers=admin,
                                         json=dict(reason='虚构网页删除模板', command_ref='schedule-http-delete')).status_code)
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM api_commands')['n'])

    @staticmethod
    def class_data(command='class-command', **changes):
        data = dict(name='虚构新班', course_id='course-a', reason='虚构建班原因', command_ref=command)
        data.update(changes)
        return data

    @staticmethod
    def student_update_data(command='student-update-command', **changes):
        data = dict(class_ids=['class-b'], name='虚构学生甲', external_ref='fiction-a', active=True,
                    expected_version=0, reason='虚构转班核对', command_ref=command)
        data.update(changes)
        return data

    def test_admin_class_create_edit_is_scoped_versioned_and_idempotent(self):
        data = self.class_data()
        self.assert_problem(403, 'admin_required', self.store.admin_create_class, USER_TOKEN, data)
        created = self.store.admin_create_class(ADMIN_TOKEN, data)
        self.assertEqual((False, 0, 0, []), (created['duplicate'], created['version'],
                                             created['student_count'], created['teacher_ids']))
        duplicate = self.store.admin_create_class(ADMIN_TOKEN, data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(created['class_id'], duplicate['class_id'])
        update = dict(self.class_data('class-edit-command', name='虚构新班更名'),
                      expected_version=0)
        edited = self.store.admin_update_class(ADMIN_TOKEN, created['class_id'], update)
        self.assertEqual(('虚构新班更名', 1, False, False), tuple(edited[key] for key in (
            'name', 'version', 'balance_changed', 'history_changed')))
        self.assertTrue(self.store.admin_update_class(ADMIN_TOKEN, created['class_id'], update)['duplicate'])
        stale = dict(update, command_ref='class-edit-stale')
        self.assert_problem(409, 'class_changed', self.store.admin_update_class,
                            ADMIN_TOKEN, created['class_id'], stale)
        self.assert_problem(409, 'class_name_conflict', self.store.admin_update_class,
                            ADMIN_TOKEN, created['class_id'], dict(update, command_ref='class-edit-duplicate-name',
                                                                  name='虚构甲班', expected_version=1))
        listed = next(row for row in self.store.admin_classes(ADMIN_TOKEN)['items']
                      if row['id'] == created['class_id'])
        self.assertEqual(('虚构新班更名', '虚构课程', 1, 0, 0), tuple(listed[key] for key in (
            'name', 'course_name', 'version', 'student_count', 'active_student_count')))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_class_created'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_class_updated'")['n'])

    def test_student_transfer_preserves_history_and_rebuilds_only_pristine_future_rosters(self):
        self.store.tablet_checkin(DEVICE_TOKEN, checkin())
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                    ('transfer-old','org-fiction','class-a',1100,1200,'scheduled',1),
                    ('transfer-processed','org-fiction','class-a',1200,1300,'scheduled',1),
                    ('transfer-new','org-fiction','class-b',1100,1200,'scheduled',1),
                    ('transfer-empty','org-fiction','class-b',1200,1300,'scheduled',1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-a','transfer-old','student-a',TRUE,1),
                    ('org-fiction','class-a','transfer-processed','student-a',TRUE,1),
                    ('org-fiction','class-b','transfer-new','student-b',TRUE,3);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','transfer-old','student-a','missing',0,NULL,1),
                    ('org-fiction','transfer-processed','student-a','leave',1,NULL,1),
                    ('org-fiction','transfer-new','student-b','missing',0,NULL,1);
                INSERT INTO lesson_charge_rules VALUES('org-fiction','transfer-empty',2,1,1);
                INSERT INTO enrollment_records VALUES
                    ('enroll-history','org-fiction','student-a','class-a',100,'enroll-history-ref','admin-a',1);
            """)
        data = self.student_update_data()
        result = self.store.admin_update_student(ADMIN_TOKEN, 'student-a', data)
        self.assertEqual((False, ['class-a'], ['class-b'], 1, 1, 2, 1, False, False),
                         tuple(result[key] for key in ('duplicate', 'previous_class_ids', 'class_ids',
                               'version', 'removed_future_count', 'added_future_count',
                               'retained_processed_count', 'balance_changed', 'history_changed')))
        self.assertEqual(['transfer-old'], result['removed_future_lesson_ids'])
        self.assertEqual(['transfer-processed'], result['retained_processed_lesson_ids'])
        self.assertEqual({'transfer-new', 'transfer-empty'},
                         {row['lesson_id'] for row in result['added_future_lessons']})
        self.assertEqual(3, next(row['charge_units'] for row in result['added_future_lessons']
                                 if row['lesson_id'] == 'transfer-new'))
        self.assertEqual(2, next(row['charge_units'] for row in result['added_future_lessons']
                                 if row['lesson_id'] == 'transfer-empty'))
        self.assertEqual('class-b', self.read(
            "SELECT class_id FROM student_class_enrollments WHERE student_id='student-a' AND left_at IS NULL")['class_id'])
        self.assertIsNotNone(self.read(
            "SELECT left_at FROM student_class_enrollments WHERE student_id='student-a' AND class_id='class-a'")['left_at'])
        self.assertEqual('class-a', self.read(
            "SELECT class_id FROM lesson_students WHERE lesson_id='lesson-a' AND student_id='student-a'")['class_id'])
        self.assertEqual('class-a', self.read(
            "SELECT class_id FROM lesson_students WHERE lesson_id='transfer-processed' AND student_id='student-a'")['class_id'])
        self.assertEqual('class-a', self.read(
            "SELECT class_id FROM enrollment_records WHERE id='enroll-history'")['class_id'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM lesson_students WHERE lesson_id='transfer-old'")['n'])
        self.assertEqual(('arrived', 1), tuple(self.read(
            "SELECT status,revision FROM attendance_records WHERE lesson_id='lesson-a' AND student_id='student-a'").values()))
        self.assertEqual((9, 2), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        duplicate = self.store.admin_update_student(ADMIN_TOKEN, 'student-a', data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(result['added_future_count'], duplicate['added_future_count'])
        self.assert_problem(409, 'student_changed', self.store.admin_update_student,
                            ADMIN_TOKEN, 'student-a', self.student_update_data('student-transfer-stale'))
        self.assertEqual(3, self.read("SELECT count(*) AS n FROM lesson_students WHERE student_id='student-a' AND lesson_id LIKE 'transfer-%%'")['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_student_updated'")['n'])

    def test_student_deactivate_and_reactivate_update_future_roster_without_balance_changes(self):
        lesson = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('deactivate-lesson'))
        disabled = self.store.admin_update_student(ADMIN_TOKEN, 'student-a', self.student_update_data(
            'student-disable', class_ids=['class-a'], active=False, reason='虚构停用学籍'))
        self.assertEqual((False, 1, 0, 1), (disabled['active'], disabled['removed_future_count'],
                                            disabled['added_future_count'], disabled['version']))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM lesson_students WHERE lesson_id=%s',
                                      (lesson['lesson_id'],))['n'])
        self.assertEqual('missing', self.read(
            "SELECT status FROM attendance_records WHERE lesson_id='lesson-a' AND student_id='student-a'")['status'])
        restored = self.store.admin_update_student(ADMIN_TOKEN, 'student-a', self.student_update_data(
            'student-reactivate', class_ids=['class-a'], active=True, expected_version=1, reason='虚构恢复学籍'))
        self.assertEqual((True, 1, 2), (restored['active'], restored['added_future_count'], restored['version']))
        roster = self.read('SELECT charge_units,expected FROM lesson_students WHERE lesson_id=%s AND student_id=%s',
                           (lesson['lesson_id'], 'student-a'))
        self.assertEqual((2, True), tuple(roster.values()))
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_class_teacher_handover_only_updates_pristine_future_lessons(self):
        pristine = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('handover-pristine'))
        processed = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'handover-processed', starts_at=1200, ends_at=1300))
        self.store.teacher_correction(USER_TOKEN, processed['lesson_id'], 'leave', correction('handover-leave'))
        data = dict(teacher_ids=['teacher-b'], expected_version=0,
                    reason='虚构任课交接', command_ref='handover-command')
        result = self.store.admin_set_class_teacher(ADMIN_TOKEN, 'class-a', data)
        self.assertEqual((False, ['teacher-a'], 'teacher-b', 1, 1, 1),
                         tuple(result[key] for key in ('duplicate', 'previous_teacher_ids', 'teacher_id',
                               'future_lessons_updated', 'processed_future_lessons_retained', 'version')))
        self.assertEqual([pristine['lesson_id']], result['updated_future_lesson_ids'])
        self.assertEqual([processed['lesson_id']], result['retained_processed_lesson_ids'])
        self.assertEqual('teacher-a', self.read(
            "SELECT user_id FROM lesson_teachers WHERE lesson_id='lesson-a'")['user_id'])
        self.assertEqual('teacher-b', self.read('SELECT user_id FROM lesson_teachers WHERE lesson_id=%s',
                                                (pristine['lesson_id'],))['user_id'])
        self.assertEqual('teacher-a', self.read('SELECT user_id FROM lesson_teachers WHERE lesson_id=%s',
                                                (processed['lesson_id'],))['user_id'])
        self.assertTrue(self.store.admin_set_class_teacher(ADMIN_TOKEN, 'class-a', data)['duplicate'])
        self.assert_problem(409, 'class_changed', self.store.admin_set_class_teacher,
                            ADMIN_TOKEN, 'class-a', dict(data, command_ref='handover-stale'))
        self.assertEqual(['teacher-b'], self.read(
            "SELECT array_agg(user_id) AS ids FROM teacher_classes WHERE class_id='class-a'")['ids'])
        self.assertEqual('leave', self.read('SELECT status FROM attendance_records WHERE lesson_id=%s',
                                            (processed['lesson_id'],))['status'])
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM balance_ledger WHERE kind<>\'opening\'')['n'])

    def test_http_admin_directory_routes_enforce_scope_validation_and_versions(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        created = client.post('/v1/admin/classes', headers=admin, json=self.class_data('http-class-create'))
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(200, client.post('/v1/admin/classes', headers=admin,
                                         json=self.class_data('http-class-create')).status_code)
        class_id = created.json()['class_id']
        edited = client.put('/v1/admin/classes/' + class_id, headers=admin,
                            json=dict(self.class_data('http-class-edit', name='虚构网页班级'), expected_version=0))
        self.assertEqual(200, edited.status_code, edited.text)
        self.assertEqual(1, edited.json()['version'])
        assigned = client.put('/v1/admin/classes/' + class_id + '/teacher', headers=admin,
                              json=dict(teacher_ids=['teacher-a'], expected_version=1,
                                        reason='虚构网页授权', command_ref='http-class-teacher'))
        self.assertEqual(200, assigned.status_code, assigned.text)
        self.assertEqual('teacher-a', assigned.json()['teacher_id'])
        transferred = client.put('/v1/admin/students/student-a', headers=admin,
                                 json=self.student_update_data('http-student-update', class_ids=[class_id]))
        self.assertEqual(200, transferred.status_code, transferred.text)
        self.assertEqual((class_id, 1), (transferred.json()['class_id'], transferred.json()['version']))
        self.assertEqual(403, client.put('/v1/admin/students/student-a', headers=teacher,
                                         json=self.student_update_data('http-student-denied')).status_code)
        self.assertEqual(403, client.post('/v1/admin/classes', headers=device,
                                         json=self.class_data('http-class-denied')).status_code)
        self.assertEqual(401, client.post('/v1/admin/classes', json=self.class_data('http-class-anonymous')).status_code)
        self.assertEqual(422, client.post('/v1/admin/classes', headers=admin,
                                         json=dict(self.class_data('http-class-extra'), unexpected=True)).status_code)
        self.assertEqual(409, client.put('/v1/admin/classes/' + class_id, headers=admin,
                                         json=dict(self.class_data('http-class-stale'), expected_version=0)).status_code)
        self.assertEqual('class-a', self.read(
            "SELECT class_id FROM lesson_students WHERE lesson_id='lesson-a' AND student_id='student-a'")['class_id'])
        self.assertEqual((10, 1), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(200, client.get('/admin-directory.js').status_code)

    def test_admin_face_profile_is_encrypted_scoped_replaceable_and_idempotent(self):
        self.store.face_key = FACE_KEY
        data = face_profile()
        self.assert_problem(403, 'admin_required', self.store.admin_face_profile,
                            USER_TOKEN, 'student-a', data)
        first = self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', data)
        self.assertEqual((3, 0, False), (first['sample_count'], first['replaced_profiles'], first['duplicate']))
        duplicate = self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['profile_id'], duplicate['profile_id'])
        stored = self.read("SELECT descriptor_ciphertext FROM face_profiles WHERE student_id='student-a'")
        self.assertNotIn(b'descriptors', bytes(stored['descriptor_ciphertext']))
        replacement = self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile('face-profile-2'))
        self.assertEqual(1, replacement['replaced_profiles'])
        self.assertEqual(2, self.read("SELECT count(*) AS n FROM face_profiles WHERE student_id='student-a'")['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM face_profiles WHERE student_id='student-a' AND active=TRUE")['n'])
        student = next(row for row in self.store.admin_students(ADMIN_TOKEN)['items'] if row['id'] == 'student-a')
        self.assertTrue(student['face_registered'])
        self.assertEqual('sface-int8bq-v1', student['face_model_version'])

    def test_tablet_face_profiles_decrypt_only_authorized_active_profiles(self):
        self.store.face_key = FACE_KEY
        first = self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-b', face_profile('face-profile-outside'))
        audits = self.read('SELECT count(*) AS n FROM audit_log')['n']
        result = self.store.tablet_face_profiles(DEVICE_TOKEN)
        self.assertEqual(('org-fiction', 'device-a', 'generation-a', 2),
                         (result['org_id'], result['device_id'], result['generation'], result['protocol']))
        self.assertEqual(['student-a'], [row['student_id'] for row in result['items']])
        item = result['items'][0]
        self.assertEqual({'profile_id', 'student_id', 'model_version', 'descriptors'}, set(item))
        self.assertEqual(first['profile_id'], item['profile_id'])
        self.assertEqual(face_frames(), item['descriptors'])
        self.assertNotIn('descriptor_ciphertext', item)
        self.assertNotIn('consent_ref', item)
        self.assertEqual(audits, self.read('SELECT count(*) AS n FROM audit_log')['n'])
        replacement = self.store.admin_face_profile(
            ADMIN_TOKEN, 'student-a', face_profile('face-profile-2', face_frames(1)))
        refreshed = self.store.tablet_face_profiles(DEVICE_TOKEN)
        self.assertEqual(1, len(refreshed['items']))
        self.assertEqual(replacement['profile_id'], refreshed['items'][0]['profile_id'])
        self.assertEqual(face_frames(1), refreshed['items'][0]['descriptors'])
        self.assert_problem(403, 'device_required', self.store.tablet_face_profiles, USER_TOKEN)

    def test_http_tablet_face_profiles_requires_device_credentials(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True, face_key=FACE_KEY))
        path = '/v1/tablet/face-profiles'
        self.assertEqual(401, client.get(path).status_code)
        self.assertEqual(403, client.get(path, headers={'Authorization': 'Bearer ' + USER_TOKEN}).status_code)
        empty = client.get(path, headers={'Authorization': 'Bearer ' + DEVICE_TOKEN})
        self.assertEqual(200, empty.status_code, empty.text)
        self.assertEqual([], empty.json()['items'])
        created = client.put('/v1/admin/students/student-a/face-profile',
                             headers={'Authorization': 'Bearer ' + ADMIN_TOKEN}, json=face_profile('tablet-http-face'))
        self.assertEqual(201, created.status_code, created.text)
        downloaded = client.get(path, headers={'Authorization': 'Bearer ' + DEVICE_TOKEN})
        self.assertEqual(200, downloaded.status_code, downloaded.text)
        self.assertEqual(created.json()['profile_id'], downloaded.json()['items'][0]['profile_id'])
        self.assertEqual(face_frames(), downloaded.json()['items'][0]['descriptors'])

    def test_tablet_face_profiles_empty_without_key_and_existing_profile_requires_key(self):
        self.assertEqual([], self.store.tablet_face_profiles(DEVICE_TOKEN)['items'])
        self.store.face_key = FACE_KEY
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        self.store.face_key = None
        self.assert_problem(503, 'face_not_configured', self.store.tablet_face_profiles, DEVICE_TOKEN)

    def test_tablet_face_profiles_corrupt_ciphertext_never_returns_partial_items(self):
        self.store.face_key = FACE_KEY
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE face_profiles SET descriptor_ciphertext=%s WHERE student_id='student-a'",
                               (b'corrupted',))
        self.assert_problem(409, 'face_profile_invalid', self.store.tablet_face_profiles, DEVICE_TOKEN)

    def test_teacher_face_checkin_uses_shared_attendance_and_debits_once(self):
        self.store.face_key = FACE_KEY
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        result = self.store.teacher_face_checkin(USER_TOKEN, 'lesson-a', face_checkin())
        self.assertEqual(('student-a', '虚构学生甲', 'arrived', 1, 2, 9),
                         (result['student_id'], result['student_name'], result['status'],
                          result['attendance_revision'], result['balance_revision'], result['balance_units']))
        duplicate = self.store.teacher_face_checkin(USER_TOKEN, 'lesson-a', face_checkin())
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(result['event_id'], duplicate['event_id'])
        self.assertEqual('web_face_checkin', self.read("SELECT kind FROM attendance_events WHERE lesson_id='lesson-a'")['kind'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assert_problem(403, 'lesson_scope', self.store.teacher_face_checkin,
                            OTHER_USER_TOKEN, 'lesson-a', face_checkin('web-face-outside'))
        self.assert_problem(403, 'user_required', self.store.teacher_face_checkin,
                            DEVICE_TOKEN, 'lesson-a', face_checkin('web-face-device'))

    def test_face_unknown_future_and_cancelled_lessons_do_not_change_attendance(self):
        self.store.face_key = FACE_KEY
        self.assert_problem(409, 'face_profiles_missing', self.store.teacher_face_checkin,
                            USER_TOKEN, 'lesson-a', face_checkin('web-face-no-profiles'))
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        self.assert_problem(409, 'face_not_recognized', self.store.teacher_face_checkin,
                            USER_TOKEN, 'lesson-a', face_checkin('web-face-unknown', face_frames(1)))
        self.store.clock = lambda: 50
        self.assert_problem(409, 'future_checkin', self.store.teacher_face_checkin,
                            USER_TOKEN, 'lesson-a', face_checkin('web-face-future'))
        self.store.clock = lambda: 1000
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE lessons SET state='cancelled' WHERE id='lesson-a'")
        self.assert_problem(409, 'lesson_unavailable', self.store.teacher_face_checkin,
                            USER_TOKEN, 'lesson-a', face_checkin('web-face-cancelled'))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_face_ambiguity_is_rejected_without_debit(self):
        self.store.face_key = FACE_KEY
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO students(id,org_id,name,external_ref,active) VALUES('student-c','org-fiction','虚构学生丙','fiction-c',TRUE);
                INSERT INTO student_class_enrollments(id,org_id,student_id,class_id,joined_at,created_at) VALUES('enrollment-c','org-fiction','student-c','class-a',0,0);
                INSERT INTO opening_balances VALUES('org-fiction','student-c',10,'manual_review','fiction-review-c');
                INSERT INTO student_balances VALUES('org-fiction','student-c',10,1,1);
                INSERT INTO lesson_students VALUES('org-fiction','class-a','lesson-a','student-c',TRUE,1);
                INSERT INTO attendance_records VALUES('org-fiction','lesson-a','student-c','missing',0,NULL,1);
            """)
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-a', face_profile())
        self.store.admin_face_profile(ADMIN_TOKEN, 'student-c', face_profile('face-profile-c'))
        self.assert_problem(409, 'face_ambiguous', self.store.teacher_face_checkin,
                            USER_TOKEN, 'lesson-a', face_checkin('web-face-ambiguous'))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_face_profile_and_checkin_statuses_and_validation(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True, face_key=FACE_KEY))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        profile_path = '/v1/admin/students/student-a/face-profile'
        created = client.put(profile_path, headers=admin, json=face_profile('http-face-profile'))
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(200, client.put(profile_path, headers=admin, json=face_profile('http-face-profile')).status_code)
        self.assertEqual(403, client.put(profile_path, headers=teacher, json=face_profile('http-face-denied')).status_code)
        checkin_path = '/v1/teacher/lessons/lesson-a/face-checkin'
        accepted = client.post(checkin_path, headers=teacher, json=face_checkin('http-web-face'))
        self.assertEqual(201, accepted.status_code, accepted.text)
        self.assertEqual(9, accepted.json()['balance_units'])
        self.assertEqual(200, client.post(checkin_path, headers=teacher, json=face_checkin('http-web-face')).status_code)
        invalid = face_checkin('http-web-invalid')
        invalid['descriptors'] = [[0.0] * 127] * 3
        self.assertEqual(422, client.post(checkin_path, headers=teacher, json=invalid).status_code)
        self.assertEqual(422, client.post(checkin_path, headers=teacher,
                                          json=dict(face_checkin('http-web-extra'), unexpected=True)).status_code)

    @staticmethod
    def reschedule_data(command='lesson-reschedule', **changes):
        data = dict(starts_at=1300, ends_at=1450, expected_version=0,
                    reason='虚构调课原因', command_ref=command)
        data.update(changes)
        return data

    @staticmethod
    def cancel_data(command='lesson-cancel', **changes):
        data = dict(expected_version=0, reason='虚构停课原因', command_ref=command)
        data.update(changes)
        return data

    def test_lesson_reschedule_is_versioned_idempotent_and_preserves_roster_and_balances(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('reschedule-source'))
        lesson_id = created['lesson_id']
        self.assertEqual((0, 0, 1000), (created['version'], created['processed_count'],
                                        self.store.admin_lessons(ADMIN_TOKEN)['server_time']))
        data = self.reschedule_data()
        changed = self.store.admin_reschedule_lesson(ADMIN_TOKEN, lesson_id, data)
        self.assertEqual((False, 1100, 1200, 1300, 1450, 1, False, False),
                         tuple(changed[key] for key in ('duplicate', 'previous_starts_at',
                               'previous_ends_at', 'starts_at', 'ends_at', 'version',
                               'roster_changed', 'balance_changed')))
        self.assertTrue(self.store.admin_reschedule_lesson(ADMIN_TOKEN, lesson_id, data)['duplicate'])
        self.assertEqual((1300, 1450, 1), tuple(self.read(
            'SELECT starts_at,ends_at,version FROM lessons JOIN lesson_versions ON lessons.org_id=lesson_versions.org_id AND lessons.id=lesson_versions.lesson_id WHERE lessons.id=%s',
            (lesson_id,)).values()))
        self.assertEqual(('student-a', True, 2), tuple(self.read(
            'SELECT student_id,expected,charge_units FROM lesson_students WHERE lesson_id=%s',
            (lesson_id,)).values()))
        self.assertEqual(('missing', 0, None), tuple(self.read(
            'SELECT status,revision,last_event_id FROM attendance_records WHERE lesson_id=%s',
            (lesson_id,)).values()))
        listed = next(row for row in self.store.admin_lessons(ADMIN_TOKEN)['items'] if row['id'] == lesson_id)
        self.assertEqual((1, 0), (listed['version'], listed['processed_count']))
        self.assert_problem(409, 'lesson_changed', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data('reschedule-stale', starts_at=1500, ends_at=1600))
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data(ends_at=1500))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_rescheduled'")['n'])

    def test_lesson_cancel_retains_roster_excludes_reports_and_allows_same_time_rebooking(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('cancel-source'))
        lesson_id = created['lesson_id']
        data = self.cancel_data()
        cancelled = self.store.admin_cancel_lesson(ADMIN_TOKEN, lesson_id, data)
        self.assertEqual((False, 'cancelled', 1, 1, True, True, False, False),
                         tuple(cancelled[key] for key in ('duplicate', 'state', 'version',
                               'roster_count', 'roster_retained', 'report_excluded',
                               'hours_reversed', 'balance_changed')))
        self.assertTrue(self.store.admin_cancel_lesson(ADMIN_TOKEN, lesson_id, data)['duplicate'])
        self.assert_problem(409, 'lesson_not_editable', self.store.admin_cancel_lesson,
                            ADMIN_TOKEN, lesson_id, self.cancel_data('cancel-again', expected_version=1))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM lesson_students WHERE lesson_id=%s',
                                      (lesson_id,))['n'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_records WHERE lesson_id=%s',
                                      (lesson_id,))['n'])
        replacement = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('cancel-rebook'))
        self.assertNotEqual(lesson_id, replacement['lesson_id'])
        self.store.clock = lambda: 1300
        report = self.store.admin_weekly_report(ADMIN_TOKEN, '1969-12-29')
        self.assertEqual(3, report['overall']['expected_count'])
        bootstrap = self.store.tablet_bootstrap(DEVICE_TOKEN)
        cancelled_copy = next(row for row in bootstrap['lessons'] if row['id'] == lesson_id)
        self.assertEqual('cancelled', cancelled_copy['state'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_lesson_cancelled'")['n'])

    def test_admin_cancelled_makeup_debits_once_and_tablet_resolution_is_non_mutating(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('cancel-makeup-source', charge_units=1))
        lesson_id = created['lesson_id']
        self.store.admin_cancel_lesson(ADMIN_TOKEN, lesson_id, self.cancel_data('cancel-makeup'))
        detail = self.store.admin_lesson(ADMIN_TOKEN, lesson_id)
        self.assertEqual(('cancelled', '虚构甲班'), (detail['lesson']['state'], detail['lesson']['class_name']))
        self.assertEqual(('student-a', 'fiction-a', 'missing', 0, 1),
                         tuple(detail['students'][0][key] for key in
                               ('student_id', 'external_ref', 'status', 'attendance_revision', 'charge_units')))
        data = dict(correction('admin-cancelled-makeup'), reason='虚构取消前已到核对')
        result = self.store.admin_cancelled_correction(ADMIN_TOKEN, lesson_id, 'makeup', data)
        self.assertEqual(('arrived', 1, 2, 9, True, True),
                         tuple(result[key] for key in ('status', 'attendance_revision', 'balance_revision',
                                                       'balance_units', 'cancelled_lesson', 'report_excluded')))
        duplicate = self.store.admin_cancelled_correction(ADMIN_TOKEN, lesson_id, 'makeup', data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(result['event_id'], duplicate['event_id'])
        wire = dict(checkin('tablet-cancel-makeup'), lesson_id=lesson_id)
        resolved = self.store.tablet_resolve_checkin(DEVICE_TOKEN, wire)
        self.assertEqual((result['event_id'], 'arrived', 9, False),
                         tuple(resolved[key] for key in ('event_id', 'status', 'balance_units', 'apply_local_debit')))
        self.assertTrue(self.store.tablet_resolve_checkin(DEVICE_TOKEN, wire)['duplicate'])
        self.assertEqual((9, 2), tuple(self.read(
            "SELECT units,revision FROM student_balances WHERE student_id='student-a'").values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(1, self.read(
            "SELECT count(*) AS n FROM audit_log WHERE action='admin_cancelled_makeup'")['n'])
        self.store.clock = lambda: 1300
        report = self.store.admin_weekly_report(ADMIN_TOKEN, '1969-12-29')
        self.assertEqual((2, 0), (report['overall']['expected_count'], report['overall']['arrived_count']))

    def test_admin_cancelled_leave_never_debits_and_still_resolves_tablet(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('cancel-leave-source'))
        lesson_id = created['lesson_id']
        self.store.admin_cancel_lesson(ADMIN_TOKEN, lesson_id, self.cancel_data('cancel-leave'))
        data = correction('admin-cancelled-leave')
        result = self.store.admin_cancelled_correction(ADMIN_TOKEN, lesson_id, 'leave', data)
        self.assertEqual(('leave', 1, 1, 10), tuple(result[key] for key in (
            'status', 'attendance_revision', 'balance_revision', 'balance_units')))
        self.assertTrue(self.store.admin_cancelled_correction(ADMIN_TOKEN, lesson_id, 'leave', data)['duplicate'])
        wire = dict(checkin('tablet-cancel-leave'), lesson_id=lesson_id)
        resolved = self.store.tablet_resolve_checkin(DEVICE_TOKEN, wire)
        self.assertEqual(('leave', result['event_id'], 10),
                         tuple(resolved[key] for key in ('status', 'event_id', 'balance_units')))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(10, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

    def test_admin_cancelled_corrections_enforce_scope_state_revision_and_roster(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('cancel-guard-source'))
        lesson_id = created['lesson_id']
        data = correction('admin-cancel-guard')
        self.assert_problem(403, 'admin_required', self.store.admin_lesson, USER_TOKEN, lesson_id)
        self.assert_problem(403, 'admin_required', self.store.admin_lesson, DEVICE_TOKEN, lesson_id)
        self.assert_problem(404, 'lesson_missing', self.store.admin_lesson, ADMIN_TOKEN, 'missing')
        self.assert_problem(409, 'lesson_not_cancelled', self.store.admin_cancelled_correction,
                            ADMIN_TOKEN, lesson_id, 'makeup', data)
        self.store.admin_cancel_lesson(ADMIN_TOKEN, lesson_id, self.cancel_data('cancel-guard'))
        for token in (USER_TOKEN, DEVICE_TOKEN):
            self.assert_problem(403, 'admin_required', self.store.admin_cancelled_correction,
                                token, lesson_id, 'leave', data)
        self.assert_problem(409, 'attendance_changed', self.store.admin_cancelled_correction,
                            ADMIN_TOKEN, lesson_id, 'makeup', dict(data, expected_revision=2))
        self.assert_problem(404, 'lesson_student_missing', self.store.admin_cancelled_correction,
                            ADMIN_TOKEN, lesson_id, 'leave', dict(data, student_id='student-b'))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_admin_cancelled_lesson_detail_corrections_and_tablet_receipt(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        device = {'Authorization': 'Bearer ' + DEVICE_TOKEN}
        created = client.post('/v1/admin/lessons', headers=admin,
                              json=self.lesson_data('http-cancel-attendance-source', charge_units=1)).json()
        lesson_id = created['lesson_id']
        path = '/v1/admin/lessons/' + lesson_id
        self.assertEqual(200, client.post(path + '/cancel', headers=admin,
                                         json=self.cancel_data('http-cancel-attendance')).status_code)
        self.assertEqual(401, client.get(path).status_code)
        self.assertEqual(403, client.get(path, headers=teacher).status_code)
        detail = client.get(path, headers=admin)
        self.assertEqual(200, detail.status_code, detail.text)
        self.assertEqual('student-a', detail.json()['students'][0]['student_id'])
        data = correction('http-admin-cancel-makeup')
        created_result = client.post(path + '/makeup', headers=admin, json=data)
        self.assertEqual(201, created_result.status_code, created_result.text)
        duplicate = client.post(path + '/makeup', headers=admin, json=data)
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(403, client.post(path + '/leave', headers=teacher,
                                         json=correction('http-admin-cancel-denied')).status_code)
        self.assertEqual(422, client.post(path + '/makeup', headers=admin,
                                         json=dict(correction('http-admin-cancel-extra'), unexpected=True)).status_code)
        wire = dict(checkin('http-tablet-cancel-resolution'), lesson_id=lesson_id)
        resolved = client.post('/v1/tablet/checkins/resolve', headers=device, json=wire)
        self.assertEqual(200, resolved.status_code, resolved.text)
        self.assertEqual(created_result.json()['event_id'], resolved.json()['event_id'])
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(3, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_lesson_changes_reject_processed_started_conflicting_and_unauthorized_requests(self):
        created = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('change-guards'))
        lesson_id = created['lesson_id']
        for method, data in ((self.store.admin_reschedule_lesson, self.reschedule_data('teacher-reschedule')),
                             (self.store.admin_cancel_lesson, self.cancel_data('teacher-cancel'))):
            self.assert_problem(403, 'admin_required', method, USER_TOKEN, lesson_id, data)
            self.assert_problem(403, 'admin_required', method, DEVICE_TOKEN, lesson_id, data)
        self.assert_problem(404, 'lesson_missing', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, 'missing', self.reschedule_data('missing-reschedule'))
        self.assert_problem(409, 'lesson_clock', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data('past-target', starts_at=1000, ends_at=1050))
        self.assert_problem(422, 'invalid_lesson_time', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data('bad-duration', ends_at=1300 + 12 * 3600 + 1))
        self.assert_problem(409, 'lesson_time_unchanged', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data('same-time', starts_at=1100, ends_at=1200))
        occupied = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'occupied-target', starts_at=1300, ends_at=1400))
        self.assert_problem(409, 'lesson_time_conflict', self.store.admin_reschedule_lesson,
                            ADMIN_TOKEN, lesson_id, self.reschedule_data('occupied'))
        self.store.teacher_correction(USER_TOKEN, occupied['lesson_id'], 'leave', correction('occupied-leave'))
        for method, data in ((self.store.admin_reschedule_lesson, self.reschedule_data('processed-reschedule', starts_at=1500, ends_at=1600)),
                             (self.store.admin_cancel_lesson, self.cancel_data('processed-cancel'))):
            self.assert_problem(409, 'lesson_attendance_conflict', method,
                                ADMIN_TOKEN, occupied['lesson_id'], data)
        self.store.clock = lambda: 1200
        self.assert_problem(409, 'lesson_not_editable', self.store.admin_cancel_lesson,
                            ADMIN_TOKEN, lesson_id, self.cancel_data('started-cancel'))
        self.assertEqual(('scheduled', 1100, 1200), tuple(self.read(
            'SELECT state,starts_at,ends_at FROM lessons WHERE id=%s', (lesson_id,)).values()))
        self.assertEqual(1, self.read('SELECT count(*) AS n FROM attendance_events')['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_lesson_changes_enforce_strict_payload_scope_and_receipts(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        created = client.post('/v1/admin/lessons', headers=admin,
                              json=self.lesson_data('http-change-source')).json()
        path = '/v1/admin/lessons/' + created['lesson_id']
        changed = client.put(path, headers=admin, json=self.reschedule_data('http-reschedule'))
        self.assertEqual(200, changed.status_code, changed.text)
        self.assertEqual(1, changed.json()['version'])
        self.assertTrue(client.put(path, headers=admin,
                                   json=self.reschedule_data('http-reschedule')).json()['duplicate'])
        cancelled = client.post(path + '/cancel', headers=admin,
                                json=self.cancel_data('http-cancel', expected_version=1))
        self.assertEqual(200, cancelled.status_code, cancelled.text)
        self.assertTrue(cancelled.json()['roster_retained'])
        self.assertEqual(403, client.post(path + '/cancel', headers=teacher,
                                          json=self.cancel_data('http-teacher-cancel', expected_version=2)).status_code)
        self.assertEqual(401, client.put(path, json=self.reschedule_data('http-anonymous')).status_code)
        self.assertEqual(422, client.post(path + '/cancel', headers=admin,
                                          json=self.cancel_data('http-extra-cancel', expected_version=2,
                                                                unexpected=True)).status_code)
        self.assertEqual(422, client.put(path, headers=admin,
                                         json=self.reschedule_data('http-negative', expected_version=-1)).status_code)
        listed = client.get('/v1/admin/lessons?date=1970-01-01', headers=admin)
        item = next(row for row in listed.json()['items'] if row['id'] == created['lesson_id'])
        self.assertEqual(('cancelled', 2, 0, 1000), (item['state'], item['version'],
                                                       item['processed_count'], listed.json()['server_time']))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    @staticmethod
    def account_data(command='account-create', **changes):
        data = dict(login='fiction-new-teacher', display_name='虚构新教师',
                    password='Fictional teacher password 2026!', reason='虚构新建教师账号',
                    command_ref=command)
        data.update(changes)
        return data

    @staticmethod
    def account_status_data(command='account-status', **changes):
        data = dict(active=False, expected_version=0, reason='虚构停用教师账号', command_ref=command)
        data.update(changes)
        return data

    @staticmethod
    def account_password_data(command='account-password', **changes):
        data = dict(password='Replacement teacher password 2026!', expected_version=1,
                    reason='虚构重置教师密码', command_ref=command)
        data.update(changes)
        return data

    def test_admin_account_create_is_scoped_idempotent_and_never_returns_password(self):
        data = self.account_data()
        self.assert_problem(403, 'admin_required', self.store.admin_create_account, USER_TOKEN, data)
        self.assert_problem(403, 'admin_required', self.store.admin_create_account, DEVICE_TOKEN, data)
        created = self.store.admin_create_account(ADMIN_TOKEN, data)
        self.assertEqual((False, True, ['teacher'], 0, False), tuple(created[key] for key in (
            'duplicate', 'active', 'roles', 'version', 'password_returned')))
        self.assertNotIn('password', created)
        duplicate = self.store.admin_create_account(ADMIN_TOKEN, data)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(created['user_id'], duplicate['user_id'])
        self.assert_problem(409, 'idempotency_conflict', self.store.admin_create_account,
                            ADMIN_TOKEN, self.account_data(display_name='不同内容'))
        self.assert_problem(409, 'login_exists', self.store.admin_create_account,
                            ADMIN_TOKEN, self.account_data('account-existing', login='fiction-teacher-a'))
        self.assert_problem(422, 'invalid_account', self.store.admin_create_account,
                            ADMIN_TOKEN, self.account_data('account-short', password='short'))
        listed = next(row for row in self.store.admin_accounts(ADMIN_TOKEN)['items']
                      if row['id'] == created['user_id'])
        self.assertEqual(('fiction-new-teacher', True, 0, True, []), tuple(listed[key] for key in (
            'login', 'active', 'version', 'password_configured', 'class_ids')))
        credential = self.read('SELECT password_hash FROM user_credentials WHERE user_id=%s',
                               (created['user_id'],))['password_hash']
        self.assertTrue(verify_password(data['password'], credential))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_teacher_account_created'")['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_account_disable_requires_handoff_and_revokes_access_until_restored(self):
        self.assert_problem(409, 'teaching_handoff_required', self.store.admin_set_account_status,
                            ADMIN_TOKEN, 'teacher-a', self.account_status_data())
        created = self.store.admin_create_account(ADMIN_TOKEN, self.account_data())
        user_id = created['user_id']
        logged_in = self.store.login('fiction', created['login'], self.account_data()['password'], 'test')['access_token']
        self.assertEqual(created['user_id'], self.store.me(logged_in)['id'])
        data = self.account_status_data()
        disabled = self.store.admin_set_account_status(ADMIN_TOKEN, user_id, data)
        self.assertEqual((False, False, 1, 1, False, False), tuple(disabled[key] for key in (
            'duplicate', 'active', 'version', 'sessions_revoked', 'assignments_changed', 'history_changed')))
        self.assertTrue(self.store.admin_set_account_status(ADMIN_TOKEN, user_id, data)['duplicate'])
        self.assert_problem(401, 'unauthorized', self.store.me, logged_in)
        self.assert_problem(401, 'invalid_login', self.store.login, 'fiction', created['login'],
                            self.account_data()['password'], 'test')
        self.assert_problem(409, 'account_state_unchanged', self.store.admin_set_account_status,
                            ADMIN_TOKEN, user_id, self.account_status_data('disable-again', expected_version=1))
        self.assert_problem(409, 'account_changed', self.store.admin_set_account_status,
                            ADMIN_TOKEN, user_id, self.account_status_data('stale-status', active=True))
        restored = self.store.admin_set_account_status(ADMIN_TOKEN, user_id, self.account_status_data(
            'restore-account', active=True, expected_version=1, reason='虚构恢复账号'))
        self.assertEqual((True, 2), (restored['active'], restored['version']))
        self.assertEqual(user_id, self.store.login('fiction', created['login'], self.account_data()['password'], 'test') and
                         self.store.login('fiction', created['login'], self.account_data()['password'], 'test')['access_token'] and
                         self.store.me(self.store.login('fiction', created['login'], self.account_data()['password'], 'test')['access_token'])['id'])
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM teacher_classes WHERE user_id=%s", (user_id,))['n'])
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_password_reset_revokes_sessions_and_accepts_only_new_password(self):
        created = self.store.admin_create_account(ADMIN_TOKEN, self.account_data())
        user_id = created['user_id']
        old_session = self.store.login('fiction', created['login'], self.account_data()['password'], 'test')['access_token']
        data = self.account_password_data(expected_version=0)
        reset = self.store.admin_reset_account_password(ADMIN_TOKEN, user_id, data)
        self.assertEqual((False, True, 1, 1, False), tuple(reset[key] for key in (
            'duplicate', 'password_changed', 'version', 'sessions_revoked', 'password_returned')))
        self.assertNotIn('password', reset)
        self.assertTrue(self.store.admin_reset_account_password(ADMIN_TOKEN, user_id, data)['duplicate'])
        self.assert_problem(401, 'unauthorized', self.store.me, old_session)
        self.assert_problem(401, 'invalid_login', self.store.login, 'fiction', created['login'],
                            self.account_data()['password'], 'test')
        new_session = self.store.login('fiction', created['login'], data['password'], 'test')['access_token']
        self.assertEqual(user_id, self.store.me(new_session)['id'])
        self.assert_problem(409, 'account_changed', self.store.admin_reset_account_password,
                            ADMIN_TOKEN, user_id, self.account_password_data('stale-password', expected_version=0))
        self.assert_problem(404, 'teacher_account_missing', self.store.admin_reset_account_password,
                            ADMIN_TOKEN, 'admin-a', self.account_password_data('admin-protected', expected_version=0))
        credential = self.read('SELECT password_hash FROM user_credentials WHERE user_id=%s', (user_id,))['password_hash']
        self.assertTrue(verify_password(data['password'], credential))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM audit_log WHERE action='admin_teacher_password_reset'")['n'])
        self.assertNotIn(data['password'], json.dumps(self.read(
            "SELECT detail FROM audit_log WHERE action='admin_teacher_password_reset'")['detail']))
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])

    def test_http_admin_accounts_enforce_strict_payload_scope_and_receipts(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        created = client.post('/v1/admin/accounts', headers=admin, json=self.account_data('http-account-create'))
        self.assertEqual(201, created.status_code, created.text)
        self.assertEqual(200, client.post('/v1/admin/accounts', headers=admin,
                                          json=self.account_data('http-account-create')).status_code)
        user_id = created.json()['user_id']
        path = '/v1/admin/accounts/' + user_id
        disabled = client.put(path + '/status', headers=admin, json=self.account_status_data('http-account-disable'))
        self.assertEqual(200, disabled.status_code, disabled.text)
        self.assertEqual(1, disabled.json()['version'])
        restored = client.put(path + '/status', headers=admin, json=self.account_status_data(
            'http-account-restore', active=True, expected_version=1))
        self.assertEqual(2, restored.json()['version'])
        reset = client.put(path + '/password', headers=admin,
                           json=self.account_password_data('http-account-reset', expected_version=2))
        self.assertEqual(200, reset.status_code, reset.text)
        self.assertTrue(reset.json()['password_changed'])
        self.assertEqual(403, client.get('/v1/admin/accounts', headers=teacher).status_code)
        self.assertEqual(401, client.post('/v1/admin/accounts', json=self.account_data('http-anonymous')).status_code)
        self.assertEqual(422, client.post('/v1/admin/accounts', headers=admin,
                                          json=self.account_data('http-short', password='short')).status_code)
        self.assertEqual(422, client.put(path + '/status', headers=admin,
                                         json=self.account_status_data('http-extra', expected_version=3,
                                                                       unexpected=True)).status_code)
        self.assertEqual(404, client.put('/v1/admin/accounts/admin-a/status', headers=admin,
                                         json=self.account_status_data('http-admin-protected')).status_code)
        self.assertNotIn('password', created.json())
        self.assertEqual(2, self.read('SELECT count(*) AS n FROM balance_ledger')['n'])
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM attendance_events')['n'])

    def test_student_multiple_classes_sync_and_leave_one_preserve_other_class_and_history(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO student_class_enrollments VALUES
                    ('enrollment-a-b','org-fiction','student-a','class-b',1,NULL,1);
                INSERT INTO lesson_students VALUES
                    ('org-fiction','class-b','lesson-b','student-a',TRUE,1);
                INSERT INTO attendance_records VALUES
                    ('org-fiction','lesson-b','student-a','missing',0,NULL,1);
            """)
        future_a = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('multi-class-a'))
        future_b = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'multi-class-b', class_id='class-b', teacher_id='teacher-b', starts_at=1200, ends_at=1300))
        preview = self.store.admin_future_lessons(ADMIN_TOKEN, 'student-a')
        self.assertEqual({'class-a', 'class-b'}, set(preview['student']['class_ids']))
        self.assertEqual({future_a['lesson_id'], future_b['lesson_id']},
                         {row['lesson_id'] for row in preview['already_present_lessons']})
        left = self.store.admin_update_student(ADMIN_TOKEN, 'student-a', self.student_update_data(
            'multi-class-leave-b', class_ids=['class-a']))
        self.assertEqual(['class-a'], left['class_ids'])
        self.assertEqual([future_b['lesson_id']], left['removed_future_lesson_ids'])
        self.assertEqual(('class-a', True), tuple(self.read(
            "SELECT class_id,left_at IS NULL FROM student_class_enrollments WHERE student_id='student-a' AND class_id='class-a'").values()))
        self.assertEqual(1, self.read(
            "SELECT count(*) AS n FROM lesson_students WHERE lesson_id=%s AND student_id='student-a'",
            (future_a['lesson_id'],))['n'])
        self.assertEqual(('class-b', 'missing'), tuple(self.read(
            "SELECT ls.class_id,ar.status FROM lesson_students ls JOIN attendance_records ar ON ar.org_id=ls.org_id AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id WHERE ls.lesson_id='lesson-b' AND ls.student_id='student-a'").values()))

    def test_alternating_teachers_follow_each_schedule_and_class_handover_keeps_them(self):
        assigned = self.store.admin_set_class_teacher(ADMIN_TOKEN, 'class-a', dict(
            teacher_ids=['teacher-a', 'teacher-b'], expected_version=0,
            reason='虚构双教师轮课', command_ref='alternating-teachers'))
        self.assertEqual(['teacher-a', 'teacher-b'], assigned['teacher_ids'])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO class_schedule_templates VALUES
                    ('schedule-a','org-fiction','class-a',6,2,'2026-11-07','14:00','15:30',90,0,'2026-11-07','teacher-a'),
                    ('schedule-b','org-fiction','class-a',6,2,'2026-11-14','14:00','15:30',90,0,'2026-11-07','teacher-b');
            """)
        first = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'alternating-a', schedule_id='schedule-a'))
        second = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'alternating-b', teacher_id='teacher-b', schedule_id='schedule-b',
            starts_at=1200, ends_at=1300))
        self.assertEqual(('schedule-a', 'teacher-a'), tuple(self.read(
            'SELECT schedule_id,user_id FROM lessons JOIN lesson_teachers ON lessons.org_id=lesson_teachers.org_id AND lessons.id=lesson_teachers.lesson_id WHERE lessons.id=%s',
            (first['lesson_id'],)).values()))
        self.assertEqual(('schedule-b', 'teacher-b'), tuple(self.read(
            'SELECT schedule_id,user_id FROM lessons JOIN lesson_teachers ON lessons.org_id=lesson_teachers.org_id AND lessons.id=lesson_teachers.lesson_id WHERE lessons.id=%s',
            (second['lesson_id'],)).values()))
        self.store.admin_set_class_teacher(ADMIN_TOKEN, 'class-a', dict(
            teacher_ids=['teacher-a', 'teacher-b'], expected_version=1,
            reason='虚构轮课授权复核', command_ref='alternating-teachers-recheck'))
        self.assertEqual({first['lesson_id']: 'teacher-a', second['lesson_id']: 'teacher-b'},
                         {first['lesson_id']: self.read('SELECT user_id FROM lesson_teachers WHERE lesson_id=%s', (first['lesson_id'],))['user_id'],
                          second['lesson_id']: self.read('SELECT user_id FROM lesson_teachers WHERE lesson_id=%s', (second['lesson_id'],))['user_id']})
        self.assert_problem(409, 'schedule_teacher_mismatch', self.store.admin_create_lesson,
                            ADMIN_TOKEN, self.lesson_data('alternating-mismatch', teacher_id='teacher-b',
                                                         schedule_id='schedule-a', starts_at=1400, ends_at=1500))

    def test_cancel_this_week_and_add_two_next_week_count_each_scheduled_lesson_once(self):
        cancelled = self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data('week-cancel'))
        self.store.admin_cancel_lesson(ADMIN_TOKEN, cancelled['lesson_id'], self.cancel_data('week-cancel-command'))
        extra = [self.store.admin_create_lesson(ADMIN_TOKEN, self.lesson_data(
            'next-week-' + str(index), starts_at=starts, ends_at=starts + 100))
            for index, starts in enumerate((1200, 1400))]
        self.store.clock = lambda: 1500
        report = self.store.admin_weekly_report(ADMIN_TOKEN, '1969-12-29')
        self.assertEqual((4, 0, 0, 4), tuple(report['overall'][key] for key in (
            'expected_count', 'arrived_count', 'leave_count', 'missing_count')))
        self.assertEqual(2, len({row['lesson_id'] for row in extra}))
        self.assertEqual(1, self.read("SELECT count(*) AS n FROM lessons WHERE state='cancelled'")['n'])

    def test_renewal_price_snapshot_survives_course_price_change(self):
        renewed = self.store.admin_renewal(ADMIN_TOKEN, self.renewal_data(
            amount_cents=200000, unit_price_cents=10000))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE courses SET unit_price_cents=16800 WHERE id='course-a'")
        snapshot = self.read('SELECT course_id,amount_cents,unit_price_cents FROM renewal_records WHERE id=%s',
                             (renewed['renewal_id'],))
        self.assertEqual(('course-a', 200000, 10000), tuple(snapshot.values()))
        self.assertEqual(16800, self.read("SELECT unit_price_cents FROM courses WHERE id='course-a'")['unit_price_cents'])

    def test_zero_balance_checkin_debits_to_negative_with_consistent_ledger(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("UPDATE student_balances SET units=0 WHERE student_id='student-a'")
        result = self.store.tablet_checkin(DEVICE_TOKEN, checkin('negative-balance') | {'previous_balance_units': 0})
        self.assertEqual(-1, result['balance_units'])
        self.assertEqual((0, -1, -1), tuple(self.read(
            "SELECT previous_units,units_delta,units FROM balance_ledger WHERE kind='checkin' AND student_id='student-a'").values()))

    def test_cross_organization_course_class_schedule_and_enrollment_references_are_rejected(self):
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO organizations VALUES('org-other','other',1);
                INSERT INTO courses VALUES('course-other','org-other','他机构课程','他机构内容',100,TRUE,1);
                INSERT INTO classes VALUES('class-other','org-other','他机构班级','course-other',1);
                INSERT INTO users VALUES('teacher-other','org-other','other-teacher','他机构教师',TRUE,1,FALSE);
            """)
        statements = [
            "INSERT INTO classes VALUES('bad-class','org-fiction','错误班级','course-other',1)",
            "INSERT INTO student_class_enrollments VALUES('bad-enrollment','org-fiction','student-a','class-other',1,NULL,1)",
            "INSERT INTO class_schedule_templates VALUES('bad-schedule','org-fiction','class-a',6,1,'2026-11-07','14:00','15:00',60,0,'2026-11-07','teacher-other')",
        ]
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with psycopg.connect(self.conninfo) as connection:
                    connection.execute(statement)
        self.assertEqual(0, self.read("SELECT count(*) AS n FROM classes WHERE id='bad-class'")['n'])

    def test_face_profile_requires_a_configured_encryption_key(self):
        self.assert_problem(503, 'face_not_configured', self.store.admin_face_profile,
                            ADMIN_TOKEN, 'student-a', face_profile('face-no-key'))
        self.assertEqual(0, self.read('SELECT count(*) AS n FROM face_profiles')['n'])


if __name__ == '__main__':
    unittest.main()
