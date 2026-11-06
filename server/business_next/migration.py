"""Create a NEW private roster review database; never modify the running source."""

from contextlib import closing
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from business.store import Store, uid
from .archive import initialize, verify

LIMIT = 10000


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def checked_json(path, expected_hash):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("An explicit reviewed file checksum is required")
    payload = path.read_bytes()
    if len(payload) > 16 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError("Reviewed input checksum or size differs")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Repeated JSON fields")
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")))


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Unsupported integer")
    return value


def label(value, empty=False):
    if (not isinstance(value, str) or len(value) > 80 or not empty and not value.strip()
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Invalid roster label")
    return value


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value or not 2000 <= date.fromisoformat(value).year <= 2100:
        raise ValueError("Invalid source date")
    return value


def validate_bundle(bundle):
    if (bundle.get("format") != "future-school-tablet-migration-preview" or bundle.get("schema_version") != 1
            or bundle.get("complete_snapshot") is not True or bundle.get("preview_only") is not True
            or bundle.get("ready_for_import") is not False):
        raise ValueError("Use the complete reviewed roster preview")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", bundle.get("organization_target", "")):
        raise ValueError("Invalid target organization")
    source = bundle["source"]
    iso_date(source["as_of_date"])
    integer(source["bytes"], 1, 256 * 1024 * 1024)
    if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        raise ValueError("Invalid source fingerprint")
    if any(b.get("code") == "DEVICE_SYNC_STATE_REVIEW_REQUIRED" for b in bundle["blockers"]):
        raise ValueError("Existing tablet synchronization needs separate review")
    classes, students = bundle["classes"], bundle["students"]
    if not isinstance(classes, list) or not isinstance(students, list) or not 1 <= len(classes) <= LIMIT or not 1 <= len(students) <= LIMIT:
        raise ValueError("Incomplete or oversized roster")
    ids, names = {}, set()
    for c in classes:
        integer(c["class_local_id"], 1, 2**53 - 1)
        for key in ("class_name", "course_name", "teacher_label"):
            label(c[key], empty=key != "class_name")
        if c["class_local_id"] in ids or c["class_name"] in names or c["teacher_identity_confirmed"] is not False:
            raise ValueError("Repeated class or inferred teacher identity")
        ids[c["class_local_id"]] = c
        names.add(c["class_name"])
        integer(c["weekday"], 1, 7)
        integer(c["interval_weeks"], 1, 2)
        integer(c["duration_minutes"], 1, 720)
        integer(c["end_day_offset"], 0, 1)
        for key in ("start_time", "end_time"):
            if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", c[key]):
                raise ValueError("Invalid schedule time")
        minutes = lambda s: int(s[:2]) * 60 + int(s[3:])
        if minutes(c["end_time"]) + c["end_day_offset"] * 1440 - minutes(c["start_time"]) != c["duration_minutes"]:
            raise ValueError("Schedule duration differs")
        if c["anchor_date"]:
            iso_date(c["anchor_date"])
            if date.fromisoformat(c["anchor_date"]).isoweekday() != c["weekday"]:
                raise ValueError("Schedule anchor weekday differs")
        elif c["interval_weeks"] == 2:
            raise ValueError("A fortnightly class needs its actual anchor date")
    seen, refs = set(), set()
    for s in students:
        integer(s["source_local_id"], 1, 2**53 - 1)
        label(s["name"])
        label(s["external_ref"])
        label(s["previous_class_name"], empty=True)
        integer(s["remaining_units"], -1000000, 10000000)
        if s["source_local_id"] in seen or s["external_ref"] in refs or s["balance_is_snapshot"] is not True:
            raise ValueError("Repeated identity or unsupported balance")
        seen.add(s["source_local_id"])
        refs.add(s["external_ref"])
        if s["status"] == "active":
            if type(s["class_source_id"]) is not int or s["class_source_id"] not in ids or ids[s["class_source_id"]]["class_name"] != s["current_class_name"]:
                raise ValueError("Active student class is ambiguous")
        elif s["status"] != "paused" or s["class_source_id"] is not None or s["current_class_name"] != "":
            raise ValueError("Unassigned archive must remain unassigned")
    active, paused = [s for s in students if s["status"] == "active"], [s for s in students if s["status"] == "paused"]
    totals = {"classes": len(classes), "students": len(students), "active": len(active), "paused": len(paused),
              "active_remaining_units": sum(s["remaining_units"] for s in active),
              "paused_remaining_units": sum(s["remaining_units"] for s in paused)}
    if any(type(bundle["summary"].get(k)) is not int or bundle["summary"][k] != v for k, v in totals.items()):
        raise ValueError("Roster totals differ from the reviewed summary")
    return totals


def roster(db, organization):
    org = db.execute("SELECT id,slug FROM organizations WHERE slug=?", (organization,)).fetchone()
    if not org:
        raise ValueError("Target organization does not exist")
    classes = [dict(r) for r in db.execute("SELECT id,name,course_name FROM classes WHERE org_id=? ORDER BY id", (org["id"],))]
    for c in classes:
        c["teachers"] = [dict(r) for r in db.execute("SELECT u.id,u.display_name AS name FROM teacher_classes tc JOIN users u ON u.org_id=tc.org_id AND u.id=tc.user_id JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher' WHERE tc.org_id=? AND tc.class_id=? AND u.active=1 ORDER BY u.id", (org["id"], c["id"]))]
    students = [dict(r) for r in db.execute("SELECT id,name,external_ref,class_id,remaining_units,active FROM students WHERE org_id=? ORDER BY id", (org["id"],))]
    for s in students:
        s["active"] = bool(s["active"])
    if len(classes) > LIMIT or len(students) > LIMIT:
        raise ValueError("Source roster exceeds supported size")
    return {"organization": org["slug"], "org_id": org["id"], "classes": classes, "students": students}


def fingerprint(db):
    """Includes every tenant, credential hash and session; outputs only a digest."""
    result, count = [], 0
    for item in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"):
        result.append(list(item))
        if item["type"] == "table":
            name = item["name"].replace('"', '""')
            rows = []
            for row in db.execute('SELECT * FROM "' + name + '"'):
                count += 1
                if count > 1000000:
                    raise ValueError("Source database exceeds bounded migration scope")
                rows.append(canonical([{"blob_hex": v.hex()} if isinstance(v, bytes) else v for v in row]))
            result.append(sorted(rows))
    return digest(result)


def connect_readonly(path):
    if not path.is_absolute() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Use an explicit non-symlink database path")
    if os.name == "posix":
        for p in (path, path.parent):
            st = p.stat()
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError("Database and its directory must be private and owned by this account")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def prepare(database, output, bundle_path, bundle_hash, review_path, review_hash):
    bundle, expected = checked_json(bundle_path, bundle_hash), checked_json(review_path, review_hash)
    totals = validate_bundle(bundle)
    if (expected.get("format") != "wxnotify-roster-review" or expected.get("schema_version") != 1
            or expected.get("complete") is not True or expected.get("source_access") != "read_only"):
        raise ValueError("A complete reviewed server roster is required")
    if not output.is_absolute() or output == database or not output.parent.is_dir() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Use a NEW absolute private output path")
    if os.name == "posix" and (output.parent.stat().st_uid != os.getuid() or output.parent.stat().st_mode & 0o077):
        raise ValueError("Output directory must be private and owned by this account")
    backup = output.with_name(output.stem + ".before-import.sqlite")
    if output.exists() or backup.exists() or backup.is_symlink():
        raise FileExistsError("Existing review or pre-import backup must not be replaced")
    with closing(connect_readonly(database)) as source:
        source.execute("BEGIN")
        if (source.execute("PRAGMA application_id").fetchone()[0] != 1465405012
                or source.execute("PRAGMA user_version").fetchone()[0] != 1
                or source.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or source.execute("PRAGMA foreign_key_check").fetchone()):
            raise ValueError("Source database identity or integrity differs")
        if source.execute("SELECT 1 FROM sqlite_schema WHERE name='roster_extension'").fetchone():
            raise ValueError("Use the original v0.4 source; do not repeat a baseline migration")
        current = roster(source, bundle["organization_target"])
        if current != {k: expected[k] for k in current}:
            raise ValueError("Server roster or authorization changed since review; export again")
        if {c["name"] for c in current["classes"]} & {c["class_name"] for c in bundle["classes"]}:
            raise ValueError("Existing class collision requires manual review; no merges")
        if {s["external_ref"] for s in current["students"]} & {s["external_ref"] for s in bundle["students"]}:
            raise ValueError("Existing student collision requires manual review; no overwrites")
        source_digest = fingerprint(source)
        # Keep a separate, unmodified consistent backup containing the original private credentials.
        # It remains on the server; it is never included in stdout or downloaded by the uploader.
        for target in (backup, output):
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=128, sleep=0.05)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Pre-import copy failed integrity check")
        with closing(connect_readonly(backup)) as frozen:
            if fingerprint(frozen) != source_digest:
                raise ValueError("Pre-import backup differs from source snapshot")
        backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    initialize(output)
    store = Store(output)
    now, org = int(time.time()), current["org_id"]
    protected = ("organizations", "users", "roles", "sessions", "teacher_classes", "lessons", "lesson_members", "leaves", "attendance", "devices", "device_classes", "inbound_events", "outbox", "student_activity", "notification_preferences", "notification_schedule", "low_hours_state", "lesson_content", "notice_skips", "audit", "login_limits")
    with store.transaction() as db:
        before = {t: digest(sorted(canonical(list(r)) for r in db.execute("SELECT * FROM " + t))) for t in protected}
        receipt = {"version": "0.5.0", "organization": bundle["organization_target"], "bundle_sha256": bundle_hash,
                   "source_database_digest": source_digest, "source_date": bundle["source"]["as_of_date"], **totals,
                   "pre_import_backup_sha256": backup_hash, "pre_import_backup_integrity": "ok",
                   "preserved_server_classes": len(current["classes"]), "preserved_server_students": len(current["students"]),
                   "opening_balances_only": True, "history_replayed": False, "accounts_created": False,
                   "permissions_granted": False, "financial_activity_created": False, "notifications_created": False,
                   "source_database_modified": False, "tablet_modified": False}
        db.execute("INSERT INTO roster_imports VALUES(?,?,?,?,?,?,?)", (org, bundle_hash, bundle["source"]["sha256"], bundle["source"]["as_of_date"], now, source_digest, canonical(receipt)))
        class_ids = {}
        for c in bundle["classes"]:
            key = uid()
            class_ids[c["class_local_id"]] = key
            db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (key, org, c["class_name"], c["course_name"], now))
            db.execute("INSERT INTO roster_source_links VALUES(?,?,?,?,?)", (org, "class", c["class_local_id"], key, bundle_hash))
            db.execute("INSERT INTO class_schedule_templates VALUES(?,?,?,?,?,?,?,?,?,?,?)", (org, key, c["weekday"], c["interval_weeks"], c["anchor_date"], c["start_time"], c["end_time"], c["duration_minutes"], c["end_day_offset"], bundle["source"]["as_of_date"], c["teacher_label"]))
        for s in bundle["students"]:
            key = uid()
            if s["status"] == "paused":
                db.execute("INSERT INTO archived_students VALUES(?,?,?,?,?,?,?)", (key, org, s["name"], s["external_ref"], s["remaining_units"], s["previous_class_name"], now))
            else:
                db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active) VALUES(?,?,?,?,?,?,1)", (key, org, class_ids[s["class_source_id"]], s["name"], s["external_ref"], s["remaining_units"]))
            db.execute("INSERT INTO roster_source_links VALUES(?,?,?,?,?)", (org, "student", s["source_local_id"], key, bundle_hash))
        # Compare every imported field, not merely sums that could hide swapped balances.
        for s in bundle["students"]:
            link = db.execute("SELECT entity_id FROM roster_source_links WHERE org_id=? AND kind='student' AND source_id=?", (org, s["source_local_id"])).fetchone()[0]
            if s["status"] == "paused":
                row = db.execute("SELECT name,external_ref,remaining_units,previous_class_name FROM archived_students WHERE id=? AND org_id=?", (link, org)).fetchone()
                wanted = (s["name"], s["external_ref"], s["remaining_units"], s["previous_class_name"])
            else:
                row = db.execute("SELECT name,external_ref,remaining_units,class_id,active FROM students WHERE id=? AND org_id=?", (link, org)).fetchone()
                wanted = (s["name"], s["external_ref"], s["remaining_units"], class_ids[s["class_source_id"]], 1)
            if not row or tuple(row) != wanted:
                raise ValueError("Imported student differs from source")
        after = {t: digest(sorted(canonical(list(r)) for r in db.execute("SELECT * FROM " + t))) for t in protected}
        if before != after or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Unexpected changes to protected history or access")
        # Every original class/student row is also preserved byte-for-field.
        for s in current["students"]:
            row = db.execute("SELECT id,name,external_ref,class_id,remaining_units,active FROM students WHERE org_id=? AND id=?", (org, s["id"])).fetchone()
            if dict(row) != s:  # SQLite 0/1 compares equal to the reviewed booleans.
                raise ValueError("Existing server student changed")
    verify(store)
    with closing(store.connect()) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Prepared database failed integrity check")
    with closing(connect_readonly(database)) as source:
        source.execute("BEGIN")
        if fingerprint(source) != source_digest:
            raise ValueError("Source changed during preparation; keep this copy offline and prepare a fresh copy")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != backup_hash:
        raise ValueError("Pre-import backup changed during preparation")
    receipt["prepared_database_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    receipt["integrity"] = "ok"
    return receipt
