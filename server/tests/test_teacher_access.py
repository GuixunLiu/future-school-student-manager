"""Fictional owner approval and teacher access; never connect to a real service."""

from contextlib import closing
import copy
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

from business.store import Store, uid, Problem
from business.security import password_hash, verify_password
from business_next.archive import initialize
from business_next.migration import digest

spec = importlib.util.spec_from_file_location("teacher_access", Path(__file__).resolve().parents[1] / "ops/teacher-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
PASSWORD = "Fictional owner and teacher password 2026!"


class TeacherAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "review.sqlite"
        self.backup = self.root / "before-access.sqlite"
        self.store = Store.bootstrap(self.path, "school-fictional", "owner", "旧管理员", PASSWORD)
        initialize(self.path)
        self.owner_label, self.teacher_label = "虚构甲老师", "虚构乙老师"
        self.hash = password_hash(PASSWORD)
        self.plan = {"format": "future-school-confirmed-teacher-access", "schema_version": 1, "confirmed_by_owner": True,
                     "organization": "school-fictional", "source_preview_sha256": "a" * 64, "owner_login": "owner",
                     "expected": {"teachers": 2, "classes": 2}, "teachers": [
                         {"name": self.owner_label, "login": "owner", "reuse_owner": True, "roles": ["admin", "teacher"],
                          "classes": [{"source_class_id": 1, "class_name": "虚构甲班", "course_name": "虚构课程"}]},
                         {"name": self.teacher_label, "login": "teacher-two", "reuse_owner": False, "roles": ["teacher"], "password_hash": self.hash,
                          "classes": [{"source_class_id": 2, "class_name": "虚构乙班", "course_name": "虚构课程"}]}]}
        with self.store.transaction() as db:
            self.org = db.execute("SELECT id FROM organizations").fetchone()[0]
            self.owner = db.execute("SELECT * FROM users").fetchone()
            db.execute("INSERT INTO roster_imports VALUES(?,?,?,?,?,?,?)", (self.org, "a" * 64, "b" * 64, "2026-10-27", 0, "c" * 64, "{}"))
            self.ids = []
            for i, label in ((1, self.owner_label), (2, self.teacher_label)):
                key = uid()
                self.ids.append(key)
                db.execute("INSERT INTO classes VALUES(?,?,?,?,0)", (key, self.org, "虚构" + ("甲" if i == 1 else "乙") + "班", "虚构课程"))
                db.execute("INSERT INTO roster_source_links VALUES(?,?,?,?,?)", (self.org, "class", i, key, "a" * 64))
                db.execute("INSERT INTO class_schedule_templates VALUES(?,?,6,1,'','10:00','11:30',90,0,'2026-10-27',?)", (self.org, key, label))
                db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", (uid(), self.org, key, "虚构学生"+str(i), "FIX"+str(i), 1000*i))
            db.execute("INSERT INTO archived_students VALUES(?,?,?,?,?,?,0)", (uid(), self.org, "虚构归档", "ARCH", 2300, ""))
            db.execute("INSERT INTO notification_preferences VALUES(?,?,'weekly_admin',1,0)", (self.org, self.owner["id"]))

    def apply(self):
        return access.apply_access(self.path, self.plan, digest(self.plan), self.backup)

    def test_retains_owner_password_assigns_scoped_teacher_and_replays_without_reset(self):
        token = self.store.login("school-fictional", "owner", PASSWORD, "fictional")["access_token"]
        result = self.apply()
        self.assertEqual((result["teachers"], result["new_accounts"], result["authorized_classes"]), (2, 1, 2))
        with self.assertRaises(Problem):
            self.store.command(token, self.store.me)
        with closing(self.store.connect()) as db:
            owner = db.execute("SELECT * FROM users WHERE login='owner'").fetchone()
            teacher = db.execute("SELECT * FROM users WHERE login='teacher-two'").fetchone()
            self.assertEqual(owner["id"], self.owner["id"])
            self.assertEqual(owner["password_hash"], self.owner["password_hash"])
            self.assertEqual(owner["display_name"], self.owner_label)
            self.assertTrue(verify_password(PASSWORD, teacher["password_hash"]))
            self.assertEqual([r[0] for r in db.execute("SELECT role FROM roles WHERE user_id=?", (teacher["id"],))], ["teacher"])
            self.assertEqual(db.execute("SELECT count(*) FROM student_activity").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM archived_students").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT sum(remaining_units) FROM students").fetchone()[0], 3000)
        with closing(Store(self.backup).connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM teacher_classes").fetchone()[0], 0)
        new = self.store.login("school-fictional", "teacher-two", PASSWORD, "fixture-two")["access_token"]
        self.assertEqual(self.store.command(new, lambda db, a: self.store.class_ids(db, a)), [self.ids[1]])
        self.assertEqual(len(self.store.command(new, self.store.students)["items"]), 1)
        again = self.apply()
        self.assertTrue(again["replayed"])
        self.assertEqual(self.store.command(new, self.store.me)["roles"], ["teacher"])

    def test_label_drift_and_existing_account_refuse_without_permission_changes(self):
        with self.store.transaction() as db:
            db.execute("UPDATE class_schedule_templates SET teacher_label='已变更' WHERE class_id=?", (self.ids[1],))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.apply()
        self.assertFalse(self.backup.exists())
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM teacher_classes").fetchone()[0], 0)

    def test_failure_after_owner_change_rolls_back_everything(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_teacher BEFORE INSERT ON users WHEN NEW.login='teacher-two' BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.apply()
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("SELECT display_name FROM users WHERE login='owner'").fetchone()[0], "旧管理员")
            self.assertEqual(db.execute("SELECT count(*) FROM roles").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM teacher_classes").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM notification_preferences").fetchone()[0], 1)
        self.assertTrue(self.backup.exists())

    def test_confirmation_privilege_duplicate_and_password_reset_requests_rejected(self):
        for change in (lambda p: p.update(confirmed_by_owner=False),
                       lambda p: p["teachers"][1].update(roles=["admin", "teacher"]),
                       lambda p: p["teachers"][0].update(password_hash=self.hash),
                       lambda p: p["teachers"][1].update(login="owner"),
                       lambda p: p["teachers"][1]["classes"][0].update(source_class_id=1)):
            p = copy.deepcopy(self.plan)
            change(p)
            with self.assertRaises(ValueError):
                access.validate(p)

    def test_no_overwrite_existing_teacher_or_partial_mapping(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,1,0)", (uid(), self.org, "teacher-two", self.teacher_label, self.hash))
        with self.assertRaisesRegex(ValueError, "Existing"):
            self.apply()
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
