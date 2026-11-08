"""Consistent private SQLite backups; never copy an open database as a plain file."""

import hashlib
import os
from pathlib import Path
import sqlite3

from .store import Store


def backup(database: Path, output: Path):
    store = Store(database)
    if not output.is_absolute() or not output.parent.is_dir():
        raise ValueError("Use an absolute output path in an existing private directory")
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source, destination = None, None
    try:
        source = store.connect()
        destination = sqlite3.connect(str(output))
        source.backup(destination, pages=128, sleep=0.05)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup failed integrity check; keep the source unchanged")
    finally:
        if source is not None:
            source.close()
        if destination is not None:
            destination.close()
    Store(output)
    with output.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": checksum, "bytes": output.stat().st_size,
            "integrity": "ok", "contains_private_data": True, "source_unchanged": True}
