"""Install a separate loopback review service; never stop or replace the v0.4 pilot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
import zipfile

OLD_RELEASE = Path("/opt/wxnotify/app/business-v0.4.0-c59dab1f")
SOURCE_DATABASE = Path("/opt/wxnotify/app/business-data-v0.4.0/pilot.sqlite")
UNIT = "wxnotify-roster-review.service"
OLD_UNIT = "wxnotify-business-pilot.service"
BASE_FILES = ("business/__init__.py", "business/api.py", "business/backup.py", "business/cli.py", "business/jobs.py", "business/models.py", "business/routes.py", "business/schema.sql", "business/security.py", "business/store.py", "business/workflows.py", "business/README.md", "business/API.md", "app/requirements.txt", "business/portal.py", "business/web/index.html", "business/web/workspace.js", "business/web/workspace.css", "business/device_sync.py")
EXTRA_FILES = ("business_next/__init__.py", "business_next/api.py", "business_next/archive.py", "business_next/schema.sql", "business_next/migration.py", "business_next/cli.py", "business_next/web/workspace.js", "business_next/README.md")


def private(path, directory=False):
    if os.name != "posix" or not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Run on the ordinary Linux account with non-symlink paths")
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077 or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        raise ValueError("A private owned path is required")


def run(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60,
                            env={k: v for k, v in os.environ.items() if k not in {"PYTHONHOME", "PYTHONPATH"}})
    if result.returncode:
        raise ValueError("Review setup step failed; existing pilot was not stopped")
    return result.stdout


def validate_package(path, package_hash, manifest_hash):
    if hashlib.sha256(path.read_bytes()).hexdigest() != package_hash:
        raise ValueError("Release ZIP checksum differs")
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(BASE_FILES + EXTRA_FILES + ("MANIFEST.json",)):
            raise ValueError("Unexpected package members")
        if sum(i.file_size for i in archive.infolist()) > 4 * 1024 * 1024:
            raise ValueError("Release exceeds supported size")
        raw = archive.read("MANIFEST.json")
        if hashlib.sha256(raw).hexdigest() != manifest_hash:
            raise ValueError("Release manifest differs")
        manifest = json.loads(raw)
        if manifest.get("version") != "0.5.0" or manifest.get("mode") != "roster_review" or set(manifest.get("files", {})) != set(BASE_FILES + EXTRA_FILES):
            raise ValueError("Unexpected release manifest")
        old_raw = (OLD_RELEASE / "MANIFEST.json").read_bytes()
        if hashlib.sha256(old_raw).hexdigest() != "ea92895290b3c4e8a6477f5f6962a8c15f60651da00c51725933acd89cad37f4":
            raise ValueError("Original release manifest differs")
        old = json.loads(old_raw)
        payloads = {}
        for name, checksum in manifest["files"].items():
            payload = archive.read(name)
            if hashlib.sha256(payload).hexdigest() != checksum or name in BASE_FILES and (old["files"][name] != checksum or hashlib.sha256((OLD_RELEASE / name).read_bytes()).hexdigest() != checksum):
                raise ValueError("Release source checksum differs")
            payloads[name] = payload
        payloads["MANIFEST.json"] = raw
        return payloads


def build_unit(release, database):
    release, database = release.as_posix(), database.as_posix()
    if any(re.search(r"[\s%\\\x00]", str(p)) for p in (release, database)):
        raise ValueError("Unsupported service paths")
    return f"""[Unit]
Description=wxnotify roster review copy (no business writes or WeChat)
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=exec
WorkingDirectory={release}
ExecStart={OLD_RELEASE.as_posix()}/.venv/bin/python -m business_next.cli serve --database {database} --review-only --port 18082
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
Environment=WXNOTIFY_ENABLE_WECHAT=0 WXNOTIFY_ENABLE_STUDENT_SYNC=0 WXNOTIFY_ENABLE_PARENT_BINDING=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=journal
StandardError=journal
SyslogIdentifier=wxnotify-roster-review
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100
[Install]
WantedBy=default.target
"""


def stage(work, package_hash, manifest_hash, bundle_hash, review_hash):
    private(work, True)
    if not re.fullmatch(r"/opt/wxnotify/app/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}", str(work)):
        raise ValueError("Use a new dedicated review directory")
    unit_dir = Path.home() / ".config/systemd/user"
    private(unit_dir, True)
    unit_path = unit_dir / UNIT
    if unit_path.exists() or unit_path.is_symlink() or (unit_dir / (UNIT + ".d")).exists():
        raise ValueError("Review service already registered; do not overwrite")
    loaded = subprocess.run(["systemctl", "--user", "show", UNIT, "--property=LoadState", "--value"], capture_output=True, text=True, timeout=15)
    if loaded.returncode not in (0, 1) or loaded.stdout.strip() != "not-found":
        raise ValueError("Review unit already exists elsewhere or its status could not be read")
    if run(["systemctl", "--user", "is-active", OLD_UNIT]).strip() != "active":
        raise ValueError("Original pilot is not active; inspect before preparation")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 18082))
    paths = [work / name for name in ("release.zip", "bundle.json", "server-review.json")]
    for path in paths:
        private(path)
    payloads = validate_package(paths[0], package_hash, manifest_hash)
    release, data = work / "release", work / "data"
    release.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    for name, payload in payloads.items():
        target = release / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    python = OLD_RELEASE / ".venv/bin/python"
    database, receipt_path = data / "roster.sqlite", data / "receipt.json"
    run([str(python), "-m", "business_next.cli", "prepare-roster", "--database", str(SOURCE_DATABASE), "--output", str(database),
         "--bundle", str(paths[1]), "--bundle-sha256", bundle_hash, "--review", str(paths[2]), "--review-sha256", review_hash,
         "--receipt", str(receipt_path), "--opening-balances-only"], cwd=release)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("integrity") != "ok" or receipt.get("bundle_sha256") != bundle_hash or hashlib.sha256(database.read_bytes()).hexdigest() != receipt["prepared_database_sha256"]:
        raise ValueError("Prepared database receipt differs; no service was registered")
    fd = os.open(unit_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(build_unit(release, database))
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", UNIT])
    opener = build_opener(ProxyHandler({}))
    health = None
    for _ in range(24):
        try:
            with opener.open("http://127.0.0.1:18082/health/ready", timeout=2) as response:
                health = json.load(response)
            break
        except OSError:
            time.sleep(0.5)
    if not health or health.get("version") != "0.5.0" or health.get("roster_review_only") is not True or any(health.get(k) is not False for k in ("business_ready", "wechat_sending_enabled", "student_sync_enabled", "parent_binding_enabled")):
        raise ValueError("New review service did not pass readiness; original pilot remains untouched")
    if run(["systemctl", "--user", "is-active", OLD_UNIT]).strip() != "active":
        raise ValueError("Original pilot status changed; inspect separately")
    status = run(["systemctl", "--user", "show", UNIT, "--no-pager", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState", "-p", "NRestarts"])
    return {"review_service": UNIT, "loopback_port": 18082, "work_directory": str(work), "health": health,
            "service_status": status, "migration": receipt, "original_pilot_still_active": True,
            "original_pilot_replaced": False, "contains_student_names": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-directory", type=Path, required=True)
    for name in ("package-sha256", "manifest-sha256", "bundle-sha256", "review-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--install-separate-review-service", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(stage(args.work_directory, args.package_sha256, args.manifest_sha256, args.bundle_sha256, args.review_sha256), sort_keys=True))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile):
        parser.exit(2, "STOP: review setup did not complete. The original pilot was not stopped or replaced. Preserve the new private folder for inspection; do not repeat or overwrite the service blindly.\n")
