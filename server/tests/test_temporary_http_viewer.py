import importlib.util
import json
from http.client import HTTPConnection
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import uvicorn
from business_next.api import create_app
from serve_sync_pilot_fixture import build, PASSWORD

spec = importlib.util.spec_from_file_location("temporary_viewer", Path(__file__).resolve().parents[1] / "ops/temporary-http-viewer.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class TemporaryViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        db = Path(cls.temp.name) / "fictional.sqlite"
        # The seeded fixture is fictional; expose its separate READ-ONLY app for the viewer tests.
        build(db, lambda: 1793156400)
        sock = socket.socket();sock.bind(("127.0.0.1", 0));port = sock.getsockname()[1];sock.close()
        cls.backend = uvicorn.Server(uvicorn.Config(create_app(db), host="127.0.0.1", port=port, access_log=False, log_level="critical", ws="none"))
        cls.bt = threading.Thread(target=cls.backend.run, daemon=True);cls.bt.start()
        for _ in range(100):
            if cls.backend.started:
                break
            time.sleep(.05)
        assert cls.backend.started
        cls.http = viewer.Viewer(("127.0.0.1", 0), "http://127.0.0.1:18083", time.time() + 3600, upstream=("127.0.0.1", port))
        cls.http.authority = "127.0.0.1:" + str(cls.http.server_port)
        cls.http.public_origin = "http://" + cls.http.authority
        cls.ht = threading.Thread(target=cls.http.serve_forever, daemon=True);cls.ht.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.ht.join(5)
        cls.backend.should_exit = True;cls.bt.join(5);cls.temp.cleanup()

    def request(self, method, path, data=None, token=None, headers=None):
        c = HTTPConnection("127.0.0.1", self.http.server_port, timeout=10)
        h = {"Host": self.http.authority, "Origin": self.http.public_origin, **(headers or {})}
        if token:
            h["Authorization"] = "Bearer " + token
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, json.dumps(data) if data is not None else None, h)
        r = c.getresponse();body = r.read();result = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), body);c.close();return result

    def login(self, name):
        status, _, raw = self.request("POST", "/v1/auth/login", {"organization": "usb-fixture", "login": name, "password": PASSWORD})
        self.assertEqual(status, 200, raw)
        return json.loads(raw)["access_token"]

    def test_existing_teacher_account_scope_admin_isolation_and_dual_role(self):
        self.assertEqual(self.request("GET", "/v1/students")[0], 401)
        teacher = self.login("usb-teacher")
        rows = json.loads(self.request("GET", "/v1/classes", token=teacher)[2])["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(json.loads(self.request("GET", "/v1/students", token=teacher)[2])["items"]), 2)
        self.assertEqual(self.request("GET", "/v1/admin/archives", token=teacher)[0], 403)
        owner = self.login("usb-owner")
        self.assertEqual(set(json.loads(self.request("GET", "/v1/me", token=owner)[2])["roles"]), {"admin", "teacher"})
        others = json.loads(self.request("GET", "/v1/students", token=owner)[2])["items"]
        other = next(s for s in others if s["external_ref"] == "26020001")
        self.assertEqual(self.request("GET", "/v1/students/" + other["id"], token=teacher)[0], 403)
        self.assertEqual(self.request("POST", "/v1/auth/logout", token=teacher)[0], 200)
        self.assertEqual(self.request("GET", "/v1/me", token=teacher)[0], 401)
        self.request("POST", "/v1/auth/logout", {}, token=owner)

    def test_business_writes_and_other_methods_never_reach_backend(self):
        for method, route in (("POST", "/v1/admin/students"), ("PUT", "/v1/admin/devices/abc/sync-access"), ("DELETE", "/v1/students/abc"), ("OPTIONS", "/v1/students"), ("GET", "/v1/auth/login")):
            self.assertEqual(self.request(method, route)[0], 405)

    def test_external_host_origin_and_ambiguous_body_limits(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/v1/auth/login", {}, headers={"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.request("GET", "/", headers={"Content-Length": "16385"})[0], 413)
        self.assertEqual(self.request("GET", "http://evil.example/")[0], 400)
        self.assertEqual(self.request("POST", "/v1/auth/login")[0], 415)

    def test_health_is_snapshot_only_no_store_banner_and_forwarded_headers_not_trusted(self):
        status, headers, raw = self.request("GET", "/", headers={"X-Forwarded-For": "203.0.113.1", "X-Forwarded-Host": "evil.example"})
        self.assertEqual(status, 200)
        self.assertIn("临时HTTP查看".encode(), raw)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", headers)
        health = json.loads(self.request("GET", "/viewer/health")[2])
        self.assertFalse(health["data_realtime"])
        self.assertFalse(health["wechat_enabled"])
        self.assertEqual(self.request("HEAD", "/")[2], b"")

    def test_expiry_unready_backend_and_login_rate_stop_access(self):
        with patch.object(self.http, "clock", return_value=self.http.expires_at + 1):
            self.assertEqual(self.request("GET", "/")[0], 410)
        with patch.object(self.http, "ready", return_value=False):
            self.assertEqual(self.request("GET", "/")[0], 503)
        for _ in range(20):
            self.assertTrue(self.http.login_allowed("fictional-rate-test"))
        self.assertFalse(self.http.login_allowed("fictional-rate-test"))


if __name__ == "__main__":
    unittest.main()
