"""Transactional tenant-scoped identity and permission store for the local pilot."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .security import ROLES, identifier, new_token, password_hash, token_digest, valid_token, verify_password

APPLICATION_ID = 1465405012


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def uid() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: int(time.time()))
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("Use an existing, explicitly initialized private database")
        db = self.connect()
        try:
            if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unknown database; no automatic migration or overwrite")
        finally:
            db.close()

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @contextmanager
    def transaction(self, *, read_only=False):
        db = self.connect()
        try:
            if read_only:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def bootstrap(cls, path: Path, organization: str, login: str, display_name: str, password: str, clock=None):
        organization, login = identifier(organization), identifier(login)
        if not display_name.strip() or len(display_name) > 80:
            raise ValueError("Invalid display name")
        encoded = password_hash(password)
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        now = int(clock() if clock else time.time())
        db = sqlite3.connect(str(path), isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            org_id, user_id = uid(), uid()
            db.execute("INSERT INTO organizations VALUES(?,?,?)", (org_id, organization, now))
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)", (user_id, org_id, login, display_name.strip(), encoded, 1, now))
            db.execute("INSERT INTO roles VALUES(?,?,?)", (org_id, user_id, "admin"))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return cls(path, clock)

    def audit(self, db, actor, action, entity_id, **metadata):
        db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?)", (uid(), actor["org_id"], actor["id"], action,
                   entity_id, json.dumps(metadata, ensure_ascii=True, sort_keys=True), self.clock()))

    @staticmethod
    def require_user(actor, role=None):
        if actor["kind"] != "user" or role and role not in actor["roles"]:
            raise Problem(403, "forbidden", "没有此操作权限")

    def authenticate(self, db, token):
        if not valid_token(token):
            raise Problem(401, "unauthorized", "请重新登录")
        digest, now = token_digest(token), self.clock()
        if token.startswith("wxu_"):
            user = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id AND u.org_id=s.org_id WHERE s.digest=? AND s.expires_at>? AND u.active=1", (digest, now)).fetchone()
            if user:
                roles = {r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=?", (user["org_id"], user["id"]))}
                return {"kind": "user", "id": user["id"], "org_id": user["org_id"], "roles": roles,
                        "login": user["login"], "display_name": user["display_name"], "session_digest": digest}
        else:
            device = db.execute("SELECT * FROM devices WHERE digest=? AND active=1 AND expires_at>?", (digest, now)).fetchone()
            if device:
                return {"kind": "device", "id": device["id"], "org_id": device["org_id"], "roles": set(), "name": device["name"]}
        raise Problem(401, "unauthorized", "请重新登录")

    def command(self, token, operation, *, read_only=False):
        with self.transaction(read_only=read_only) as db:
            return operation(db, self.authenticate(db, token))

    def login(self, organization, login, password, source):
        # Failed attempts must COMMIT, not be rolled back by the HTTP error.
        now = self.clock()
        org_slug, login = identifier(organization), identifier(login)
        buckets = [("ip:" + token_digest(source), 30), ("account:" + token_digest(org_slug + ":" + login), 5)]
        result, failure = None, None
        with self.transaction() as db:
            db.execute("DELETE FROM login_limits WHERE window_start<? AND blocked_until<?", (now - 86400, now))
            limits = [(bucket, maximum, db.execute("SELECT * FROM login_limits WHERE bucket=?", (bucket,)).fetchone()) for bucket, maximum in buckets]
            if any(row and row["blocked_until"] > now for _, _, row in limits):
                failure = Problem(429, "login_limited", "尝试过于频繁，请稍后再试")
            else:
                user = db.execute("SELECT u.* FROM users u JOIN organizations o ON o.id=u.org_id WHERE o.slug=? AND u.login=?", (org_slug, login)).fetchone()
                valid = verify_password(password, user["password_hash"] if user else None)
                if not valid or not user or not user["active"]:
                    for bucket, maximum, row in limits:
                        fresh = not row or now - row["window_start"] >= 300
                        count, start = (1, now) if fresh else (row["failures"] + 1, row["window_start"])
                        blocked = now + 300 if count >= maximum else 0
                        db.execute("INSERT INTO login_limits VALUES(?,?,?,?) ON CONFLICT(bucket) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until", (bucket, count, start, blocked))
                    failure = Problem(401, "invalid_login", "账号或密码不正确")
                else:
                    db.execute("DELETE FROM login_limits WHERE bucket=?", (buckets[1][0],))
                    db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
                    # Bound sessions per person, keeping at most five active sessions.
                    db.execute("DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions WHERE org_id=? AND user_id=? ORDER BY created_at DESC,digest DESC LIMIT -1 OFFSET 4)", (user["org_id"], user["id"]))
                    token = new_token("user")
                    db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (token_digest(token), user["org_id"], user["id"], now + 8 * 3600, now))
                    result = {"access_token": token, "token_type": "Bearer", "expires_at": now + 8 * 3600, "identity_provider": "local_test_password"}
        if failure:
            raise failure
        return result

    def me(self, db, actor):
        public = {key: actor[key] for key in ("id", "org_id", "kind")}
        if actor["kind"] == "user":
            public.update(login=actor["login"], display_name=actor["display_name"], roles=sorted(actor["roles"]),
                          identity_provider="local_test_password", wechat_verified=False)
        else:
            public.update(name=actor["name"], class_ids=self.class_ids(db, actor))
        return public

    def entity(self, db, actor, table, entity_id):
        if table not in {"users", "classes", "students", "devices", "lessons"}:
            raise ValueError("Invalid entity table")
        row = db.execute(f"SELECT * FROM {table} WHERE org_id=? AND id=?", (actor["org_id"], entity_id)).fetchone()
        if not row:
            raise Problem(404, "not_found", "未找到该记录")
        return row

    def class_ids(self, db, actor):
        if actor["kind"] == "device":
            rows = db.execute("SELECT class_id FROM device_classes WHERE org_id=? AND device_id=? ORDER BY class_id", (actor["org_id"], actor["id"]))
        elif "admin" in actor["roles"]:
            rows = db.execute("SELECT id FROM classes WHERE org_id=? ORDER BY id", (actor["org_id"],))
        elif "teacher" in actor["roles"]:
            rows = db.execute("SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=? ORDER BY class_id", (actor["org_id"], actor["id"]))
        else:
            return []
        return [r[0] for r in rows]

    def require_class(self, db, actor, class_id, *, allow_parent=False):
        self.entity(db, actor, "classes", class_id)
        if class_id in self.class_ids(db, actor):
            return
        raise Problem(403, "class_forbidden", "没有该班级权限")

    def create_person(self, db, actor, data):
        self.require_user(actor, "admin")
        person_id = uid()
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)", (person_id, actor["org_id"], data["login"], data["display_name"], password_hash(data["password"]), 1, self.clock()))
        for role in data["roles"]:
            db.execute("INSERT INTO roles VALUES(?,?,?)", (actor["org_id"], person_id, role))
        self.audit(db, actor, "person_created", person_id, roles=sorted(data["roles"]))
        return self.person(db, actor, person_id)

    def person(self, db, actor, person_id):
        self.require_user(actor, "admin")
        row = self.entity(db, actor, "users", person_id)
        roles = [r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=? ORDER BY role", (actor["org_id"], person_id))]
        return {key: row[key] for key in ("id", "login", "display_name", "active")} | {"roles": roles, "wechat_verified": False}

    def change_person(self, db, actor, person_id, data):
        self.require_user(actor, "admin")
        target = self.entity(db, actor, "users", person_id)
        old_roles = {r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))}
        if "expected_roles" in data and data["expected_roles"] is not None and set(data["expected_roles"]) != old_roles or "expected_active" in data and data["expected_active"] is not None and bool(target["active"]) != data["expected_active"]:
            raise Problem(409, "person_changed", "人员权限已被更新，请刷新后再修改")
        new_roles = set(data.get("roles", old_roles))
        active = data.get("active", bool(target["active"]))
        if not new_roles <= ROLES:
            raise Problem(422, "invalid_roles", "无效身份")
        if target["active"] and "admin" in old_roles and (not active or "admin" not in new_roles):
            count = db.execute("SELECT count(*) FROM users u JOIN roles r ON u.id=r.user_id AND u.org_id=r.org_id WHERE u.org_id=? AND u.active=1 AND r.role='admin'", (actor["org_id"],)).fetchone()[0]
            if count <= 1:
                raise Problem(409, "last_admin", "不能撤销或停用最后一位管理员")
        if "roles" in data:
            db.execute("DELETE FROM roles WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
            for role in new_roles:
                db.execute("INSERT INTO roles VALUES(?,?,?)", (actor["org_id"], person_id, role))
        db.execute("UPDATE users SET active=? WHERE org_id=? AND id=?", (int(active), actor["org_id"], person_id))
        if not active or "teacher" not in new_roles:
            db.execute("DELETE FROM teacher_classes WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
        if "parent" not in new_roles:
            db.execute("DELETE FROM test_parent_links WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
        db.execute("DELETE FROM sessions WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
        db.execute("DELETE FROM notification_preferences WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
        db.execute("UPDATE outbox SET status='cancelled',cancel_reason='person_access_changed' WHERE org_id=? AND recipient_id=? AND status='pending_simulation'", (actor["org_id"], person_id))
        self.audit(db, actor, "person_access_changed", person_id, roles=sorted(new_roles), active=bool(active))
        return self.person(db, actor, person_id)

    def set_teachers(self, db, actor, class_id, teacher_ids, expected=None):
        self.require_user(actor, "admin")
        self.entity(db, actor, "classes", class_id)
        for teacher_id in teacher_ids:
            user = self.entity(db, actor, "users", teacher_id)
            role = db.execute("SELECT 1 FROM roles WHERE org_id=? AND user_id=? AND role='teacher'", (actor["org_id"], teacher_id)).fetchone()
            if not user["active"] or not role:
                raise Problem(409, "not_teacher", "请先为在用账号授予老师身份")
        previous = {r[0] for r in db.execute("SELECT user_id FROM teacher_classes WHERE org_id=? AND class_id=?", (actor["org_id"], class_id))}
        if expected is not None and set(expected) != previous:
            raise Problem(409, "assignment_changed", "班级授权已被更新，请刷新后再修改")
        for teacher_id in previous - set(teacher_ids):
            db.execute("UPDATE outbox SET status='cancelled',cancel_reason='class_authorization_removed' WHERE org_id=? AND recipient_id=? AND status='pending_simulation' AND notice_type IN ('absence_teacher','low_hours_teacher','weekly_teacher') AND (class_id=? OR notice_type='weekly_teacher')", (actor["org_id"], teacher_id, class_id))
        db.execute("DELETE FROM teacher_classes WHERE org_id=? AND class_id=?", (actor["org_id"], class_id))
        for teacher_id in teacher_ids:
            db.execute("INSERT INTO teacher_classes VALUES(?,?,?)", (actor["org_id"], teacher_id, class_id))
        self.audit(db, actor, "class_teachers_changed", class_id, teacher_ids=sorted(teacher_ids))
        return {"class_id": class_id, "teacher_ids": sorted(teacher_ids)}

    def students(self, db, actor, after="", limit=40):
        self.require_user(actor)
        allowed = self.class_ids(db, actor)
        linked = {r[0] for r in db.execute("SELECT student_id FROM test_parent_links WHERE org_id=? AND user_id=?", (actor["org_id"], actor["id"]))} if "parent" in actor["roles"] else set()
        filters, values = [], [actor["org_id"], after]
        for column, ids in (("s.class_id", allowed), ("s.id", sorted(linked))):
            if ids:
                filters.append(column + " IN (" + ",".join("?" for _ in ids) + ")")
                values.extend(ids)
        if not filters:
            return {"items": [], "next_cursor": None}
        values.append(limit + 1)
        rows = list(db.execute("SELECT s.id,s.class_id,s.name,s.external_ref,s.remaining_units,s.active,c.name AS class_name,c.course_name FROM students s JOIN classes c ON c.id=s.class_id AND c.org_id=s.org_id WHERE s.org_id=? AND s.id>? AND (" + " OR ".join(filters) + ") ORDER BY s.id LIMIT ?", values))
        return {"items": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def require_student(self, db, actor, student_id, *, write=False):
        student = self.entity(db, actor, "students", student_id)
        if actor["kind"] == "user" and student["class_id"] in self.class_ids(db, actor):
            return student
        if not write and actor["kind"] == "user" and "parent" in actor["roles"] and db.execute("SELECT 1 FROM test_parent_links WHERE org_id=? AND user_id=? AND student_id=?", (actor["org_id"], actor["id"], student_id)).fetchone():
            return student
        raise Problem(403, "student_forbidden", "没有该学生权限")

    def create_device(self, db, actor, data):
        self.require_user(actor, "admin")
        for class_id in data["class_ids"]:
            self.entity(db, actor, "classes", class_id)
        device_id, token = uid(), new_token("device")
        expires_at = self.clock() + 90 * 86400
        db.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?)", (device_id, actor["org_id"], data["name"], token_digest(token), 1, expires_at, self.clock()))
        for class_id in data["class_ids"]:
            db.execute("INSERT INTO device_classes VALUES(?,?,?)", (actor["org_id"], device_id, class_id))
        self.audit(db, actor, "device_created", device_id, class_ids=sorted(data["class_ids"]))
        return {"id": device_id, "device_token": token, "expires_at": expires_at, "class_ids": data["class_ids"], "secret_shown_once": True}
