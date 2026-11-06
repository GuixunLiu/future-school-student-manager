"""Synthetic/local tests only. No server login, Docker or WeChat requests."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.request import urlopen
import zipfile

from fastapi.testclient import TestClient
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from wxnotify_api import MODE, VERSION, create_app
from preflight import inspect

spec = importlib.util.spec_from_file_location("package_release", ROOT / "package-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {
            "WXNOTIFY_MODE": MODE,
            "WXNOTIFY_ENABLE_WECHAT": "0",
            "WXNOTIFY_ENABLE_STUDENT_SYNC": "0",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(create_app())

    def test_health_reports_infrastructure_not_business_success(self):
        with mock.patch("socket.create_connection", side_effect=AssertionError("Unexpected outbound request")):
            self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})
            self.assertEqual(self.client.get("/health/ready").json(), {
                "status": "ready", "mode": MODE, "version": VERSION,
                "business_ready": False, "wechat_sending_enabled": False,
                "student_sync_enabled": False, "parent_binding_enabled": False,
            })
            self.assertEqual(self.client.get("/").json(), {
                "service": "wxnotify", "mode": MODE, "version": VERSION,
            })

    def test_no_business_documentation_or_file_routes(self):
        for route in ("/docs", "/redoc", "/openapi.json", "/students", "/v1/events",
                      "/wechat/callback", "/admin", "/.env", "/Dockerfile", "/etc/passwd"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 404)

    def test_no_data_uploads_no_cors_and_no_reflected_secret(self):
        marker = "synthetic-secret-do-not-reflect"
        for route in ("/", "/health/live", "/health/ready", "/v1/events"):
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(route=route, method=method):
                    response = self.client.request(method, route, content=marker,
                        headers={"Authorization": "Bearer " + marker,
                                 "Origin": "https://example.invalid"})
                    self.assertIn(response.status_code, (404, 405))
                    self.assertNotIn(marker, response.text)
                    self.assertNotIn("access-control-allow-origin", response.headers)

    def test_security_headers_errors_heads_and_queries(self):
        for route in ("/", "/health/live", "/health/ready", "/unknown"):
            response = self.client.get(route + "?token=synthetic-never-echo")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertNotIn("synthetic-never-echo", response.text)
        self.assertEqual(self.client.head("/health/ready").status_code, 200)
        self.assertEqual(self.client.head("/health/ready").content, b"")
        self.assertEqual(self.client.get("/health/ready/", follow_redirects=False).status_code, 404)

    def test_environment_cannot_enable_unimplemented_features(self):
        for name in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC"):
            for value in ("1", "true", "TRUE", "false", "", "typo"):
                with self.subTest(name=name, value=value), mock.patch.dict(os.environ, {name: value}):
                    with self.assertRaises(RuntimeError):
                        create_app()
        with mock.patch.dict(os.environ, {"WXNOTIFY_MODE": "production"}):
            with self.assertRaises(RuntimeError):
                create_app()


class DeploymentTests(unittest.TestCase):
    def test_compose_only_loopback_nonroot_bounded_without_host_mounts(self):
        config = yaml.safe_load((ROOT / "for-admin/docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(set(config), {"services"})
        self.assertEqual(set(config["services"]), {"api"})
        service = config["services"]["api"]
        self.assertEqual(service["build"], {"context": "./app", "dockerfile": "Dockerfile"})
        self.assertEqual(service["ports"], ["127.0.0.1:18080:8000"])
        self.assertEqual(service["user"], "10001:10001")
        self.assertTrue(service["read_only"])
        self.assertTrue(service["init"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(service["cpus"], 0.5)
        self.assertEqual(service["mem_limit"], "256m")
        self.assertEqual(service["pids_limit"], 64)
        self.assertEqual(service["logging"]["options"], {"max-size": "10m", "max-file": "3"})
        for forbidden in ("volumes", "privileged", "network_mode", "devices", "env_file", "container_name"):
            self.assertNotIn(forbidden, service)
        self.assertEqual(service["environment"], {
            "WXNOTIFY_MODE": MODE, "WXNOTIFY_ENABLE_WECHAT": "0", "WXNOTIFY_ENABLE_STUDENT_SYNC": "0",
        })
        check = service["healthcheck"]["test"]
        self.assertEqual(check[:3], ["CMD", "python", "-c"])
        compile(check[3], "healthcheck", "exec")
        self.assertIn("d.get('business_ready') is False", check[3])

    def test_dockerfile_limits_copy_and_uses_reviewed_server_flags(self):
        dockerfile = (ROOT / "app/Dockerfile").read_text(encoding="utf-8")
        command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith("CMD ")))
        for flag in ("--no-access-log", "--no-server-header", "--no-proxy-headers"):
            self.assertIn(flag, command)
        self.assertNotIn("--reload", command)
        self.assertEqual(command[command.index("--workers") + 1], "1")
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertNotIn("COPY .", dockerfile)
        ignore = (ROOT / "app/.dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(ignore[0], "*")
        self.assertEqual(set(ignore[1:]), {"!Dockerfile", "!requirements.txt", "!wxnotify_api.py"})

    def test_release_allowlist_and_exclusive_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output, digest = release.package(Path(directory))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(release.FILES))
                for name in archive.namelist():
                    self.assertFalse(name.startswith(("/", "\\")))
                    self.assertNotIn("..", Path(name).parts)
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
            self.assertEqual(len(digest), 64)
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                release.package(Path(directory))
            self.assertEqual(before, output.read_bytes())

    def test_preflight_missing_config_read_only_and_busy_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            for name in ("Dockerfile", "requirements.txt", "wxnotify_api.py"):
                (root / "app" / name).touch()
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            with mock.patch("preflight.socket.socket") as probe:
                result = inspect(root, 1000)
                self.assertFalse(result["ok"])
                self.assertIn("Administrator must install the reviewed docker-compose.yml", result["errors"])
                probe.return_value.close.assert_called_once()
            with mock.patch("preflight.socket.socket") as probe:
                probe.return_value.bind.side_effect = OSError("busy")
                result = inspect(root, 1000)
                self.assertTrue(any("port is unavailable" in error for error in result["errors"]))
                probe.return_value.close.assert_called_once()
            self.assertEqual(before, sorted(str(path.relative_to(root)) for path in root.rglob("*")))

    def test_preflight_accepts_safe_layout_rejects_writable_config_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            app_dir.mkdir()
            for name in ("Dockerfile", "requirements.txt", "wxnotify_api.py"):
                (app_dir / name).touch()
            config = root / "docker-compose.yml"
            config.touch()
            actual_stat = Path.stat

            def safe_stat(path, *args, **kwargs):
                info = list(actual_stat(path, *args, **kwargs))
                if path in (root, config):
                    info[0] &= ~0o022
                    info[4] = 0
                return os.stat_result(info)

            def safe_access(path, mode):
                return mode != os.W_OK or path == app_dir

            with mock.patch.object(Path, "stat", autospec=True, side_effect=safe_stat), \
                 mock.patch("preflight.os.access", side_effect=safe_access), \
                 mock.patch("preflight.socket.socket"):
                self.assertTrue(inspect(root, 1000)["ok"])
                with mock.patch("preflight.os.access", return_value=True):
                    result = inspect(root, 1000)
                    self.assertFalse(result["ok"])
                    self.assertTrue(any("root must remain" in error for error in result["errors"]))
                    self.assertTrue(any("must not be able" in error for error in result["errors"]))
                with mock.patch.object(Path, "is_symlink", return_value=True):
                    self.assertFalse(inspect(root, 1000)["ok"])


class HttpSmokeTest(unittest.TestCase):
    def test_real_loopback_server_without_student_data(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", WXNOTIFY_MODE=MODE,
            WXNOTIFY_ENABLE_WECHAT="0", WXNOTIFY_ENABLE_STUDENT_SYNC="0")
        command = [sys.executable, "-m", "uvicorn", "wxnotify_api:app", "--host", "127.0.0.1",
            "--port", str(port), "--no-access-log", "--no-proxy-headers", "--no-server-header", "--ws", "none"]
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(command, cwd=ROOT / "app", env=environment,
                stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        self.fail("Local server stopped: " + log.read().decode("utf-8", errors="replace"))
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=1) as response:
                            result = json.load(response)
                            self.assertEqual(response.status, 200)
                            self.assertFalse(result["business_ready"])
                            self.assertNotIn("server", response.headers)
                            break
                    except URLError:
                        if time.monotonic() >= deadline:
                            self.fail("Local server did not become ready")
                        time.sleep(0.05)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
