"""Real PostgreSQL tests for the fictional-only runtime schema."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from management import runtime_schema


@unittest.skipUnless(importlib.util.find_spec("pgserver"), "optional pgserver is not installed")
class RuntimePostgresSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pgserver
        cls.tmp = tempfile.TemporaryDirectory(prefix="runtime-schema-pg-")
        cls.root = Path(cls.tmp.name)
        cls.server = pgserver.get_server(str(cls.root / "data"), cleanup_mode="stop")
        parsed = urlsplit(cls.server.get_uri())
        cls.base = make_conninfo(host="127.0.0.1", port=parsed.port,
                                 user=parsed.username, password=parsed.password)
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.tmp.cleanup()

    def database(self):
        type(self).sequence += 1
        name = "runtime_schema_rehearsal_" + str(type(self).sequence)
        with psycopg.connect(self.base, dbname="postgres", autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self.addCleanup(self.drop_database, name)
        return make_conninfo(self.base, dbname=name)

    @classmethod
    def drop_database(cls, name):
        with psycopg.connect(cls.base, dbname="postgres", autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def populated(self):
        target = self.database()
        connection = psycopg.connect(target)
        self.addCleanup(connection.close)
        connection.execute(runtime_schema.full_schema())
        self.seed(connection)
        return connection

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
                ('teacher-a','org-fiction','fiction-teacher','虚构教师',TRUE,1,FALSE);
            INSERT INTO lessons VALUES
                ('lesson-a','org-fiction','class-a',100,200,'scheduled',1),
                ('lesson-b','org-fiction','class-b',100,200,'scheduled',1);
            INSERT INTO lesson_students VALUES
                ('org-fiction','class-a','lesson-a','student-a',TRUE,1),
                ('org-fiction','class-b','lesson-b','student-b',TRUE,1);
            INSERT INTO opening_balances VALUES
                ('org-fiction','student-a',10,'manual_review','fiction-review'),
                ('org-fiction','student-b',10,'manual_review','fiction-review');
            INSERT INTO student_balances VALUES
                ('org-fiction','student-a',10,1,1),
                ('org-fiction','student-b',10,1,1);
            INSERT INTO balance_ledger VALUES
                ('ledger-a','org-fiction','student-a','opening','opening-a',0,10,10,0,1,'system',NULL,'虚构期初',1),
                ('ledger-b','org-fiction','student-b','opening','opening-b',0,10,10,0,1,'system',NULL,'虚构期初',1);
            INSERT INTO attendance_records VALUES
                ('org-fiction','lesson-a','student-a','missing',0,NULL,1),
                ('org-fiction','lesson-b','student-b','missing',0,NULL,1);
            INSERT INTO attendance_events VALUES
                ('event-a','org-fiction','lesson-a','student-a','tablet_checkin','event-ref-a','device','device-a',NULL,100,101);
            UPDATE attendance_records
                SET status='arrived', revision=1, last_event_id='event-a', updated_at=101
                WHERE org_id='org-fiction' AND lesson_id='lesson-a' AND student_id='student-a';
        """)

    def test_core_tables_constraints_and_migration_exist(self):
        connection = self.populated()
        tables = {row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        expected = {'lessons', 'lesson_students', 'attendance_records', 'attendance_events',
                    'student_balances', 'balance_ledger', 'enrollment_records',
                    'renewal_records', 'face_profiles', 'wechat_identities',
                    'parent_student_bindings', 'notification_outbox', 'report_runs',
                    'audit_log', 'api_commands', 'schema_migrations'}
        self.assertTrue(expected <= tables)
        constraints = {row[0] for row in connection.execute(
            "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace")}
        self.assertIn('attendance_records_last_event_fk', constraints)
        self.assertIn('students_org_class_id_key', constraints)
        applied_at = connection.execute(
            "SELECT applied_at FROM schema_migrations WHERE version='fresh-start-runtime-v1'").fetchone()[0]
        self.assertGreater(applied_at, 0)

    def test_roster_rejects_cross_class_and_unknown_students(self):
        connection = self.populated()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("INSERT INTO lesson_students VALUES('org-fiction','class-b','lesson-b','student-a',TRUE,1)")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("INSERT INTO lesson_students VALUES('org-fiction','class-a','lesson-a','missing',TRUE,1)")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("""INSERT INTO enrollment_records VALUES
                    ('enroll-bad','org-fiction','student-a','class-b',1,'enroll-ref-bad','teacher-a',1)""")

    def test_latest_event_must_match_lesson_and_student(self):
        connection = self.populated()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("UPDATE attendance_records SET last_event_id='event-a' WHERE lesson_id='lesson-b'")

    def test_valid_ledger_entry_commits(self):
        connection = self.populated()
        with connection.transaction():
            connection.execute("UPDATE student_balances SET units=9,revision=2,updated_at=102 WHERE student_id='student-a'")
            connection.execute("""INSERT INTO balance_ledger VALUES
                ('ledger-checkin','org-fiction','student-a','checkin','event-a',10,-1,9,1,2,'device','device-a','虚构签到',102)""")
        self.assertEqual(connection.execute("SELECT units FROM student_balances WHERE student_id='student-a'").fetchone()[0], 9)

    def test_ledger_rejects_incorrect_units_arithmetic(self):
        connection = self.populated()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute("""INSERT INTO balance_ledger VALUES
                    ('ledger-bad-units','org-fiction','student-a','checkin','bad-units',10,-1,10,1,2,'device','device-a','虚构错误',102)""")

    def test_ledger_rejects_revision_gap(self):
        connection = self.populated()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute("""INSERT INTO balance_ledger VALUES
                    ('ledger-bad-revision','org-fiction','student-a','checkin','bad-revision',10,-1,9,1,3,'device','device-a','虚构错误',102)""")

    def test_business_records_reject_update(self):
        connection = self.populated()
        connection.execute("INSERT INTO audit_log(org_id,actor_type,action,entity_id,detail,created_at) VALUES('org-fiction','system','fiction','event-a','{}',1)")
        statements = [
            "UPDATE attendance_events SET reason='changed' WHERE id='event-a'",
            "UPDATE balance_ledger SET reason='changed' WHERE id='ledger-a'",
            "UPDATE audit_log SET action='changed' WHERE entity_id='event-a'",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(psycopg.Error, 'immutable business record'):
                    with connection.transaction():
                        connection.execute(statement)

    def test_business_records_reject_delete(self):
        connection = self.populated()
        connection.execute("INSERT INTO audit_log(org_id,actor_type,action,entity_id,detail,created_at) VALUES('org-fiction','system','fiction','event-a','{}',1)")
        statements = [
            "DELETE FROM attendance_events WHERE id='event-a'",
            "DELETE FROM balance_ledger WHERE id='ledger-a'",
            "DELETE FROM audit_log WHERE entity_id='event-a'",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(psycopg.Error, 'immutable business record'):
                    with connection.transaction():
                        connection.execute(statement)

    def test_duplicate_event_and_ledger_references_are_rejected(self):
        connection = self.populated()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute("""INSERT INTO attendance_events VALUES
                    ('event-duplicate','org-fiction','lesson-a','student-a','tablet_checkin','event-ref-a','device','device-a',NULL,100,102)""")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                connection.execute("""INSERT INTO balance_ledger VALUES
                    ('ledger-duplicate','org-fiction','student-a','opening','opening-a',10,0,10,1,2,'system',NULL,'虚构重复',102)""")

    def test_external_failure_rolls_back_ddl_and_rows(self):
        class DeliberateFailure(Exception):
            pass
        target = self.database()
        with self.assertRaises(DeliberateFailure):
            with psycopg.connect(target) as connection:
                connection.execute(runtime_schema.full_schema())
                self.seed(connection)
                raise DeliberateFailure()
        with psycopg.connect(target) as connection:
            count = connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()
