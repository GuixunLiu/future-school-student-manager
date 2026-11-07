"""Explicitly local-test APIs. No public deployment or real WeChat identity yet."""

import asyncio
import json
import sqlite3
import re
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import VERSION
from . import models
from .security import new_token, token_digest
from .store import Problem, Store, uid


class LocalGuard:
    """Bound body memory/time before parsing; reject non-loopback pilot access."""
    def __init__(self, app, allow_test_client=False):
        self.app, self.allow_test_client = app, allow_test_client

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = (scope.get("client") or ("", 0))[0]
        allowed = {"127.0.0.1", "::1"} | ({"testclient"} if self.allow_test_client else set())
        headers = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                   (b"referrer-policy", b"no-referrer")]

        async def outgoing(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() not in {name for name, _ in headers}] + headers
            await send(message)

        async def reject(status, code):
            response = JSONResponse({"error": {"code": code, "message": "请求未被接受"}}, status_code=status)
            await response(scope, receive, outgoing)

        if client not in allowed:
            return await reject(403, "local_test_only")
        hosts = [v.decode("latin-1").lower() for k, v in scope.get("headers", []) if k.lower() == b"host"]
        host = hosts[0] if len(hosts) == 1 else ""
        if not re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", host) and not (self.allow_test_client and host == "testserver"):
            return await reject(403, "invalid_host")
        origins = [v.decode("latin-1").lower() for k, v in scope.get("headers", []) if k.lower() == b"origin"]
        if origins and (len(origins) != 1 or origins[0] != scope.get("scheme", "http") + "://" + host):
            return await reject(403, "cross_origin_rejected")
        if scope.get("path") in {"/", "/workspace.js", "/workspace.css", "/runtime", "/runtime.js", "/runtime.css"}:
            headers = [(k, v) if k != b"content-security-policy" else (k, b"default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'") for k, v in headers]
        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths and (len(lengths) != 1 or len(lengths[0]) > 5 or not lengths[0].isdigit() or int(lengths[0]) > 16384):
            return await reject(413, "body_limit")
        body = bytearray()

        async def collect():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return False
                body.extend(message.get("body", b""))
                if len(body) > 16384:
                    raise OverflowError
                if not message.get("more_body", False):
                    return True
        try:
            if not await asyncio.wait_for(collect(), 10):
                return
        except OverflowError:
            return await reject(413, "body_limit")
        except TimeoutError:
            return await reject(408, "body_timeout")

        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, outgoing)


def create_app(database: Path, clock=None, *, allow_test_client=False) -> FastAPI:
    store = Store(database, clock)
    api = FastAPI(title="wxnotify local business pilot", version=VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None, redirect_slashes=False)
    api.state.store = store
    api.add_middleware(LocalGuard, allow_test_client=allow_test_client)

    @api.exception_handler(Problem)
    async def problem(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status,
                            headers={"WWW-Authenticate": "Bearer"} if error.status == 401 else {})

    @api.exception_handler(RequestValidationError)
    async def invalid(request, error):
        # Pydantic's normal error payload can echo input/passwords. Never return it.
        return JSONResponse({"error": {"code": "invalid_request", "message": "字段格式不正确或包含未支持的字段"}}, status_code=422)

    @api.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, error):
        return JSONResponse({"error": {"code": "conflict", "message": "记录冲突，未保存任何更改"}}, status_code=409)

    @api.exception_handler(sqlite3.OperationalError)
    async def storage_busy(request, error):
        return JSONResponse({"error": {"code": "storage_unavailable", "message": "暂时无法保存，请稍后重试"}}, status_code=503)

    def bearer(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 128:
            raise Problem(401, "unauthorized", "请重新登录")
        return authorization[7:]
    Token = Annotated[str, Depends(bearer)]
    Page = Annotated[int, Query(ge=1, le=100)]
    Cursor = Annotated[str, Query(pattern=r"^(?:|[0-9a-f]{32})$")]

    @api.get("/health/ready")
    def ready():
        with store.transaction() as db:
            db.execute("SELECT id FROM organizations LIMIT 1").fetchone()
        return {"status": "ready", "version": VERSION, "mode": "local_test", "server_time": store.clock(), "business_ready": False,
                "account_api_enabled": True, "class_authorization_enabled": True,
                "wechat_sending_enabled": False, "student_sync_enabled": False, "parent_binding_enabled": False}

    @api.post("/v1/auth/login")
    def login(data: models.Login, request: Request):
        return store.login(data.organization, data.login, data.password, request.client.host)

    @api.get("/v1/me")
    def me(token: Token):
        return store.command(token, store.me)

    @api.post("/v1/auth/logout")
    def logout(token: Token):
        def run(db, actor):
            store.require_user(actor)
            db.execute("DELETE FROM sessions WHERE digest=?", (actor["session_digest"],))
            return {"logged_out": True}
        return store.command(token, run)

    @api.post("/v1/admin/people", status_code=201)
    def create_person(data: models.Person, token: Token):
        return store.command(token, lambda db, actor: store.create_person(db, actor, data.model_dump()))

    @api.get("/v1/admin/people")
    def people(token: Token, after: Cursor = "", limit: Page = 40):
        def run(db, actor):
            store.require_user(actor, "admin")
            rows = list(db.execute("SELECT id FROM users WHERE org_id=? AND id>? ORDER BY id LIMIT ?", (actor["org_id"], after, limit + 1)))
            return {"items": [store.person(db, actor, row[0]) for row in rows[:limit]],
                    "next_cursor": rows[limit - 1][0] if len(rows) > limit else None}
        return store.command(token, run)

    @api.patch("/v1/admin/people/{person_id}")
    def access(person_id: models.ID, data: models.PersonAccess, token: Token):
        return store.command(token, lambda db, actor: store.change_person(db, actor, person_id, data.model_dump(exclude_unset=True)))

    @api.post("/v1/admin/people/{person_id}/reset-password")
    def reset_password(person_id: models.ID, data: models.PasswordReset, token: Token):
        from .security import password_hash
        def run(db, actor):
            store.require_user(actor, "admin")
            store.entity(db, actor, "users", person_id)
            db.execute("UPDATE users SET password_hash=? WHERE org_id=? AND id=?", (password_hash(data.password), actor["org_id"], person_id))
            db.execute("DELETE FROM sessions WHERE org_id=? AND user_id=?", (actor["org_id"], person_id))
            store.audit(db, actor, "password_reset", person_id)
            return {"reauthentication_required": True}
        return store.command(token, run)

    @api.post("/v1/admin/classes", status_code=201)
    def create_class(data: models.Class, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            class_id = uid()
            db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (class_id, actor["org_id"], data.name, data.course_name, store.clock()))
            store.audit(db, actor, "class_created", class_id)
            return {"id": class_id, **data.model_dump()}
        return store.command(token, run)

    @api.get("/v1/classes")
    def classes(token: Token, after: Cursor = "", limit: Page = 40):
        def run(db, actor):
            store.require_user(actor)
            allowed = store.class_ids(db, actor)
            rows = [] if not allowed else list(db.execute("SELECT id,name,course_name FROM classes WHERE org_id=? AND id>? AND id IN (" + ",".join("?" for _ in allowed) + ") ORDER BY id LIMIT ?", (actor["org_id"], after, *allowed, limit + 1)))
            return {"items": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}
        return store.command(token, run)

    @api.put("/v1/admin/classes/{class_id}/teachers")
    def teachers(class_id: models.ID, data: models.Teachers, token: Token):
        return store.command(token, lambda db, actor: store.set_teachers(db, actor, class_id, data.teacher_ids, data.expected_teacher_ids))

    @api.post("/v1/admin/students", status_code=201)
    def create_student(data: models.Student, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            store.entity(db, actor, "classes", data.class_id)
            student_id = uid()
            db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)", (student_id, actor["org_id"], data.class_id, data.name, data.external_ref, data.remaining_units))
            store.audit(db, actor, "student_created", student_id)
            return {"id": student_id, **data.model_dump()}
        return store.command(token, run)

    @api.get("/v1/students")
    def students(token: Token, after: Cursor = "", limit: Page = 40):
        return store.command(token, lambda db, actor: store.students(db, actor, after, limit))

    @api.get("/v1/students/{student_id}")
    def student(student_id: models.ID, token: Token):
        def run(db, actor):
            row = store.require_student(db, actor, student_id)
            return {key: row[key] for key in ("id", "name", "class_id", "remaining_units", "active")}
        return store.command(token, run)

    @api.post("/v1/test/parent-links", status_code=201)
    def link(data: models.TestParentLink, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            parent = store.entity(db, actor, "users", data.parent_id)
            if not parent["active"] or not db.execute("SELECT 1 FROM roles WHERE org_id=? AND user_id=? AND role='parent'", (actor["org_id"], data.parent_id)).fetchone():
                raise Problem(409, "not_parent", "请先设置测试家长身份")
            store.entity(db, actor, "students", data.student_id)
            db.execute("INSERT INTO test_parent_links VALUES(?,?,?,?)", (actor["org_id"], data.parent_id, data.student_id, store.clock()))
            store.audit(db, actor, "test_parent_link_created", data.student_id, parent_id=data.parent_id)
            return {"test_only": True, "wechat_verified": False, **data.model_dump()}
        return store.command(token, run)

    @api.delete("/v1/test/parent-links")
    def unlink(data: models.TestParentLink, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            store.entity(db, actor, "users", data.parent_id)
            store.entity(db, actor, "students", data.student_id)
            db.execute("DELETE FROM test_parent_links WHERE org_id=? AND user_id=? AND student_id=?", (actor["org_id"], data.parent_id, data.student_id))
            db.execute("UPDATE outbox SET status='cancelled',cancel_reason='test_parent_unlinked' WHERE org_id=? AND recipient_id=? AND student_id=? AND status='pending_simulation' AND notice_type IN ('attendance','low_hours_parent','lesson_content','absence_parent')", (actor["org_id"], data.parent_id, data.student_id))
            store.audit(db, actor, "test_parent_link_removed", data.student_id, parent_id=data.parent_id)
            return {"unlinked": True}
        return store.command(token, run)

    @api.post("/v1/admin/devices", status_code=201)
    def device(data: models.Device, token: Token):
        return store.command(token, lambda db, actor: store.create_device(db, actor, data.model_dump()))

    @api.put("/v1/admin/devices/{device_id}/access")
    def device_access(device_id: models.ID, data: models.DeviceAccess, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            store.entity(db, actor, "devices", device_id)
            for class_id in data.class_ids:
                store.entity(db, actor, "classes", class_id)
            db.execute("UPDATE devices SET active=? WHERE org_id=? AND id=?", (int(data.active), actor["org_id"], device_id))
            db.execute("DELETE FROM device_classes WHERE org_id=? AND device_id=?", (actor["org_id"], device_id))
            for class_id in data.class_ids:
                db.execute("INSERT INTO device_classes VALUES(?,?,?)", (actor["org_id"], device_id, class_id))
            store.audit(db, actor, "device_access_changed", device_id, active=data.active, class_ids=data.class_ids)
            return {"id": device_id, **data.model_dump()}
        return store.command(token, run)

    @api.post("/v1/admin/devices/{device_id}/rotate")
    def rotate_device(device_id: models.ID, token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            store.entity(db, actor, "devices", device_id)
            secret, expires_at = new_token("device"), store.clock() + 90 * 86400
            db.execute("UPDATE devices SET digest=?,expires_at=? WHERE org_id=? AND id=?", (token_digest(secret), expires_at, actor["org_id"], device_id))
            store.audit(db, actor, "device_token_rotated", device_id)
            return {"id": device_id, "device_token": secret, "expires_at": expires_at, "secret_shown_once": True}
        return store.command(token, run)

    @api.get("/v1/admin/audit")
    def audit(token: Token, after: Cursor = "", limit: Page = 40):
        def run(db, actor):
            store.require_user(actor, "admin")
            rows = list(db.execute("SELECT id,actor_id,action,entity_id,metadata,created_at FROM audit WHERE org_id=? AND id>? ORDER BY id LIMIT ?", (actor["org_id"], after, limit + 1)))
            return {"items": [dict(row) | {"metadata": json.loads(row["metadata"])} for row in rows[:limit]],
                    "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}
        return store.command(token, run)

    from .routes import install_routes
    install_routes(api, store, bearer)
    from .portal import install_portal
    install_portal(api, store, bearer)
    from .device_sync import install_device_sync
    install_device_sync(api, store, bearer)
    return api
