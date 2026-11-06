from datetime import datetime, timedelta
import unittest

import test_business_identity as fixtures
from business.workflows import SCHOOL_TZ


class DeviceSyncTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.IdentityTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.IdentityTests.tearDownClass.__func__)
    setUp = fixtures.IdentityTests.setUp
    req = fixtures.IdentityTests.req

    def grant(self, leaves=False, enabled=True):
        r = self.req("PUT", f"/v1/admin/devices/{self.device['id']}/sync-access", self.owner, json={"enabled": enabled, "allow_local_leaves": leaves})
        self.assertEqual(r.status_code, 200)

    def lesson(self):
        start = datetime.fromtimestamp(self.clock, SCHOOL_TZ)
        r = self.req("POST", "/v1/admin/lessons", self.owner, json={"class_id": self.class_a, "teacher_id": self.ids["teacher-only"], "starts_at": start.isoformat(), "ends_at": (start+timedelta(hours=1)).isoformat()})
        self.assertEqual(r.status_code, 201)
        return r.json()["id"]

    def send(self, kind, payload, status=200, token=None):
        r = self.req("POST", "/v1/device/events", token or self.device["device_token"], json={"kind": kind, "payload": payload})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_opt_in_grant_and_minimal_scoped_plan(self):
        self.assertEqual(self.req("GET", "/v1/device/sync-plan", self.device["device_token"]).status_code, 403)
        lesson = self.lesson()
        self.grant()
        plan = self.req("GET", "/v1/device/sync-plan", self.device["device_token"]).json()
        self.assertEqual(plan["device_id"], self.device["id"])
        self.assertEqual([r["id"] for r in plan["students"]], [self.student_a])
        self.assertEqual(plan["lessons"][0]["student_ids"], [self.student_a])
        self.assertEqual(plan["lessons"][0]["id"], lesson)
        self.assertNotIn(self.student_b, str(plan))
        self.assertFalse(plan["wechat_sending_enabled"])
        self.assertEqual(set(plan["students"][0]), {"id", "name", "external_ref", "class_id", "remaining_units", "source_revision", "source_device_id"})

    def test_sync_grant_is_admin_only_and_revocation_takes_effect_on_retries(self):
        path = f"/v1/admin/devices/{self.device['id']}/sync-access"
        self.assertEqual(self.req("PUT", path, self.tokens["teacher-only"], json={"enabled": True}).status_code, 403)
        self.assertEqual(self.req("PUT", path, self.other_owner, json={"enabled": True}).status_code, 404)
        self.grant()
        event = {"event_ref": "b-1", "student_id": self.student_a, "occurred_at": datetime.fromtimestamp(self.now, SCHOOL_TZ).isoformat(), "remaining_units": 1900, "student_revision": 1, "source": "live"}
        first = self.send("balance", event)
        self.assertTrue(first["accepted"])
        self.assertEqual(first["event_ref"], "b-1")
        self.assertTrue(self.send("balance", event)["duplicate"])
        self.grant(enabled=False)
        self.send("balance", event, 403)

    def test_leave_delegation_provenance_and_cancel_idempotency(self):
        self.grant(leaves=False)
        lesson = self.lesson()
        payload = {"event_ref": "leave-1", "lesson_id": lesson, "student_id": self.student_a, "local_leave_ref": "123", "occurred_at": datetime.fromtimestamp(self.now, SCHOOL_TZ).isoformat(), "reason": "虚构本次事假", "operator_label": "本机备注老师"}
        self.send("leave", payload, 403)
        self.grant(leaves=True)
        result = self.send("leave", payload)
        self.assertFalse(result["teacher_identity_verified"])
        self.assertTrue(self.send("leave", payload)["duplicate"])
        detail = self.req("GET", f"/v1/lessons/{lesson}/workspace", self.tokens["teacher-only"]).json()
        self.assertEqual((detail["expected"], detail["leave_count"], detail["arrived"], detail["rate_percent"]), (1, 1, 0, 0))
        self.assertIn("平板代办", detail["leaves"][0]["recorded_by"])
        self.assertNotEqual(detail["leaves"][0]["recorded_by"], "本机备注老师")
        cancel = {"event_ref": "cancel-1", "leave_id": result["id"], "occurred_at": payload["occurred_at"], "reason": "虚构撤假", "operator_label": "另一条本机备注"}
        self.assertTrue(self.send("cancel_leave", cancel)["cancelled"])
        self.assertTrue(self.send("cancel_leave", cancel)["duplicate"])
        self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", self.owner).json()["remaining_units"], 2000)

    def test_device_cannot_cancel_human_leave_or_fake_extra_fields(self):
        self.grant(leaves=True)
        lesson = self.lesson()
        leave = self.req("POST", f"/v1/lessons/{lesson}/leaves", self.tokens["teacher-only"], json={"student_id": self.student_a, "reason": "虚构网页代办"}).json()
        data = {"event_ref": "cancel-human", "leave_id": leave["id"], "occurred_at": datetime.fromtimestamp(self.now, SCHOOL_TZ).isoformat(), "reason": "不能撤销", "operator_label": "任意名字"}
        self.send("cancel_leave", data, 403)
        self.send("cancel_leave", data | {"role": "admin"}, 422)
        self.assertEqual(self.req("GET", "/v1/admin/devices", self.tokens["teacher-only"]).status_code, 403)

    def test_unified_attendance_retry_revoke_balance_no_second_charge(self):
        self.grant()
        lesson = self.lesson()
        payload = {"event_ref": "att-1", "lesson_id": lesson, "student_id": self.student_a, "occurred_at": datetime.fromtimestamp(self.now, SCHOOL_TZ).isoformat(), "consumed_units": 100, "remaining_units": 1900, "student_revision": 1, "source": "live"}
        result = self.send("attendance", payload)
        self.assertEqual(self.send("attendance", payload)["id"], result["id"])
        self.send("attendance", payload | {"remaining_units": 1800}, 409)
        self.send("revoke", {"event_ref": "rev-1", "attendance_id": result["id"], "reason": "虚构撤销"})
        self.send("balance", {"event_ref": "refund-1", "student_id": self.student_a, "occurred_at": payload["occurred_at"], "remaining_units": 2000, "student_revision": 2, "source": "live"})
        self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", self.owner).json()["remaining_units"], 2000)
        self.assertEqual(self.req("GET", f"/v1/lessons/{lesson}", self.owner).json()["arrived"], 0)

    def test_stale_grant_and_human_cancellation_are_not_overwritten(self):
        self.grant(leaves=True)
        path = f"/v1/admin/devices/{self.device['id']}/sync-access"
        self.assertEqual(self.req("PUT", path, self.owner, json={"enabled": False, "expected_enabled": False}).status_code, 409)
        lesson = self.lesson()
        stamp = datetime.fromtimestamp(self.now, SCHOOL_TZ).isoformat()
        result = self.send("leave", {"event_ref": "leave-rev", "lesson_id": lesson, "student_id": self.student_a, "local_leave_ref": "456", "occurred_at": stamp, "reason": "虚构代办", "operator_label": "本机备注"})
        self.assertEqual(self.req("POST", f"/v1/leaves/{result['id']}/cancel", self.tokens["teacher-only"], json={"reason": "网页教师已先核对撤销"}).status_code, 200)
        ack = self.send("cancel_leave", {"event_ref": "later-cancel", "leave_id": result["id"], "occurred_at": stamp, "reason": "平板随后撤销", "operator_label": "本机备注"})
        self.assertTrue(ack["already_cancelled"])
        with self.app.state.store.transaction() as db:
            row = db.execute("SELECT cancelled_by,cancel_reason FROM leaves WHERE id=?", (result["id"],)).fetchone()
            self.assertEqual(tuple(row), (self.ids["teacher-only"], "网页教师已先核对撤销"))

    def test_date_only_makeup_has_no_fabricated_arrival_time_or_notice(self):
        self.grant()
        lesson = self.lesson()
        result = self.send("attendance", {"event_ref": "makeup-date-only", "lesson_id": lesson, "student_id": self.student_a, "occurred_at": None, "consumed_units": 100, "remaining_units": 1900, "student_revision": 1, "source": "makeup"})
        self.assertEqual(result["simulation_notices_queued"], 0)
        with self.app.state.store.transaction() as db:
            row = db.execute("SELECT metadata FROM audit WHERE action='attendance_mirrored' AND entity_id=?", (result["id"],)).fetchone()
            import json
            self.assertFalse(json.loads(row[0])["occurred_at_known"])


if __name__ == "__main__":
    unittest.main()
