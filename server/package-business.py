"""Reproducible local-test business ZIP: no credentials, database, SSH or deployment."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from business import VERSION

FILES = (
    "business/__init__.py", "business/api.py", "business/backup.py", "business/cli.py",
    "business/jobs.py", "business/models.py", "business/routes.py", "business/schema.sql",
    "business/security.py", "business/store.py", "business/workflows.py", "business/README.md",
    "business/API.md", "app/requirements.txt",
    "business/portal.py", "business/web/index.html", "business/web/workspace.js", "business/web/workspace.css",
    "business/device_sync.py",
)


def package(output_dir: Path):
    root = Path(__file__).resolve().parent
    payloads = {}
    for name in FILES:
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise ValueError("Missing or symlinked allowlisted source")
        payloads[name] = source.read_bytes()
    manifest = {"version": VERSION, "mode": "local_test", "deployment_performed": False,
                "wechat_sending_enabled": False, "real_tablet_sync_enabled": False,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
    payloads["MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"wxnotify-business-v{VERSION}-local-test.zip"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 25, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    target, checksum = package(args.output_dir)
    print(target)
    print("SHA256: " + checksum)
