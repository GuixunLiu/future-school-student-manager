"""Prepare an OFFLINE private pilot copy. Never start a service, create lessons or enable devices."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from datetime import datetime, timedelta, timezone

APP_ID = 1465405012
MUTABLE = {"sessions", "login_limits", "devices", "device_classes", "notification_preferences", "notification_schedule", "outbox", "test_parent_links"}


def private(path, directory=False):
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Absolute nonlinked private paths required")
    s = path.stat()
    if not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):
        raise ValueError("Unexpected file type")
    if os.name == "posix" and (os.geteuid() == 0 or s.st_uid != os.getuid() or s.st_mode & 0o077):
        raise ValueError("Ordinary account and private owned paths required")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def connect(path, readonly=True):
    db = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if readonly:
        db.execute("PRAGMA query_only=ON")
    return db


def validate_plan(plan, now):
    if plan.get("format") != "future-school-owner-confirmed-pilot-lesson" or plan.get("schema_version") != 1 or plan.get("owner_confirmed") is not True or plan.get("plan_only") is not True:
        raise ValueError("Explicit confirmed lesson plan required")
    if any(plan.get(k) is not False for k in ("ready_to_enable", "server_written", "lesson_created", "device_enabled", "wechat_enabled")):
        raise ValueError("Only a not-yet-enabled plan is supported")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", plan["organization"]) or not re.fullmatch(r"[a-f0-9]{32}", plan["server_class_id"]) or type(plan["class_source_id"]) is not int or plan["class_source_id"] <= 0:
        raise ValueError("Explicit source and server identities required")
    start, end = datetime.fromisoformat(plan["starts_at"]), datetime.fromisoformat(plan["ends_at"])
    if plan["timezone"] != "Asia/Shanghai" or start.utcoffset() != timedelta(hours=8) or end.utcoffset() != timedelta(hours=8) or start.timestamp() <= now or end <= start:
        raise ValueError("A future actual lesson in school time is required; no history replay")
    if type(plan["duration_minutes"]) is not int or (end-start).total_seconds() != plan["duration_minutes"] * 60 or not 1 <= plan["duration_minutes"] <= 720:
        raise ValueError("Explicit lesson duration differs")
    if plan["leave_policy"] != {"included_in_expected": True, "counts_as_arrived": False, "charges_units": False}:
        raise ValueError("Leave must remain in expected count without attendance or charge")
    members = plan["members"]
    if type(plan["expected_students"]) is not int or not 1 <= len(members) <= 500 or plan["expected_students"] != len(members):
        raise ValueError("Explicit complete lesson roster required")
    for key in ("source_local_id", "external_ref", "server_student_id"):
        if len({m[key] for m in members}) != len(members):
            raise ValueError("Ambiguous student mapping")
    for m in members:
        if type(m["source_local_id"]) is not int or m["source_local_id"] <= 0 or not re.fullmatch(r"[a-f0-9]{32}", m["server_student_id"]) or not isinstance(m["external_ref"], str) or not 1 <= len(m["external_ref"]) <= 64 or type(m["expected_remaining_units"]) is not int:
            raise ValueError("Invalid explicit student baseline")
    return start, end


def verify(db):
    if db.execute("PRAGMA application_id").fetchone()[0] != APP_ID or db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute("SELECT version FROM roster_extension").fetchall()[0][0] != 1:
        raise ValueError("Frozen v0.5 schema required")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Database integrity check failed")


def preserved_digest(db):
    digest = hashlib.sha256()
    for row in db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' ORDER BY name"):
        name, sql = row
        if not re.fullmatch(r"[a-z_]+", name):
            raise ValueError("Unexpected schema table")
        digest.update(json.dumps([name, sql], ensure_ascii=True).encode())
        if name in MUTABLE:
            continue
        rows = [list(r) for r in db.execute('SELECT * FROM "' + name + '"')]
        digest.update(json.dumps(sorted(rows, key=lambda r: json.dumps(r, ensure_ascii=True)), ensure_ascii=True).encode())
    # Preserve device identities and credential hashes, while clearing activation only in the candidate.
    rows = db.execute("SELECT id,org_id,name,digest,expires_at,created_at FROM devices ORDER BY id").fetchall()
    digest.update(json.dumps([list(r) for r in rows], ensure_ascii=True).encode())
    return digest.hexdigest()


def compare(db, plan, start):
    org = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()
    if org is None:
        raise ValueError("Organization differs")
    org = org[0];cid = plan["server_class_id"]
    c = db.execute("SELECT name FROM classes WHERE org_id=? AND id=?", (org, cid)).fetchone()
    link = db.execute("SELECT entity_id FROM roster_source_links WHERE org_id=? AND kind='class' AND source_id=?", (org, plan["class_source_id"])).fetchone()
    if c is None or c[0] != plan["class_name"] or link is None or link[0] != cid:
        raise ValueError("Imported class identity differs")
    teachers = db.execute("SELECT u.display_name FROM teacher_classes tc JOIN users u ON u.id=tc.user_id AND u.org_id=tc.org_id JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher' WHERE tc.org_id=? AND tc.class_id=? AND u.active=1", (org, cid)).fetchall()
    if len(teachers) != 1 or teachers[0][0] != plan["teacher_label"]:
        raise ValueError("Current teacher authorization differs")
    schedule = db.execute("SELECT * FROM class_schedule_templates WHERE org_id=? AND class_id=?", (org, cid)).fetchone()
    if schedule is None or schedule["weekday"] != start.isoweekday() or schedule["start_time"] != start.strftime("%H:%M") or schedule["duration_minutes"] != plan["duration_minutes"]:
        raise ValueError("Current lesson schedule differs")
    if schedule["interval_weeks"] == 2:
        anchor = datetime.fromisoformat(schedule["anchor_date"]).date()
        if (start.date()-anchor).days < 0 or (start.date()-anchor).days % 14:
            raise ValueError("Confirmed day falls in the fortnightly off week")
    rows = list(db.execute("SELECT id,external_ref,remaining_units,source_device_id,source_revision FROM students WHERE org_id=? AND class_id=? AND active=1", (org, cid)))
    expected = {m["server_student_id"]: m for m in plan["members"]}
    if {r["id"] for r in rows} != set(expected):
        raise ValueError("Current active roster differs; do not add or remove students automatically")
    for r in rows:
        m = expected[r["id"]]
        link = db.execute("SELECT entity_id FROM roster_source_links WHERE org_id=? AND kind='student' AND source_id=?", (org, m["source_local_id"])).fetchone()
        if r["external_ref"] != m["external_ref"] or r["remaining_units"] != m["expected_remaining_units"] or link is None or link[0] != r["id"]:
            raise ValueError("Current student identity or opening balance differs")
        if r["source_device_id"] is not None or r["source_revision"] != 0:
            raise ValueError("Existing sync provenance requires separate review")
    if db.execute("SELECT 1 FROM lessons WHERE org_id=? AND class_id=? LIMIT 1", (org, cid)).fetchone():
        raise ValueError("Selected class already has server lessons; review before staging")
    return {"selected_students": len(rows), "selected_remaining_units": sum(r["remaining_units"] for r in rows)}


def snapshot(source, target):
    with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"):
        pass
    src = connect(source)
    dst = connect(target, False)
    try:
        src.backup(dst, pages=128, sleep=.05)
        dst.commit();verify(dst)
    finally:
        src.close();dst.close()


def prepare(source, output, plan, plan_sha256, *, now=None):
    """Called by a pinned SSH wrapper or fictional tests; result contains counts/hashes only."""
    now = int(time.time()) if now is None else now
    start, _ = validate_plan(plan, now)
    if not re.fullmatch(r"[a-f0-9]{64}", plan_sha256):
        raise ValueError("Pinned plan checksum required")
    source, output = Path(source), Path(output)
    private(source)
    if source.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Source exceeds the reviewed staging size bound")
    if not output.is_absolute() or output.exists() or output.is_symlink() or any(p.is_symlink() for p in output.parents) or not output.parent.is_dir():
        raise ValueError("A new dedicated output directory is required")
    output.mkdir(mode=0o700, exist_ok=False)
    private(output, True)
    backup, candidate = output / "baseline-backup.sqlite", output / "pilot-offline.sqlite"
    snapshot(source, backup)
    db = connect(backup)
    try:
        summary = compare(db, plan, start);before = preserved_digest(db)
        users = db.execute("SELECT count(*) FROM users").fetchone()[0]
    finally:
        db.close()
    snapshot(backup, candidate)
    db = connect(candidate, False)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM login_limits")
        db.execute("UPDATE devices SET active=0")
        db.execute("DELETE FROM device_classes")
        db.execute("DELETE FROM test_parent_links")
        db.execute("UPDATE notification_preferences SET enabled=0")
        db.execute("UPDATE notification_schedule SET enabled=0")
        db.execute("UPDATE outbox SET status='cancelled',cancel_reason='offline_pilot_staging' WHERE status='pending_simulation'")
        if preserved_digest(db) != before:
            raise ValueError("Candidate changed protected identities, authorizations or business data")
        db.commit();verify(db)
        compare(db, plan, start)
        if db.execute("SELECT count(*) FROM sessions").fetchone()[0] or db.execute("SELECT count(*) FROM devices WHERE active=1").fetchone()[0]:
            raise ValueError("Candidate authentication state is not isolated")
    finally:
        db.close()
    receipt = {"format": "future-school-offline-pilot-stage", "schema_version": 1, "stage_complete": True,
               "plan_sha256": plan_sha256, "source_opened_read_only": True, "users_preserved": users, **summary,
               "baseline_backup_sha256": checksum(backup), "candidate_sha256": checksum(candidate), "preserved_data_sha256": before,
               "contains_private_data": True, "candidate_sessions_cleared": True, "candidate_devices_disabled": True,
               "candidate_notifications_disabled": True, "existing_services_modified": False, "service_started": False,
               "lesson_created": False, "device_enabled": False, "history_replayed": False, "wechat_enabled": False}
    for name, value in (("confirmed-plan.private.json", plan), ("stage-receipt.json", receipt)):
        with os.fdopen(os.open(output / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
    return receipt
