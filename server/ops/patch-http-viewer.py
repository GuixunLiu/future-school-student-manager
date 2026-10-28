"""Atomic code-only fix on the existing temporary viewer; preserve its unit, expiry, accounts and data."""
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

UNIT = "wxnotify-http-viewer.service"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private(path, directory=False):
    if os.name != "posix" or os.geteuid() == 0 or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Ordinary account and nonlinked paths required")
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077 or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        raise ValueError("Private owned path required")


def run(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise ValueError("Viewer operation failed")
    return r.stdout.strip()


def probe(authority, method="GET", route="/viewer/health"):
    c = HTTPConnection("127.0.0.1", 18083, timeout=3)
    try:
        c.request(method, route, headers={"Host": authority, "Content-Length": "0"})
        r = c.getresponse();raw = r.read(32769)
        return r.status, json.loads(raw)
    finally:
        c.close()


def validate_config(config, payload):
    if not re.fullmatch(r"/opt/wxnotify/app/http-viewer-[0-9]{14}-[a-f0-9]{8}", config["work_directory"]) or not re.fullmatch(r"http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}:18083", config["public_origin"]):
        raise ValueError("Only the existing viewer is supported")
    for k in ("expected_old_sha256", "new_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", config[k]):
            raise ValueError("Explicit source checksums required")
    if not isinstance(payload, bytes) or len(payload) > 131072 or hashlib.sha256(payload).hexdigest() != config["new_sha256"]:
        raise ValueError("New code checksum differs")
    compile(payload, "<viewer-update>", "exec")
    if not isinstance(config["expires_at"], int) or isinstance(config["expires_at"], bool) or config["expires_at"] <= time.time():
        raise ValueError("Existing deadline must remain unexpired")
    return Path(config["work_directory"])


def install(config, payload):
    work = validate_config(config, payload)
    private(work, True)
    target = work / "temporary-http-viewer.py";private(target)
    unit_file = Path.home() / ".config/systemd/user" / UNIT;private(unit_file)
    if (unit_file.parent / (UNIT + ".d")).exists():
        raise ValueError("Viewer overrides require review")
    unit = unit_file.read_text(encoding="utf-8")
    if unit.count("WorkingDirectory=" + str(work)) != 1 or "--origin " + config["public_origin"] + " --expires-at " + str(config["expires_at"]) + " --temporary-http-confirmed" not in unit:
        raise ValueError("Viewer unit or expiry changed")
    if run(["systemctl", "--user", "show", UNIT, "-p", "WorkingDirectory", "--value"]) != str(work):
        raise ValueError("Active viewer directory differs")
    authority = config["public_origin"][7:]
    status, before = probe(authority)
    if status != 200 or before.get("expires_at") != config["expires_at"] or before.get("backend_ready") is not True:
        raise ValueError("Existing viewer must be healthy and unexpired")
    current = digest(target)
    if current not in {config["expected_old_sha256"], config["new_sha256"]} or before.get("gateway_sha256") != current:
        raise ValueError("Unexpected existing code; no overwrite")
    backup = work / "temporary-http-viewer.before-logout-fix.py"
    if current != config["new_sha256"]:
        if backup.exists() or backup.is_symlink():
            raise ValueError("Prior update backup exists; inspect before retry")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(target.read_bytes())
        if digest(backup) != current:
            raise ValueError("Update backup differs")
        pending = work / "temporary-http-viewer.logout-fix.pending"
        with os.fdopen(os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(payload)
        run(["systemctl", "--user", "stop", UNIT])
        try:
            os.replace(pending, target)
            run(["systemctl", "--user", "start", UNIT])
            healthy = False
            for _ in range(20):
                try:
                    status, data = probe(authority)
                    if status == 200 and data.get("gateway_sha256") == config["new_sha256"] and data.get("expires_at") == config["expires_at"] and data.get("backend_ready") is True:
                        healthy = True;break
                except OSError:
                    pass
                time.sleep(.5)
            if not healthy:
                raise ValueError("New viewer readiness failed")
            if probe(authority, "POST", "/v1/auth/logout")[0] != 401:
                raise ValueError("Header-only logout compatibility failed")
        except BaseException:
            run(["systemctl", "--user", "stop", UNIT])
            restore = work / "temporary-http-viewer.logout-rollback.pending"
            with os.fdopen(os.open(restore, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
                stream.write(backup.read_bytes())
            os.replace(restore, target)
            run(["systemctl", "--user", "start", UNIT])
            raise
    # No real credentials: a header-only logout should reach the backend and reject anonymous access.
    status, _ = probe(authority, "POST", "/v1/auth/logout")
    if status != 401:
        raise ValueError("Header-only logout was not accepted by the authenticated backend")
    return {"format": "future-school-http-viewer-logout-fixed", "schema_version": 1, "public_url": config["public_origin"],
            "old_sha256": config["expected_old_sha256"], "new_sha256": digest(target), "expires_at": config["expires_at"],
            "logout_without_json_reaches_backend": True, "anonymous_logout_rejected": True,
            "unit_config_changed": False, "database_opened": False, "teacher_credentials_used": False,
            "expiry_changed": False, "other_services_modified": False}
