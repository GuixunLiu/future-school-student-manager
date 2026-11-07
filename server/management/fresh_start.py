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


FORMAT = "future-school-fresh-start-v2"
REVIEW_FORMAT = "future-school-opening-balance-review-v2"
MIN_UNITS = -1000000
MAX_UNITS = 10000000

POSTGRES_SCHEMA = """
BEGIN;
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE courses (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    active BOOLEAN NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, name)
);
CREATE TABLE classes (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    course_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, name),
    FOREIGN KEY(org_id, course_id) REFERENCES courses(org_id, id)
);
CREATE TABLE students (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, external_ref)
);
CREATE TABLE student_class_enrollments (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    joined_at BIGINT NOT NULL,
    left_at BIGINT,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id),
    CHECK(left_at IS NULL OR left_at >= joined_at)
);
CREATE UNIQUE INDEX student_class_active_enrollment
    ON student_class_enrollments(org_id, student_id, class_id) WHERE left_at IS NULL;
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
CREATE TABLE class_schedule_templates (
    id TEXT PRIMARY KEY,
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
    teacher_id TEXT NOT NULL,
    UNIQUE(org_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id),
    FOREIGN KEY(org_id, teacher_id) REFERENCES users(org_id, id)
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


def stable_id(prefix, *parts):
    raw = json.dumps([prefix, *parts], ensure_ascii=False, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def schedule_id(row):
    return stable_id("schedule", row["org_id"], row["class_id"], row["weekday"],
        row["interval_weeks"], row["anchor_date"], row["start_time"], row["end_time"],
        row["duration_minutes"], row["end_day_offset"], row["effective_date"],
        row.get("teacher_label", ""))


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


def required_course_entries(data):
    return [{"course_name": name, "content": "", "unit_price_cents": None,
             "confirmed": False}
            for name in sorted({row["course_name"] for row in data["classes"]})]


def required_schedule_entries(data):
    return [{"schedule_id": schedule_id(row), "class_id": row["class_id"],
             "teacher_label": row.get("teacher_label", ""), "teacher_id": None,
             "confirmed": False} for row in data["class_schedule_templates"]]


def review_template(source, source_sha256):
    connection, data = read_source(source, source_sha256)
    try:
        return {
            "format": REVIEW_FORMAT,
            "source_sha256": source_sha256,
            "instructions": "Confirm every course's content and price, every schedule's authorized teacher, and every listed balance discrepancy. Legacy content, prices and schedule teachers are never guessed.",
            "entries": required_balance_entries(data),
            "course_entries": required_course_entries(data),
            "schedule_entries": required_schedule_entries(data),
        }
    finally:
        connection.close()


def reviewed_entries(review, key, id_key, required, label):
    entries = review.get(key)
    if not isinstance(entries, list):
        raise FreshStartError(label + " review entries must be a list")
    required_ids = {entry[id_key] for entry in required}
    reviewed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(id_key), str):
            raise FreshStartError("Invalid " + label + " review entry")
        identity = entry[id_key]
        if identity in reviewed or identity not in required_ids:
            raise FreshStartError("Repeated or unnecessary " + label + " review entry")
        reviewed[identity] = entry
    if reviewed.keys() != required_ids:
        raise FreshStartError("Every " + label + " requires explicit review")
    return reviewed


def course_metadata(data, review):
    required = required_course_entries(data)
    reviewed = reviewed_entries(review, "course_entries", "course_name", required, "course")
    result = {}
    for expected in required:
        name = expected["course_name"]
        entry = reviewed[name]
        content = entry.get("content")
        price = entry.get("unit_price_cents")
        if entry.get("confirmed") is not True or not isinstance(content, str) or not content.strip() or len(content) > 4000:
            raise FreshStartError("Each course needs confirmed, nonempty content")
        if type(price) is not int or not 0 <= price <= 100000000:
            raise FreshStartError("Each course needs a confirmed nonnegative integer price in cents")
        result[name] = {"content": content.strip(), "unit_price_cents": price}
    return result


def schedule_teachers(data, review):
    required = required_schedule_entries(data)
    reviewed = reviewed_entries(review, "schedule_entries", "schedule_id", required, "schedule")
    teacher_ids = {row["user_id"] for row in data["roles"] if row["role"] == "teacher"}
    active_users = {row["id"] for row in data["users"] if row["active"]}
    authorized = {}
    for row in data["teacher_classes"]:
        authorized.setdefault(row["class_id"], set()).add(row["user_id"])
    result = {}
    for expected in required:
        identity = expected["schedule_id"]
        entry = reviewed[identity]
        teacher_id = entry.get("teacher_id")
        if entry.get("confirmed") is not True or not isinstance(teacher_id, str):
            raise FreshStartError("Each schedule needs a confirmed teacher")
        if (teacher_id not in teacher_ids or teacher_id not in active_users or
                teacher_id not in authorized.get(expected["class_id"], set())):
            raise FreshStartError("Schedule teacher must be an active teacher assigned to its class")
        result[identity] = teacher_id
    return result


def opening_balances(data, review):
    if review.get("format") != REVIEW_FORMAT:
        raise FreshStartError("Unsupported review format")
    required = required_balance_entries(data)
    reviewed = reviewed_entries(review, "entries", "student_id", required, "balance discrepancy")
    result = []
    for student in data["students"]:
        student_id = student["id"]
        management = data["management_balances"].get(student_id)
        if student_id in reviewed:
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
            raise FreshStartError("Review belongs to a different source backup")
        balances = opening_balances(data, review)
        metadata = course_metadata(data, review)
        teachers = schedule_teachers(data, review)
        org_id = data["organizations"][0]["id"]
        course_ids = {name: stable_id("course", org_id, name) for name in metadata}
        courses = []
        for name in sorted(metadata):
            created_at = min(row["created_at"] for row in data["classes"] if row["course_name"] == name)
            courses.append({"id": course_ids[name], "org_id": org_id, "name": name,
                            "created_at": created_at, "active": True, **metadata[name]})
        classes = [{"id": row["id"], "org_id": row["org_id"], "name": row["name"],
                    "course_id": course_ids[row["course_name"]], "created_at": row["created_at"]}
                   for row in data["classes"]]
        students = [{"id": row["id"], "org_id": row["org_id"], "name": row["name"],
                     "external_ref": row["external_ref"], "active": bool(row["active"])}
                    for row in data["students"]]
        # Legacy data has no reliable enrollment timestamps; zero is an explicit unknown-time sentinel.
        enrollments = [{"id": stable_id("enrollment", row["org_id"], row["id"], row["class_id"]),
                        "org_id": row["org_id"], "student_id": row["id"], "class_id": row["class_id"],
                        "joined_at": 0, "left_at": None, "created_at": 0}
                       for row in data["students"]]
        schedules = []
        for row in data["class_schedule_templates"]:
            identity = schedule_id(row)
            schedules.append({"id": identity, "org_id": row["org_id"], "class_id": row["class_id"],
                "weekday": row["weekday"], "interval_weeks": row["interval_weeks"],
                "anchor_date": row["anchor_date"], "start_time": row["start_time"],
                "end_time": row["end_time"], "duration_minutes": row["duration_minutes"],
                "end_day_offset": row["end_day_offset"], "effective_date": row["effective_date"],
                "teacher_id": teachers[identity]})
        users = [dict(row, active=bool(row["active"]), password_reset_required=True) for row in data["users"]]
        devices = [dict(row, active=bool(row["active"]), token_reissue_required=True) for row in data["devices"]]
        tables = {"organizations": data["organizations"], "courses": courses, "classes": classes,
            "students": students, "student_class_enrollments": enrollments,
            "class_schedule_templates": schedules, "opening_balances": balances, "users": users,
            "roles": data["roles"], "teacher_classes": data["teacher_classes"], "devices": devices}
        return {
            "format": FORMAT,
            "source_sha256": source_sha256,
            **tables,
            "counts": {table: len(rows) for table, rows in tables.items()},
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
