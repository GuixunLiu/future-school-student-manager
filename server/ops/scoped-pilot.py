"""One confirmed lesson: teacher leave/draft writes only. All device and notification writes remain blocked."""
import argparse
import asyncio
from contextlib import closing
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

DYNAMIC = {"sessions", "login_limits", "audit", "leaves", "lesson_content"}


def seal(db):
    digest = hashlib.sha256()
    for name, sql in db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' ORDER BY name"):
        if not re.fullmatch(r"[a-z_]+", name):raise ValueError("Unexpected schema table")
        digest.update(json.dumps([name, sql], ensure_ascii=True).encode())
        if name not in DYNAMIC:
            rows = [list(r) for r in db.execute('SELECT * FROM "' + name + '"')]
            digest.update(json.dumps(sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=True)), ensure_ascii=True).encode())
    return digest.hexdigest()


def prepare_database(source, target, plan, stage_receipt, helper, actor_login, *, clock=None):
    """Operator step on a NEW copy only. Confirmed owner intent, not an automated teacher login."""
    from business.store import Store
    from business.workflows import Workflows
    now = int(time.time()) if clock is None else int(clock())
    start, _ = helper.validate_plan(plan, now)
    helper.private(source)
    if helper.checksum(source) != stage_receipt["candidate_sha256"]:raise ValueError("Confirmed candidate changed")
    with closing(helper.connect(source)) as db:
        helper.verify(db);helper.compare(db, plan, start)
        if helper.preserved_digest(db) != stage_receipt["preserved_data_sha256"]:raise ValueError("Protected baseline differs")
    helper.snapshot(source, target)
    store = Store(target, clock)
    with store.transaction() as db:
        org = db.execute("SELECT id FROM organizations WHERE slug=?", (plan["organization"],)).fetchone()[0]
        actor = db.execute("SELECT id FROM users WHERE org_id=? AND login=? AND active=1", (org, actor_login)).fetchone()
        if actor is None:raise ValueError("Confirmed operator account missing")
        roles = {r[0] for r in db.execute("SELECT role FROM roles WHERE org_id=? AND user_id=?", (org, actor[0]))}
        if "admin" not in roles:raise ValueError("Operator must remain an active administrator")
        teachers = db.execute("SELECT u.id FROM teacher_classes t JOIN users u ON u.id=t.user_id AND u.org_id=t.org_id WHERE t.org_id=? AND t.class_id=? AND u.active=1 AND u.display_name=?", (org, plan["server_class_id"], plan["teacher_label"])).fetchall()
        if len(teachers) != 1:raise ValueError("Confirmed teacher no longer uniquely assigned")
        a = {"kind": "user", "id": actor[0], "org_id": org, "roles": roles}
        result = Workflows(store).create_lesson(db, a, {"class_id": plan["server_class_id"], "teacher_id": teachers[0][0],
                    "starts_at": plan["starts_at"], "ends_at": plan["ends_at"], "student_ids": [m["server_student_id"] for m in plan["members"]]})
        store.audit(db, a, "confirmed_pilot_prepared", result["id"], authorization="owner_confirmed_plan_via_ssh", plan_sha256=stage_receipt["plan_sha256"])
        scope = {"organization_id": org, "class_id": plan["server_class_id"], "lesson_id": result["id"],
                 "student_ids": sorted(m["server_student_id"] for m in plan["members"]), "starts_at": result["starts_at"],
                 "ends_at": result["ends_at"], "teacher_id": teachers[0][0], "master_sha256": seal(db)}
    return scope


def verify_scope(database, scope, helper):
    with closing(helper.connect(database)) as db:
        helper.verify(db)
        if seal(db) != scope["master_sha256"]:raise ValueError("Pilot master data changed")
        lesson = db.execute("SELECT org_id,class_id,teacher_id,starts_at,ends_at FROM lessons WHERE id=?", (scope["lesson_id"],)).fetchone()
        if lesson is None or list(lesson) != [scope["organization_id"], scope["class_id"], scope["teacher_id"], scope["starts_at"], scope["ends_at"]]:raise ValueError("Confirmed lesson changed")
        ids = [r[0] for r in db.execute("SELECT student_id FROM lesson_members WHERE org_id=? AND lesson_id=? ORDER BY student_id", (scope["organization_id"], scope["lesson_id"]))]
        if ids != scope["student_ids"]:raise ValueError("Confirmed lesson membership changed")
        if db.execute("SELECT count(*) FROM devices WHERE active=1").fetchone()[0] or db.execute("SELECT count(*) FROM device_classes").fetchone()[0] or db.execute("SELECT count(*) FROM outbox WHERE status='pending_simulation'").fetchone()[0]:raise ValueError("Device or notification state is not disabled")


class ScopeGuard:
    def __init__(self, app, database, scope, helper):self.app,self.database,self.scope,self.helper = app,database,scope,helper

    async def __call__(self, request, receive, send):
        from starlette.responses import JSONResponse
        async def reject(code, message):
            return await JSONResponse({"error": {"code": code, "message": message}}, status_code=403 if code != "pilot_baseline_changed" else 503)(request, receive, send)
        if request["type"] != "http":return await self.app(request, receive, send)
        method, path = request["method"], request["path"]
        if method in {"GET", "HEAD"} or path in {"/v1/auth/login", "/v1/auth/logout"} and method == "POST":return await self.app(request, receive, send)
        approved = (method == "POST" and path == "/v1/lessons/" + self.scope["lesson_id"] + "/leaves"
                    or method == "PUT" and path == "/v1/lessons/" + self.scope["lesson_id"] + "/content")
        cancel = re.fullmatch(r"/v1/leaves/([a-f0-9]{32})/cancel", path) if method == "POST" else None
        if not approved and not cancel:return await reject("pilot_scope_only", "此试点仅允许已确认课次的请假、撤假和课程草稿；设备及其他修改尚未开放。")
        try:await asyncio.to_thread(verify_scope, self.database, self.scope, self.helper)
        except (ValueError, OSError, sqlite3.Error):return await reject("pilot_baseline_changed", "试点基线已变化，暂时停止修改，请核对资料。")
        if cancel:
            with closing(self.helper.connect(self.database)) as db:
                row = db.execute("SELECT lesson_id,student_id FROM leaves WHERE org_id=? AND id=?", (self.scope["organization_id"], cancel[1])).fetchone()
            if row is None or row[0] != self.scope["lesson_id"] or row[1] not in self.scope["student_ids"]:return await reject("pilot_scope_only", "请假记录不在已确认试点课次内。")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":return
            body.extend(message.get("body", b""))
            if len(body) > 16384:return await reject("pilot_body_limit", "提交内容过长。")
            if not message.get("more_body", False):break
        try:data = json.loads(body)
        except (ValueError, UnicodeDecodeError):return await reject("pilot_invalid_body", "提交内容格式不正确。")
        if not isinstance(data, dict):return await reject("pilot_invalid_body", "提交内容格式不正确。")
        if path.endswith("/leaves") and data.get("student_id") not in self.scope["student_ids"]:return await reject("pilot_scope_only", "该学生不在本次确认名单中。")
        if path.endswith("/content") and data.get("publish_after_class", False) is not False:return await reject("pilot_notifications_disabled", "当前只保存课程草稿，通知尚未开启。")
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:delivered=True;return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        return await self.app(request, replay, send)


def build_app(database, plan, scope, helper, *, clock=None, allow_test_client=False):
    from typing import Annotated
    from fastapi import Depends, Header
    from fastapi.responses import Response, JSONResponse
    from starlette.middleware import Middleware
    from business.store import Problem
    from business_next.api import create_app
    verify_scope(database, scope, helper)
    running_code_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    api = create_app(database, clock, allow_test_client=allow_test_client, review_only=False)
    api.user_middleware.append(Middleware(ScopeGuard, database=database, scope=scope, helper=helper))
    original_ready = next(r.endpoint for r in api.router.routes if r.path == "/health/ready")
    api.router.routes[:] = [r for r in api.router.routes if r.path not in {"/", "/workspace.js", "/health/ready"}]

    def bearer(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 128:raise Problem(401,"unauthorized","请重新登录")
        return authorization[7:]
    Token = Annotated[str, Depends(bearer)]

    @api.get("/pilot/context")
    def context(token: Token):
        verify_scope(database, scope, helper)
        def run(db, actor):
            api.state.store.require_user(actor);api.state.store.require_class(db, actor, scope["class_id"])
            return {"lesson_id": scope["lesson_id"], "class_id": scope["class_id"], "class_name": plan["class_name"],
                    "starts_at": scope["starts_at"], "ends_at": scope["ends_at"], "expected": len(scope["student_ids"])}
        return api.state.store.command(token, run)

    @api.get("/pilot/leaves")
    def active_leaves(token: Token):
        def run(db, actor):
            api.state.store.require_user(actor);api.state.store.require_class(db, actor, scope["class_id"])
            return {"items": [dict(r) for r in db.execute("SELECT id,student_id,reason,created_at FROM leaves WHERE org_id=? AND lesson_id=? AND status='approved' ORDER BY id", (scope["organization_id"],scope["lesson_id"]))]}
        return api.state.store.command(token, run)

    @api.get("/pilot/students")
    def pilot_students(token: Token):
        def run(db, actor):
            api.state.store.require_user(actor);api.state.store.require_class(db, actor, scope["class_id"])
            return {"items": [dict(r) for r in db.execute("SELECT s.id,s.name,s.external_ref,s.remaining_units FROM students s JOIN lesson_members m ON m.org_id=s.org_id AND m.student_id=s.id WHERE m.org_id=? AND m.lesson_id=? ORDER BY s.id", (scope["organization_id"],scope["lesson_id"]))]}
        return api.state.store.command(token, run)

    @api.get("/health/ready")
    def ready():
        try:verify_scope(database, scope, helper)
        except (ValueError, OSError, sqlite3.Error):return JSONResponse({"status":"not_ready","mode":"pilot_scoped_teacher"},status_code=503)
        return original_ready() | {"mode": "pilot_scoped_teacher", "pilot_phase": "teacher_preparation", "roster_review_only": False,
                 "scoped_code_sha256": running_code_sha256, "lesson_id": scope["lesson_id"],
                 "selected_students": len(scope["student_ids"]), "teacher_class_writes_enabled": True, "business_ready": False,
                 "device_registration_enabled": False, "student_sync_enabled": False, "wechat_sending_enabled": False, "parent_binding_enabled": False}

    @api.get("/", include_in_schema=False)
    def home():return Response(Path(__file__).with_name("scoped-pilot.html").read_bytes(),media_type="text/html")
    @api.get("/workspace.js", include_in_schema=False)
    def script():return Response(Path(__file__).with_name("scoped-pilot-ui.js").read_bytes(),media_type="text/javascript")
    @api.get("/pilot.css", include_in_schema=False)
    def style():return Response(Path(__file__).with_name("scoped-pilot.css").read_bytes(),media_type="text/css")
    return api


def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);args=p.parse_args();work=args.config.parent
    cfg=json.loads(args.config.read_bytes());helper=load_module("scope_helper",work/"stage-single-class-pilot.py")
    helper.private(work,True);helper.private(args.config)
    if cfg["format"]!="future-school-scoped-teacher-config" or args.config.name!="scoped.private.json":raise ValueError("Explicit scoped phase required")
    if set(cfg["files"])!={"scoped-pilot.py","scoped-pilot.html","scoped-pilot-ui.js","scoped-pilot.css","stage-single-class-pilot.py","pilot-standby.py"}:raise ValueError("Complete pinned scoped runtime required")
    for name, expected in cfg["files"].items():
        target=work/name;helper.private(target)
        if name not in {"scoped-pilot.py","scoped-pilot.html","scoped-pilot-ui.js","scoped-pilot.css","stage-single-class-pilot.py","pilot-standby.py"} or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise ValueError("Scoped tools changed")
    verifier=load_module("scope_release",work/"pilot-standby.py");verifier.verify_release(verifier.RELEASE)
    sys.path.insert(0,str(verifier.RELEASE))
    import uvicorn
    api=build_app(work/"business.sqlite",cfg["plan"],cfg["scope"],helper)
    uvicorn.run(api,host="127.0.0.1",port=18085,access_log=False,proxy_headers=False,server_header=False,ws="none",limit_concurrency=16,timeout_keep_alive=5,timeout_graceful_shutdown=10)


if __name__ == "__main__":main()
