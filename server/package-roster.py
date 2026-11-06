"""Reproducible additive roster-review ZIP; no private data, venv or credentials."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frozen_package", ROOT / "package-business.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
EXTRA_FILES = ("business_next/__init__.py", "business_next/api.py", "business_next/archive.py",
               "business_next/schema.sql", "business_next/migration.py", "business_next/cli.py",
               "business_next/web/workspace.js", "business_next/README.md")
FILES = base.FILES + EXTRA_FILES


def package(output_dir):
    frozen = ROOT / "../../../outputs/server-business-v0.4.0/wxnotify-business-v0.4.0-local-test.zip"
    if hashlib.sha256(frozen.read_bytes()).hexdigest() != "c59dab1f0f9b6b5ead1587c80dab9c845d34360ee6494f894ddbde80c0791d19":
        raise ValueError("Frozen v0.4 package differs")
    with zipfile.ZipFile(frozen) as old:
        if any((ROOT / name).read_bytes() != old.read(name) for name in base.FILES):
            raise ValueError("A frozen v0.4 source file changed")
    payloads = {}
    for name in FILES:
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("Missing or symlinked allowlisted source")
        payloads[name] = source.read_bytes()
    manifest = {"version": "0.5.0", "mode": "roster_review", "base_version": "0.4.0", "base_source_unchanged": True,
                "wechat_sending_enabled": False, "student_sync_enabled": False, "parent_binding_enabled": False,
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()}}
    raw_manifest = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    payloads["MANIFEST.json"] = raw_manifest
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = output_dir / "wxnotify-business-v0.5.0-roster-review.zip"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payloads.items()):
            entry = zipfile.ZipInfo(name, (2026, 10, 27, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, content)
    return {"path": str(target.resolve()), "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(), "files": len(FILES)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output_dir), indent=2))
