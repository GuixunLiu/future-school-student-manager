"""Explicit operator steps; preparation writes only a new review copy."""

import argparse
import json
import os
from pathlib import Path
import sqlite3

from .migration import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("prepare-roster")
    for name in ("database", "output", "bundle", "review", "receipt"):
        stage.add_argument("--" + name, type=Path, required=True)
    stage.add_argument("--bundle-sha256", required=True)
    stage.add_argument("--review-sha256", required=True)
    stage.add_argument("--opening-balances-only", action="store_true", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--database", type=Path, required=True)
    serve.add_argument("--port", type=int, default=18082, choices=range(1024, 65536))
    serve.add_argument("--review-only", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if any(os.environ.get(k, "0") != "0" for k in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC", "WXNOTIFY_ENABLE_PARENT_BINDING")):
            raise ValueError("Real WeChat and synchronization remain disabled")
        if args.command == "prepare-roster":
            if not args.receipt.is_absolute() or args.receipt.exists() or args.receipt.is_symlink():
                raise ValueError("Use a new absolute private receipt path")
            receipt = prepare(args.database, args.output, args.bundle, args.bundle_sha256, args.review, args.review_sha256)
            fd = os.open(args.receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(receipt, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
            print(json.dumps(receipt, sort_keys=True))
        else:
            if not args.database.is_absolute():
                raise ValueError("Use an absolute database path")
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(args.database, review_only=True), host="127.0.0.1", port=args.port,
                        access_log=False, proxy_headers=False, server_header=False, ws="none", limit_concurrency=32,
                        timeout_keep_alive=5, timeout_graceful_shutdown=10)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        parser.exit(2, "STOP: review preparation failed. Existing source was not overwritten. Keep any new incomplete copy offline and inspect privately.\n")


if __name__ == "__main__":
    main()
