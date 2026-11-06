"""Local operator setup only. Passwords are prompted, never command-line arguments."""

import argparse
import getpass
import os
from pathlib import Path
import sqlite3

from .api import create_app
from .store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Initialize a NEW private local-test database and its owner")
    bootstrap.add_argument("--database", type=Path, required=True)
    bootstrap.add_argument("--organization", required=True)
    bootstrap.add_argument("--login", required=True)
    bootstrap.add_argument("--display-name", required=True)
    backup = commands.add_parser("backup", help="Consistent private backup to a NEW file; never upload to Git")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser("serve", help="Loopback-only business pilot; not the running v0.1 service")
    serve.add_argument("--database", type=Path, required=True)
    serve.add_argument("--port", type=int, default=18081, choices=range(1024, 65536), metavar="PORT")
    serve.add_argument("--local-test", action="store_true", required=True)
    args = parser.parse_args()
    if not args.database.is_absolute():
        parser.error("Use an explicit absolute private database path")
    os.umask(0o077)
    try:
        if args.command == "bootstrap":
            password = getpass.getpass("New local-test owner password (15-128 characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                parser.error("Passwords do not match; database was not initialized")
            Store.bootstrap(args.database, args.organization, args.login, args.display_name, password)
            print("Initialized a new local-test database. No WeChat identity, remote upload or messaging enabled.")
        elif args.command == "backup":
            from .backup import backup as create_backup
            import json
            print(json.dumps(create_backup(args.database, args.output), sort_keys=True))
        else:
            if any(os.environ.get(key, "0") != "0" for key in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC", "WXNOTIFY_ENABLE_PARENT_BINDING")):
                parser.error("WeChat and real tablet sync are not implemented; do not enable them")
            import uvicorn
            uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port,
                        access_log=False, proxy_headers=False, server_header=False, ws="none",
                        limit_concurrency=32, timeout_keep_alive=5, timeout_graceful_shutdown=10)
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(2, "STOP: database/path initialization failed; no existing database was overwritten. Review privately before retrying.\n")


if __name__ == "__main__":
    main()
