"""Apply owner-confirmed teacher access to the v0.5 review copy only."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.request import build_opener, ProxyHandler

MANIFEST_SHA256 = "df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359"


def validate(plan):
    if plan.get("format") != "future-school-confirmed-teacher-access" or plan.get("schema_version") != 1 or plan.get("confirmed_by_owner") is not True:
        raise ValueError("Explicit owner confirmation is required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", plan.get("organization", "")) or not re.fullmatch(r"[a-f0-9]{64}", plan.get("source_preview_sha256", "")):
        raise ValueError("Invalid organization or reviewed source")
    people = plan["teachers"]
    if not isinstance(people, list) or not 1 <= len(people) <= 100 or plan["expected"]["teachers"] != len(people):
        raise ValueError("Invalid teacher count")
    names, logins, classes, owners = set(), set(), set(), []
    for person in people:
        if (not isinstance(person["name"], str) or not person["name"].strip() or len(person["name"]) > 80
                or any(ord(c) < 32 for c in person["name"]) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", person["login"])
                or person["name"] in names or person["login"] in logins or type(person["reuse_owner"]) is not bool):
            raise ValueError("Invalid or repeated person identity")
        names.add(person["name"])
        logins.add(person["login"])
        if person["reuse_owner"]:
            owners.append(person)
            if person["roles"] != ["admin", "teacher"] or "password_hash" in person:
                raise ValueError("Owner must keep the existing password and administrator identity")
        elif person["roles"] != ["teacher"] or not re.fullmatch(r"pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}", person.get("password_hash", "")):
            raise ValueError("New teacher must have only teacher access and a supported password hash")
        if not isinstance(person["classes"], list) or not person["classes"]:
            raise ValueError("Explicit class mapping is required")
        for c in person["classes"]:
            if type(c["source_class_id"]) is not int or c["source_class_id"] <= 0 or c["source_class_id"] in classes:
                raise ValueError("Repeated or invalid class mapping")
            classes.add(c["source_class_id"])
            for key in ("class_name", "course_name"):
                if not isinstance(c[key], str) or len(c[key]) > 80 or key == "class_name" and not c[key].strip():
                    raise ValueError("Invalid class label")
    if len(owners) != 1 or plan["owner_login"] != owners[0]["login"] or plan["expected"]["classes"] != len(classes):
        raise ValueError("One retained owner and the complete class mapping are required")
    return owners[0]


def mappings(db, org, plan):
    imported = db.execute("SELECT 1 FROM roster_imports WHERE org_id=? AND bundle_sha256=?", (org, plan["source_preview_sha256"])).fetchone()
    if not imported:
        raise ValueError("This database was not prepared from the approved roster")
    rows = list(db.execute("SELECT l.source_id,c.id,c.name,c.course_name,t.teacher_label FROM roster_source_links l JOIN classes c ON c.org_id=l.org_id AND c.id=l.entity_id JOIN class_schedule_templates t ON t.org_id=c.org_id AND t.class_id=c.id WHERE l.org_id=? AND l.kind='class' AND l.bundle_sha256=?", (org, plan["source_preview_sha256"])))
    by_id = {r["source_id"]: r for r in rows}
    if len(by_id) != plan["expected"]["classes"]:
        raise ValueError("Imported class mapping is incomplete")
    seen = set()
    for person in plan["teachers"]:
        for c in person["classes"]:
            row = by_id.get(c["source_class_id"])
            if not row or (row["name"], row["course_name"], row["teacher_label"]) != (c["class_name"], c["course_name"], person["name"]):
                raise ValueError("Class or teacher label changed since owner confirmation")
            seen.add(row["source_id"])
    if seen != set(by_id):
        raise ValueError("Class authorization plan is partial")
    return by_id


def state(db, plan, replay=False):
    organization = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()
    if not organization:
        raise ValueError("Target organization is missing")
    org = organization[0]
    classes = mappings(db, org, plan)
    people = {}
    for person in plan["teachers"]:
        user = db.execute("SELECT * FROM users WHERE org_id=? AND login=?", (org, person["login"])).fetchone()
        if replay or person["reuse_owner"]:
            if not user or user["active"] != 1:
                raise ValueError("Expected active account is missing")
            roles = sorted(r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=?", (org, user["id"])))
            wanted = person["roles"] if replay else ["admin"]
            if roles != wanted:
                raise ValueError("Account roles changed; no overwrite")
            if replay:
                if user["display_name"] != person["name"] or not person["reuse_owner"] and user["password_hash"] != person["password_hash"]:
                    raise ValueError("Existing account differs; do not reset credentials")
            people[person["login"]] = user
        elif user or db.execute("SELECT 1 FROM users WHERE org_id=? AND display_name=?", (org, person["name"])).fetchone():
            raise ValueError("Existing teacher identity needs separate review; no duplicate or password reset")
        if replay:
            wanted_ids = {classes[c["source_class_id"]]["id"] for c in person["classes"]}
            actual = {r[0] for r in db.execute("SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?", (org, user["id"]))}
            if actual != wanted_ids:
                raise ValueError("Teacher assignment changed; do not reapply")
    if not replay:
        for row in classes.values():
            if db.execute("SELECT 1 FROM teacher_classes WHERE org_id=? AND class_id=?", (org, row["id"])).fetchone():
                raise ValueError("A formal class already has a teacher; no overwrite")
        owner = people[plan["owner_login"]]
        if db.execute("SELECT 1 FROM teacher_classes WHERE org_id=? AND user_id=?", (org, owner["id"])).fetchone():
            raise ValueError("Owner already has assignments; review first")
    return org, classes, people


def apply_access(database, plan, plan_hash, backup_path):
    from business.backup import backup
    from business.store import Store, uid
    from business_next.archive import verify
    from business_next.migration import fingerprint, digest, canonical
    validate(plan)
    if digest(plan) != plan_hash:
        raise ValueError("Confirmed authorization plan digest differs")
    store = Store(database)
    verify(store)
    with store.transaction() as db:
        org = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()
        prior = None if not org else db.execute("SELECT metadata FROM audit WHERE org_id=? AND action='teacher_access_applied' AND entity_id=?", (org[0], plan_hash)).fetchone()
        if prior:
            state(db, plan, replay=True)
            return json.loads(prior[0]) | {"replayed": True}
        state(db, plan)
    backup_info = backup(database, backup_path)
    with closing(Store(backup_path).connect()) as frozen:
        baseline = fingerprint(frozen)
    with store.transaction() as db:
        if fingerprint(db) != baseline:
            raise ValueError("Review database changed during backup; prepare a fresh attempt")
        org, classes, people = state(db, plan)
        owner = people[plan["owner_login"]]
        actor = {"kind": "user", "org_id": org, "id": owner["id"], "roles": {"admin"}}
        allowed = {"users", "roles", "teacher_classes", "sessions", "notification_preferences", "outbox", "audit"}
        protected = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'") if r[0] not in allowed]
        def protected_digest():
            return {t: digest(sorted(canonical(list(r)) for r in db.execute('SELECT * FROM "' + t.replace('"','""') + '"'))) for t in protected}
        before = protected_digest()
        old_users = {r["id"]: dict(r) for r in db.execute("SELECT * FROM users")}
        outbox_count = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
        for person in plan["teachers"]:
            if person["reuse_owner"]:
                user_id = owner["id"]
                store.change_person(db, actor, user_id, {"roles": ["admin", "teacher"], "expected_roles": ["admin"], "expected_active": True})
                db.execute("UPDATE users SET display_name=? WHERE org_id=? AND id=?", (person["name"], org, user_id))
            else:
                user_id = uid()
                db.execute("INSERT INTO users VALUES(?,?,?,?,?,1,?)", (user_id, org, person["login"], person["name"], person["password_hash"], store.clock()))
                db.execute("INSERT INTO roles VALUES(?,?,'teacher')", (org, user_id))
                store.audit(db, actor, "person_created", user_id, roles=["teacher"])
            for c in person["classes"]:
                store.set_teachers(db, actor, classes[c["source_class_id"]]["id"], [user_id], expected=[])
        state(db, plan, replay=True)
        if protected_digest() != before or db.execute("SELECT count(*) FROM outbox").fetchone()[0] != outbox_count:
            raise ValueError("Unexpected business data changes; transaction rolled back")
        for key, old in old_users.items():
            current = dict(db.execute("SELECT * FROM users WHERE id=?", (key,)).fetchone())
            if key == owner["id"]:
                old = old | {"display_name": next(p["name"] for p in plan["teachers"] if p["reuse_owner"])}
            if current != old:
                raise ValueError("Existing user or password changed; transaction rolled back")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database relationship check failed")
        result = {"version": "0.5.0", "plan_sha256": plan_hash, "teachers": len(plan["teachers"]),
                  "new_accounts": len(plan["teachers"]) - 1, "authorized_classes": len(classes), "owner_roles": ["admin", "teacher"],
                  "owner_password_changed": False, "owner_relogin_required": True, "business_data_unchanged": True,
                  "wechat_sending_enabled": False, "roster_review_only": True, "backup_sha256": backup_info["sha256"],
                  "backup_integrity": "ok", "replayed": False}
        store.audit(db, actor, "teacher_access_applied", plan_hash, **result)
    if hashlib.sha256(backup_path.read_bytes()).hexdigest() != backup_info["sha256"]:
        raise ValueError("Backup checksum changed after commit; inspect before any retry")
    return result


def private(path, directory=False):
    import stat
    if os.name != "posix" or not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Use explicit private Linux paths")
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077 or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        raise ValueError("Expected a private owned path")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "release", "plan", "backup"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--apply-owner-confirmed-access", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if not re.fullmatch(r"/opt/wxnotify/app/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}/release", str(args.release)) or args.database != args.release.parent / "data/roster.sqlite":
            raise ValueError("Only the independent v0.5 review database is supported")
        for p in (args.database, args.plan):
            private(p)
        for p in (args.database.parent, args.release, args.backup.parent):
            private(p, True)
        raw = (args.release / "MANIFEST.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
            raise ValueError("Deployed release manifest differs")
        manifest = json.loads(raw)
        if len(manifest["files"]) != 27:
            raise ValueError("Release file list differs")
        for name, expected in manifest["files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                raise ValueError("Unsafe release manifest path")
            source = args.release / name
            private(source)
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError("Deployed release source differs")
        payload = args.plan.read_bytes()
        if len(payload) > 1024*1024 or hashlib.sha256(payload).hexdigest() != args.plan_file_sha256:
            raise ValueError("Authorization plan file differs")
        plan = json.loads(payload)
        sys.path.insert(0, str(args.release))
        from business_next.migration import digest
        opener = build_opener(ProxyHandler({}))
        def health():
            with opener.open("http://127.0.0.1:18082/health/ready", timeout=5) as response:
                h = json.load(response)
            if h.get("version") != "0.5.0" or h.get("roster_review_only") is not True or any(h.get(k) is not False for k in ("wechat_sending_enabled", "student_sync_enabled", "parent_binding_enabled")):
                raise ValueError("Expected the unchanged read-only review service")
        health()
        result = apply_access(args.database, plan, digest(plan), args.backup)
        health()
        old = subprocess.run(["systemctl", "--user", "is-active", "wxnotify-business-pilot.service"], capture_output=True, text=True, timeout=15)
        if old.returncode or old.stdout.strip() != "active":
            raise ValueError("Original service status changed; inspect without retrying")
        result["original_pilot_still_active"] = True
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, subprocess.SubprocessError):
        parser.exit(2, "STOP: teacher access was not confirmed complete. Preserve the private backup and inspect before retrying. No passwords are printed or reset.\n")


if __name__ == "__main__":
    main()
