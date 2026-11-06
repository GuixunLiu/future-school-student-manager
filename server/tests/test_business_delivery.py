"""Backup, reproducible package, CLI boundaries and real loopback HTTP checks."""

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from business.backup import backup
from business import cli
from business import VERSION
from business.store import Store
from business.api import create_app
from fastapi.testclient import TestClient

TEST_PASSWORD = "Fictional delivery test passphrase!"


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def initialize(self):
        database = self.folder / "private.sqlite"
        store = Store.bootstrap(database, "fixture-school", "fixture-owner", "Fictional owner", TEST_PASSWORD)
        return database, store

    def test_backup_consistent_restorable_and_never_overwrites(self):
        database, store = self.initialize()
        target = self.folder / "new-backup.sqlite"
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        result = backup(database, target)
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
        restored = Store(target).login("fixture-school", "fixture-owner", TEST_PASSWORD, "127.0.0.1")
        self.assertTrue(restored["access_token"].startswith("wxu_"))
        with self.assertRaises(FileExistsError):
            backup(database, target)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertTrue(store.path.exists())

    def test_removed_database_is_not_recreated_by_api_connection(self):
        database, store = self.initialize()
        app = create_app(database, allow_test_client=True)
        database.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            store.connect()
        with TestClient(app) as client:
            self.assertEqual(client.get("/health/ready").status_code, 503)
        self.assertFalse(database.exists())

    def test_cli_password_is_prompted_no_default_or_argument_and_no_auto_start(self):
        target = self.folder / "prompted.sqlite"
        argv = ["business.cli", "bootstrap", "--database", str(target), "--organization", "fixture-school", "--login", "fixture-owner", "--display-name", "Fictional owner"]
        with patch.object(sys, "argv", argv), patch("business.cli.getpass.getpass", side_effect=[TEST_PASSWORD, TEST_PASSWORD]) as prompt, patch("builtins.print") as output:
            cli.main()
        self.assertEqual(prompt.call_count, 2)
        self.assertNotIn(TEST_PASSWORD, str(output.call_args_list))
        self.assertTrue(target.is_file())
        with patch.object(sys, "argv", argv + ["--password", TEST_PASSWORD]), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                cli.main()

    def test_cli_loopback_only_and_failclosed_flags(self):
        database, _ = self.initialize()
        argv = ["business.cli", "serve", "--database", str(database), "--local-test", "--port", "18081"]
        for flag in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC", "WXNOTIFY_ENABLE_PARENT_BINDING"):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: "1"}), patch.object(sys, "argv", argv), patch("sys.stderr"), patch("uvicorn.run") as run:
                with self.assertRaises(SystemExit):
                    cli.main()
                run.assert_not_called()
        environment = {key: "0" for key in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC", "WXNOTIFY_ENABLE_PARENT_BINDING")}
        with patch.dict(os.environ, environment), patch.object(sys, "argv", argv), patch("uvicorn.run") as run:
            cli.main()
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertFalse(run.call_args.kwargs["proxy_headers"])
        self.assertFalse(run.call_args.kwargs["access_log"])
        self.assertEqual(run.call_args.kwargs["limit_concurrency"], 32)

    def test_business_zip_allowlist_hashes_reproducible_and_exclusive(self):
        spec = importlib.util.spec_from_file_location("business_packaging", ROOT / "package-business.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first, checksum = module.package(self.folder / "first")
        second, checksum2 = module.package(self.folder / "second")
        self.assertEqual(checksum, checksum2)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(FileExistsError):
            module.package(self.folder / "first")
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(set(archive.namelist()), set(module.FILES) | {"MANIFEST.json"})
            manifest = json.loads(archive.read("MANIFEST.json"))
            self.assertFalse(manifest["wechat_sending_enabled"])
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            self.assertTrue(all(not name.endswith((".sqlite", ".db", ".env", ".apk")) for name in archive.namelist()))
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                module.package(self.folder / "symlink")

    def test_real_http_login_authorized_class_and_restart_persistence(self):
        database, _ = self.initialize()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", WXNOTIFY_ENABLE_WECHAT="0", WXNOTIFY_ENABLE_STUDENT_SYNC="0", WXNOTIFY_ENABLE_PARENT_BINDING="0")
        command = [sys.executable, "-m", "business.cli", "serve", "--database", str(database), "--local-test", "--port", str(port)]
        opener = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        def request(path, data=None, token=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            request = Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            with opener.open(request, timeout=2) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("server", response.headers)
                return json.load(response)
        with tempfile.TemporaryFile(mode="w+b") as log:
            class_id, token = None, None
            for run_number in range(2):
                process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            health = request("/health/ready")
                            break
                        except URLError:
                            if process.poll() is not None or time.monotonic() >= deadline:
                                self.fail("Local test process failed to become ready")
                            time.sleep(0.05)
                    self.assertEqual(health["version"], VERSION)
                    self.assertFalse(health["business_ready"])
                    if run_number == 0:
                        token = request("/v1/auth/login", {"organization": "fixture-school", "login": "fixture-owner", "password": TEST_PASSWORD})["access_token"]
                        class_id = request("/v1/admin/classes", {"name": "Fictional HTTP class", "course_name": "Fictional course"}, token)["id"]
                    self.assertEqual(request("/v1/classes", token=token)["items"][0]["id"], class_id)
                    self.assertFalse(request("/v1/admin/notification-schedule", token=token)["settings"]["enabled"])
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            log.seek(0)
            log_text = log.read().decode("utf-8", errors="replace")
            self.assertNotIn(TEST_PASSWORD, log_text)
            self.assertNotIn(token, log_text)
            self.assertNotIn("Fictional HTTP class", log_text)


if __name__ == "__main__":
    unittest.main()
