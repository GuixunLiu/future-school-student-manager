"""Offline fresh-start migration: master data and reviewed opening balances only.

No historical business rows, password hashes, sessions or device secrets are
accepted. The export is a private intermediate bundle, not a live apply path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


FORMAT = "future-school-fresh-start-v1"
REVIEW_FORMAT = "future-school-opening-balance-review-v1"
MIN_UNITS = -1000000
MAX_UNITS = 10000000

POSTGRES_SCHEMA = """
BEGIN;
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE classes (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    course_name TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, name)
);
CREATE TABLE class_schedule_templates (
    org_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 1 AND 7),
    interval_weeks INTEGER NOT NULL CHECK(interval_weeks IN (1, 2)),
    anchor_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 1 AND 720),
    end_day_offset INTEGER NOT NULL CHECK(end_day_offset IN (0, 1)),
    effective_date TEXT NOT NULL,
    teacher_label TEXT NOT NULL,
    PRIMARY KEY(org_id, class_id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id)
);
CREATE TABLE students (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    class_id TEXT NOT NULL,
    name TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, external_ref),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id)
);
CREATE TABLE opening_balances (
    org_id TEXT NOT NULL REFERENCES organizations(id),
    student_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK(units BETWEEN -1000000 AND 10000000),
    source TEXT NOT NULL CHECK(source IN ('matched_management_balance', 'manual_review')),
    review_ref TEXT,
    PRIMARY KEY(org_id, student_id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    login TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    created_at BIGINT NOT NULL,
    password_reset_required BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(org_id, id),
    UNIQUE(org_id, login)
);
CREATE TABLE roles (
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'teacher')),
    PRIMARY KEY(org_id, user_id, role),
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id)
);
CREATE TABLE teacher_classes (
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    PRIMARY KEY(org_id, user_id, class_id),
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id)
);
CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    created_at BIGINT NOT NULL,
    token_reissue_required BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(org_id, id)
);
CREATE TABLE migration_receipts (
    org_id TEXT NOT NULL REFERENCES organizations(id),
    source_sha256 TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    history_migrated BOOLEAN NOT NULL DEFAULT FALSE,
    credentials_migrated BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(org_id, source_sha256)
);
COMMIT;
""".strip() + "\n"


class FreshStartError(ValueError):
    pass


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_hash(value):
    if not re.fullmatch(r"[a-f0-9]{64}", value or ""):
        raise FreshStartError("An explicit lowercase SHA-256 is required")
    return value


def read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise FreshStartError("Review file exceeds the size limit")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise FreshStartError("Repeated JSON fields are not allowed")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(FreshStartError("Invalid JSON number")))


def write_private(path, raw):
    path = Path(path)
    if not path.is_absolute() or not path.parent.is_dir():
        raise FreshStartError("Use a new absolute output path in an existing directory")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def read_source(path, expected_sha256):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise FreshStartError("Use an explicit SQLite backup file")
    checked_hash(expected_sha256)
    if file_sha256(path) != expected_sha256:
        raise FreshStartError("Source backup checksum differs")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise FreshStartError("Source backup integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise FreshStartError("Source backup has foreign-key violations")
        organizations = [dict(row) for row in connection.execute("SELECT id,slug,created_at FROM organizations ORDER BY id")]
        if len(organizations) != 1:
            raise FreshStartError("This rehearsal requires exactly one organization")
        org_id = organizations[0]["id"]
        data = {
            "organizations": organizations,
            "classes": [dict(row) for row in connection.execute(
                "SELECT id,org_id,name,course_name,created_at FROM classes WHERE org_id=? ORDER BY id", (org_id,))],
            "class_schedule_templates": [dict(row) for row in connection.execute(
                "SELECT * FROM class_schedule_templates WHERE org_id=? ORDER BY class_id", (org_id,))],
            "students": [dict(row) for row in connection.execute(
                "SELECT id,org_id,class_id,name,external_ref,active FROM students WHERE org_id=? ORDER BY id", (org_id,))],
            "users": [dict(row) for row in connection.execute(
                "SELECT id,org_id,login,display_name,active,created_at FROM users WHERE org_id=? ORDER BY id", (org_id,))],
            "roles": [dict(row) for row in connection.execute(
                "SELECT org_id,user_id,role FROM roles WHERE org_id=? AND role IN ('admin','teacher') ORDER BY user_id,role", (org_id,))],
            "teacher_classes": [dict(row) for row in connection.execute(
                "SELECT org_id,user_id,class_id FROM teacher_classes WHERE org_id=? ORDER BY user_id,class_id", (org_id,))],
            "devices": [dict(row) for row in connection.execute(
                "SELECT id,org_id,name,active,created_at FROM devices WHERE org_id=? ORDER BY id", (org_id,))],
        }
        if not data["classes"] or not data["students"]:
            raise FreshStartError("Classes and students are required")
        data["management_balances"] = {
            row["student_id"]: row["units"] for row in connection.execute(
                "SELECT student_id,units FROM management_balances WHERE org_id=?", (org_id,))
        }
        data["student_units"] = {
            row["id"]: row["remaining_units"] for row in connection.execute(
                "SELECT id,remaining_units FROM students WHERE org_id=?", (org_id,))
        }
        return connection, data
    except Exception:
        connection.close()
        raise


def required_balance_entries(data):
    entries = []
    for student in data["students"]:
        student_id = student["id"]
        management = data["management_balances"].get(student_id)
        legacy = data["student_units"][student_id]
        if management is None or management != legacy:
            entries.append({
                "student_id": student_id,
                "name": student["name"],
                "external_ref": student["external_ref"],
                "active": bool(student["active"]),
                "student_remaining_units": legacy,
                "management_units": management,
                "suggested_opening_units": management,
                "opening_units": None,
                "confirmed": False,
                "review_ref": "",
            })
    return entries


def review_template(source, source_sha256):
    connection, data = read_source(source, source_sha256)
    try:
        entries = required_balance_entries(data)
        return {
            "format": REVIEW_FORMAT,
            "source_sha256": source_sha256,
            "instructions": "Only required discrepancies are listed. Set opening_units, confirmed=true and review_ref after human review.",
            "entries": entries,
        }
    finally:
        connection.close()


def opening_balances(data, review):
    if review.get("format") != REVIEW_FORMAT:
        raise FreshStartError("Unsupported balance review format")
    required = required_balance_entries(data)
    required_ids = {entry["student_id"] for entry in required}
    entries = review.get("entries")
    if not isinstance(entries, list):
        raise FreshStartError("Balance review entries must be a list")
    reviewed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("student_id"), str):
            raise FreshStartError("Invalid balance review entry")
        student_id = entry["student_id"]
        if student_id in reviewed or student_id not in required_ids:
            raise FreshStartError("Repeated or unnecessary balance review entry")
        reviewed[student_id] = entry
    if reviewed.keys() != required_ids:
        raise FreshStartError("Every balance discrepancy requires explicit review")
    result = []
    for student in data["students"]:
        student_id = student["id"]
        management = data["management_balances"].get(student_id)
        if student_id in required_ids:
            entry = reviewed[student_id]
            units = entry.get("opening_units")
            review_ref = entry.get("review_ref")
            if entry.get("confirmed") is not True or type(units) is not int or not MIN_UNITS <= units <= MAX_UNITS:
                raise FreshStartError("A confirmed, bounded opening balance is required")
            if not isinstance(review_ref, str) or not review_ref.strip() or len(review_ref) > 80:
                raise FreshStartError("Each manual balance review needs a concise reference")
            source = "manual_review"
        else:
            units = management
            if type(units) is not int or not MIN_UNITS <= units <= MAX_UNITS:
                raise FreshStartError("Matched management balance is outside the supported range")
            review_ref = None
            source = "matched_management_balance"
        result.append({
            "org_id": student["org_id"],
            "student_id": student_id,
            "units": units,
            "source": source,
            "review_ref": review_ref,
        })
    return result


def build_bundle(source, source_sha256, review):
    connection, data = read_source(source, source_sha256)
    try:
        if review.get("source_sha256") != source_sha256:
            raise FreshStartError("Balance review belongs to a different source backup")
        balances = opening_balances(data, review)
        students = [dict(row, active=bool(row["active"])) for row in data["students"]]
        users = [dict(row, active=bool(row["active"]), password_reset_required=True) for row in data["users"]]
        devices = [dict(row, active=bool(row["active"]), token_reissue_required=True) for row in data["devices"]]
        return {
            "format": FORMAT,
            "source_sha256": source_sha256,
            "organizations": data["organizations"],
            "classes": data["classes"],
            "class_schedule_templates": data["class_schedule_templates"],
            "students": students,
            "opening_balances": balances,
            "users": users,
            "roles": data["roles"],
            "teacher_classes": data["teacher_classes"],
            "devices": devices,
            "counts": {
                "organizations": len(data["organizations"]),
                "classes": len(data["classes"]),
                "class_schedule_templates": len(data["class_schedule_templates"]),
                "students": len(students),
                "opening_balances": len(balances),
                "users": len(users),
                "roles": len(data["roles"]),
                "teacher_classes": len(data["teacher_classes"]),
                "devices": len(devices),
            },
            "history_migrated": False,
            "archived_students_migrated": 0,
            "historical_attendance_migrated": 0,
            "historical_requests_migrated": 0,
            "password_hashes_migrated": 0,
            "sessions_migrated": 0,
            "device_secrets_migrated": 0,
            "live_apply_authorized": False,
        }
    finally:
        connection.close()


def bundle_bytes(bundle):
    return (json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline fresh-start migration rehearsal")
    subparsers = parser.add_subparsers(dest="command", required=True)
    schema = subparsers.add_parser("schema")
    schema.add_argument("--output", required=True)
    review = subparsers.add_parser("review-template")
    review.add_argument("--source", required=True)
    review.add_argument("--sha256", required=True)
    review.add_argument("--output", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--source", required=True)
    export.add_argument("--sha256", required=True)
    export.add_argument("--review", required=True)
    export.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "schema":
        write_private(args.output, POSTGRES_SCHEMA.encode("utf-8"))
        print(f"SCHEMA_OK\t{args.output}")
    elif args.command == "review-template":
        template = review_template(args.source, args.sha256)
        write_private(args.output, bundle_bytes(template))
        print(f"REVIEW_REQUIRED\t{len(template['entries'])}\t{args.output}")
    else:
        bundle = build_bundle(args.source, args.sha256, read_json(args.review))
        raw = bundle_bytes(bundle)
        write_private(args.output, raw)
        print(f"BUNDLE_OK\tstudents={bundle['counts']['students']}\tbalances={bundle['counts']['opening_balances']}"
              f"\thistory_migrated=0\tcredentials_migrated=0\tsha256={hashlib.sha256(raw).hexdigest()}")


if __name__ == "__main__":
    main()
