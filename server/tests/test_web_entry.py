import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


def module(name):
    path = Path(__file__).resolve().parents[1] / "ops" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


nginx, inventory = module("viewer-nginx"), module("inspect-web-entry")


class WebEntryTests(unittest.TestCase):
    def test_origin_and_path_injection_private_ip_and_plain_http_rejected(self):
        for origin in ("http://school.example", "https://127.0.0.1", "https://10.0.0.1", "https://[::1]", "https://school.example/path", "https://u:p@school.example", "https://school.example#x", "https://school.example;evil"):
            with self.assertRaises(ValueError):
                nginx.render(origin, "/etc/ssl/view/fullchain.pem", "/etc/ssl/view/privkey.pem")
        for path in ("/tmp/key;evil", "/tmp/../key", "/tmp/$key", "relative/key", "/tmp/key\n}"):
            with self.assertRaises(ValueError):
                nginx.render("https://school.example", path, "/etc/key.pem")

    def test_ip_or_domain_template_preserves_readonly_auth_and_external_origin_gate(self):
        for origin in ("https://8.8.8.8:18443", "https://school.example"):
            config = nginx.render(origin, "/etc/view/fullchain.pem", "/etc/view/privkey.pem")
            self.assertIn('if ($wxnotify_viewer_origin != "' + origin + '")', config)
            self.assertIn('proxy_set_header Host "127.0.0.1:18082"', config)
            self.assertEqual(config.count("proxy_pass http://127.0.0.1:18082;"), 3)
            self.assertIn("limit_except GET { deny all; }", config)
            self.assertEqual(config.count("limit_except POST { deny all; }"), 2)
            self.assertIn("access_log off;", config)
            self.assertNotIn("listen 80;", config)
            self.assertNotIn("default_server", config)

    def test_inventory_only_reports_selected_ports_and_no_process_or_raw_address(self):
        ports = inventory.listening("LISTEN 0 128 127.0.0.1:18082 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:443 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:9999 0.0.0.0:*")
        self.assertEqual(ports, [{"port": 443, "loopback_only": False}, {"port": 18082, "loopback_only": True}])

    def test_inventory_does_not_claim_public_readiness_or_expose_arbitrary_health_fields(self):
        health = {"status": "ready", "version": "0.5.0", "mode": "roster_review", "archive_enabled": True, "roster_review_only": True, "business_ready": False, "wechat_sending_enabled": False, "student_sync_enabled": False, "parent_binding_enabled": False, "secret": "MUST_NOT_EXPORT"}
        from io import BytesIO
        class Opener:
            def open(self, *a, **kw):
                return BytesIO(json.dumps(health).encode())
        with patch.object(inventory, "build_opener", return_value=Opener()), patch.object(inventory, "read_command", return_value=""), patch.object(inventory.shutil, "which", return_value=None):
            result = inventory.inspect()
        self.assertTrue(result["review_backend_ready"])
        self.assertFalse(result["public_entry_enabled"])
        self.assertFalse(result["database_opened"])
        self.assertNotIn("MUST_NOT_EXPORT", json.dumps(result))
        with patch.object(inventory, "build_opener", return_value=Opener()), patch.object(inventory, "read_command", return_value=None):
            self.assertFalse(inventory.inspect()["port_inventory_available"])


if __name__ == "__main__":
    unittest.main()
