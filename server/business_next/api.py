"""Roster review is loopback-only and cannot send WeChat or sync the tablet."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field
from starlette.middleware import Middleware

from business.api import create_app as base_app
from business.models import ID, Input
from business.store import Problem
from . import VERSION
from .archive import restore, verify


class Restore(Input):
    class_id: ID
    expected_remaining_units: int = Field(ge=-1000000, le=10000000)
    confirmed: bool


class ReviewGuard:
    def __init__(self, app, enabled):
        self.app, self.enabled = app, enabled

    async def __call__(self, scope, receive, send):
        if (self.enabled and scope["type"] == "http" and scope["method"] not in {"GET", "HEAD"}
                and scope["path"] not in {"/v1/auth/login", "/v1/auth/logout"}):
            return await JSONResponse({"error": {"code": "roster_review_only", "message": "当前是名册核对副本，暂不修改业务资料。平板仍为准。"}}, status_code=409)(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(database: Path, clock=None, *, allow_test_client=False, review_only=True):
    api = base_app(database, clock, allow_test_client=allow_test_client)
    store = api.state.store
    verify(store)
    original_ready = next(r.endpoint for r in api.router.routes if r.path == "/health/ready")
    original_home = next(r.endpoint for r in api.router.routes if r.path == "/")
    api.router.routes[:] = [r for r in api.router.routes if r.path not in {"/health/ready", "/workspace.js", "/"}]
    api.version = VERSION
    # Place the review lock inside LocalGuard, so rejected requests retain its host/origin/body checks and headers.
    api.user_middleware.append(Middleware(ReviewGuard, enabled=review_only))

    def bearer(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 128:
            raise Problem(401, "unauthorized", "请重新登录")
        return authorization[7:]
    Token = Annotated[str, Depends(bearer)]

    @api.get("/health/ready")
    def ready():
        verify(store)
        return original_ready() | {"version": VERSION, "mode": "roster_review" if review_only else "local_test",
                                   "archive_enabled": True, "roster_review_only": review_only}

    @api.get("/", include_in_schema=False)
    def home():
        content = original_home().body.decode("utf-8")
        content = content.replace("资料尚未与平板同步 · 通知仅作演练", "名册核对副本 · 余额以平板最新记录为准 · 微信尚未启用")
        return Response(content, media_type="text/html")

    @api.get("/workspace.js", include_in_schema=False)
    def script():
        return Response(Path(__file__).with_name("web").joinpath("workspace.js").read_bytes(), media_type="text/javascript")

    @api.get("/v1/admin/archives")
    def archives(token: Token, after: Annotated[str, Query(pattern=r"^(|[0-9a-f]{32})$")] = "",
                 limit: Annotated[int, Query(ge=1, le=100)] = 40):
        def run(db, actor):
            store.require_user(actor, "admin")
            rows = list(db.execute("SELECT id,name,external_ref,remaining_units,previous_class_name FROM archived_students WHERE org_id=? AND id>? ORDER BY id LIMIT ?", (actor["org_id"], after, limit + 1)))
            return {"items": [dict(r) for r in rows[:limit]], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
                    "review_only": review_only}
        return store.command(token, run)

    @api.post("/v1/admin/archives/{student_id}/restore")
    def restore_student(student_id: ID, data: Restore, token: Token):
        if not data.confirmed:
            raise Problem(422, "confirmation_required", "请核对学生、余额和恢复班级")
        return store.command(token, lambda db, actor: restore(store, db, actor, student_id, data.class_id, data.expected_remaining_units))

    @api.get("/v1/admin/roster-summary")
    def summary(token: Token):
        def run(db, actor):
            store.require_user(actor, "admin")
            org = actor["org_id"]
            active = db.execute("SELECT count(*),COALESCE(sum(remaining_units),0) FROM students WHERE org_id=? AND active=1", (org,)).fetchone()
            archived = db.execute("SELECT count(*),COALESCE(sum(remaining_units),0) FROM archived_students WHERE org_id=?", (org,)).fetchone()
            imports = [dict(r) for r in db.execute("SELECT source_date,source_sha256,imported_at FROM roster_imports WHERE org_id=? ORDER BY imported_at", (org,))]
            return {"active_students": active[0], "active_remaining_units": active[1], "archived_students": archived[0],
                    "archived_remaining_units": archived[1], "review_only": review_only, "imports": imports,
                    "classes": db.execute("SELECT count(*) FROM classes WHERE org_id=?", (org,)).fetchone()[0]}
        return store.command(token, run)

    return api
