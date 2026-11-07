"""One fictional cross-role acceptance chain through HTTP, backup and independent restore."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from management import runtime_release, runtime_schema
from tests import test_runtime_service as fixtures


@unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
class RuntimeAcceptanceTests(unittest.TestCase):
    def test_admin_teacher_tablet_reports_backup_and_restore_form_one_chain(self):
        import pgserver
        from fastapi.testclient import TestClient
        from management.runtime_api import create_app

        with tempfile.TemporaryDirectory(prefix='runtime-final-acceptance-') as folder:
            root = Path(folder)
            server = pgserver.get_server(str(root / 'pg'), cleanup_mode='stop')
            try:
                parsed = urlsplit(server.get_uri())
                base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                     user=parsed.username, password=parsed.password)
                with psycopg.connect(base, dbname='postgres', autocommit=True) as connection:
                    for name in ('runtime_rehearsal_acceptance_source',
                                 'runtime_rehearsal_acceptance_restored'):
                        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                source = make_conninfo(base, dbname='runtime_rehearsal_acceptance_source')
                restored = make_conninfo(base, dbname='runtime_rehearsal_acceptance_restored')
                with psycopg.connect(source) as connection:
                    connection.execute(runtime_schema.full_schema())
                    fixtures.RuntimeBusinessTests.seed(connection)
                    connection.execute("""
                        INSERT INTO lessons(id,org_id,class_id,starts_at,ends_at,state,created_at) VALUES
                            ('lesson-web','org-fiction','class-a',300,400,'scheduled',1);
                        INSERT INTO lesson_teachers VALUES
                            ('org-fiction','lesson-web','teacher-a','虚构教师甲');
                        INSERT INTO lesson_students VALUES
                            ('org-fiction','class-a','lesson-web','student-a',TRUE,1);
                        INSERT INTO attendance_records VALUES
                            ('org-fiction','lesson-web','student-a','missing',0,NULL,1);
                    """)

                admin = {'Authorization': 'Bearer ' + fixtures.ADMIN_TOKEN}
                teacher = {'Authorization': 'Bearer ' + fixtures.USER_TOKEN}
                device = {'Authorization': 'Bearer ' + fixtures.DEVICE_TOKEN}
                with TestClient(create_app(source, lambda: 1000, allow_test_client=True)) as client:
                    self.assertEqual(200, client.get('/v1/me', headers=admin).status_code)
                    renewal = client.post('/v1/admin/renewals', headers=admin, json={
                        'student_id': 'student-a', 'course_id': 'course-a',
                        'units_added': 5, 'amount_cents': 64000, 'unit_price_cents': 12800,
                        'occurred_at': 100, 'source_ref': 'acceptance-renewal-source',
                        'reason': '虚构端到端续费', 'command_ref': 'acceptance-renewal-command'})
                    self.assertEqual(201, renewal.status_code, renewal.text)
                    self.assertEqual((15, 2), (renewal.json()['balance_units'],
                                               renewal.json()['balance_revision']))
                    future = client.post('/v1/admin/lessons', headers=admin, json={
                        'class_id': 'class-a', 'teacher_id': 'teacher-a',
                        'starts_at': 2000, 'ends_at': 2600, 'charge_units': 1,
                        'command_ref': 'acceptance-future-lesson'})
                    self.assertEqual(201, future.status_code, future.text)

                    bootstrap = client.get('/v1/tablet/bootstrap', headers=device)
                    self.assertEqual(200, bootstrap.status_code, bootstrap.text)
                    first = client.post('/v1/tablet/checkins', headers=device, json={
                        'event_ref': 'acceptance-tablet-checkin', 'lesson_id': 'lesson-a',
                        'student_id': 'student-a', 'occurred_at': 100,
                        'attendance_revision': 0, 'balance_revision': 2,
                        'previous_balance_units': 15, 'consumed_units': 1})
                    self.assertEqual(201, first.status_code, first.text)
                    self.assertEqual(14, first.json()['balance_units'])
                    duplicate = client.post('/v1/tablet/checkins', headers=device, json={
                        'event_ref': 'acceptance-tablet-checkin', 'lesson_id': 'lesson-a',
                        'student_id': 'student-a', 'occurred_at': 100,
                        'attendance_revision': 0, 'balance_revision': 2,
                        'previous_balance_units': 15, 'consumed_units': 1})
                    self.assertEqual(200, duplicate.status_code, duplicate.text)
                    self.assertTrue(duplicate.json()['duplicate'])

                    lessons = client.get('/v1/teacher/lessons?date=1970-01-01', headers=teacher)
                    self.assertEqual(200, lessons.status_code, lessons.text)
                    self.assertIn('lesson-web', [item['id'] for item in lessons.json()['items']])
                    leave = client.post('/v1/teacher/lessons/lesson-web/leave', headers=teacher, json={
                        'student_id': 'student-a', 'reason': '虚构端到端请假',
                        'event_ref': 'acceptance-teacher-leave', 'expected_revision': 0})
                    self.assertEqual(201, leave.status_code, leave.text)
                    self.assertEqual(14, leave.json()['balance_units'])
                    resolution = client.post('/v1/tablet/checkins/resolve', headers=device, json={
                        'event_ref': 'acceptance-offline-leave', 'lesson_id': 'lesson-web',
                        'student_id': 'student-a', 'occurred_at': 350,
                        'attendance_revision': 0, 'balance_revision': 3,
                        'previous_balance_units': 14, 'consumed_units': 1})
                    self.assertEqual(200, resolution.status_code, resolution.text)
                    self.assertEqual(('leave', 14, False), tuple(resolution.json()[key] for key in (
                        'status', 'balance_units', 'apply_local_debit')))
                    resolved_again = client.post('/v1/tablet/checkins/resolve', headers=device, json={
                        'event_ref': 'acceptance-offline-leave', 'lesson_id': 'lesson-web',
                        'student_id': 'student-a', 'occurred_at': 350,
                        'attendance_revision': 0, 'balance_revision': 3,
                        'previous_balance_units': 14, 'consumed_units': 1})
                    self.assertEqual(200, resolved_again.status_code)
                    self.assertTrue(resolved_again.json()['duplicate'])

                    teacher_week = client.get(
                        '/v1/teacher/reports/weekly?week_start=1969-12-29', headers=teacher)
                    admin_week = client.get(
                        '/v1/admin/reports/weekly?week_start=1969-12-29', headers=admin)
                    month = client.get('/v1/admin/reports/monthly?month=1970-01', headers=admin)
                    self.assertEqual(50.0, teacher_week.json()['overall']['rate_percent'])
                    self.assertEqual((3, 1, 1, 1, 33.3), tuple(admin_week.json()['overall'][key]
                        for key in ('expected_count', 'arrived_count', 'leave_count',
                                    'missing_count', 'rate_percent')))
                    self.assertEqual((1, 1, 5), tuple(month.json()['overall'][key]
                        for key in ('renewal_students', 'renewal_events', 'renewal_units')))

                with psycopg.connect(source) as connection:
                    counts = connection.execute('''SELECT
                        (SELECT count(*) FROM attendance_events),
                        (SELECT count(*) FROM balance_ledger),
                        (SELECT count(*) FROM api_commands),
                        (SELECT units FROM student_balances WHERE student_id='student-a')''').fetchone()
                self.assertEqual((2, 4, 5, 14), counts)
                before = runtime_release.snapshot(source)
                backup = runtime_release.backup_local(
                    source, root / 'acceptance.pgdump', True)
                self.assertEqual(before, backup['snapshot'])
                restored_result = runtime_release.restore_local(
                    root / 'acceptance.pgdump', restored, True, before)
                self.assertEqual(before, restored_result['snapshot'])
                with TestClient(create_app(restored, lambda: 1000, allow_test_client=True)) as client:
                    health = client.get('/health/ready')
                    self.assertEqual(200, health.status_code)
                    self.assertEqual('ready', health.json()['status'])
                    self.assertEqual(200, client.get('/v1/me', headers=admin).status_code)
                    restored_week = client.get(
                        '/v1/admin/reports/weekly?week_start=1969-12-29', headers=admin)
                    self.assertEqual(admin_week.json()['overall'], restored_week.json()['overall'])
            finally:
                server.cleanup()


if __name__ == '__main__':
    unittest.main()
