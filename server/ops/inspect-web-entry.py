"""Read-only network-entry inventory. No database, certificate key or privileged command reads."""
import json
import re
import shutil
import subprocess
from urllib.request import build_opener, ProxyHandler

PORTS = {22, 80, 443, 18080, 18081, 18082, 18183, 18443}


def listening(text):
    result = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address = fields[3]
        match = re.search(r":([0-9]+)$", address)
        if match and int(match[1]) in PORTS:
            host = address[:match.start()].strip("[]")
            result.append({"port": int(match[1]), "loopback_only": host in {"127.0.0.1", "::1"}})
    return sorted(result, key=lambda r: (r["port"], r["loopback_only"]))


def read_command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=8)
        return r.stdout[:65536] if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def inspect():
    sockets = read_command(["ss", "-ltnH"])
    ports = listening(sockets or "")
    health = None
    try:
        with build_opener(ProxyHandler({})).open("http://127.0.0.1:18082/health/ready", timeout=3) as response:
            raw = response.read(32769)
        if len(raw) <= 32768:
            obj = json.loads(raw)
            keys = ("status", "version", "mode", "archive_enabled", "roster_review_only", "business_ready", "wechat_sending_enabled", "student_sync_enabled", "parent_binding_enabled")
            if all(isinstance(obj.get(k), (str, bool)) for k in keys):
                health = {k: obj[k] for k in keys}
    except (OSError, ValueError):
        pass
    backend_ok = bool(health and health.get("status") == "ready" and health.get("version") == "0.5.0"
                      and health.get("roster_review_only") is True and health.get("mode") == "roster_review"
                      and all(health.get(k) is False for k in ("business_ready", "wechat_sending_enabled", "student_sync_enabled", "parent_binding_enabled")))
    status = read_command(["systemctl", "--user", "show", "wxnotify-roster-review.service", "--no-pager", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState"])
    service = dict(line.split("=", 1) for line in (status or "").splitlines() if line.startswith(("ActiveState=", "SubState=", "UnitFileState=")))
    return {"format": "future-school-web-entry-inventory", "schema_version": 1, "read_only": True,
            "ports": ports, "port_inventory_available": sockets is not None,
            "tools": {name: bool(shutil.which(name)) for name in ("nginx", "caddy", "certbot", "openssl")},
            "review_health": health, "review_backend_ready": backend_ok, "review_service": service,
            "server_modified": False, "database_opened": False, "private_keys_read": False,
            "public_entry_enabled": False,
            "pending": ["trusted_certificate_for_selected_address", "https_listener_and_proxy_configuration", "firewall_and_cloud_network_check", "external_teacher_login_acceptance"]}


if __name__ == "__main__":
    print(json.dumps(inspect(), separators=(",", ":")))
