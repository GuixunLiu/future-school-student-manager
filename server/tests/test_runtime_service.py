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

from business.security import password_hash, token_digest
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
            INSERT INTO classes VALUES
                ('class-a','org-fiction','虚构甲班','虚构课程',1),
                ('class-b','org-fiction','虚构乙班','虚构课程',1);
            INSERT INTO students(id,org_id,class_id,name,external_ref,active) VALUES
                ('student-a','org-fiction','class-a','虚构学生甲','fiction-a',TRUE),
                ('student-b','org-fiction','class-b','虚构学生乙','fiction-b',TRUE);
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
            INSERT INTO lessons VALUES
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
                INSERT INTO lessons VALUES
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
                INSERT INTO lessons VALUES
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
                INSERT INTO classes VALUES('class-other','org-other','其他机构班','其他课程',1);
                INSERT INTO lessons VALUES('lesson-other','org-other','class-other',100,200,'scheduled',1);
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
        self.assertEqual((0, 0, 0, 0), tuple(initial['overall'].values()))
        self.assertFalse(initial['final_period'])
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO enrollment_records VALUES
                    ('enroll-a','org-fiction','student-a','class-a',100,'enroll-source-a','admin-a',9999),
                    ('enroll-b','org-fiction','student-b','class-b',2678400,'enroll-source-b','admin-a',9999);
                INSERT INTO renewal_records VALUES
                    ('renewal-a1','org-fiction','student-a',20,100,'renewal-source-a1','admin-a','虚构续费一',9999),
                    ('renewal-a2','org-fiction','student-a',30,200,'renewal-source-a2','admin-a','虚构续费二',9999),
                    ('renewal-b1','org-fiction','student-b',10,300,'renewal-source-b1','admin-a','虚构续费三',9999),
                    ('renewal-next','org-fiction','student-a',5,2678400,'renewal-source-next','admin-a','下月续费',9999);
            """)
        result = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-01')
        self.assertEqual(('1970-01-01', '1970-01-31'), (result['month_start'], result['month_end']))
        self.assertEqual((1, 2, 3, 60), tuple(result['overall'][key] for key in (
            'new_students', 'renewal_students', 'renewal_events', 'renewal_units')))
        self.assertEqual(1, len(result['enrollments']))
        self.assertEqual(3, len(result['renewals']))
        self.assertEqual(['renewal-a1', 'renewal-a2', 'renewal-b1'], [row['id'] for row in result['renewals']])
        self.assertEqual('虚构甲班', result['enrollments'][0]['class_name'])
        self.assertFalse(result['details_truncated'])
        following = self.store.admin_monthly_report(ADMIN_TOKEN, '1970-02')
        self.assertEqual((1, 1, 1, 5), tuple(following['overall'].values()))
        historical = self.store.admin_monthly_report(ADMIN_TOKEN, '1969-12')
        self.assertEqual((0, 0, 0, 0), tuple(historical['overall'].values()))
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
        self.assertEqual((0, 0, 0, 0), tuple(response.json()['overall'].values()))
        with psycopg.connect(self.conninfo) as connection:
            connection.execute("""
                INSERT INTO enrollment_records VALUES('enroll-http','org-fiction','student-a','class-a',100,'enroll-http-source','admin-a',9999);
                INSERT INTO renewal_records VALUES('renewal-http','org-fiction','student-a',25,100,'renewal-http-source','admin-a','虚构续费',9999);
            """)
        updated = client.get('/v1/admin/reports/monthly?month=1970-01', headers=admin)
        self.assertEqual((1, 1, 1, 25), tuple(updated.json()['overall'].values()))
        self.assertEqual('虚构学生甲', updated.json()['renewals'][0]['student_name'])
        self.assertEqual(403, client.get('/v1/admin/reports/monthly', headers=teacher).status_code)
        self.assertEqual(403, client.get('/v1/admin/reports/monthly', headers=device).status_code)
        self.assertEqual(401, client.get('/v1/admin/reports/monthly').status_code)
        for invalid in ('1970-13', '1970-1', 'bad'):
            self.assertEqual(422, client.get('/v1/admin/reports/monthly?month=' + invalid, headers=admin).status_code)
        self.assertEqual(200, client.get('/admin-monthly.js').status_code)

    def test_http_teacher_list_and_makeup_are_scoped_idempotent_and_versioned(self):
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        client = TestClient(create_app(self.conninfo, lambda: 1000, allow_test_client=True))
        teacher = {'Authorization': 'Bearer ' + USER_TOKEN}
        other = {'Authorization': 'Bearer ' + OTHER_USER_TOKEN}
        listed = client.get('/v1/teacher/lessons?date=1970-01-01', headers=teacher)
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual(['lesson-a'], [row['id'] for row in listed.json()['items']])
        self.assertEqual(422, client.get('/v1/teacher/lessons?date=not-a-date', headers=teacher).status_code)
        path = '/v1/teacher/lessons/lesson-a/makeup'
        stale = client.post(path, headers=teacher,
                            json=dict(correction('http-makeup-stale'), expected_revision=2))
        self.assertEqual(409, stale.status_code, stale.text)
        self.assertEqual('attendance_changed', stale.json()['error']['code'])
        created = client.post(path, headers=teacher, json=correction('http-makeup'))
        self.assertEqual(201, created.status_code, created.text)
        duplicate = client.post(path, headers=teacher, json=correction('http-makeup'))
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()['duplicate'])
        self.assertEqual(created.json()['event_id'], duplicate.json()['event_id'])
        denied = client.post('/v1/teacher/lessons/lesson-a/leave', headers=other,
                             json=correction('http-other-scope'))
        self.assertEqual(403, denied.status_code, denied.text)
        self.assertEqual(9, self.read("SELECT units FROM student_balances WHERE student_id='student-a'")['units'])

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


if __name__ == '__main__':
    unittest.main()
