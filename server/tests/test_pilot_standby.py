import base64
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from business.security import password_hash
import test_single_class_stage as stage_fixture

stage, NOW = stage_fixture.stage, stage_fixture.NOW

ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ops" / file)
    value = importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


runtime = module("pilot_standby", "pilot-standby.py")
installer = module("pilot_standby_install", "install-pilot-standby.py")
PASSWORD = "Fictional standby-only password 2026!"


class StandbyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = stage_fixture.SingleClassStageTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        db = sqlite3.connect(self.fixture.source)
        db.execute("UPDATE users SET password_hash=?", (password_hash(PASSWORD),));db.commit();db.close()
        self.receipt = self.fixture.prepare()
        self.database = self.fixture.output / "pilot-offline.sqlite"

    def test_readonly_api_login_roles_logout_and_health_are_truthful(self):
        api = runtime.build_app(self.database, self.fixture.plan, self.receipt, stage, clock=lambda: NOW, allow_test_client=True)
        with TestClient(api, base_url="http://127.0.0.1:18084") as client:
            state = client.get("/health/ready").json()
            self.assertEqual(state["mode"], "pilot_standby");self.assertEqual(state["selected_students"], 1)
            for key in ("business_ready", "student_sync_enabled", "wechat_sending_enabled", "device_registration_enabled", "single_class_write_enabled"):
                self.assertFalse(state[key])
            response = client.post("/v1/auth/login", json={"organization": "fictional", "login": "fictional-teacher", "password": PASSWORD})
            self.assertEqual(response.status_code, 200)
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            self.assertEqual(set(client.get("/v1/me", headers=headers).json()["roles"]), {"admin", "teacher"})
            self.assertEqual(client.post("/v1/auth/logout", headers=headers).status_code, 200)
            self.assertEqual(client.get("/v1/me", headers=headers).status_code, 401)
            self.assertEqual(client.get("/health/ready").status_code, 200)  # Session writes do not invalidate the business baseline.
            self.assertIn("单班试运行待启用", client.get("/").text)

    def test_no_business_or_legacy_device_write_can_bypass_standby(self):
        before = self.receipt["preserved_data_sha256"]
        api = runtime.build_app(self.database, self.fixture.plan, self.receipt, stage, clock=lambda: NOW, allow_test_client=True)
        with TestClient(api, base_url="http://127.0.0.1:18084") as client:
            for method, route in (("POST", "/v1/admin/devices"), ("POST", "/v1/device/events"), ("POST", "/v1/device/attendance"), ("POST", "/v1/admin/lessons"), ("PUT", "/v1/admin/devices/" + "f" * 32 + "/sync-access"), ("POST", "/v1/jobs/tick")):
                self.assertEqual(client.request(method, route, json={}).status_code, 409)
            self.assertEqual(client.get("/v1/me").status_code, 401)
        db = stage.connect(self.database);self.assertEqual(stage.preserved_digest(db), before);db.close()

    def test_baseline_change_or_device_activation_cannot_claim_ready(self):
        api = runtime.build_app(self.database, self.fixture.plan, self.receipt, stage, clock=lambda: NOW, allow_test_client=True)
        db = sqlite3.connect(self.database);db.execute("UPDATE devices SET active=1");db.commit();db.close()
        with TestClient(api, base_url="http://127.0.0.1:18084", raise_server_exceptions=False) as client:
            self.assertEqual(client.get("/health/ready").status_code, 500)
        with self.assertRaises(ValueError):runtime.build_app(self.database, self.fixture.plan, self.receipt, stage)

    def install_scenario(self, fail=False):
        config = {"stage_directory": str(self.fixture.output), "stage_receipt": self.receipt}
        files = {}
        for name in ("pilot-standby.py", "stage-single-class-pilot.py"):
            raw = (ROOT / "ops" / name).read_bytes();files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}
        home = self.fixture.root / "home";units = home / ".config/systemd/user";units.mkdir(parents=True)
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if "is-active" in args:return "active"
            if "LoadState" in args:return "not-found"
            if "enable" in args and fail:raise ValueError("fixture startup failure")
            return "ActiveState=active\nSubState=running\nUnitFileState=enabled\nNRestarts=0"
        def health(port):
            if port == 18082:return {"roster_review_only": True, "student_sync_enabled": False}
            return {"mode": "pilot_standby", "standby_code_sha256": files["pilot-standby.py"]["sha256"], "selected_students": 1,
                    "roster_review_only": True, **{k: False for k in ("business_ready", "student_sync_enabled", "wechat_sending_enabled", "device_registration_enabled", "single_class_write_enabled", "parent_binding_enabled")}}
        before = stage.checksum(self.database)
        with patch.object(installer, "stage_path", return_value=self.fixture.output), patch.object(installer, "verify_runtime"), patch.object(installer, "unit", return_value="fictional new unit"), patch.object(installer.Path, "home", return_value=home), patch.object(installer, "run", side_effect=run), patch.object(installer, "health", side_effect=health), patch.object(installer.socket, "socket"), patch.object(installer.time, "time", return_value=NOW):
            if fail:
                with self.assertRaises(ValueError):installer.install(config, files)
                self.assertIn(["systemctl", "--user", "disable", "--now", installer.UNIT], calls)
            else:
                result = installer.install(config, files);self.assertTrue(result["standby_ready"])
                self.assertFalse(result["business_writes_enabled"])
                with self.assertRaises(ValueError):installer.install(config, files)
        self.assertEqual(stage.checksum(self.database), before)
        self.assertTrue(all(item[-1] == installer.UNIT for item in calls if "disable" in item or "stop" in item))
        self.assertTrue((self.fixture.output / "standby/business.sqlite").exists())
        self.assertEqual((self.fixture.output / "standby/install-receipt.json").exists(), not fail)

    def test_new_unit_working_copy_and_idempotence_refusal(self):self.install_scenario()

    def test_failed_start_disables_only_new_unit(self):self.install_scenario(fail=True)

    def test_unit_fixed_loopback_runtime_and_no_secrets(self):
        content = installer.unit(PurePosixPath("/opt/wxnotify/app/single-class-offline-20261028121655-da40e468/standby"))
        self.assertIn("NoNewPrivileges=yes", content);self.assertIn("standby.private.json", content)
        self.assertNotIn("sudo", content);self.assertNotIn("password", content)
        with self.assertRaises(ValueError):installer.stage_path("/opt/other")

    def test_pause_verifies_working_directory_and_only_stops_new_unit(self):
        config = {"stage_directory": "/opt/wxnotify/app/single-class-offline-20261028121655-da40e468"}
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return config["stage_directory"] + "/standby" if "WorkingDirectory" in args else "ActiveState=inactive\nUnitFileState=disabled"
        with patch.object(installer, "stage_path", return_value=PurePosixPath(config["stage_directory"])), patch.object(installer, "run", side_effect=run):self.assertEqual(installer.pause(config)["unit"], installer.UNIT)
        self.assertIn(["systemctl", "--user", "disable", "--now", installer.UNIT], calls)
        with patch.object(installer, "run", return_value="/wrong"):
            with self.assertRaises(ValueError):installer.pause(config)


if __name__ == "__main__":unittest.main()
