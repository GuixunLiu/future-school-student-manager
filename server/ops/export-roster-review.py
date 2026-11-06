"""Export one organization's minimal roster for offline review; never import or send data."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

LIMIT = 10000


def private_path(path, directory=False):
    if os.name != "posix":
        raise ValueError("Run on the ordinary Linux deployment account")
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Use explicit non-symlink private paths")
    value = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
        raise ValueError("Path must be private and owned by the deployment account")


def snapshot(database, organization):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", organization):
        raise ValueError("Use the exact organization identifier")
    private_path(database.parent, True)
    private_path(database)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != 1465405012 or db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database identity or integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Database relationships are inconsistent; no partial snapshot was written")
        org = db.execute("SELECT id,slug FROM organizations WHERE slug=?", (organization,)).fetchone()
        if org is None:
            raise ValueError("Organization does not exist; no file was exported")
        classes = [dict(r) for r in db.execute("SELECT id,name,course_name FROM classes WHERE org_id=? ORDER BY id LIMIT ?", (org["id"], LIMIT + 1))]
        students = [dict(r) for r in db.execute("SELECT id,name,external_ref,class_id,remaining_units,active FROM students WHERE org_id=? ORDER BY id LIMIT ?", (org["id"], LIMIT + 1))]
        if len(classes) > LIMIT or len(students) > LIMIT:
            raise ValueError("Review export limit exceeded; no partial snapshot was written")
        teachers = list(db.execute("SELECT tc.class_id,u.id,u.display_name FROM teacher_classes tc JOIN users u ON u.org_id=tc.org_id AND u.id=tc.user_id JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher' WHERE tc.org_id=? AND u.active=1 ORDER BY tc.class_id,u.id LIMIT 50001", (org["id"],)))
        if len(teachers) > 50000:
            raise ValueError("Teacher mapping limit exceeded")
        groups = {}
        for teacher in teachers:
            groups.setdefault(teacher["class_id"], []).append({"id": teacher["id"], "name": teacher["display_name"]})
        for school_class in classes:
            school_class["teachers"] = groups.get(school_class["id"], [])
            if len(school_class["teachers"]) > 50:
                raise ValueError("Class teacher count exceeds supported review scope")
        for student in students:
            if student["active"] not in (0, 1):
                raise ValueError("Unexpected student status")
            student["active"] = bool(student["active"])
        return {"format": "wxnotify-roster-review", "schema_version": 1, "complete": True,
                "generated_at": datetime.now(timezone.utc).isoformat(), "organization": org["slug"], "org_id": org["id"],
                "source_access": "read_only", "contains_private_data": True, "classes": classes, "students": students}


def export(database, organization, output):
    private_path(output.parent, True)
    data = snapshot(database, organization)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "classes": len(data["classes"]), "students": len(data["students"]), "contains_private_data": True,
            "source_access": "read_only", "mutation_performed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_absolute() or not args.output.is_absolute():
        parser.error("Use absolute private input and output paths")
    os.umask(0o077)
    try:
        print(json.dumps(export(args.database, args.organization, args.output), sort_keys=True))
    except (ValueError, OSError, sqlite3.Error) as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("STOP: " + detail + "; source opened read-only, existing output was not replaced. Preserve any incomplete new output for review.", file=sys.stderr)
        sys.exit(2)
