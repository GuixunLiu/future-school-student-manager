"""Fictional PostgreSQL writer contract tests; no live database is required."""
import copy
import json
import unittest

from management import fresh_start
from management import fresh_start_postgres as pg


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.answer = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        if "FROM pg_catalog.pg_tables" in sql:
            self.answer = (self.connection.public_tables,)
        elif sql.startswith("SELECT count(*) FROM "):
            table = sql.rsplit(" ", 1)[-1]
            self.answer = (self.connection.counts.get(table, 0) + self.connection.count_offset,)
        elif sql.startswith("SELECT source_sha256,bundle_sha256"):
            self.answer = self.connection.receipt
        elif sql.startswith("INSERT INTO migration_receipts"):
            self.connection.counts["migration_receipts"] = 1
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("fictional database failure")

    def executemany(self, sql, rows):
        self.connection.statements.append((sql, rows))
        table = sql.split(" ", 2)[2].split(" ", 1)[0]
        self.connection.counts[table] = self.connection.counts.get(table, 0) + len(rows)
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("fictional insert failure")

    def fetchone(self):
        return self.answer


class FakeConnection:
    def __init__(self, public_tables=0, fail_on=None, count_offset=0):
        self.public_tables = public_tables
        self.fail_on = fail_on
        self.count_offset = count_offset
        self.counts = {}
        self.statements = []
        self.receipt = None
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False


def fictional_bundle():
    org = "org"
    tables = {
        "organizations": [dict(id=org, slug="fictional", created_at=100)],
        "courses": [dict(id="course", org_id=org, name="虚构课程", content="虚构课程内容",
                         unit_price_cents=12800, active=True, created_at=100)],
        "classes": [dict(id="class", org_id=org, name="虚构班", course_id="course", created_at=100)],
        "students": [dict(id="student-a", org_id=org, name="虚构学生甲", external_ref="F001", active=True),
                     dict(id="student-b", org_id=org, name="虚构学生乙", external_ref="F002", active=False)],
        "student_class_enrollments": [
            dict(id="enrollment-a", org_id=org, student_id="student-a", class_id="class", joined_at=0, left_at=None, created_at=0),
            dict(id="enrollment-b", org_id=org, student_id="student-b", class_id="class", joined_at=0, left_at=None, created_at=0)],
        "opening_balances": [dict(org_id=org, student_id="student-a", units=900,
                                  source="matched_management_balance", review_ref=None),
                             dict(org_id=org, student_id="student-b", units=850,
                                  source="manual_review", review_ref="fictional-review")],
        "users": [dict(id="user", org_id=org, login="fictional", display_name="虚构教师",
                       active=True, created_at=100, password_reset_required=True)],
        "roles": [dict(org_id=org, user_id="user", role="teacher")],
        "teacher_classes": [dict(org_id=org, user_id="user", class_id="class")],
        "class_schedule_templates": [dict(id="schedule", org_id=org, class_id="class", weekday=6,
            interval_weeks=1, anchor_date="2026-11-01", start_time="14:00", end_time="15:30",
            duration_minutes=90, end_day_offset=0, effective_date="2026-11-01", teacher_id="user")],
        "devices": [dict(id="device", org_id=org, name="虚构设备", active=True,
                         created_at=100, token_reissue_required=True)],
    }
    return {
        "format": fresh_start.FORMAT,
        "source_sha256": "a" * 64,
        **tables,
        "counts": {table: len(rows) for table, rows in tables.items()},
        "history_migrated": False,
        "archived_students_migrated": 0,
        "historical_attendance_migrated": 0,
        "historical_requests_migrated": 0,
        "password_hashes_migrated": 0,
        "sessions_migrated": 0,
        "device_secrets_migrated": 0,
        "live_apply_authorized": False,
    }


class PostgresWriterTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fictional_bundle()
        self.raw = fresh_start.bundle_bytes(self.bundle)

    def populate(self, connection=None):
        connection = connection or FakeConnection()
        bundle = json.loads(self.raw)
        # The fake receipt follows PostgreSQL's four-column result contract.
        import hashlib
        connection.receipt = (bundle["source_sha256"], hashlib.sha256(self.raw).hexdigest(), False, False)
        return pg.populate(connection, bundle, self.raw, 123456), connection

    def test_canonical_bundle_populates_all_rows_and_receipt(self):
        receipt, connection = self.populate()
        self.assertTrue(receipt["rehearsal_only"])
        self.assertFalse(receipt["live_apply_authorized"])
        self.assertEqual(receipt["counts"], self.bundle["counts"])
        inserted = [sql for sql, _ in connection.statements if sql.startswith("INSERT INTO")]
        self.assertEqual(len(inserted), 12)
        self.assertEqual(connection.counts["migration_receipts"], 1)
        self.assertFalse(any(sql.lstrip().startswith(("DELETE", "UPDATE", "DROP", "TRUNCATE"))
                             for sql, _ in connection.statements))
        self.assertNotIn("password_hash", json.dumps(connection.statements))
        self.assertNotIn("token_hash", json.dumps(connection.statements))

    def test_nonempty_database_is_refused_before_schema_or_writes(self):
        with self.assertRaisesRegex(pg.FreshStartError, "nonempty"):
            self.populate(FakeConnection(public_tables=1))
        connection = FakeConnection(public_tables=1)
        with self.assertRaises(pg.FreshStartError):
            self.populate(connection)
        self.assertFalse(any("CREATE TABLE" in sql or sql.startswith("INSERT") for sql, _ in connection.statements))

    def test_history_credentials_or_live_apply_are_refused(self):
        for key, value in (("history_migrated", True), ("live_apply_authorized", True),
                           ("password_hashes_migrated", 1), ("sessions_migrated", 1),
                           ("device_secrets_migrated", 1)):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.bundle)
                changed[key] = value
                with self.assertRaises(pg.FreshStartError):
                    pg.validate_bundle(changed, fresh_start.bundle_bytes(changed))

    def test_unknown_columns_and_incomplete_balances_are_refused(self):
        changed = copy.deepcopy(self.bundle)
        changed["users"][0]["password_hash"] = "not allowed"
        with self.assertRaisesRegex(pg.FreshStartError, "columns"):
            pg.validate_bundle(changed, fresh_start.bundle_bytes(changed))
        changed = copy.deepcopy(self.bundle)
        changed["opening_balances"].pop()
        changed["counts"]["opening_balances"] = 1
        with self.assertRaisesRegex(pg.FreshStartError, "uniquely and exactly"):
            pg.validate_bundle(changed, fresh_start.bundle_bytes(changed))
        changed = copy.deepcopy(self.bundle)
        changed["users"][0]["password_reset_required"] = False
        with self.assertRaisesRegex(pg.FreshStartError, "password reset"):
            pg.validate_bundle(changed, fresh_start.bundle_bytes(changed))

    def test_connection_context_rolls_back_failed_population(self):
        connection = FakeConnection(fail_on="INSERT INTO teacher_classes")
        with self.assertRaisesRegex(RuntimeError, "fictional"):
            with connection:
                self.populate(connection)
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)

    def test_count_mismatch_rolls_back(self):
        connection = FakeConnection(count_offset=1)
        with self.assertRaisesRegex(pg.FreshStartError, "row count"):
            with connection:
                self.populate(connection)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)

    def test_loopback_and_rehearsal_database_name_are_mandatory(self):
        valid = "host=127.0.0.1 dbname=fresh_start_rehearsal_test user=fictional"
        self.assertEqual(pg.local_rehearsal_dsn(valid, True), valid)
        for dsn in ("host=47.97.226.219 dbname=fresh_start_rehearsal_test",
                    "host=127.0.0.1 dbname=production", "dbname=fresh_start_rehearsal_test"):
            with self.subTest(dsn=dsn):
                with self.assertRaises(pg.FreshStartError):
                    pg.local_rehearsal_dsn(dsn, True)
        with self.assertRaisesRegex(pg.FreshStartError, "Explicit"):
            pg.local_rehearsal_dsn(valid, False)

    def test_schema_is_executed_inside_callers_transaction(self):
        body = pg.schema_body()
        self.assertNotIn("BEGIN;", body)
        self.assertNotIn("COMMIT;", body)
        self.assertIn("CREATE TABLE migration_receipts", body)


if __name__ == "__main__":
    unittest.main()
