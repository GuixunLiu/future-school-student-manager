"""Authorization and concurrency checks for the local teacher/admin workbench."""

from contextlib import closing
from datetime import datetime, timedelta
import sqlite3
import unittest

import test_business_identity as fixtures
from business.workflows import SCHOOL_TZ


class PortalTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.IdentityTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.IdentityTests.tearDownClass.__func__)
    setUp = fixtures.IdentityTests.setUp
    req = fixtures.IdentityTests.req

    def create_lesson(self, class_id=None, teacher_id=None, offset=0):
        start = datetime.fromtimestamp(self.clock + offset, SCHOOL_TZ)
        response = self.req("POST", "/v1/admin/lessons", self.owner, json={"class_id": class_id or self.class_a, "teacher_id": teacher_id or self.ids["teacher-only"], "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_static_ui_has_self_only_csp_no_private_content_or_file_routes(self):
        for path, mime in (("/", "text/html"), ("/workspace.js", "text/javascript"), ("/workspace.css", "text/css")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(mime, response.headers["content-type"])
            self.assertIn("script-src 'self'", response.headers["content-security-policy"])
            self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(self.student_a, response.text)
            self.assertNotIn(fixtures.TEST_PASSWORD, response.text)
        for path in ("/schema.sql", "/business/cli.py", "/.env", "/workspace.js.map", "/web/index.html"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/v1/lessons?day=2026-10-24").status_code, 401)

    def test_foreign_origin_dns_rebinding_and_null_origin_rejected_before_login(self):
        data = {"organization": "school-alpha", "login": "owner-alpha", "password": fixtures.TEST_PASSWORD}
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://testserver:9999"}):
            with self.subTest(headers=headers):
                response = self.client.post("/v1/auth/login", json=data, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(fixtures.TEST_PASSWORD, response.text)
        self.assertEqual(self.client.post("/v1/auth/login", json=data, headers={"Origin": "http://testserver"}).status_code, 200)

    def test_assignment_reads_are_admin_only_and_stale_save_does_not_overwrite(self):
        path = f"/v1/admin/classes/{self.class_a}/teachers"
        self.assertEqual(self.req("GET", path, self.tokens["teacher-only"]).status_code, 403)
        self.assertEqual(self.req("GET", path, self.other_owner).status_code, 404)
        old = self.req("GET", path, self.owner).json()["teacher_ids"]
        self.assertEqual(old, [self.ids["teacher-only"]])
        self.assertEqual(self.req("PUT", path, self.owner, json={"teacher_ids": [], "expected_teacher_ids": old}).status_code, 200)
        self.assertEqual(self.req("PUT", path, self.owner, json={"teacher_ids": old, "expected_teacher_ids": old}).status_code, 409)
        self.assertEqual(self.req("GET", path, self.owner).json()["teacher_ids"], [])

    def test_stale_person_permissions_do_not_restore_revoked_role(self):
        person_id = self.ids["teacher-admin"]
        path = f"/v1/admin/people/{person_id}"
        self.assertEqual(self.req("PATCH", path, self.owner, json={"roles": ["teacher"], "expected_roles": ["admin", "teacher"], "expected_active": True}).status_code, 200)
        response = self.req("PATCH", path, self.owner, json={"roles": ["admin", "teacher"], "expected_roles": ["admin", "teacher"], "expected_active": True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.req("PATCH", path, self.owner, json={"expected_roles": ["teacher"]}).status_code, 422)
        people = self.req("GET", "/v1/admin/people", self.owner).json()["items"]
        self.assertEqual(next(p for p in people if p["id"] == person_id)["roles"], ["teacher"])

    def test_lesson_list_scope_pagination_dates_and_teacher_admin_own(self):
        first = self.create_lesson()
        second = self.create_lesson(offset=4000)
        third = self.create_lesson(self.class_b, self.ids["teacher-admin"])
        day = datetime.fromtimestamp(self.clock, SCHOOL_TZ).date().isoformat()
        path = "/v1/lessons?day=" + day
        own = self.req("GET", path, self.tokens["teacher-only"]).json()
        self.assertEqual({r["id"] for r in own["items"]}, {first, second})
        page = self.req("GET", path + "&limit=1", self.owner).json()
        after = self.req("GET", path + "&limit=100&after=" + page["next_cursor"], self.owner).json()
        self.assertEqual(len(page["items"]) + len(after["items"]), 3)
        dual = self.req("GET", path + "&scope=teaching", self.tokens["teacher-admin"]).json()
        self.assertEqual([r["id"] for r in dual["items"]], [third])
        self.assertEqual(self.req("GET", path, self.tokens["parent-only"]).status_code, 403)
        self.assertEqual(self.req("GET", path, self.device["device_token"]).status_code, 403)
        self.assertEqual(self.req("GET", path, self.other_owner).json()["items"], [])
        self.assertEqual(self.req("GET", "/v1/lessons?day=2026-13-40", self.owner).status_code, 422)

    def test_workspace_leave_ids_reasons_homonyms_and_revocation(self):
        lesson_id = self.create_lesson()
        path = f"/v1/lessons/{lesson_id}/workspace"
        leave = self.req("POST", f"/v1/lessons/{lesson_id}/leaves", self.tokens["teacher-only"], json={"student_id": self.student_a, "reason": "虚构事假理由"}).json()
        detail = self.req("GET", path, self.tokens["teacher-only"]).json()
        self.assertEqual(detail["leaves"][0]["id"], leave["id"])
        self.assertEqual(detail["leaves"][0]["reason"], "虚构事假理由")
        self.assertEqual(detail["members"][0]["external_ref"], "fixture-a")
        self.assertEqual(detail["members"][0]["remaining_units"], 2000)
        self.assertEqual(self.req("GET", path, self.tokens["parent-only"]).status_code, 403)
        self.assertEqual(self.req("GET", path, self.other_owner).status_code, 404)
        self.req("PUT", f"/v1/admin/classes/{self.class_a}/teachers", self.owner, json={"teacher_ids": []})
        self.assertEqual(self.req("GET", path, self.tokens["teacher-only"]).status_code, 403)

    def test_teacher_list_applies_permission_before_limit(self):
        self.create_lesson()
        org = self.req("GET", "/v1/me", self.owner).json()["org_id"]
        with closing(sqlite3.connect(self.database)) as db, db:
            for i in range(101):
                db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?)", (f"{i:032x}", org, self.class_b, self.ids["teacher-admin"], self.clock+i, self.clock+i+1, self.clock))
        day = datetime.fromtimestamp(self.clock, SCHOOL_TZ).date().isoformat()
        result = self.req("GET", "/v1/lessons?day=" + day + "&limit=1", self.tokens["teacher-only"]).json()
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNone(result["next_cursor"])


if __name__ == "__main__":
    unittest.main()
