"""Fictional fixture tests for archive isolation, preserved baselines and refusal paths."""

from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business.store import Store, uid
from business_next.api import create_app
from business_next.archive import initialize
from business_next.migration import fingerprint, prepare, roster, validate_bundle

PASSWORD = "Fictional roster test password 2026!"


def bundle():
    c = {"class_local_id": 11, "class_name": "虚构隔周班", "course_name": "虚构提高课程", "teacher_label": "待核对老师",
         "weekday": 6, "interval_weeks": 2, "anchor_date": "2026-10-31", "start_time": "23:30", "duration_minutes": 120,
         "end_time": "01:30", "end_day_offset": 1, "duration_source": "explicit", "teacher_identity_confirmed": False}
    students = [{"source_local_id": 1, "external_ref": "0001", "name": "虚构同名", "status": "active", "class_source_id": 11,
                 "current_class_name": c["class_name"], "previous_class_name": "", "remaining_units": -150, "balance_is_snapshot": True},
                {"source_local_id": 2, "external_ref": "0002", "name": "虚构同名", "status": "paused", "class_source_id": None,
                 "current_class_name": "", "previous_class_name": "", "remaining_units": 2300, "balance_is_snapshot": True},
                {"source_local_id": 3, "external_ref": "0003", "name": "虚构归档零余额", "status": "paused", "class_source_id": None,
                 "current_class_name": "", "previous_class_name": "已删除旧班", "remaining_units": 0, "balance_is_snapshot": True}]
    return {"format": "future-school-tablet-migration-preview", "schema_version": 1, "complete_snapshot": True,
            "preview_only": True, "ready_for_import": False, "organization_target": "school-fictional",
            "source": {"sha256": "a" * 64, "bytes": 1000, "as_of_date": "2026-10-27"}, "classes": [c], "students": students,
            "summary": {"classes": 1, "students": 3, "active": 1, "paused": 2, "active_remaining_units": -150, "paused_remaining_units": 2300}, "blockers": []}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "original.sqlite"
        self.output = self.root / "review.sqlite"
        self.store = Store.bootstrap(self.source, "school-fictional", "owner", "虚构管理员", PASSWORD)
        with self.store.transaction() as db:
            self.org = db.execute("SELECT id FROM organizations").fetchone()[0]
            self.original_class, self.original_student = uid(), uid()
            db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (self.original_class, self.org, "原测试班", "原测试课程", 1))
            db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", (self.original_student, self.org, self.original_class, "原测试学生", "TEST001", 1000))
            self.before = fingerprint(db)
            review = {"format": "wxnotify-roster-review", "schema_version": 1, "complete": True, "source_access": "read_only", **roster(db, "school-fictional")}
        self.bundle = bundle()
        self.bp, self.bh = self.write("bundle.json", self.bundle)
        self.rp, self.rh = self.write("review.json", review)

    def write(self, name, data):
        payload = (json.dumps(data, ensure_ascii=True) + "\n").encode()
        p = self.root / name
        p.write_bytes(payload)
        return p, hashlib.sha256(payload).hexdigest()

    def prepare(self):
        return prepare(self.source, self.output, self.bp, self.bh, self.rp, self.rh)

    def login(self, client, login="owner"):
        return client.post("/v1/auth/login", json={"organization": "school-fictional", "login": login, "password": PASSWORD}).json()["access_token"]

    def test_preserves_all_balances_statuses_original_and_no_side_effects(self):
        result = self.prepare()
        self.assertEqual((result["students"], result["active"], result["paused"]), (3, 1, 2))
        self.assertEqual((result["active_remaining_units"], result["paused_remaining_units"]), (-150, 2300))
        backup = self.output.with_name("review.before-import.sqlite")
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), result["pre_import_backup_sha256"])
        with closing(Store(backup).connect()) as db:
            self.assertEqual(fingerprint(db), self.before)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_schema WHERE name='roster_extension'").fetchone()[0], 0)
        with closing(Store(self.output).connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM students").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM archived_students").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT interval_weeks,duration_minutes,end_day_offset FROM class_schedule_templates").fetchone()[:], (2, 120, 1))
            for table in ("student_activity", "outbox", "attendance", "lessons", "teacher_classes"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT remaining_units FROM students WHERE id=?", (self.original_student,)).fetchone()[0], 1000)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with closing(self.store.connect()) as db:
            self.assertEqual(fingerprint(db), self.before)
        first = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(self.output.read_bytes(), first)

    def test_stale_server_wrong_tenant_or_checksum_stops_before_output(self):
        with self.store.transaction() as db:
            db.execute("UPDATE students SET remaining_units=900")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.bh = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare()

    def test_invalid_source_semantics_and_totals(self):
        for change in (lambda b: b["summary"].update(paused=0),
                       lambda b: b["students"][1].update(external_ref="0001"),
                       lambda b: b["students"][1].update(class_source_id=11),
                       lambda b: b["classes"][0].update(anchor_date="2026-11-01"),
                       lambda b: b["classes"][0].update(duration_minutes=90),
                       lambda b: b["students"][0].update(remaining_units=True),
                       lambda b: b["blockers"].append({"code": "DEVICE_SYNC_STATE_REVIEW_REQUIRED"})):
            bad = copy.deepcopy(self.bundle)
            change(bad)
            with self.assertRaises(ValueError):
                validate_bundle(bad)

    def test_cross_table_identity_guards_on_insert_and_update(self):
        self.prepare()
        with closing(Store(self.output).connect()) as db:
            archived = db.execute("SELECT * FROM archived_students WHERE external_ref='0002'").fetchone()
            for ref, key in (("0002", uid()), ("NEW", archived["id"])):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", (key, self.org, self.original_class, "虚构", ref, 0))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE students SET external_ref='0002' WHERE id=?", (self.original_student,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE archived_students SET external_ref='TEST001' WHERE id=?", (archived["id"],))

    def test_readonly_api_locks_all_writes_and_hides_archive_from_teacher(self):
        self.prepare()
        st = Store(self.output)
        with st.transaction() as db:
            owner_id = db.execute("SELECT id FROM users WHERE login='owner'").fetchone()[0]
            st.create_person(db, {"kind": "user", "org_id": self.org, "id": owner_id, "roles": {"admin"}}, {"login": "teacher", "display_name": "虚构老师", "password": PASSWORD, "roles": ["teacher"]})
        with TestClient(create_app(self.output, allow_test_client=True)) as client:
            admin = {"Authorization": "Bearer " + self.login(client)}
            teacher = {"Authorization": "Bearer " + self.login(client, "teacher")}
            self.assertEqual(client.get("/v1/admin/archives", headers=teacher).status_code, 403)
            self.assertEqual(client.get("/v1/admin/archives", headers=admin).json()["items"].__len__(), 2)
            self.assertTrue(client.get("/health/ready").json()["roster_review_only"])
            response = client.post("/v1/admin/students", headers=admin, json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(client.get("/v1/admin/roster-summary", headers=admin).json()["active_students"], 2)

    def test_restore_requires_admin_explicit_class_balance_and_is_atomic(self):
        self.prepare()
        st = Store(self.output)
        with closing(st.connect()) as db:
            archived = dict(db.execute("SELECT * FROM archived_students WHERE external_ref='0002'").fetchone())
        with TestClient(create_app(self.output, allow_test_client=True, review_only=False)) as client:
            admin = {"Authorization": "Bearer " + self.login(client)}
            target = "/v1/admin/archives/" + archived["id"] + "/restore"
            payload = {"class_id": self.original_class, "expected_remaining_units": 2300, "confirmed": True}
            self.assertEqual(client.post(target, headers=admin, json=payload | {"confirmed": False}).status_code, 422)
            self.assertEqual(client.post(target, headers=admin, json=payload | {"class_id": uid()}).status_code, 404)
            self.assertEqual(client.post(target, headers=admin, json=payload | {"expected_remaining_units": 0}).status_code, 409)
            with closing(st.connect()) as db:
                db.execute("CREATE TRIGGER test_fail_restore BEFORE INSERT ON students BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            self.assertEqual(client.post(target, headers=admin, json=payload).status_code, 409)
            with closing(st.connect()) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM archived_students WHERE id=?", (archived["id"],)).fetchone()[0], 1)
                db.execute("DROP TRIGGER test_fail_restore")
            self.assertEqual(client.post(target, headers=admin, json=payload).status_code, 200)
            self.assertEqual(client.post(target, headers=admin, json=payload).status_code, 404)
            with closing(st.connect()) as db:
                self.assertEqual(db.execute("SELECT class_id,remaining_units,active FROM students WHERE id=?", (archived["id"],)).fetchone()[:], (self.original_class, 2300, 1))
                self.assertEqual(db.execute("SELECT count(*) FROM student_activity").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
