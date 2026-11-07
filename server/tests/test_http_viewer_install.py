import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("http_install", ROOT / "ops/install-http-viewer.py")
installer = importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
READY = {"status": "ready", "version": "0.5.0", "mode": "roster_review", "roster_review_only": True, "archive_enabled": True,
         "business_ready": False, "wechat_sending_enabled": False, "student_sync_enabled": False, "parent_binding_enabled": False}


class ViewerInstallTests(unittest.TestCase):
    def test_unit_only_targets_new_unprivileged_port_and_no_credentials(self):
        work = PurePosixPath("/opt/wxnotify/app/http-viewer-20261028090000-aabbccdd")
        text = installer.unit(work, "http://8.8.8.8:18083", 2000000000, "/usr/bin/python3")
        self.assertIn("--temporary-http-confirmed", text)
        self.assertIn("NoNewPrivileges=yes", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("roster.sqlite", text)
        self.assertNotIn("password", text)
        self.assertIn("StandardOutput=null", text)
        with self.assertRaises(ValueError):
            installer.unit(work, "http://8.8.8.8:80", 2000000000, "/usr/bin/python3")

    def scenario(self, failure=False):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp);units = home / ".config/systemd/user";units.mkdir(parents=True)
            work = home / "work";work.mkdir();file = work / "temporary-http-viewer.py";shutil.copyfile(ROOT / "ops/temporary-http-viewer.py", file)
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            config = {"owner_accepted_plain_http": True, "expires_after_days": 14, "public_origin": "http://8.8.8.8:18083", "viewer_sha256": digest}
            calls = []
            def run(args):
                calls.append(args)
                if "is-active" in args:
                    return "active"
                if "enable" in args and failure:
                    raise ValueError("fixture failure")
                return "ActiveState=active\nSubState=running\nUnitFileState=enabled\nNRestarts=0"
            own = {"gateway_sha256": digest, "backend_ready": True, "expires_at": 2000000000 + 14 * 86400}
            with patch.object(installer, "private"), patch.object(installer.Path, "home", return_value=home), patch.object(installer, "unit", return_value="fictional unit"), patch.object(installer, "verify_source"), patch.object(installer, "run", side_effect=run), patch.object(installer.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="not-found")) as commands, patch.object(installer, "health", side_effect=[READY, own]), patch.object(installer.socket, "socket"), patch.object(installer.time, "time", return_value=2000000000):
                if failure:
                    with self.assertRaises(ValueError):
                        installer.install(work, config)
                    self.assertIn(["systemctl", "--user", "disable", "--now", installer.UNIT], [c.args[0] for c in commands.call_args_list])
                else:
                    result = installer.install(work, config)
                    self.assertTrue(result["local_ready"])
                    self.assertFalse(result["external_access_verified"])
                    self.assertFalse(result["database_directly_opened"])
                    self.assertTrue((work / "deployment-receipt.json").exists())
                    with self.assertRaises(ValueError):
                        installer.install(work, config)
            self.assertFalse(any("disable" in a or "stop" in a for a in calls))

    def test_separate_registration_receipt_and_existing_unit_refusal(self):
        self.scenario()

    def test_failed_start_disables_only_the_new_viewer(self):
        self.scenario(failure=True)

    def test_unconfirmed_http_refused_before_reading_or_writing_files(self):
        with patch.object(installer, "private"):
            with self.assertRaises(ValueError):
                installer.install(Path("/unused"), {"owner_accepted_plain_http": False})


if __name__ == "__main__":
    unittest.main()
