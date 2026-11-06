"""Register the wxnotify infrastructure USER unit only; do not enable or start it."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

UNIT_NAME = "wxnotify-infrastructure.service"
RELEASE_DIR = Path("/opt/wxnotify/app/infrastructure-v0.1.0-9eecbcbf")


def build_unit(base: Path = RELEASE_DIR) -> str:
    prefix = base.as_posix()
    return f"""[Unit]
Description=wxnotify infrastructure probe (no student or WeChat business)
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=exec
WorkingDirectory={prefix}/app
ExecStart={prefix}/.venv/bin/python -m uvicorn wxnotify_api:app --host 127.0.0.1 --port 18080 --workers 1 --no-access-log --no-proxy-headers --no-server-header --no-use-colors --ws none --limit-concurrency 32 --timeout-keep-alive 5 --timeout-graceful-shutdown 10
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
Environment=WXNOTIFY_MODE=infrastructure_only WXNOTIFY_ENABLE_WECHAT=0 WXNOTIFY_ENABLE_STUDENT_SYNC=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=journal
StandardError=journal
SyslogIdentifier=wxnotify-infrastructure
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100

[Install]
WantedBy=default.target
"""


def invoke(arguments: list[str], *, allow_not_found: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
    if allow_not_found and result.stdout.strip() == "not-found" and result.returncode in (0, 1):
        return result
    if result.returncode:
        detail = result.stderr.strip()[:2000] or result.stdout.strip()[:2000]
        raise RuntimeError("Command failed: " + " ".join(arguments) + "\n" + detail)
    return result


def register(base: Path = RELEASE_DIR, home: Path | None = None) -> Path:
    if sys.platform != "linux" or not hasattr(os, "getuid") or os.getuid() == 0:
        raise RuntimeError("Run --install as the ordinary Linux deployment user, never with sudo")
    if not base.is_dir() or base.is_symlink() or not (base / "app").is_dir() or (base / "app").is_symlink():
        raise RuntimeError("Expected real, existing release and app directories")
    python = base / ".venv/bin/python"
    for path in (python, base / ".venv/pyvenv.cfg", base / "app/wxnotify_api.py", base / "app/requirements.txt"):
        if not path.is_file():
            raise RuntimeError("Missing reviewed release component: " + str(path))
    if not os.access(python, os.X_OK):
        raise RuntimeError("Virtualenv Python is not executable")
    for name in ("systemctl", "loginctl", "systemd-analyze"):
        if not Path("/usr/bin", name).is_file():
            raise RuntimeError("Required system tool is not available: " + name)

    unit_dir = (home or Path.home()) / ".config/systemd/user"
    target = unit_dir / UNIT_NAME
    if target.exists() or target.is_symlink() or (unit_dir / (UNIT_NAME + ".d")).exists():
        raise RuntimeError("Existing unit or drop-in found; refusing to overwrite: " + str(target))

    linger = invoke(["/usr/bin/loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"])
    if linger.stdout.strip() != "yes":
        raise RuntimeError("User lingering is not enabled; no settings were changed")
    loaded = invoke(["/usr/bin/systemctl", "--user", "show", UNIT_NAME, "--property=LoadState", "--value"],
                    allow_not_found=True)
    if loaded.stdout.strip() != "not-found":
        raise RuntimeError("A unit with this name already exists; not replacing or restarting it")
    invoke([str(python), "-m", "pip", "check"])

    text = build_unit(base)
    with tempfile.TemporaryDirectory(prefix=".wxnotify-unit-check-", dir=base) as directory:
        candidate = Path(directory) / UNIT_NAME
        candidate.write_text(text, encoding="utf-8", newline="\n")
        validation = invoke(["/usr/bin/systemd-analyze", "--user", "verify", str(candidate)])
        if validation.stderr.strip():
            raise RuntimeError("Unit validation emitted diagnostics; review before installing:\n" +
                               validation.stderr.strip()[:2000])

    unit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(text)
    # Reload definitions only; do not enable, start, stop or restart any service.
    invoke(["/usr/bin/systemctl", "--user", "daemon-reload"])
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Validate and register this user's unit, without starting it")
    options = parser.parse_args()
    if not options.install:
        print(build_unit(), end="")
    else:
        try:
            installed = register()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print("STOP: " + str(error), file=sys.stderr)
            print("No service was enabled, started, stopped or restarted. Review the error before retrying.", file=sys.stderr)
            sys.exit(2)
        print("Installed user unit (not enabled or started): " + str(installed))
        print("Keep the foreground test running until the separate switchover step.")
