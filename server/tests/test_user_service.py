"""No actual systemd, remote connections or real service changes in these tests."""

import configparser
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("user_service", ROOT / "ops/install-user-service.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UnitTests(unittest.TestCase):
    def test_rendered_unit_is_own_user_loopback_bounded_and_not_business(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(module.build_unit())
        self.assertEqual(set(config.sections()), {"Unit", "Service", "Install"})
        service = config["Service"]
        self.assertIn("--host 127.0.0.1 --port 18080", service["ExecStart"])
        self.assertIn(module.RELEASE_DIR.as_posix() + "/.venv/bin/python", service["ExecStart"])
        self.assertTrue(service["WorkingDirectory"].startswith("/opt/wxnotify/app/"))
        self.assertNotIn("\\", service["ExecStart"])
        self.assertNotIn("sudo", service["ExecStart"])
        self.assertNotIn("--reload", service["ExecStart"])
        self.assertNotIn("0.0.0.0", service["ExecStart"])
        self.assertNotIn("User", service)
        self.assertEqual(service["MemoryMax"], "256M")
        self.assertEqual(service["CPUQuota"], "50%")
        self.assertEqual(service["TasksMax"], "64")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(config["Unit"]["StartLimitBurst"], "3")
        self.assertIn("WXNOTIFY_ENABLE_WECHAT=0", service["Environment"])
        self.assertIn("WXNOTIFY_ENABLE_STUDENT_SYNC=0", service["Environment"])
        self.assertEqual(service["StandardOutput"], "journal")
        self.assertEqual(config["Install"]["WantedBy"], "default.target")

    def test_root_or_windows_cannot_register(self):
        with mock.patch.object(module.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "ordinary Linux"):
                module.register()
        with mock.patch.object(module.sys, "platform", "linux"), \
             mock.patch.object(module.os, "getuid", return_value=0, create=True):
            with self.assertRaisesRegex(RuntimeError, "never with sudo"):
                module.register()

    def fixture(self, base):
        for name in (".venv/bin/python", ".venv/pyvenv.cfg", "app/wxnotify_api.py", "app/requirements.txt"):
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()

    def test_install_exclusive_validate_first_no_enable_start_or_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "release"
            home = Path(directory) / "home"
            self.fixture(base)
            actual_is_file = Path.is_file

            def tools_available(path):
                return str(path).replace("\\", "/").startswith("/usr/bin/") or actual_is_file(path)

            def run(arguments, **kwargs):
                if "--property=Linger" in arguments:
                    return subprocess.CompletedProcess(arguments, 0, "yes\n", "")
                if "--property=LoadState" in arguments:
                    return subprocess.CompletedProcess(arguments, 1, "not-found\n", "")
                if "verify" in arguments:
                    self.assertFalse((home / ".config/systemd/user" / module.UNIT_NAME).exists())
                    self.assertEqual(Path(arguments[-1]).read_text(encoding="utf-8"), module.build_unit(base))
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with mock.patch.object(module.sys, "platform", "linux"), \
                 mock.patch.object(module.os, "getuid", return_value=1000, create=True), \
                 mock.patch.object(module.os, "access", return_value=True), \
                 mock.patch.object(Path, "is_file", autospec=True, side_effect=tools_available), \
                 mock.patch.object(module.subprocess, "run", side_effect=run) as invoked:
                result = module.register(base, home)
                self.assertEqual(result.read_text(encoding="utf-8"), module.build_unit(base))
                commands = [call.args[0] for call in invoked.call_args_list]
                for command in commands:
                    self.assertFalse(set(command) & {"sudo", "start", "restart", "stop", "enable", "kill", "enable-linger"})
                self.assertEqual(commands[-1], ["/usr/bin/systemctl", "--user", "daemon-reload"])
                before = result.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                    module.register(base, home)
                self.assertEqual(before, result.read_bytes())

    def test_missing_release_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(module.sys, "platform", "linux"), \
             mock.patch.object(module.os, "getuid", return_value=1000, create=True), \
             mock.patch.object(module.subprocess, "run") as invoked:
            home = Path(directory) / "home"
            with self.assertRaisesRegex(RuntimeError, "existing release"):
                module.register(Path(directory) / "missing", home)
            self.assertFalse(home.exists())
            invoked.assert_not_called()

    def test_runner_refuses_validation_or_bus_errors(self):
        with mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "denied")):
            with self.assertRaisesRegex(RuntimeError, "denied"):
                module.invoke(["systemctl", "--user", "show"], allow_not_found=True)
        with mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "not-found\n", "")):
            self.assertEqual(module.invoke(["systemctl"], allow_not_found=True).stdout.strip(), "not-found")

    def test_failed_prerequisites_never_create_or_start_unit(self):
        for failure in ("no_linger", "bus_error", "existing_unit", "pip_error", "verify_error", "verify_warning"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                base = Path(directory) / "release"
                home = Path(directory) / "home"
                self.fixture(base)
                actual_is_file = Path.is_file

                def tools_available(path):
                    return str(path).replace("\\", "/").startswith("/usr/bin/") or actual_is_file(path)

                def run(arguments, **kwargs):
                    if "--property=Linger" in arguments:
                        return subprocess.CompletedProcess(arguments, 0, "no\n" if failure == "no_linger" else "yes\n", "")
                    if "--property=LoadState" in arguments:
                        if failure == "bus_error":
                            return subprocess.CompletedProcess(arguments, 1, "", "Cannot connect to user bus")
                        return subprocess.CompletedProcess(arguments, 0, "loaded\n" if failure == "existing_unit" else "not-found\n", "")
                    if "pip" in arguments and failure == "pip_error":
                        return subprocess.CompletedProcess(arguments, 1, "", "Broken requirements")
                    if "verify" in arguments:
                        if failure == "verify_error":
                            return subprocess.CompletedProcess(arguments, 1, "", "Invalid unit")
                        if failure == "verify_warning":
                            return subprocess.CompletedProcess(arguments, 0, "", "Unknown key")
                    return subprocess.CompletedProcess(arguments, 0, "", "")

                with mock.patch.object(module.sys, "platform", "linux"), \
                     mock.patch.object(module.os, "getuid", return_value=1000, create=True), \
                     mock.patch.object(module.os, "access", return_value=True), \
                     mock.patch.object(Path, "is_file", autospec=True, side_effect=tools_available), \
                     mock.patch.object(module.subprocess, "run", side_effect=run) as invoked:
                    with self.assertRaises(RuntimeError):
                        module.register(base, home)
                    self.assertFalse(home.exists())
                    self.assertTrue(all("daemon-reload" not in call.args[0] for call in invoked.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
