"""Install a separate temporary HTTP viewer user unit; never modify the existing site or database."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler, Request

UNIT = "wxnotify-http-viewer.service"
REVIEW = Path("/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release")
MANIFEST = "df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359"


def private(path, directory=False):
    if os.name != "posix" or os.geteuid() == 0 or not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Use the ordinary deployment account and nonlinked paths")
    s = path.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o077 or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):
        raise ValueError("A private owned path is required")


def run(args):
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    p = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)
    if p.returncode:
        raise ValueError("Viewer user-service operation failed")
    return p.stdout.strip()


def verify_source():
    if any(p.is_symlink() for p in (REVIEW, *REVIEW.parents)):
        raise ValueError("Linked backend source refused")
    raw = (REVIEW / "MANIFEST.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST:
        raise ValueError("Reviewed source manifest differs")
    files = json.loads(raw)["files"]
    if len(files) != 27:
        raise ValueError("Unexpected reviewed runtime")
    for name, expected in files.items():
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
            raise ValueError("Unexpected source path")
        p = REVIEW / name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError("Reviewed source file differs")


def health(url, authority=None):
    request = Request(url, headers={"Host": authority} if authority else {})
    with build_opener(ProxyHandler({})).open(request, timeout=3) as r:
        raw = r.read(32769)
    if len(raw) > 32768:
        raise ValueError("Health response too large")
    return json.loads(raw)


def unit(work, public_origin, expires_at, python):
    if not re.fullmatch(r"/opt/wxnotify/app/http-viewer-[0-9]{14}-[a-f0-9]{8}", str(work)) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", python):
        raise ValueError("Invalid fixed viewer paths")
    if not re.fullmatch(r"http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}:18083", public_origin) or type(expires_at) is not int:
        raise ValueError("Explicit temporary HTTP origin required")
    return f"""[Unit]
Description=Future School temporary read-only HTTP viewer (owner approved)
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=exec
WorkingDirectory={work}
ExecStart={python} -u {work}/temporary-http-viewer.py --origin {public_origin} --expires-at {expires_at} --temporary-http-confirmed
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=yes
UMask=0077
LimitCORE=0
LimitNOFILE=256
MemoryMax=256M
CPUQuota=50%
TasksMax=32
Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=null
StandardError=journal
SyslogIdentifier=wxnotify-http-viewer
[Install]
WantedBy=default.target
"""


def install(work, config):
    private(work, True)
    if config.get("owner_accepted_plain_http") is not True or config.get("expires_after_days") != 14:
        raise ValueError("Explicit temporary HTTP acceptance required")
    viewer = work / "temporary-http-viewer.py"
    private(viewer)
    if hashlib.sha256(viewer.read_bytes()).hexdigest() != config["viewer_sha256"]:
        raise ValueError("Viewer source differs")
    spec = importlib.util.spec_from_file_location("temporary_viewer", viewer)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    authority = module.origin(config["public_origin"])
    if not authority.endswith(":18083"):
        raise ValueError("Only separate port 18083 is supported")
    expires = int(time.time()) + 14 * 86400
    content = unit(work, config["public_origin"], expires, sys.executable)
    unit_dir = Path.home() / ".config/systemd/user"
    private(unit_dir, True)
    target = unit_dir / UNIT
    if target.exists() or target.is_symlink() or (unit_dir / (UNIT + ".d")).exists():
        raise ValueError("Viewer unit already exists; inspect it instead of overwriting")
    result = subprocess.run(["systemctl", "--user", "show", UNIT, "-p", "LoadState", "--value"], capture_output=True, text=True, timeout=15)
    if result.returncode not in (0, 1) or result.stdout.strip() != "not-found":
        raise ValueError("Viewer unit state is not new")
    for old in ("wxnotify-roster-review.service", "wxnotify-business-pilot.service"):
        if run(["systemctl", "--user", "is-active", old]) != "active":
            raise ValueError("Existing service needs inspection first")
    verify_source()
    if not module.backend_ready(health("http://127.0.0.1:18082/health/ready")):
        raise ValueError("Read-only backend not ready")
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", 18083))
    written = False
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        written = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", UNIT])
        state = None
        for _ in range(24):
            try:
                state = health("http://127.0.0.1:18083/viewer/health", authority)
                break
            except OSError:
                time.sleep(.5)
        if not state or state.get("gateway_sha256") != config["viewer_sha256"] or state.get("backend_ready") is not True or state.get("expires_at") != expires:
            raise ValueError("New viewer did not pass local readiness")
        verify_source()
        for old in ("wxnotify-roster-review.service", "wxnotify-business-pilot.service"):
            if run(["systemctl", "--user", "is-active", old]) != "active":
                raise ValueError("Existing service state changed")
        service = run(["systemctl", "--user", "show", UNIT, "--no-pager", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState", "-p", "NRestarts"])
        receipt = {"format": "future-school-temporary-http-viewer-deployed", "schema_version": 1,
                   "owner_accepted_plain_http": True, "public_url": config["public_origin"], "service": UNIT,
                   "work_directory": str(work), "loopback_backend_port": 18082, "viewer_port": 18083,
                   "viewer_sha256": config["viewer_sha256"], "expires_at": expires, "local_ready": True,
                   "service_status": service, "business_writes_enabled": False, "data_realtime": False,
                   "existing_services_unchanged": True, "runtime_source_unchanged": True, "database_directly_opened": False,
                   "nginx_or_firewall_modified": False, "passwords_saved": False, "external_access_verified": False}
        with (work / "deployment-receipt.json").open("x", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        return receipt
    except BaseException:
        if written:
            subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True, timeout=30)
        raise
