"""Atomic code-only fix on the existing temporary viewer; preserve its unit, expiry, accounts and data."""
import errno
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import threading
import time

UNIT = "wxnotify-http-viewer.service"


def wait_bindable(reuse, timeout=90):
    """Wait for old connections/listeners to release the port before starting a unit."""
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if reuse:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", 18083))
                return
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
        if time.monotonic() >= deadline:
            raise ValueError("Viewer port did not become bindable within the bounded wait")
        if not announced:
            print("Waiting for previous connections to release port 18083 (up to 90 seconds)...", file=sys.stderr, flush=True)
            announced = True
        time.sleep(1)


def restart_smoke(payload, source_path):
    """Real OS socket preflight using ephemeral loopback ports and fictional health only."""
    ns = {"__name__": "viewer_restart_preflight", "__file__": str(source_path)}
    exec(compile(payload, "<viewer-preflight>", "exec"), ns)
    cls = ns["Viewer"]
    if cls.allow_reuse_address is not True or getattr(cls, "allow_reuse_port", False):
        raise ValueError("Restartable viewer without shared listening ports required")
    port = 0
    for _ in range(2):
        server = cls(("127.0.0.1", port), "http://127.0.0.1:18083", time.time() + 600)
        port = server.server_port
        server.ready = lambda: True  # No request to the real backend or its database.
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        try:
            if server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 1:
                raise ValueError("Restart socket option was not applied")
            c = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                c.request("GET", "/viewer/health", headers={"Host": "127.0.0.1:18083"})
                response = c.getresponse()
                time.sleep(.1)  # Let the server initiate close before the client drains the response.
                data = json.loads(response.read(32768))
                if response.status != 200 or data.get("data_realtime") is not False:
                    raise ValueError("Isolated restart health failed")
            finally:
                c.close()
        finally:
            server.shutdown();server.server_close();thread.join(3)
        if thread.is_alive():
            raise ValueError("Isolated restart did not stop")
    return {"platform": sys.platform, "rounds": 2, "real_backend_accessed": False}


def ready_matches(authority, source_hash, expires, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, data = probe(authority)
            if status == 200 and data.get("gateway_sha256") == source_hash and data.get("expires_at") == expires and data.get("backend_ready") is True:
                return True
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(.5)


def journal_tail():
    try:
        return run(["journalctl", "--user", "-u", UNIT, "-n", "40", "--no-pager", "-o", "cat"])[-8000:]
    except ValueError:
        return "Viewer journal unavailable to this account"


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


def recover_original(config, payload):
    """Inspect only this viewer's journal and restart its verified original code; no source replacement."""
    work = validate_config(config, payload)
    private(work, True)
    target = work / "temporary-http-viewer.py";private(target)
    unit_file = Path.home() / ".config/systemd/user" / UNIT;private(unit_file)
    unit = unit_file.read_text(encoding="utf-8")
    if (unit_file.parent / (UNIT + ".d")).exists() or unit.count("WorkingDirectory=" + str(work)) != 1 or "--origin " + config["public_origin"] + " --expires-at " + str(config["expires_at"]) + " --temporary-http-confirmed" not in unit:
        raise ValueError("Unexpected viewer unit; recovery refused")
    if digest(target) != config["expected_old_sha256"]:
        raise ValueError("Original viewer source not restored; inspect without overwriting")
    fields = ["-p", "ActiveState", "-p", "SubState", "-p", "Result", "-p", "NRestarts", "-p", "ExecMainStatus"]
    before = run(["systemctl", "--user", "show", UNIT, "--no-pager", *fields])
    def read_journal():
        try:
            return run(["journalctl", "--user", "-u", UNIT, "-n", "60", "--no-pager", "-o", "cat"])[-8000:]
        except ValueError:
            return "Viewer journal unavailable to this account"
    journal = read_journal()
    authority = config["public_origin"][7:]
    def healthy():
        try:
            status, data = probe(authority)
            return status == 200 and data.get("gateway_sha256") == config["expected_old_sha256"] and data.get("expires_at") == config["expires_at"] and data.get("backend_ready") is True
        except (OSError, ValueError):
            return False
    already_healthy = healthy()
    start_failed = False
    if not already_healthy:
        try:
            run(["systemctl", "--user", "reset-failed", UNIT])
            run(["systemctl", "--user", "start", UNIT])
        except ValueError:
            start_failed = True
    recovered = False
    for _ in range(30):
        if healthy():
            recovered = True;break
        time.sleep(.5)
    after = run(["systemctl", "--user", "show", UNIT, "--no-pager", *fields])
    return {"format": "future-school-http-viewer-original-recovery", "schema_version": 1,
            "public_url": config["public_origin"], "gateway_sha256": digest(target), "expires_at": config["expires_at"],
            "original_viewer_ready": recovered, "already_healthy": already_healthy,
            "service_before": before, "service_after": after, "viewer_journal_tail": journal,
            "viewer_journal_after": read_journal(), "start_operation_failed": start_failed,
            "source_changed": False, "unit_config_changed": False, "database_opened": False,
            "teacher_credentials_used": False, "expiry_changed": False, "other_services_modified": False}


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
    preflight = restart_smoke(payload, target)  # Must pass before stopping the existing public viewer.
    backup = work / "temporary-http-viewer.before-logout-fix.py"
    if current != config["new_sha256"]:
        if backup.exists() or backup.is_symlink():
            private(backup)
        else:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(target.read_bytes())
        if digest(backup) != config["expected_old_sha256"]:
            raise ValueError("Update backup differs")
        pending = work / ("temporary-http-viewer.update-" + config["new_sha256"][:12] + ".pending")
        with os.fdopen(os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(payload)
        run(["systemctl", "--user", "stop", UNIT])
        phase = "wait_for_port_release"
        try:
            wait_bindable(True)  # Old non-reuse sockets may need their natural timeout even with new code.
            os.replace(pending, target)
            phase = "start_updated_viewer"
            run(["systemctl", "--user", "reset-failed", UNIT])
            run(["systemctl", "--user", "start", UNIT])
            phase = "verify_updated_viewer"
            if not ready_matches(authority, config["new_sha256"], config["expires_at"]):
                raise ValueError("New viewer readiness failed")
            if probe(authority, "POST", "/v1/auth/logout")[0] != 401:
                raise ValueError("Header-only logout compatibility failed")
        except Exception:
            failure_journal = journal_tail()
            restored = False
            try:
                run(["systemctl", "--user", "stop", UNIT])
                if digest(target) != config["expected_old_sha256"]:
                    restore = work / ("temporary-http-viewer.rollback-" + config["new_sha256"][:12] + ".pending")
                    with os.fdopen(os.open(restore, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
                        stream.write(backup.read_bytes())
                    os.replace(restore, target)
                wait_bindable(False)  # Verify that the original non-reuse listener can bind too.
                run(["systemctl", "--user", "reset-failed", UNIT])
                run(["systemctl", "--user", "start", UNIT])
                restored = ready_matches(authority, config["expected_old_sha256"], config["expires_at"])
            except Exception:
                pass
            return {"format": "future-school-http-viewer-update-failed", "schema_version": 1,
                    "public_url": config["public_origin"], "expires_at": config["expires_at"],
                    "requested_sha256": config["new_sha256"], "active_source_sha256": digest(target),
                    "failed_phase": phase, "original_viewer_restored": restored, "viewer_journal_tail": failure_journal,
                    "viewer_journal_after": journal_tail(), "isolated_restart_preflight": preflight,
                    "unit_config_changed": False, "database_opened": False, "teacher_credentials_used": False,
                    "expiry_changed": False, "other_services_modified": False}
    # No real credentials: a header-only logout should reach the backend and reject anonymous access.
    status, _ = probe(authority, "POST", "/v1/auth/logout")
    if status != 401:
        raise ValueError("Header-only logout was not accepted by the authenticated backend")
    return {"format": "future-school-http-viewer-logout-fixed", "schema_version": 1, "public_url": config["public_origin"],
            "old_sha256": config["expected_old_sha256"], "new_sha256": digest(target), "expires_at": config["expires_at"],
            "isolated_restart_preflight": preflight,
            "logout_without_json_reaches_backend": True, "anonymous_logout_rejected": True,
            "unit_config_changed": False, "database_opened": False, "teacher_credentials_used": False,
            "expiry_changed": False, "other_services_modified": False}
