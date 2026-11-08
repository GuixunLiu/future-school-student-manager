"""Build an allowlisted, credential-free infrastructure handoff ZIP. No SSH."""

import argparse
import hashlib
from pathlib import Path
import zipfile

FILES = (
    "app/.dockerignore",
    "app/Dockerfile",
    "app/requirements.txt",
    "app/wxnotify_api.py",
    "app/preflight.py",
    "for-admin/docker-compose.yml",
    "DEPLOYMENT.md",
)


def package(output_dir: Path) -> tuple[Path, str]:
    root = Path(__file__).resolve().parent
    for name in FILES:
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise ValueError("Missing or symlinked release file: " + name)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "wxnotify-infrastructure-v0.1.0.zip"
    # Exclusive creation: never silently overwrite a previous handoff package.
    with zipfile.ZipFile(output, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            source = root / name
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    return output, hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    target, digest = package(args.output_dir)
    print(target)
    print("SHA256: " + digest)
