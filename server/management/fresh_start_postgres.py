"""Transactional writer for an EMPTY, loopback-only PostgreSQL rehearsal database.

This module deliberately has no production apply mode. It never deletes or
updates existing tables and refuses any database with a public user table.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from .fresh_start import (FORMAT, MAX_UNITS, MIN_UNITS, POSTGRES_SCHEMA,
                          FreshStartError, bundle_bytes, checked_hash, read_json)


ROW_COLUMNS = {
    "organizations": ("id", "slug", "created_at"),
    "classes": ("id", "org_id", "name", "course_name", "created_at"),
    "class_schedule_templates": ("org_id", "class_id", "weekday", "interval_weeks",
        "anchor_date", "start_time", "end_time", "duration_minutes", "end_day_offset",
        "effective_date", "teacher_label"),
    "students": ("id", "org_id", "class_id", "name", "external_ref", "active"),
    "opening_balances": ("org_id", "student_id", "units", "source", "review_ref"),
    "users": ("id", "org_id", "login", "display_name", "active", "created_at",
              "password_reset_required"),
    "roles": ("org_id", "user_id", "role"),
    "teacher_classes": ("org_id", "user_id", "class_id"),
    "devices": ("id", "org_id", "name", "active", "created_at", "token_reissue_required"),
}
TOP_LEVEL = {"format", "source_sha256", *ROW_COLUMNS, "counts", "history_migrated",
    "archived_students_migrated", "historical_attendance_migrated", "historical_requests_migrated",
    "password_hashes_migrated", "sessions_migrated", "device_secrets_migrated", "live_apply_authorized"}
ZERO_COUNTERS = {"archived_students_migrated", "historical_attendance_migrated",
    "historical_requests_migrated", "password_hashes_migrated", "sessions_migrated",
    "device_secrets_migrated"}


def validate_bundle(bundle, raw):
    if not isinstance(bundle, dict) or set(bundle) != TOP_LEVEL or bundle.get("format") != FORMAT:
        raise FreshStartError("Unexpected or incomplete fresh-start bundle")
    if bundle_bytes(bundle) != raw:
        raise FreshStartError("Bundle must use the canonical reviewed JSON representation")
    checked_hash(bundle.get("source_sha256"))
    if bundle.get("history_migrated") is not False or bundle.get("live_apply_authorized") is not False:
        raise FreshStartError("History or live apply must remain disabled")
    if any(bundle.get(key) != 0 or type(bundle.get(key)) is not int for key in ZERO_COUNTERS):
        raise FreshStartError("Historical and credential counters must be zero")
    if not isinstance(bundle.get("counts"), dict) or set(bundle["counts"]) != set(ROW_COLUMNS):
        raise FreshStartError("Bundle table counts are incomplete")
    if len(bundle["organizations"]) != 1:
        raise FreshStartError("Exactly one organization is supported")
    org_id = bundle["organizations"][0].get("id")
    for table, columns in ROW_COLUMNS.items():
        rows = bundle[table]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise FreshStartError("Invalid table row collection")
        if bundle["counts"][table] != len(rows) or type(bundle["counts"][table]) is not int:
            raise FreshStartError("Bundle table count differs")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise FreshStartError("Row columns differ from the migration contract")
            if table != "organizations" and row.get("org_id") != org_id:
                raise FreshStartError("Cross-organization rows are not supported")
    students = {row["id"]: row for row in bundle["students"]}
    balances = bundle["opening_balances"]
    if len(students) != len(bundle["students"]) or len(balances) != len(students):
        raise FreshStartError("Student and opening-balance identities must be unique and complete")
    if {row["student_id"] for row in balances} != set(students):
        raise FreshStartError("Opening balances must exactly cover current students")
    for row in balances:
        if type(row["units"]) is not int or not MIN_UNITS <= row["units"] <= MAX_UNITS:
            raise FreshStartError("Opening balance is outside the supported range")
        if row["source"] == "matched_management_balance":
            if row["review_ref"] is not None:
                raise FreshStartError("Matched balance must not carry a manual review reference")
        elif row["source"] == "manual_review":
            if not isinstance(row["review_ref"], str) or not row["review_ref"].strip() or len(row["review_ref"]) > 80:
                raise FreshStartError("Manual opening balance needs a concise review reference")
        else:
            raise FreshStartError("Unknown opening-balance source")
    for table in ("students", "users", "devices"):
        for row in bundle[table]:
            if type(row["active"]) is not bool:
                raise FreshStartError("Active flags must be JSON booleans")
    for row in bundle["users"]:
        if row["password_reset_required"] is not True:
            raise FreshStartError("Every imported account must require a password reset")
    for row in bundle["devices"]:
        if row["token_reissue_required"] is not True:
            raise FreshStartError("Every imported device must require token reissue")


def schema_body():
    lines = POSTGRES_SCHEMA.strip().splitlines()
    if lines[0] != "BEGIN;" or lines[-1] != "COMMIT;":
        raise FreshStartError("Unexpected PostgreSQL schema wrapper")
    return "\n".join(lines[1:-1]) + "\n"


def populate(connection, bundle, raw, now):
    """Populate inside the caller's transaction; never commits the connection."""
    validate_bundle(bundle, raw)
    if type(now) is not int or now < 0:
        raise FreshStartError("A nonnegative integer receipt timestamp is required")
    bundle_sha256 = hashlib.sha256(raw).hexdigest()
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'")
        if cursor.fetchone()[0] != 0:
            raise FreshStartError("Refusing a nonempty public schema")
        cursor.execute("SET LOCAL statement_timeout='10s'")
        cursor.execute("SET LOCAL lock_timeout='2s'")
        cursor.execute(schema_body())
        for table, columns in ROW_COLUMNS.items():
            rows = bundle[table]
            if not rows:
                continue
            sql = "INSERT INTO " + table + " (" + ",".join(columns) + ") VALUES (" + ",".join(["%s"] * len(columns)) + ")"
            cursor.executemany(sql, [[row[column] for column in columns] for row in rows])
        cursor.execute("INSERT INTO migration_receipts VALUES(%s,%s,%s,%s,FALSE,FALSE)",
                       (bundle["organizations"][0]["id"], bundle["source_sha256"], bundle_sha256, now))
        for table in (*ROW_COLUMNS, "migration_receipts"):
            cursor.execute("SELECT count(*) FROM " + table)
            expected = 1 if table == "migration_receipts" else len(bundle[table])
            if cursor.fetchone()[0] != expected:
                raise FreshStartError("Written row count differs for " + table)
        cursor.execute("SELECT source_sha256,bundle_sha256,history_migrated,credentials_migrated FROM migration_receipts")
        receipt = cursor.fetchone()
        if receipt != (bundle["source_sha256"], bundle_sha256, False, False):
            raise FreshStartError("Migration receipt differs")
    return dict(format="future-school-local-postgresql-rehearsal-v1", rehearsal_only=True,
                live_apply_authorized=False, source_sha256=bundle["source_sha256"],
                bundle_sha256=bundle_sha256, counts=bundle["counts"], committed_by="connection_context")


def local_rehearsal_dsn(dsn, enabled):
    if enabled is not True:
        raise FreshStartError("Explicit --local-rehearsal is required")
    if not dsn:
        raise FreshStartError("Set FRESH_START_PG_DSN for the local rehearsal database")
    try:
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(dsn)
    except Exception as error:
        raise FreshStartError("A valid PostgreSQL DSN and psycopg are required") from error
    if info.get("host", "") not in ("localhost", "127.0.0.1", "::1"):
        raise FreshStartError("Only an explicit loopback PostgreSQL host is allowed")
    if not info.get("dbname", "").startswith("fresh_start_rehearsal"):
        raise FreshStartError("Database name must begin with fresh_start_rehearsal")
    return dsn


def apply_local(bundle_path, dsn, enabled, now=None):
    raw = Path(bundle_path).read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise FreshStartError("Bundle exceeds the size limit")
    bundle = read_json(bundle_path)
    local_rehearsal_dsn(dsn, enabled)
    import psycopg
    with psycopg.connect(dsn) as connection:
        return populate(connection, bundle, raw, int(time.time()) if now is None else now)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Empty local PostgreSQL rehearsal only")
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply_parser = subparsers.add_parser("apply-local")
    apply_parser.add_argument("--bundle", required=True)
    apply_parser.add_argument("--local-rehearsal", action="store_true")
    args = parser.parse_args(argv)
    receipt = apply_local(args.bundle, os.environ.get("FRESH_START_PG_DSN", ""), args.local_rehearsal)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
