"""Owner-requested login/password change for existing formal teachers; preserves identity and scope."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


def validate(plan):
    if plan.get("format") != "future-school-confirmed-login-change" or plan.get("schema_version") != 1 or plan.get("confirmed_by_owner") is not True:
        raise ValueError("An explicitly confirmed credential change is required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", plan.get("organization", "")) or not re.fullmatch(r"[a-f0-9]{64}", plan.get("previous_access_plan_sha256", "")):
        raise ValueError("Invalid organization or access baseline")
    people = plan["people"]
    if not isinstance(people, list) or len(people) != plan["expected"]["accounts"] or not 1 <= len(people) <= 100:
        raise ValueError("Invalid account count")
    old, new, names, classes = set(), set(), set(), set()
    admins = []
    for p in people:
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", p["old_login"]) or not re.fullmatch(r"[a-z]{3,64}", p["new_login"])
                or not isinstance(p["name"], str) or not p["name"].strip() or len(p["name"]) > 80
                or any(ord(c) < 32 for c in p["name"]) or p["old_login"] in old or p["new_login"] in new or p["name"] in names):
            raise ValueError("Invalid or duplicate account mapping")
        if p["roles"] == ["admin", "teacher"]:
            admins.append(p)
        elif p["roles"] != ["teacher"]:
            raise ValueError("Existing teacher/admin roles must be explicit")
        if not re.fullmatch(r"pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}", p.get("password_hash", "")):
            raise ValueError("Use the supported salted password hash")
        if not isinstance(p["class_source_ids"], list) or not p["class_source_ids"]:
            raise ValueError("Class identity baseline is required")
        for key in p["class_source_ids"]:
            if type(key) is not int or key <= 0 or key in classes:
                raise ValueError("Invalid or repeated class scope")
            classes.add(key)
        old.add(p["old_login"]);new.add(p["new_login"]);names.add(p["name"])
    if len(admins) != 1 or admins[0]["old_login"] != plan["owner_old_login"] or admins[0]["new_login"] != plan["owner_new_login"] or len(classes) != plan["expected"]["classes"]:
        raise ValueError("One retained administrator and complete formal class scope are required")


def inspect(db, plan, replay=False):
    org = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()
    if not org:
        raise ValueError("Organization does not exist")
    org = org[0]
    if not db.execute("SELECT 1 FROM audit WHERE org_id=? AND action='teacher_access_applied' AND entity_id=?", (org, plan["previous_access_plan_sha256"])).fetchone():
        raise ValueError("Approved access baseline is missing")
    users = {}
    for p in plan["people"]:
        user = db.execute("SELECT * FROM users WHERE org_id=? AND login=?", (org, p["new_login"] if replay else p["old_login"])).fetchone()
        if not user or not user["active"] or user["display_name"] != p["name"]:
            raise ValueError("Account identity changed; no overwrite")
        roles = sorted(r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=?", (org, user["id"])))
        if roles != p["roles"]:
            raise ValueError("Roles changed; no overwrite")
        actual = {r[0] for r in db.execute("SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?", (org, user["id"]))}
        expected = set()
        for source_id in p["class_source_ids"]:
            row = db.execute("SELECT entity_id FROM roster_source_links WHERE org_id=? AND kind='class' AND source_id=?", (org, source_id)).fetchone()
            if not row:
                raise ValueError("Class baseline missing")
            expected.add(row[0])
        if actual != expected:
            raise ValueError("Teacher assignments changed; preserve them for review")
        collision = db.execute("SELECT id FROM users WHERE org_id=? AND login=?", (org, p["new_login"])).fetchone()
        if collision and collision[0] != user["id"]:
            raise ValueError("Requested login is already used")
        if replay and user["password_hash"] != p["password_hash"]:
            raise ValueError("Password changed after application; do not reset again")
        users[p["old_login"]] = user
    return org, users


def change(database, plan, plan_digest, backup_path):
    from business.store import Store
    from business.security import token_digest
    from business.backup import backup
    from business_next.archive import verify
    from business_next.migration import digest, canonical, fingerprint
    validate(plan)
    if digest(plan) != plan_digest:
        raise ValueError("Credential plan digest differs")
    store = Store(database)
    verify(store)
    with store.transaction() as db:
        org = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()
        old = None if not org else db.execute("SELECT metadata FROM audit WHERE org_id=? AND action='teacher_logins_changed' AND entity_id=?", (org[0], plan_digest)).fetchone()
        if old:
            inspect(db, plan, replay=True)
            return json.loads(old[0]) | {"replayed": True}
        inspect(db, plan)
    info = backup(database, backup_path)
    with closing(Store(backup_path).connect()) as db:
        baseline = fingerprint(db)
    with store.transaction() as db:
        if fingerprint(db) != baseline:
            raise ValueError("Database changed during backup; use a fresh attempt")
        org, users = inspect(db, plan)
        actor = {"kind": "user", "org_id": org, "id": users[plan["owner_old_login"]]["id"], "roles": {"admin"}}
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'") if r[0] not in {"users", "sessions", "login_limits", "audit"}]
        def protected():
            return {t: digest(sorted(canonical(list(r)) for r in db.execute('SELECT * FROM "'+t.replace('"','""')+'"'))) for t in tables}
        before = protected()
        original_users = {r["id"]: dict(r) for r in db.execute("SELECT * FROM users")}
        wanted = {}
        for p in plan["people"]:
            key = users[p["old_login"]]["id"]
            wanted[key] = original_users[key] | {"login": p["new_login"], "password_hash": p["password_hash"]}
            db.execute("UPDATE users SET login=?,password_hash=? WHERE org_id=? AND id=?", (p["new_login"], p["password_hash"], org, key))
            db.execute("DELETE FROM sessions WHERE org_id=? AND user_id=?", (org, key))
            for login in {p["old_login"],p["new_login"]}:
                db.execute("DELETE FROM login_limits WHERE bucket=?", ("account:"+token_digest(plan["organization"]+":"+login),))
            store.audit(db, actor, "person_login_password_changed", key, old_login=p["old_login"], new_login=p["new_login"])
        inspect(db, plan, replay=True)
        after_users = {r["id"]: dict(r) for r in db.execute("SELECT * FROM users")}
        if set(after_users) != set(original_users) or any(after_users[k] != wanted.get(k,v) for k,v in original_users.items()) or protected() != before or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Unexpected identity or business changes; rolled back")
        result = {"version": "0.5.0", "plan_sha256": plan_digest, "accounts_changed": len(users), "new_accounts": 0,
                  "classes_preserved": plan["expected"]["classes"], "roles_and_user_ids_preserved": True,
                  "business_data_unchanged": True, "sessions_revoked": True, "owner_login": plan["owner_new_login"],
                  "roster_review_only": True, "wechat_sending_enabled": False, "backup_integrity": "ok",
                  "backup_sha256": info["sha256"], "replayed": False}
        store.audit(db, actor, "teacher_logins_changed", plan_digest, **result)
    if hashlib.sha256(backup_path.read_bytes()).hexdigest() != info["sha256"]:
        raise ValueError("Backup changed after commit; inspect before retrying")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("database", "release", "plan", "backup"):
        parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--apply-confirmed-login-change", action="store_true", required=True)
    args = parser.parse_args();os.umask(0o077)
    try:
        # Reuse the reviewed deployment checks without modifying the frozen service package.
        import importlib.util
        shared = Path(__file__).with_name("teacher-access.py")
        spec=importlib.util.spec_from_file_location("access_checks",shared);checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(checks)
        if not re.fullmatch(r"/opt/wxnotify/app/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}/release",str(args.release)) or args.database != args.release.parent/"data/roster.sqlite":
            raise ValueError("Only the independent review database may change")
        for p in (args.database,args.plan,shared):checks.private(p)
        for p in (args.database.parent,args.release,args.backup.parent):checks.private(p,True)
        raw=(args.release/"MANIFEST.json").read_bytes()
        if hashlib.sha256(raw).hexdigest()!=checks.MANIFEST_SHA256:raise ValueError("Release manifest differs")
        manifest=json.loads(raw)
        if len(manifest["files"])!=27:raise ValueError("Release file count differs")
        for name,expected in manifest["files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:raise ValueError("Unsafe release path")
            p=args.release/name;checks.private(p)
            if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError("Deployed source changed")
        payload=args.plan.read_bytes()
        if len(payload)>1024*1024 or hashlib.sha256(payload).hexdigest()!=args.plan_file_sha256:raise ValueError("Plan file changed")
        sys.path.insert(0,str(args.release))
        from business_next.migration import digest
        from urllib.request import build_opener,ProxyHandler
        opener=build_opener(ProxyHandler({}))
        def healthy():
            with opener.open("http://127.0.0.1:18082/health/ready",timeout=5) as response:h=json.load(response)
            if h.get("version")!="0.5.0" or h.get("roster_review_only") is not True or any(h.get(k) is not False for k in ("wechat_sending_enabled","student_sync_enabled","parent_binding_enabled")):raise ValueError("Expected read-only service")
        healthy();plan=json.loads(payload);result=change(args.database,plan,digest(plan),args.backup);healthy()
        print(json.dumps(result,sort_keys=True))
    except Exception:
        parser.exit(2,"STOP: credential change was not confirmed complete. Preserve backup and inspect before retrying. No passwords printed.\n")


if __name__=="__main__":main()
