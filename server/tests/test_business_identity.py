"""Fictional tenants and credentials only; never touch the tablet or server."""

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from business.api import create_app
from business.security import ITERATIONS, password_hash, verify_password, token_digest
from business.store import Store, uid

TEST_PASSWORD = "Fictional test-only passphrase 2026!"


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tempfile.TemporaryDirectory()
        cls.base = Path(cls.source.name) / "fixture.sqlite"
        cls.clock = 1792886400
        store = Store.bootstrap(cls.base, "school-alpha", "owner-alpha", "虚构管理员", TEST_PASSWORD, lambda: cls.clock)
        client = TestClient(create_app(cls.base, lambda: cls.clock, allow_test_client=True))

        def request(method, url, token=None, **kwargs):
            response = client.request(method, url, headers={"Authorization": "Bearer " + token} if token else {}, **kwargs)
            if response.status_code >= 400:
                raise AssertionError((url, response.status_code, response.text))
            return response.json()
        cls.owner = request("POST", "/v1/auth/login", json={"organization": "school-alpha", "login": "owner-alpha", "password": TEST_PASSWORD})["access_token"]
        cls.owner_id = request("GET", "/v1/me", cls.owner)["id"]
        cls.ids, cls.tokens = {}, {}
        for login, roles in (("teacher-only", ["teacher"]), ("teacher-admin", ["teacher", "admin"]), ("parent-only", ["parent"]), ("unassigned", [])):
            cls.ids[login] = request("POST", "/v1/admin/people", cls.owner, json={"login": login, "display_name": "虚构" + login, "password": TEST_PASSWORD, "roles": roles})["id"]
            cls.tokens[login] = request("POST", "/v1/auth/login", json={"organization": "school-alpha", "login": login, "password": TEST_PASSWORD})["access_token"]
        cls.class_a = request("POST", "/v1/admin/classes", cls.owner, json={"name": "虚构奥赛周六班", "course_name": "虚构信息学奥赛"})["id"]
        cls.class_b = request("POST", "/v1/admin/classes", cls.owner, json={"name": "虚构绘画周日班", "course_name": "虚构绘画"})["id"]
        request("PUT", f"/v1/admin/classes/{cls.class_a}/teachers", cls.owner, json={"teacher_ids": [cls.ids["teacher-only"]]})
        request("PUT", f"/v1/admin/classes/{cls.class_b}/teachers", cls.owner, json={"teacher_ids": [cls.ids["teacher-admin"]]})
        cls.student_a = request("POST", "/v1/admin/students", cls.owner, json={"class_id": cls.class_a, "name": "虚构重名学生", "external_ref": "fixture-a", "remaining_units": 2000})["id"]
        cls.student_b = request("POST", "/v1/admin/students", cls.owner, json={"class_id": cls.class_b, "name": "虚构重名学生", "external_ref": "fixture-b", "remaining_units": -100})["id"]
        request("POST", "/v1/test/parent-links", cls.owner, json={"parent_id": cls.ids["parent-only"], "student_id": cls.student_a})
        cls.device = request("POST", "/v1/admin/devices", cls.owner, json={"name": "虚构平板", "class_ids": [cls.class_a]})
        with store.transaction() as db:
            cls.org_b, cls.user_b = uid(), uid()
            encoded = db.execute("SELECT password_hash FROM users WHERE id=?", (cls.owner_id,)).fetchone()[0]
            db.execute("INSERT INTO organizations VALUES(?,?,?)", (cls.org_b, "school-beta", cls.clock))
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)", (cls.user_b, cls.org_b, "owner-beta", "虚构另一机构", encoded, 1, cls.clock))
            db.execute("INSERT INTO roles VALUES(?,?,?)", (cls.org_b, cls.user_b, "admin"))
        cls.other_owner = request("POST", "/v1/auth/login", json={"organization": "school-beta", "login": "owner-beta", "password": TEST_PASSWORD})["access_token"]
        client.close()

    @classmethod
    def tearDownClass(cls):
        cls.source.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "case.sqlite"
        shutil.copyfile(self.base, self.database)
        self.now = self.clock
        self.app = create_app(self.database, lambda: self.now, allow_test_client=True)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def req(self, method, path, token=None, **kwargs):
        return self.client.request(method, path, headers={"Authorization": "Bearer " + token} if token else {}, **kwargs)

    def login(self, login="owner-alpha", password=TEST_PASSWORD, organization="school-alpha"):
        return self.req("POST", "/v1/auth/login", json={"organization": organization, "login": login, "password": password})

    def test_multi_role_union_and_admin_only_assignment(self):
        account = self.req("GET", "/v1/me", self.tokens["teacher-admin"]).json()
        self.assertEqual(account["roles"], ["admin", "teacher"])
        self.assertFalse(account["wechat_verified"])
        self.assertEqual(len(self.req("GET", "/v1/classes", self.tokens["teacher-admin"]).json()["items"]), 2)
        for token in (self.tokens["teacher-only"], self.tokens["parent-only"], self.device["device_token"]):
            response = self.req("PATCH", f"/v1/admin/people/{self.ids['teacher-only']}", token, json={"roles": ["admin"]})
            self.assertEqual(response.status_code, 403)

    def test_teacher_and_parent_read_only_their_authorized_students(self):
        teacher = self.tokens["teacher-only"]
        parent = self.tokens["parent-only"]
        for token in (teacher, parent):
            self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", token).status_code, 200)
            self.assertEqual(self.req("GET", f"/v1/students/{self.student_b}", token).status_code, 403)
            self.assertEqual([row["id"] for row in self.req("GET", "/v1/students", token).json()["items"]], [self.student_a])
        self.assertNotEqual(self.student_a, self.student_b)
        self.assertEqual(self.req("GET", "/v1/students", self.tokens["unassigned"]).json()["items"], [])

    def test_class_authorization_removal_takes_effect_without_relogin(self):
        response = self.req("PUT", f"/v1/admin/classes/{self.class_a}/teachers", self.owner, json={"teacher_ids": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", self.tokens["teacher-only"]).status_code, 403)

    def test_cross_tenant_even_admin_cannot_access_or_assign(self):
        for path in (f"/v1/students/{self.student_a}",):
            self.assertEqual(self.req("GET", path, self.other_owner).status_code, 404)
        self.assertEqual(self.req("GET", "/v1/classes", self.other_owner).json()["items"], [])
        self.assertEqual(self.req("PUT", f"/v1/admin/classes/{self.class_a}/teachers", self.other_owner, json={"teacher_ids": []}).status_code, 404)
        self.assertEqual(self.req("PUT", f"/v1/admin/classes/{self.class_a}/teachers", self.owner, json={"teacher_ids": [self.user_b]}).status_code, 404)
        self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", self.tokens["teacher-only"]).status_code, 200)

    def test_revocation_disable_and_last_admin_protection(self):
        multi_id = self.ids["teacher-admin"]
        self.assertEqual(self.req("PATCH", f"/v1/admin/people/{multi_id}", self.owner, json={"roles": ["teacher"]}).status_code, 200)
        self.assertEqual(self.req("GET", "/v1/me", self.tokens["teacher-admin"]).status_code, 401)
        for data in ({"roles": []}, {"active": False}):
            response = self.req("PATCH", f"/v1/admin/people/{self.owner_id}", self.owner, json=data)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "last_admin")
        self.assertEqual(self.req("GET", "/v1/me", self.owner).status_code, 200)
        teacher_id = self.ids["teacher-only"]
        self.req("PATCH", f"/v1/admin/people/{teacher_id}", self.owner, json={"active": False})
        self.assertEqual(self.req("GET", "/v1/me", self.tokens["teacher-only"]).status_code, 401)
        self.assertEqual(self.login("teacher-only").status_code, 401)

    def test_password_reset_revokes_tokens_and_never_leaks_password(self):
        replacement = "Another fictional test-only passphrase!"
        response = self.req("POST", f"/v1/admin/people/{self.ids['teacher-only']}/reset-password", self.owner, json={"password": replacement})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(replacement, response.text)
        self.assertEqual(self.req("GET", "/v1/me", self.tokens["teacher-only"]).status_code, 401)
        self.assertEqual(self.login("teacher-only").status_code, 401)
        self.assertEqual(self.login("teacher-only", replacement).status_code, 200)
        with self.app.state.store.transaction() as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(replacement, dump)
        self.assertNotIn(TEST_PASSWORD, dump)
        self.assertNotIn(self.owner, dump)
        self.assertNotIn(self.device["device_token"], dump)

    def test_logout_session_expiry_and_device_rotation(self):
        self.assertEqual(self.req("POST", "/v1/auth/logout", self.tokens["teacher-only"]).status_code, 200)
        self.assertEqual(self.req("GET", "/v1/me", self.tokens["teacher-only"]).status_code, 401)
        rotated = self.req("POST", f"/v1/admin/devices/{self.device['id']}/rotate", self.owner).json()
        self.assertEqual(self.req("GET", "/v1/me", self.device["device_token"]).status_code, 401)
        self.assertEqual(self.req("GET", "/v1/me", rotated["device_token"]).status_code, 200)
        self.now += 8 * 3600 + 1
        self.assertEqual(self.req("GET", "/v1/me", self.owner).status_code, 401)

    def test_device_cannot_be_used_as_human_or_override_scope(self):
        token = self.device["device_token"]
        self.assertEqual(self.req("GET", "/v1/me", token).json()["class_ids"], [self.class_a])
        self.assertEqual(self.req("GET", "/v1/students", token).status_code, 403)
        self.req("PUT", f"/v1/admin/devices/{self.device['id']}/access", self.owner, json={"active": False, "class_ids": []})
        self.assertEqual(self.req("GET", "/v1/me", token).status_code, 401)

    def test_test_parent_link_is_not_wechat_binding_and_can_be_removed(self):
        parent = self.tokens["parent-only"]
        self.assertFalse(self.req("GET", "/v1/me", parent).json()["wechat_verified"])
        self.assertEqual(self.req("POST", "/v1/test/parent-links", parent, json={"parent_id": self.ids["parent-only"], "student_id": self.student_b}).status_code, 403)
        self.assertEqual(self.req("DELETE", "/v1/test/parent-links", self.owner, json={"parent_id": self.ids["parent-only"], "student_id": self.student_a}).status_code, 200)
        self.assertEqual(self.req("GET", f"/v1/students/{self.student_a}", parent).status_code, 403)

    def test_login_throttle_persists_and_unknown_accounts_are_generic(self):
        for _ in range(5):
            self.assertEqual(self.login(password="wrong fictional value").status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        self.now += 301
        self.assertEqual(self.login().status_code, 200)
        unknown = self.login("unknown-user", "wrong fictional value")
        wrong = self.login("teacher-only", "wrong fictional value")
        self.assertEqual(unknown.json(), wrong.json())

    def test_no_anonymous_setup_no_role_input_no_validation_echo(self):
        self.assertEqual(self.req("GET", "/v1/me").status_code, 401)
        self.assertEqual(self.req("POST", "/v1/bootstrap").status_code, 404)
        marker = "canary-secret-should-not-be-reflected"
        response = self.req("POST", "/v1/auth/login", json={"organization": "school-alpha", "login": "owner-alpha", "password": marker, "roles": ["admin"]})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(marker, response.text)
        self.assertEqual(self.req("POST", "/v1/admin/people", self.owner, json={"login": "invalid-user", "display_name": "虚构", "password": marker, "roles": ["superadmin"]}).status_code, 422)

    def test_body_size_forwarded_headers_and_nonlocal_access(self):
        response = self.req("POST", "/v1/auth/login", content="x" * 17000)
        self.assertEqual(response.status_code, 413)
        external = TestClient(self.app, client=("203.0.113.10", 1000))
        try:
            self.assertEqual(external.get("/health/ready", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)
        finally:
            external.close()
        response = self.req("GET", "/v1/me", self.owner)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_parameterized_sql_duplicate_rollback_and_pagination(self):
        duplicate = self.req("POST", "/v1/admin/people", self.owner, json={"login": "TEACHER-ONLY", "display_name": "SQL'); DROP TABLE users;--", "password": TEST_PASSWORD, "roles": ["admin"]})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.req("GET", "/v1/me", self.owner).status_code, 200)
        first = self.req("GET", "/v1/admin/people?limit=2", self.owner).json()
        second = self.req("GET", "/v1/admin/people?limit=2&after=" + first["next_cursor"], self.owner).json()
        self.assertTrue(set(row["id"] for row in first["items"]).isdisjoint(row["id"] for row in second["items"]))
        self.assertEqual(self.req("GET", "/v1/classes?limit=0", self.owner).status_code, 422)
        audit = self.req("GET", "/v1/admin/audit", self.owner)
        self.assertEqual(audit.status_code, 200)
        self.assertNotIn(TEST_PASSWORD, audit.text)
        self.assertEqual(self.req("GET", "/v1/admin/audit", self.tokens["teacher-only"]).status_code, 403)


class PasswordAndDatabaseTests(unittest.TestCase):
    def test_real_password_hash_work_factor_and_random_salts(self):
        one, two = password_hash(TEST_PASSWORD), password_hash(TEST_PASSWORD)
        self.assertNotEqual(one, two)
        self.assertEqual(ITERATIONS, 600000)
        self.assertTrue(verify_password(TEST_PASSWORD, one))
        self.assertFalse(verify_password("wrong fictional value", one))
        self.assertFalse(verify_password(TEST_PASSWORD, "bad encoding"))
        self.assertFalse(verify_password("x" * 129, one))

    def test_existing_database_is_never_bootstrapped_over(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.sqlite"
            Store.bootstrap(target, "test-school", "test-owner", "虚构", TEST_PASSWORD)
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                Store.bootstrap(target, "other-school", "other-owner", "虚构", TEST_PASSWORD)
            self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
