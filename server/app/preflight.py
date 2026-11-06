"""Read-only checks before using the administrator's fixed deployment command."""

import json
import os
from pathlib import Path
import socket
import stat
import sys


def inspect(root: Path, uid: int, port: int = 18080) -> dict:
    errors = []
    app_dir = root / "app"
    config = root / "docker-compose.yml"
    if not root.is_dir() or root.is_symlink():
        errors.append("Deployment root is missing or is a symlink")
    elif root.stat().st_uid != 0 or stat.S_IMODE(root.stat().st_mode) & 0o022 or os.access(root, os.W_OK):
        errors.append("Deployment root must remain administrator-owned and not writable by this account")
    if not app_dir.is_dir() or app_dir.is_symlink():
        errors.append("Application directory is missing or is a symlink")
    elif not os.access(app_dir, os.W_OK):
        errors.append("Current account cannot write the application directory")

    for name in ("Dockerfile", "requirements.txt", "wxnotify_api.py"):
        path = app_dir / name
        if not path.is_file() or path.is_symlink():
            errors.append("Missing or symlinked application file: " + name)

    if not config.is_file() or config.is_symlink():
        errors.append("Administrator must install the reviewed docker-compose.yml")
    else:
        info = config.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            errors.append("Compose file must be root-owned and not group/world writable")
        if info.st_uid == uid or os.access(config, os.W_OK):
            errors.append("Deployment account must not be able to change the root-managed Compose file")
        if not os.access(config, os.R_OK):
            errors.append("Compose file is not readable by the deployment account")

    # Briefly reserve then immediately release loopback only; do not start a service.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        errors.append("Loopback port is unavailable; do not stop an existing service")
    finally:
        probe.close()
    return {
        "ok": not errors,
        "mode": "infrastructure_only",
        "errors": errors,
        "notice": "No files changed; Docker, sudo and deployment were not executed",
    }


if __name__ == "__main__":
    if sys.version_info < (3, 11) or not hasattr(os, "getuid"):
        print("Run this check with Python 3.11+ as the non-root Linux deployment user")
        sys.exit(2)
    if os.getuid() == 0:
        print("Do not run this check as root or with sudo")
        sys.exit(2)
    report = inspect(Path("/opt/wxnotify"), os.getuid())
    print(json.dumps(report, ensure_ascii=True, indent=2))
    sys.exit(0 if report["ok"] else 2)
