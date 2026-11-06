"""Business unit registration tests: fictional databases, no remote/systemd operations."""

import configparser
from contextlib import ExitStack, closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from business.store import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("business_user_service", ROOT / "ops/install-business-user-service.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BusinessUnitTests(unittest.TestCase):
    def test_unit_targets_only_separate_pilot_and_database(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(module.build_unit())
        service = config["Service"]
        self.assertEqual(module.UNIT_NAME, "wxnotify-business-pilot.service")
        self.assertEqual(service["WorkingDirectory"], module.RELEASE_DIR.as_posix())
        self.assertIn("--database " + module.DATABASE.as_posix() + " --local-test --port 18081", service["ExecStart"])
        self.assertNotIn("18080", service["ExecStart"])
        self.assertNotIn("0.0.0.0", service["ExecStart"])
        self.assertNotIn("User", service)
        self.assertEqual(service["MemoryMax"], "256M")
        self.assertEqual(service["CPUQuota"], "50%")
        self.assertEqual(service["TasksMax"], "64")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["UMask"], "0077")
        for flag in ("WXNOTIFY_ENABLE_WECHAT=0", "WXNOTIFY_ENABLE_STUDENT_SYNC=0", "WXNOTIFY_ENABLE_PARENT_BINDING=0", "WEB_CONCURRENCY=1"):
            self.assertIn(flag, service["Environment"])
        self.assertEqual(service["Restart"], "on-failure")
        with self.assertRaises(ValueError):
            module.build_unit(Path("/bad path"))

    def fixture(self, folder):
        base, database = Path(folder) / "release", Path(folder) / "private/pilot.sqlite"
        base.mkdir()
        files = {}
        for name in ["business/cli.py", *[f"business/fixture-{i}.txt" for i in range(18)]]:
            source = base / name
            source.parent.mkdir(exist_ok=True)
            source.write_text("Fictional installer test source: " + name, encoding="utf-8")
            files[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest = base / "MANIFEST.json"
        manifest.write_text(json.dumps({"version": "0.4.0", "files": files}), encoding="utf-8")
        python = base / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        (base / ".venv/pyvenv.cfg").touch()
        Store.bootstrap(database, "test-school", "test-owner", "Fictional test operator", "Only fictional installer test passphrase!")
        backup = database.with_name("pilot-before-service-test.sqlite")
        from business.backup import backup as make_backup
        result = make_backup(database, backup)
        return base, database, result["sha256"], hashlib.sha256(manifest.read_bytes()).hexdigest()

    def test_source_pin_and_backup_checks_run_on_real_disposable_files(self):
        with tempfile.TemporaryDirectory(prefix="wxnotify-business-unit-test-") as folder:
            base, database, checksum, manifest_hash = self.fixture(folder)
            with mock.patch.object(module, "private_path"), mock.patch.object(module.os, "access", return_value=True), mock.patch.object(module, "MANIFEST_SHA256", manifest_hash):
                self.assertEqual(module.verify_release(base), base / ".venv/bin/python")
                module.database_check(database)
                self.assertEqual(module.verified_backup(database, checksum).name, "pilot-before-service-test.sqlite")
                with self.assertRaisesRegex(RuntimeError, "No private"):
                    module.verified_backup(database, "0" * 64)
                with self.assertRaises(ValueError):
                    module.verified_backup(database, "invalid")
                (base / "business/cli.py").write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    module.verify_release(base)
                (base / "MANIFEST.json").write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "manifest differs"):
                    module.verify_release(base)

    def test_database_refuses_unknown_or_no_active_admin_without_modification(self):
        with tempfile.TemporaryDirectory(prefix="wxnotify-business-unit-test-") as folder:
            base, database, checksum, manifest_hash = self.fixture(folder)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE users SET active=0")
                connection.commit()
            before = database.read_bytes()
            with mock.patch.object(module, "private_path"):
                with self.assertRaisesRegex(RuntimeError, "No active administrator"):
                    module.database_check(database)
            self.assertEqual(database.read_bytes(), before)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA application_id=0")
                connection.commit()
            with mock.patch.object(module, "private_path"):
                with self.assertRaisesRegex(RuntimeError, "identity or integrity"):
                    module.database_check(database)

    def test_private_paths_reject_broad_permissions_wrong_owner_and_symlinks(self):
        path = Path("test-only")
        with mock.patch.object(module.os, "getuid", return_value=1000, create=True):
            for mode, owner in ((stat.S_IFREG | 0o644, 1000), (stat.S_IFREG | 0o600, 1001), (stat.S_IFLNK | 0o600, 1000)):
                with mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=owner)):
                    with self.assertRaises(RuntimeError):
                        module.private_path(path)
            with mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)):
                module.private_path(path)

    def test_registration_refuses_windows_and_root(self):
        with mock.patch.object(module.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "ordinary Linux"):
                module.register("0" * 64)
        with mock.patch.object(module.sys, "platform", "linux"), mock.patch.object(module.os, "getuid", return_value=0, create=True):
            with self.assertRaisesRegex(RuntimeError, "ordinary Linux"):
                module.register("0" * 64)

    def lifecycle(self, failure=None):
        with tempfile.TemporaryDirectory(prefix="wxnotify-business-unit-test-") as folder, ExitStack() as stack:
            base, database, checksum, manifest_hash = self.fixture(folder)
            home, commands = Path(folder) / "home", []
            target = home / ".config/systemd/user" / module.UNIT_NAME
            database_before = database.read_bytes()
            actual_file = Path.is_file

            def is_file(path):
                return str(path).replace("\\", "/").startswith("/usr/bin/") or actual_file(path)

            def run(arguments, **kwargs):
                commands.append(arguments)
                if "--property=Linger" in arguments:
                    return subprocess.CompletedProcess(arguments, 0, "no" if failure == "linger" else "yes", "")
                if "--property=LoadState" in arguments:
                    return subprocess.CompletedProcess(arguments, 0 if failure == "loaded" else 1, "loaded" if failure == "loaded" else "not-found", "")
                if "verify" in arguments:
                    self.assertFalse(target.exists())
                    self.assertEqual(Path(arguments[-1]).read_text(encoding="utf-8"), module.build_unit(base, database))
                    return subprocess.CompletedProcess(arguments, 0, "", "diagnostic" if failure == "warning" else "")
                if "pip" in arguments and failure == "pip":
                    return subprocess.CompletedProcess(arguments, 1, "", "failure")
                if "daemon-reload" in arguments and failure == "reload":
                    return subprocess.CompletedProcess(arguments, 1, "", "failure")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            for patch in (mock.patch.object(module.sys, "platform", "linux"), mock.patch.object(module.os, "getuid", return_value=1000, create=True), mock.patch.object(module, "MANIFEST_SHA256", manifest_hash), mock.patch.object(module, "private_path"), mock.patch.object(module.os, "access", return_value=True), mock.patch.object(Path, "is_file", autospec=True, side_effect=is_file), mock.patch.object(module.subprocess, "run", side_effect=run)):
                stack.enter_context(patch)
            if failure:
                with self.assertRaises(RuntimeError):
                    module.register(checksum, base, database, home)
                self.assertEqual(target.exists(), failure == "reload")
            else:
                self.assertEqual(module.register(checksum, base, database, home), target)
                previous = target.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                    module.register(checksum, base, database, home)
                self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(database.read_bytes(), database_before)
            self.assertTrue(all(not (set(c) & {"enable", "start", "stop", "restart", "kill", "sudo", "enable-linger"}) for c in commands))
            self.assertTrue(all("wxnotify-infrastructure.service" not in c for c in commands))

    def test_registration_is_exclusive_and_only_reloads_definitions(self):
        self.lifecycle()

    def test_failed_checks_never_start_or_replace_any_service(self):
        for failure in ("linger", "loaded", "pip", "warning", "reload"):
            with self.subTest(failure=failure):
                self.lifecycle(failure)


if __name__ == "__main__":
    unittest.main()
