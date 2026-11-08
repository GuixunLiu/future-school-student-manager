import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pilot_stage", ROOT / "ops/stage-single-class-pilot.py")
stage = importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
ORG, OWNER, CLASS, STUDENT, DEVICE = [str(n) * 32 for n in range(1, 6)]
NOW = 1793156400


class SingleClassStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.source = self.root / "source.sqlite";self.output = self.root / "new-offline-stage"
        db = sqlite3.connect(self.source)
        db.executescript((ROOT / "business/schema.sql").read_text(encoding="utf-8") + (ROOT / "business_next/schema.sql").read_text(encoding="utf-8"))
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO organizations VALUES(?,?,?)", (ORG, "fictional", 1))
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)", (OWNER, ORG, "fictional-teacher", "虚构教师", "fictional-hash-not-a-password", 1, 1))
        for role in ("admin", "teacher"):
            db.execute("INSERT INTO roles VALUES(?,?,?)", (ORG, OWNER, role))
        db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (CLASS, ORG, "虚构提高班", "虚构课程", 1))
        db.execute("INSERT INTO teacher_classes VALUES(?,?,?)", (ORG, OWNER, CLASS))
        db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", (STUDENT, ORG, CLASS, "虚构学生", "TEST001", -150))
        db.execute("INSERT INTO roster_imports VALUES(?,?,?,?,?,?,?)", (ORG, "a" * 64, "b" * 64, "2026-10-28", 1, "c" * 64, "{}"))
        for kind, sid, entity in (("class", 11, CLASS), ("student", 22, STUDENT)):
            db.execute("INSERT INTO roster_source_links VALUES(?,?,?,?,?)", (ORG, kind, sid, entity, "a" * 64))
        db.execute("INSERT INTO class_schedule_templates VALUES(?,?,?,?,?,?,?,?,?,?,?)", (ORG, CLASS, 6, 2, "2026-10-31", "14:00", "16:00", 120, 0, "2026-10-28", "虚构教师"))
        db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", ("fictional-session", ORG, OWNER, 2000000000, 1))
        db.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?)", (DEVICE, ORG, "虚构测试设备", "fictional-device-digest", 1, 2000000000, 1))
        db.execute("INSERT INTO device_classes VALUES(?,?,?)", (ORG, DEVICE, CLASS))
        db.execute("INSERT INTO notification_preferences VALUES(?,?,?,?,?)", (ORG, OWNER, "weekly", 1, 1))
        db.execute("INSERT INTO notification_schedule VALUES(?,?,?,?,?,?,?,?)", (ORG, 1, 1, 7, 21, 0, 0, 300))
        db.execute("INSERT INTO outbox(id,org_id,notice_type,origin_id,recipient_id,payload,status,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)", ("6" * 32, ORG, "weekly", "fictional-origin", OWNER, "{}", "pending_simulation", 2000000000, 1))
        db.commit();db.close()
        self.plan = {"format": "future-school-owner-confirmed-pilot-lesson", "schema_version": 1, "organization": "fictional", "owner_confirmed": True,
                     "server_class_id": CLASS, "class_source_id": 11, "class_name": "虚构提高班", "teacher_label": "虚构教师",
                     "starts_at": "2026-10-31T14:00:00+08:00", "ends_at": "2026-10-31T16:00:00+08:00", "timezone": "Asia/Shanghai", "duration_minutes": 120,
                     "expected_students": 1, "members": [{"source_local_id": 22, "external_ref": "TEST001", "server_student_id": STUDENT, "expected_remaining_units": -150}],
                     "leave_policy": {"included_in_expected": True, "counts_as_arrived": False, "charges_units": False},
                     "plan_only": True, "ready_to_enable": False, "server_written": False, "lesson_created": False, "device_enabled": False, "wechat_enabled": False}

    def prepare(self, plan=None):
        plan = self.plan if plan is None else plan
        return stage.prepare(self.source.resolve(), self.output.resolve(), plan, hashlib.sha256(json.dumps(plan).encode()).hexdigest(), now=NOW)

    def test_consistent_backup_preserved_accounts_balances_and_isolated_candidate(self):
        before = stage.checksum(self.source);result = self.prepare()
        self.assertEqual(stage.checksum(self.source), before)
        self.assertEqual((result["selected_students"], result["selected_remaining_units"]), (1, -150))
        for key in ("service_started", "lesson_created", "device_enabled", "wechat_enabled", "history_replayed", "existing_services_modified"):
            self.assertFalse(result[key])
        for file in (self.source, self.output / "baseline-backup.sqlite", self.output / "pilot-offline.sqlite"):
            db = sqlite3.connect(file)
            self.assertEqual(db.execute("SELECT password_hash FROM users").fetchone()[0], "fictional-hash-not-a-password")
            self.assertEqual(db.execute("SELECT remaining_units FROM students").fetchone()[0], -150)
            self.assertEqual(db.execute("SELECT count(*) FROM teacher_classes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM lessons").fetchone()[0], 0)
            candidate = file.name == "pilot-offline.sqlite"
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0 if candidate else 1)
            self.assertEqual(db.execute("SELECT active FROM devices").fetchone()[0], 0 if candidate else 1)
            self.assertEqual(db.execute("SELECT enabled FROM notification_schedule").fetchone()[0], 0 if candidate else 1)
            self.assertEqual(db.execute("SELECT status FROM outbox").fetchone()[0], "cancelled" if candidate else "pending_simulation")
            db.close()
        self.assertEqual(stage.checksum(self.output / "baseline-backup.sqlite"), result["baseline_backup_sha256"])
        receipt = json.loads((self.output / "stage-receipt.json").read_text())
        self.assertEqual(receipt, result)
        self.assertNotIn("fictional-hash", json.dumps(receipt))

    def test_balance_change_refuses_without_candidate_or_source_write(self):
        self.plan["members"][0]["expected_remaining_units"] = 0
        before = stage.checksum(self.source)
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(stage.checksum(self.source), before)
        self.assertTrue((self.output / "baseline-backup.sqlite").exists())
        self.assertFalse((self.output / "pilot-offline.sqlite").exists())

    def test_identity_or_teacher_change_is_not_guessed(self):
        self.plan["teacher_label"] = "另一位虚构教师"
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.output / "stage-receipt.json").exists())

    def test_duplicate_mapping_and_past_lesson_fail_before_output(self):
        plan = copy.deepcopy(self.plan);plan["members"] *= 2;plan["expected_students"] = 2
        with self.assertRaises(ValueError):self.prepare(plan)
        plan = copy.deepcopy(self.plan);plan["starts_at"] = "2026-10-24T14:00:00+08:00";plan["ends_at"] = "2026-10-24T16:00:00+08:00"
        with self.assertRaises(ValueError):self.prepare(plan)
        self.assertFalse(self.output.exists())

    def test_off_week_is_not_created_as_a_lesson(self):
        self.plan["starts_at"] = "2026-11-07T14:00:00+08:00";self.plan["ends_at"] = "2026-11-07T16:00:00+08:00"
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.output / "stage-receipt.json").exists())

    def test_extra_active_member_refuses_no_automatic_removal(self):
        db = sqlite3.connect(self.source);db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", ("7" * 32, ORG, CLASS, "虚构新增学生", "TEST002", 500));db.commit();db.close()
        with self.assertRaises(ValueError):self.prepare()
        db = sqlite3.connect(self.source);self.assertEqual(db.execute("SELECT count(*) FROM students").fetchone()[0], 2);db.close()

    def test_repeated_stage_never_overwrites(self):
        self.prepare();before = stage.checksum(self.output / "pilot-offline.sqlite")
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(stage.checksum(self.output / "pilot-offline.sqlite"), before)

    def test_existing_sync_binding_is_not_silently_reset(self):
        db = sqlite3.connect(self.source);db.execute("UPDATE students SET source_device_id=?,source_revision=1", (DEVICE,));db.commit();db.close()
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.output / "pilot-offline.sqlite").exists())

    def test_selected_class_with_existing_server_lesson_requires_review(self):
        db = sqlite3.connect(self.source);db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?)", ("8" * 32, ORG, CLASS, OWNER, 1793407200, 1793414400, 1));db.commit();db.close()
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.output / "pilot-offline.sqlite").exists())

    def test_guard_failure_rolls_back_candidate_and_cannot_mark_complete(self):
        before = stage.checksum(self.source)
        with patch.object(stage, "preserved_digest", side_effect=["original", "different"]):
            with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(stage.checksum(self.source), before)
        self.assertFalse((self.output / "stage-receipt.json").exists())
        db = sqlite3.connect(self.output / "pilot-offline.sqlite");self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1);db.close()


if __name__ == "__main__":unittest.main()
