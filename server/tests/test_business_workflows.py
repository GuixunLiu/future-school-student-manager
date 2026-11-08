"""End-to-end fictional school scenarios; no real students, servers or messages."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_business_identity as fixtures
from business.workflows import SCHOOL_TZ


class WorkflowTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.IdentityTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.IdentityTests.tearDownClass.__func__)
    setUp = fixtures.IdentityTests.setUp
    req = fixtures.IdentityTests.req

    def call(self, method, path, token=None, data=None, status=200):
        response = self.req(method, path, token or self.owner, **({"json": data} if data is not None else {}))
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def move(self, timestamp):
        self.now = int(timestamp)
        # Keep fixture sessions valid when testing weeks/months; expiry has dedicated identity tests.
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE sessions SET expires_at=?", (self.now + 3600,))
            db.execute("UPDATE devices SET expires_at=?", (self.now + 3600,))

    @staticmethod
    def iso(timestamp):
        return datetime.fromtimestamp(timestamp, SCHOOL_TZ).isoformat()

    def lesson(self, offset=600, students=None, class_id=None, teacher=None):
        start = self.clock + offset
        return self.call("POST", "/v1/admin/lessons", data={"class_id": class_id or self.class_a, "teacher_id": teacher or self.ids["teacher-only"], "starts_at": self.iso(start), "ends_at": self.iso(start + 3600), "student_ids": students or [self.student_a]}, status=201)

    def event(self, lesson, ref="event-1", student=None, **changes):
        return {"event_ref": ref, "lesson_id": lesson["id"], "student_id": student or self.student_a, "occurred_at": self.iso(lesson["starts_at"] + 60), "consumed_units": 150, "remaining_units": 1850, "student_revision": 1, "source": "live"} | changes

    def opt(self, token, **preferences):
        self.call("PUT", "/v1/me/notification-preferences", token, {"preferences": preferences})

    def notices(self, token=None):
        return self.call("GET", "/v1/me/notifications?limit=100", token or self.tokens["parent-only"])["items"]

    def configure(self, **changes):
        self.call("PUT", "/v1/admin/notification-schedule", data={"enabled": True} | changes)

    def test_snapshot_leave_denominator_and_no_deduction(self):
        lesson = self.lesson()
        later = self.call("POST", "/v1/admin/students", data={"class_id": self.class_a, "name": "虚构后来学生", "external_ref": "late-enrollment", "remaining_units": 900}, status=201)
        leave = self.call("POST", f"/v1/lessons/{lesson['id']}/leaves", self.tokens["teacher-only"], {"student_id": self.student_a, "reason": "虚构测试事假"}, status=201)
        detail = self.call("GET", f"/v1/lessons/{lesson['id']}")
        self.assertEqual((detail["expected"], detail["arrived"], detail["leave_count"], detail["rate_percent"], detail["unexcused"]), (1, 0, 1, 0, []))
        self.assertFalse(leave["hours_deducted"])
        self.assertEqual(self.call("GET", f"/v1/students/{self.student_a}")["remaining_units"], 2000)
        self.move(lesson["starts_at"] + 100)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson), status=409)
        self.call("POST", f"/v1/leaves/{leave['id']}/cancel", self.tokens["teacher-only"], {"reason": "虚构撤假"})
        self.assertEqual(self.call("GET", f"/v1/lessons/{lesson['id']}")["not_arrived_total"], 1)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson, student=later["id"]), status=409)

    def test_device_retry_atomic_balance_not_double_charge(self):
        self.opt(self.tokens["parent-only"], attendance=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        event = self.event(lesson)
        first = self.call("POST", "/v1/device/attendance", self.device["device_token"], event)
        second = self.call("POST", "/v1/device/attendance", self.device["device_token"], event)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])
        self.assertFalse(first["hours_deducted_by_server"])
        self.assertEqual(self.call("GET", f"/v1/students/{self.student_a}")["remaining_units"], 1850)
        self.assertEqual(len(self.notices()), 1)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], event | {"remaining_units": 1700}, status=409)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], event | {"event_ref": "different-id"}, status=409)

    def test_concurrent_retries_one_event(self):
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        def send(_):
            return self.req("POST", "/v1/device/attendance", self.device["device_token"], json=self.event(lesson))
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(send, range(4)))
        self.assertEqual([r.status_code for r in replies], [200] * 4)
        self.assertEqual(sum(not r.json()["duplicate"] for r in replies), 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attendance").fetchone()[0], 1)

    def test_audit_failure_rolls_back_attendance_balance_and_outbox(self):
        self.opt(self.tokens["parent-only"], attendance=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        with patch.object(self.app.state.store, "audit", side_effect=RuntimeError("fictional rollback fault")):
            with self.assertRaises(RuntimeError):
                self.req("POST", "/v1/device/attendance", self.device["device_token"], json=self.event(lesson))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual([db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("attendance", "inbound_events", "outbox")], [0, 0, 0])
        self.assertEqual(self.call("GET", f"/v1/students/{self.student_a}")["remaining_units"], 2000)

    def test_scope_revoked_device_retry_is_rejected(self):
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        self.call("PUT", f"/v1/admin/devices/{self.device['id']}/access", data={"active": True, "class_ids": []})
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson), status=403)
        self.call("POST", "/v1/device/balance", self.tokens["teacher-only"], {"event_ref": "b", "student_id": self.student_a, "occurred_at": self.iso(self.now), "remaining_units": 100, "student_revision": 2, "source": "live"}, status=403)

    def test_stale_revision_and_other_device_cannot_overwrite(self):
        event = {"event_ref": "new-balance", "student_id": self.student_a, "occurred_at": self.iso(self.now), "remaining_units": 3000, "student_revision": 4, "source": "live"}
        self.call("POST", "/v1/device/balance", self.device["device_token"], event)
        old = self.call("POST", "/v1/device/balance", self.device["device_token"], event | {"event_ref": "older", "student_revision": 3, "remaining_units": -50})
        self.assertFalse(old["balance_applied"])
        device = self.call("POST", "/v1/admin/devices", data={"name": "虚构另一设备", "class_ids": [self.class_a]}, status=201)
        self.call("POST", "/v1/device/balance", device["device_token"], event, status=409)
        self.assertEqual(self.call("GET", f"/v1/students/{self.student_a}")["remaining_units"], 3000)

    def test_low_hours_episode_replenishment_and_consent(self):
        self.opt(self.tokens["parent-only"], low_hours_parent=True)
        self.opt(self.tokens["teacher-only"], low_hours_teacher=True)
        event = {"event_ref": "low-1", "student_id": self.student_a, "occurred_at": self.iso(self.now), "remaining_units": 1500, "student_revision": 1, "source": "live"}
        first = self.call("POST", "/v1/device/balance", self.device["device_token"], event)
        self.assertEqual(first["simulation_notices_queued"], 2)
        self.assertEqual(self.call("POST", "/v1/device/balance", self.device["device_token"], event | {"event_ref": "low-2", "student_revision": 2})["simulation_notices_queued"], 0)
        self.call("POST", "/v1/device/balance", self.device["device_token"], event | {"event_ref": "topup", "remaining_units": 3000, "student_revision": 3})
        self.assertEqual(self.notices()[0]["status"], "cancelled")
        again = self.call("POST", "/v1/device/balance", self.device["device_token"], event | {"event_ref": "low-again", "student_revision": 4})
        self.assertEqual(again["simulation_notices_queued"], 2)

    def test_historical_makeup_expired_and_late_consent_do_not_notify(self):
        self.opt(self.tokens["parent-only"], attendance=True, low_hours_parent=True)
        for index, source in enumerate(("historical", "makeup")):
            with self.subTest(source=source):
                lesson = self.lesson(offset=600 + index * 4000)
                self.move(lesson["starts_at"] + 100)
                event = self.event(lesson, ref=source, source=source, remaining_units=1000, student_revision=index + 1)
                self.assertEqual(self.call("POST", "/v1/device/attendance", self.device["device_token"], event)["simulation_notices_queued"], 0)
        self.opt(self.tokens["parent-only"], attendance=True, low_hours_parent=True)
        second = self.lesson(offset=9000)
        self.move(second["starts_at"] + 100)
        self.opt(self.tokens["parent-only"], attendance=True)
        self.assertEqual(self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(second, "late-opt", student_revision=3))["simulation_notices_queued"], 0)
        third = self.lesson(offset=14000)
        self.move(third["starts_at"] + 86500)
        self.assertEqual(self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(third, "expired", student_revision=4))["simulation_notices_queued"], 0)

    def test_revoke_cancels_unsent_and_can_record_corrected_checkin(self):
        self.opt(self.tokens["parent-only"], attendance=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        result = self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        revoke = self.call("POST", f"/v1/device/attendance/{result['id']}/revoke", self.device["device_token"], {"event_ref": "revoke-1", "reason": "虚构误签到"})
        self.assertFalse(revoke["automatic_refund"])
        self.assertEqual(self.notices()[0]["status"], "cancelled")
        self.assertEqual(self.call("GET", f"/v1/lessons/{lesson['id']}")["arrived"], 0)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson, "corrected", student_revision=2))
        self.assertEqual(self.call("GET", f"/v1/lessons/{lesson['id']}")["arrived"], 1)

    def test_absence_teacher_at_five_minutes_parent_requires_confirmation(self):
        self.configure()
        self.opt(self.tokens["teacher-only"], absence_teacher=True)
        self.opt(self.tokens["parent-only"], absence_parent=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 299)
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.call("POST", f"/v1/lessons/{lesson['id']}/absence-notices", self.tokens["teacher-only"], {"student_ids": [self.student_a]}, status=409)
        self.move(self.now + 1)
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 1)
        self.assertEqual(self.notices(), [])
        self.assertEqual(self.call("POST", f"/v1/lessons/{lesson['id']}/absence-notices", self.tokens["teacher-only"], {"student_ids": [self.student_a]})["simulation_notices_queued"], 1)
        self.call("POST", f"/v1/lessons/{lesson['id']}/leaves", self.tokens["teacher-only"], {"student_id": self.student_a, "reason": "虚构请假"}, status=201)
        result = self.call("POST", "/v1/test/simulate")
        self.assertEqual((result["cancelled"], result["simulated"]), (2, 0))

    def test_content_draft_after_class_arrived_audience_and_lock(self):
        self.configure()
        self.opt(self.tokens["parent-only"], lesson_content=True)
        lesson = self.lesson()
        path = f"/v1/lessons/{lesson['id']}/content"
        self.call("PUT", path, self.tokens["teacher-only"], {"body": "虚构课程：条件判断\n练习一", "publish_after_class": True})
        self.call("GET", path, self.tokens["parent-only"], status=403)
        self.move(lesson["starts_at"] + 100)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.move(lesson["ends_at"])
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 1)
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.assertTrue(self.call("GET", path, self.tokens["parent-only"])["published_in_test"])
        self.call("PUT", path, self.tokens["teacher-only"], {"body": "不能覆盖的更正"}, status=409)
        self.call("DELETE", "/v1/test/parent-links", data={"parent_id": self.ids["parent-only"], "student_id": self.student_a})
        self.assertEqual(self.notices(), [])
        self.assertEqual(self.call("POST", "/v1/test/simulate")["cancelled"], 0)
        self.call("GET", path, self.tokens["parent-only"], status=403)

    def test_weekly_weighted_and_current_teacher_scope(self):
        students = [self.student_a]
        for i in range(9):
            students.append(self.call("POST", "/v1/admin/students", data={"class_id": self.class_a, "name": f"虚构学生{i}", "external_ref": f"weighted-{i}", "remaining_units": 2000}, status=201)["id"])
        lesson = self.lesson(students=students)
        second = self.lesson(offset=5000, students=students[:2])
        self.move(second["ends_at"])
        for i, student in enumerate(students[:8]):
            self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson, f"weighted-{i}", student=student))
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(second, "weighted-second", student_revision=2))
        for student in students[8:]:
            self.call("POST", f"/v1/lessons/{lesson['id']}/leaves", data={"student_id": student, "reason": "虚构事假"}, status=201)
        local = datetime.fromtimestamp(lesson["starts_at"], SCHOOL_TZ).date()
        monday = (local - timedelta(days=local.weekday())).isoformat()
        report = self.call("GET", "/v1/reports/weekly?week_start=" + monday)
        self.assertEqual((report["expected"], report["arrived"], report["rate_percent"], report["leave_count"]), (12, 9, 75, 2))
        self.assertEqual(len(report["sessions"][0]["unexcused"]), 0)
        self.call("GET", "/v1/reports/weekly?week_start=" + monday, self.tokens["teacher-only"], status=403)
        self.assertEqual(self.call("GET", "/v1/me/reports/weekly?week_start=" + monday, self.tokens["teacher-only"])["expected"], 12)
        self.call("PUT", f"/v1/admin/classes/{self.class_a}/teachers", data={"teacher_ids": []})
        self.assertEqual(self.call("GET", "/v1/me/reports/weekly?week_start=" + monday, self.tokens["teacher-only"])["expected"], 0)

    def test_monthly_unique_students_events_boundaries_no_balance_mutation(self):
        self.move(datetime(2026, 11, 1, 0, 10, tzinfo=SCHOOL_TZ).timestamp())
        for ref, kind, time in (("new", "new", "2026-10-01T00:00:00+08:00"), ("r1", "renewal", "2026-10-31T23:59:59+08:00"), ("r2", "renewal", "2026-10-31T12:00:00+08:00"), ("next-month", "renewal", "2026-11-01T00:00:00+08:00")):
            data = {"student_id": self.student_a, "source_ref": ref, "kind": kind, "occurred_at": time}
            self.call("POST", "/v1/admin/student-activity", data=data, status=201)
            self.assertTrue(self.call("POST", "/v1/admin/student-activity", data=data, status=201)["duplicate"])
        report = self.call("GET", "/v1/reports/monthly?month=2026-10")
        self.assertEqual((report["new_students"], report["renewal_students"], report["renewal_events"], report["final_period"]), (1, 1, 2, True))
        self.assertEqual(self.call("GET", f"/v1/students/{self.student_a}")["remaining_units"], 2000)
        self.call("GET", "/v1/reports/monthly?month=2026-10", self.tokens["teacher-only"], status=403)
        self.call("GET", "/v1/reports/monthly?month=2026-13", status=422)

    def test_jobs_default_off_no_historical_replay_or_early_weekly(self):
        self.assertFalse(self.call("POST", "/v1/admin/jobs/tick")["enabled"])
        self.opt(self.owner, weekly_admin=True)
        self.opt(self.tokens["teacher-only"], weekly_teacher=True)
        local = datetime.fromtimestamp(self.clock, SCHOOL_TZ)
        cutoff = local + timedelta(minutes=15)
        self.configure(weekly_weekday=cutoff.weekday(), weekly_hour=cutoff.hour, weekly_minute=cutoff.minute)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 500)
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.move(lesson["ends_at"])
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 2)
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.assertEqual({r["notice_type"] for r in self.notices(self.owner)}, {"weekly_admin"})

    def test_cross_tenant_parent_teacher_and_invalid_time_denied(self):
        lesson = self.lesson()
        self.call("GET", f"/v1/lessons/{lesson['id']}", self.other_owner, status=404)
        self.call("GET", f"/v1/lessons/{lesson['id']}", self.tokens["parent-only"], status=403)
        self.call("POST", f"/v1/lessons/{lesson['id']}/leaves", self.tokens["teacher-admin"], {"student_id": self.student_b, "reason": "虚构"}, status=409)
        self.call("PUT", "/v1/me/notification-preferences", self.tokens["parent-only"], {"preferences": {"weekly_admin": True}}, status=403)
        event = self.event(lesson)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], event | {"occurred_at": "2026-10-24T10:00:00"}, status=422)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], event | {"occurred_at": self.iso(self.now + 86400)}, status=422)

    def test_dual_identity_weekly_distinct_messages_and_monthly_not_early(self):
        teacher = self.tokens["teacher-admin"]
        self.opt(teacher, weekly_teacher=True, weekly_admin=True, monthly_admin=True)
        local = datetime.fromtimestamp(self.clock, SCHOOL_TZ)
        self.configure(weekly_weekday=local.weekday(), weekly_hour=local.hour, weekly_minute=local.minute)
        lesson = self.lesson(class_id=self.class_b, teacher=self.ids["teacher-admin"], students=[self.student_b])
        self.move(lesson["ends_at"])
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 2)
        self.assertEqual({r["notice_type"] for r in self.notices(teacher)}, {"weekly_admin", "weekly_teacher"})
        self.assertFalse(self.call("GET", "/v1/reports/monthly?month=2026-10")["final_period"])
        self.move(datetime(2026, 11, 1, tzinfo=SCHOOL_TZ).timestamp())
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 1)
        monthly = [r for r in self.notices(teacher) if r["notice_type"] == "monthly_admin"]
        self.assertEqual(len(monthly), 1)
        self.assertTrue(monthly[0]["payload"]["data"]["final_period"])
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)

    def test_late_schedule_enable_does_not_backfill_content_or_absence(self):
        self.opt(self.tokens["parent-only"], lesson_content=True)
        self.opt(self.tokens["teacher-only"], absence_teacher=True)
        lesson = self.lesson()
        self.call("PUT", f"/v1/lessons/{lesson['id']}/content", data={"body": "虚构过去课程", "audience": "roster", "publish_after_class": True})
        self.move(lesson["ends_at"] + 100)
        self.configure()
        self.assertEqual(self.call("POST", "/v1/admin/jobs/tick")["simulation_notices_queued"], 0)
        self.assertEqual(self.notices(), [])

    def test_dispatch_rechecks_consent_expiry_and_actual_arrival(self):
        self.opt(self.tokens["parent-only"], attendance=True, absence_parent=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 300)
        self.call("POST", f"/v1/lessons/{lesson['id']}/absence-notices", self.tokens["teacher-only"], {"student_ids": [self.student_a]})
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        self.assertEqual(self.call("POST", "/v1/test/simulate")["cancelled"], 1)
        self.assertEqual(sum(r["status"] == "simulated" for r in self.notices()), 1)
        second = self.lesson(offset=5000)
        self.move(second["starts_at"] + 100)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(second, "expire", student_revision=2))
        self.move(self.now + 86400)
        self.assertEqual(self.call("POST", "/v1/test/simulate")["expired"], 1)

    def test_no_consent_no_queue_and_teacher_cannot_simulate(self):
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        self.assertEqual(self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))["simulation_notices_queued"], 0)
        self.assertEqual(self.notices(), [])
        self.call("POST", "/v1/test/simulate", self.tokens["teacher-only"], status=403)
        self.call("GET", "/v1/admin/outbox", self.tokens["parent-only"], status=403)
        self.call("POST", "/v1/admin/jobs/tick", self.device["device_token"], status=403)

    def test_queue_capacity_does_not_rollback_valid_attendance(self):
        self.opt(self.tokens["parent-only"], attendance=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        org = self.call("GET", "/v1/me")["org_id"]
        with closing(sqlite3.connect(self.database)) as db, db:
            for i in range(2000):
                db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"{i:032x}", org, "attendance", f"fixture-cap-{i}", self.ids["parent-only"], self.student_a, self.class_a, json.dumps({"data": {}, "channel": "simulation"}), "pending_simulation", self.now + 86400, self.now, None, None))
        result = self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        self.assertEqual(result["simulation_notices_queued"], 0)
        self.assertEqual(self.call("GET", f"/v1/lessons/{lesson['id']}")["arrived"], 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT count FROM notice_skips WHERE reason='queue_capacity'").fetchone()[0], 1)

    def test_unlink_then_relink_never_revives_old_pending_notice(self):
        parent = self.tokens["parent-only"]
        self.opt(parent, attendance=True)
        lesson = self.lesson()
        self.move(lesson["starts_at"] + 100)
        self.call("POST", "/v1/device/attendance", self.device["device_token"], self.event(lesson))
        link = {"parent_id": self.ids["parent-only"], "student_id": self.student_a}
        self.call("DELETE", "/v1/test/parent-links", data=link)
        self.call("POST", "/v1/test/parent-links", data=link, status=201)
        self.assertEqual(self.notices()[0]["status"], "cancelled")
        self.assertEqual(self.call("POST", "/v1/test/simulate")["simulated"], 0)

    def test_teacher_own_report_limit_filters_before_pagination(self):
        lesson = self.lesson()
        self.move(lesson["ends_at"])
        org = self.call("GET", "/v1/me")["org_id"]
        with closing(sqlite3.connect(self.database)) as db, db:
            for i in range(1001):
                db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?)", (f"{i:032x}", org, self.class_b, self.ids["teacher-admin"], self.clock + i, self.clock + i + 1, self.clock))
        local = datetime.fromtimestamp(self.clock, SCHOOL_TZ).date()
        monday = (local - timedelta(days=local.weekday())).isoformat()
        own = self.call("GET", "/v1/me/reports/weekly?week_start=" + monday, self.tokens["teacher-only"])
        self.assertEqual(own["expected"], 1)
        self.call("GET", "/v1/reports/weekly?week_start=" + monday, status=422)


if __name__ == "__main__":
    unittest.main()
