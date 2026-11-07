"""Fictional offline fresh-start fixtures; no production data or credentials."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from management import fresh_start


class FreshStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.sqlite"
        self.create_source()
        self.sha256 = fresh_start.file_sha256(self.source)

    def create_source(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE organizations(id TEXT PRIMARY KEY,slug TEXT UNIQUE NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE classes(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,
                    course_name TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(org_id,id));
                CREATE TABLE class_schedule_templates(org_id TEXT NOT NULL,class_id TEXT NOT NULL,
                    weekday INTEGER NOT NULL,interval_weeks INTEGER NOT NULL,anchor_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,end_time TEXT NOT NULL,duration_minutes INTEGER NOT NULL,
                    end_day_offset INTEGER NOT NULL,effective_date TEXT NOT NULL,teacher_label TEXT NOT NULL,
                    PRIMARY KEY(org_id,class_id));
                CREATE TABLE students(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,class_id TEXT NOT NULL,
                    name TEXT NOT NULL,external_ref TEXT NOT NULL,remaining_units INTEGER NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE management_balances(org_id TEXT NOT NULL,student_id TEXT NOT NULL,units INTEGER NOT NULL,
                    PRIMARY KEY(org_id,student_id));
                CREATE TABLE users(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,login TEXT NOT NULL,
                    display_name TEXT NOT NULL,password_hash TEXT NOT NULL,active INTEGER NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE roles(org_id TEXT NOT NULL,user_id TEXT NOT NULL,role TEXT NOT NULL);
                CREATE TABLE teacher_classes(org_id TEXT NOT NULL,user_id TEXT NOT NULL,class_id TEXT NOT NULL);
                CREATE TABLE devices(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,
                    token_hash TEXT NOT NULL,active INTEGER NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE archived_students(id TEXT PRIMARY KEY,name TEXT NOT NULL);
                CREATE TABLE management_attendance(id TEXT PRIMARY KEY,student_id TEXT NOT NULL);
                CREATE TABLE management_requests(id TEXT PRIMARY KEY,student_id TEXT NOT NULL);
                CREATE TABLE sessions(id TEXT PRIMARY KEY,secret TEXT NOT NULL);
                INSERT INTO organizations VALUES('org','fictional',100);
                INSERT INTO classes VALUES('class','org','虚构班','虚构课程',100);
                INSERT INTO class_schedule_templates VALUES('org','class',6,1,'2026-11-01','14:00','15:30',90,0,'2026-11-01','虚构教师');
                INSERT INTO students VALUES('matched','org','class','虚构学生甲','F001',900,1);
                INSERT INTO students VALUES('different','org','class','虚构学生乙','F002',800,1);
                INSERT INTO students VALUES('missing','org','class','虚构学生丙','F003',700,0);
                INSERT INTO management_balances VALUES('org','matched',900);
                INSERT INTO management_balances VALUES('org','different',850);
                INSERT INTO users VALUES('user','org','fictional-teacher','虚构教师','old password hash',1,100);
                INSERT INTO roles VALUES('org','user','teacher');
                INSERT INTO teacher_classes VALUES('org','user','class');
                INSERT INTO devices VALUES('device','org','虚构设备','old device secret',1,100);
                INSERT INTO archived_students VALUES('archived','不迁移的虚构学生');
                INSERT INTO management_attendance VALUES('attendance','matched');
                INSERT INTO management_requests VALUES('request','matched');
                INSERT INTO sessions VALUES('session','old session secret');
            """)

    def template(self):
        return fresh_start.review_template(self.source, self.sha256)

    def complete_review(self):
        review = self.template()
        for entry in review["entries"]:
            entry.update(opening_units=entry["suggested_opening_units"] or 0,
                         confirmed=True, review_ref="fictional-review-2026")
        for entry in review["course_entries"]:
            entry.update(content="虚构课程内容", unit_price_cents=12800, confirmed=True)
        for entry in review["schedule_entries"]:
            entry.update(teacher_id="user", confirmed=True)
        return review

    def test_matched_balances_export_without_history_or_credentials(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("DELETE FROM students WHERE id IN ('different','missing')")
            db.execute("DELETE FROM management_balances WHERE student_id IN ('different','missing')")
        self.sha256 = fresh_start.file_sha256(self.source)
        review = self.complete_review()
        self.assertEqual(review["entries"], [])
        bundle = fresh_start.build_bundle(self.source, self.sha256, review)
        self.assertEqual(bundle["counts"]["opening_balances"], 1)
        self.assertEqual(bundle["opening_balances"][0]["units"], 900)
        self.assertEqual(bundle["opening_balances"][0]["source"], "matched_management_balance")
        for key in ("history_migrated", "live_apply_authorized"):
            self.assertIs(bundle[key], False)
        for key in ("archived_students_migrated", "historical_attendance_migrated",
                    "historical_requests_migrated", "password_hashes_migrated",
                    "sessions_migrated", "device_secrets_migrated"):
            self.assertEqual(bundle[key], 0)
        serialized = json.dumps(bundle)
        self.assertNotIn("old password hash", serialized)
        self.assertNotIn("old device secret", serialized)
        self.assertNotIn("old session secret", serialized)

    def test_discrepant_and_missing_balances_require_review(self):
        review = self.template()
        self.assertEqual([entry["student_id"] for entry in review["entries"]], ["different", "missing"])
        self.assertEqual(review["entries"][0]["student_remaining_units"], 800)
        self.assertEqual(review["entries"][0]["management_units"], 850)
        self.assertIsNone(review["entries"][1]["management_units"])
        self.assertFalse(all(entry["confirmed"] for entry in review["entries"]))
        with self.assertRaisesRegex(fresh_start.FreshStartError, "confirmed"):
            fresh_start.build_bundle(self.source, self.sha256, review)

    def test_complete_review_is_deterministic_and_preserves_master_data(self):
        review = self.complete_review()
        first = fresh_start.build_bundle(self.source, self.sha256, review)
        second = fresh_start.build_bundle(self.source, self.sha256, copy.deepcopy(review))
        self.assertEqual(fresh_start.bundle_bytes(first), fresh_start.bundle_bytes(second))
        self.assertEqual(first["counts"], dict(organizations=1, courses=1, classes=1,
            student_class_enrollments=3, class_schedule_templates=1, students=3,
            opening_balances=3, users=1, roles=1, teacher_classes=1, devices=1))
        sources = {row["student_id"]: row["source"] for row in first["opening_balances"]}
        self.assertEqual(sources, dict(matched="matched_management_balance",
                                       different="manual_review", missing="manual_review"))
        self.assertNotIn("remaining_units", first["students"][0])
        self.assertEqual(first["users"], [dict(id="user", org_id="org", login="fictional-teacher",
            display_name="虚构教师", active=True, created_at=100, password_reset_required=True)])
        self.assertNotIn("password_hash", first["users"][0])
        self.assertEqual(first["devices"], [dict(id="device", org_id="org", name="虚构设备",
            active=True, created_at=100, token_reissue_required=True)])
        self.assertNotIn("token_hash", first["devices"][0])

    def test_course_and_schedule_require_explicit_valid_confirmation(self):
        review = self.complete_review()
        review["course_entries"][0]["unit_price_cents"] = None
        with self.assertRaisesRegex(fresh_start.FreshStartError, "price"):
            fresh_start.build_bundle(self.source, self.sha256, review)
        review = self.complete_review()
        review["schedule_entries"][0]["teacher_id"] = "unknown"
        with self.assertRaisesRegex(fresh_start.FreshStartError, "assigned to its class"):
            fresh_start.build_bundle(self.source, self.sha256, review)
        review = self.template()
        self.assertFalse(review["course_entries"][0]["confirmed"])
        self.assertFalse(review["schedule_entries"][0]["confirmed"])
        with self.assertRaises(fresh_start.FreshStartError):
            fresh_start.build_bundle(self.source, self.sha256, review)

    def test_changed_source_and_invalid_review_sets_are_refused(self):
        wrong_hash = "0" * 64
        with self.assertRaisesRegex(fresh_start.FreshStartError, "checksum"):
            fresh_start.review_template(self.source, wrong_hash)
        complete = self.complete_review()
        other_source = dict(complete, source_sha256=wrong_hash)
        with self.assertRaisesRegex(fresh_start.FreshStartError, "different source"):
            fresh_start.build_bundle(self.source, self.sha256, other_source)
        missing = copy.deepcopy(complete)
        missing["entries"].pop()
        with self.assertRaisesRegex(fresh_start.FreshStartError, "Every balance discrepancy"):
            fresh_start.build_bundle(self.source, self.sha256, missing)
        repeated = copy.deepcopy(complete)
        repeated["entries"].append(copy.deepcopy(repeated["entries"][0]))
        with self.assertRaisesRegex(fresh_start.FreshStartError, "Repeated or unnecessary"):
            fresh_start.build_bundle(self.source, self.sha256, repeated)
        unnecessary = copy.deepcopy(complete)
        unnecessary["entries"][0]["student_id"] = "matched"
        with self.assertRaisesRegex(fresh_start.FreshStartError, "Repeated or unnecessary"):
            fresh_start.build_bundle(self.source, self.sha256, unnecessary)

    def test_private_output_is_never_overwritten(self):
        output = self.root / "review.private.json"
        fresh_start.write_private(output, b"first")
        with self.assertRaises(FileExistsError):
            fresh_start.write_private(output, b"second")
        self.assertEqual(output.read_bytes(), b"first")
        with self.assertRaisesRegex(fresh_start.FreshStartError, "absolute"):
            fresh_start.write_private("relative.json", b"refused")

    def test_invalid_confirmation_values_are_refused(self):
        for units, confirmed, reference in ((850.0, True, "review"), (850, 1, "review"),
                                             (850, True, " "), (10000001, True, "review")):
            with self.subTest(units=units, confirmed=confirmed, reference=reference):
                review = self.complete_review()
                review["entries"][0].update(opening_units=units, confirmed=confirmed, review_ref=reference)
                with self.assertRaises(fresh_start.FreshStartError):
                    fresh_start.build_bundle(self.source, self.sha256, review)


if __name__ == "__main__":
    unittest.main()
