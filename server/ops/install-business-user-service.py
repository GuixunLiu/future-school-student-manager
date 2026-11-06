"""Validate and register the v0.4.0 loopback pilot USER unit; never start or replace a service."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile

UNIT_NAME = "wxnotify-business-pilot.service"
RELEASE_DIR = Path("/opt/wxnotify/app/business-v0.4.0-c59dab1f")
DATABASE = Path("/opt/wxnotify/app/business-data-v0.4.0/pilot.sqlite")
MANIFEST_SHA256 = "ea92895290b3c4e8a6477f5f6962a8c15f60651da00c51725933acd89cad37f4"


def build_unit(base=RELEASE_DIR, database=DATABASE):
    prefix, data = base.as_posix(), database.as_posix()
    if any(re.search(r"[\s%\\\x00]", text) for text in (prefix, data)):
        raise ValueError("Unsupported unit path")
    return f"""[Unit]
Description=wxnotify business pilot (internal test, no WeChat sending)
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=exec
WorkingDirectory={prefix}
ExecStart={prefix}/.venv/bin/python -m business.cli serve --database {data} --local-test --port 18081
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=yes
UMask=0077
LimitCORE=0
LimitNOFILE=1024
MemoryAccounting=yes
CPUAccounting=yes
TasksAccounting=yes
MemoryMax=256M
CPUQuota=50%
TasksMax=64
Environment=WXNOTIFY_ENABLE_WECHAT=0 WXNOTIFY_ENABLE_STUDENT_SYNC=0 WXNOTIFY_ENABLE_PARENT_BINDING=0 WEB_CONCURRENCY=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=journal
StandardError=journal
SyslogIdentifier=wxnotify-business-pilot
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100

[Install]
WantedBy=default.target
"""


def invoke(arguments, *, allow_not_found=False):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=60)
    if allow_not_found and result.stdout.strip() == "not-found" and result.returncode in (0, 1):
        return result
    if result.returncode:
        raise RuntimeError("Prerequisite command failed: " + " ".join(arguments[:4]))
    return result


def private_path(path, *, directory=False):
    information = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(information.st_mode) or information.st_uid != os.getuid() or information.st_mode & 0o077:
        raise RuntimeError("Expected an owned private non-symlink path: " + str(path))


def database_check(path):
    private_path(path.parent, directory=True)
    private_path(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA application_id").fetchone()[0] != 1465405012 or db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise RuntimeError("Database identity or integrity check failed; nothing was changed")
        if not db.execute("SELECT 1 FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.active=1 AND r.role='admin' LIMIT 1").fetchone():
            raise RuntimeError("No active administrator; initialize separately before registration")


def verify_release(base):
    private_path(base, directory=True)
    manifest_path = base / "MANIFEST.json"
    if manifest_path.is_symlink() or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("Release manifest differs from the reviewed v0.4.0 package")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != "0.4.0" or len(manifest.get("files", {})) != 19:
        raise RuntimeError("Unexpected release manifest")
    for name, checksum in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise RuntimeError("Unsafe manifest path")
        source = base / relative
        if any(p.is_symlink() for p in (source, *source.parents) if p != base.parent) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
            raise RuntimeError("Release source changed or is missing: " + name)
    python = base / ".venv/bin/python"
    if (base / ".venv").is_symlink() or not (base / ".venv/pyvenv.cfg").is_file() or not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("A dedicated executable virtualenv is required")
    return python


def verified_backup(database, checksum):
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Use the SHA-256 from the completed backup result")
    for candidate in database.parent.glob("pilot-before-service-*.sqlite"):
        private_path(candidate)
        if hashlib.sha256(candidate.read_bytes()).hexdigest() == checksum:
            database_check(candidate)
            return candidate
    raise RuntimeError("No private consistent backup matches the supplied SHA-256")


def register(backup_sha256, base=RELEASE_DIR, database=DATABASE, home=None):
    if sys.platform != "linux" or not hasattr(os, "getuid") or os.getuid() == 0:
        raise RuntimeError("Run as the ordinary Linux deployment user, never with sudo")
    python = verify_release(base)
    database_check(database)
    verified_backup(database, backup_sha256)
    for name in ("systemctl", "loginctl", "systemd-analyze"):
        if not Path("/usr/bin", name).is_file():
            raise RuntimeError("Required system tool is unavailable: " + name)
    unit_dir = (home or Path.home()) / ".config/systemd/user"
    if any(p.is_symlink() for p in (unit_dir, *unit_dir.parents)):
        raise RuntimeError("Symlinked user configuration path; refusing to write")
    target, dropin = unit_dir / UNIT_NAME, unit_dir / (UNIT_NAME + ".d")
    if target.exists() or target.is_symlink() or dropin.exists() or dropin.is_symlink():
        raise RuntimeError("Existing unit or drop-in; refusing to overwrite")
    if invoke(["/usr/bin/loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"]).stdout.strip() != "yes":
        raise RuntimeError("User lingering is not enabled; no settings changed")
    loaded = invoke(["/usr/bin/systemctl", "--user", "show", UNIT_NAME, "--property=LoadState", "--value"], allow_not_found=True)
    if loaded.stdout.strip() != "not-found":
        raise RuntimeError("A unit with this name is already loaded")
    invoke([str(python), "-m", "pip", "check"])
    text = build_unit(base, database)
    with tempfile.TemporaryDirectory(prefix=".business-unit-check-", dir=base) as directory:
        candidate = Path(directory) / UNIT_NAME
        candidate.write_text(text, encoding="utf-8", newline="\n")
        result = invoke(["/usr/bin/systemd-analyze", "--user", "verify", str(candidate)])
        if result.stderr.strip():
            raise RuntimeError("Unit validation reported diagnostics; review before installing")
    unit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    # Definitions only: do not change the foreground process or start/enable anything.
    invoke(["/usr/bin/systemctl", "--user", "daemon-reload"])
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--backup-sha256")
    args = parser.parse_args()
    if not args.install:
        print(build_unit(), end="")
    else:
        if not args.backup_sha256:
            parser.error("--install requires --backup-sha256 from the successful backup")
        try:
            target = register(args.backup_sha256)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
            detail = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
            print("STOP: " + detail, file=sys.stderr)
            print("No service was started, enabled, stopped or restarted. Preserve existing files. A unit may remain if daemon-reload failed.", file=sys.stderr)
            sys.exit(2)
        print("Installed user unit (not enabled or started): " + str(target))
        print("Keep the foreground business process until the separate switchover step.")
