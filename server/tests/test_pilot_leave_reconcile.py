import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ops/pilot-leave-reconcile.py"
spec = importlib.util.spec_from_file_location("leave_review", SOURCE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
CLASS, LESSON, STUDENT, OTHER, LEAVE = [s * 32 for s in "abcde"]
DEVICE = "9" * 32


class LeaveReviewTests(unittest.TestCase):
    def setUp(self):
        side = {"class_id": CLASS, "lesson_id": LESSON, "snapshot_sha256": "f" * 64,
                "identity_mapping_verified": True, "arrived_student_ids": [], "leaves": []}
        self.data = {"format": "future-school-pilot-leave-review-input", "schema_version": 1,
                     "class_id": CLASS, "lesson_id": LESSON, "student_ids": [STUDENT, OTHER], "device_id": DEVICE,
                     "tablet": copy.deepcopy(side), "server": copy.deepcopy(side)}

    def local(self, status="approved", ref=LEAVE):
        self.data["tablet"]["leaves"].append({"local_ref": "100", "server_leave_id": ref,
            "student_id": STUDENT, "class_id": CLASS, "lesson_id": LESSON, "status": status})

    def remote(self, status="approved", origin="device"):
        self.data["server"]["leaves"].append({"id": LEAVE, "student_id": STUDENT,
            "class_id": CLASS, "lesson_id": LESSON, "status": status, "origin": origin, "recorded_by": DEVICE if origin == "device" else "8" * 32})

    def codes(self):
        before = copy.deepcopy(self.data)
        result = policy.review(self.data)
        self.assertEqual(self.data, before)
        self.assertFalse(result["changes_applied"])
        self.assertFalse(result["events_generated"])
        self.assertFalse(result["activation_permitted"])
        self.assertFalse(result["notice_sending_enabled"])
        self.assertEqual(result["expected_students"], 2)
        return {r["code"] for r in result["issues"]}

    def test_matching_leave_and_cancellation_never_authorize_activation(self):
        self.local();self.remote();self.assertEqual(self.codes(), set())
        self.data["tablet"]["leaves"][0]["status"] = "cancelled"
        self.data["server"]["leaves"][0]["status"] = "cancelled"
        self.assertEqual(self.codes(), set())

    def test_web_leave_is_missing_on_tablet_and_not_silently_adopted(self):
        self.remote(origin="teacher")
        self.assertEqual(self.codes(), {"server_teacher_leave_missing_tablet"})

    def test_local_preexisting_leave_is_not_replayed(self):
        self.local(ref=None)
        self.assertEqual(self.codes(), {"tablet_leave_not_on_server"})

    def test_independent_leave_records_are_not_merged_by_student(self):
        self.local(ref=None);self.remote(origin="teacher")
        self.assertEqual(self.codes(), {"tablet_leave_not_on_server", "server_teacher_leave_missing_tablet", "independent_active_leaves"})

    def test_web_cancellation_does_not_silently_cancel_local_leave(self):
        self.local();self.remote(status="cancelled")
        self.assertEqual(self.codes(), {"server_cancellation_missing_tablet"})

    def test_offline_local_cancellation_does_not_overwrite_server(self):
        self.local(status="cancelled");self.remote()
        self.assertEqual(self.codes(), {"tablet_cancellation_missing_server"})

    def test_attendance_vs_other_side_leave_is_explicit_conflict(self):
        self.data["tablet"]["arrived_student_ids"] = [STUDENT];self.remote(origin="teacher")
        self.assertEqual(self.codes(), {"tablet_attendance_not_mirrored", "server_teacher_leave_missing_tablet", "attendance_leave_conflict"})
        self.data["tablet"]["arrived_student_ids"] = [];self.data["server"]["arrived_student_ids"] = [STUDENT]
        self.data["server"]["leaves"] = [];self.local(ref=None)
        self.assertIn("attendance_leave_conflict", self.codes())

    def test_matching_attendance_does_not_change_hours_or_create_events(self):
        self.data["tablet"]["arrived_student_ids"] = [OTHER]
        self.data["server"]["arrived_student_ids"] = [OTHER]
        self.assertEqual(self.codes(), set())

    def test_missing_receipt_and_wrong_origin_need_review(self):
        self.local();self.assertEqual(self.codes(), {"acknowledged_leave_missing_server"})
        self.remote(origin="teacher");self.assertEqual(self.codes(), {"receipt_origin_mismatch"})

    def test_unknown_or_foreign_device_receipt_cannot_match(self):
        self.local();self.remote();self.data["device_id"] = None
        self.assertEqual(self.codes(), {"device_identity_unverified"})
        self.data["device_id"] = "7" * 32
        self.assertEqual(self.codes(), {"foreign_device_leave"})

    def test_multiple_active_leaves_are_not_deduplicated(self):
        self.remote();second = copy.deepcopy(self.data["server"]["leaves"][0]);second["id"] = "f" * 32
        self.data["server"]["leaves"].append(second)
        self.assertIn("multiple_active_leaves", self.codes())

    def test_bad_identity_scope_or_receipt_owner_is_rejected(self):
        original = copy.deepcopy(self.data)
        for mutate in [lambda d:d["tablet"].update(identity_mapping_verified=False),
                       lambda d:d["server"].update(lesson_id="f" * 32),
                       lambda d:d.update(student_ids=[STUDENT, STUDENT]),
                       lambda d:d["tablet"].update(arrived_student_ids=["f" * 32]),
                       lambda d:d["server"].update(snapshot_sha256="missing")]:
            self.data = copy.deepcopy(original);mutate(self.data)
            with self.assertRaises(ValueError):policy.review(self.data)
        self.data = original;self.local();self.remote();self.data["server"]["leaves"][0]["student_id"] = OTHER
        with self.assertRaises(ValueError):policy.review(self.data)

    def test_cli_is_read_only_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root / "fictional-input.json";output = root / "report.json"
            source.write_text(json.dumps(self.data), encoding="utf-8");before = source.read_bytes()
            args = [sys.executable, str(SOURCE), "--input", str(source), "--output", str(output)]
            run = subprocess.run(args, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse(json.loads(run.stdout)["activation_permitted"])
            report = output.read_bytes();self.assertEqual(source.read_bytes(), before)
            self.assertNotEqual(subprocess.run(args, capture_output=True, timeout=10).returncode, 0)
            self.assertEqual(output.read_bytes(), report)


if __name__ == "__main__":unittest.main()
