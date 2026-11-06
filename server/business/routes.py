"""Authenticated local-test workflow endpoints; no outbound delivery adapter."""

import json
from typing import Annotated

from fastapi import Depends, Query

from . import models
from .jobs import Jobs
from .store import Problem, uid
from .workflows import Workflows


def install_routes(api, store, bearer):
    flow = Workflows(store)
    jobs = Jobs(flow)
    Token = Annotated[str, Depends(bearer)]
    Cursor = Annotated[str, Query(pattern=r"^(|[0-9a-f]{32})$")]
    Page = Annotated[int, Query(ge=1, le=100)]

    @api.post("/v1/admin/lessons", status_code=201)
    def create_lesson(data: models.Lesson, token: Token):
        return store.command(token, lambda db, a: flow.create_lesson(db, a, data.model_dump()))

    @api.get("/v1/lessons/{lesson_id}")
    def lesson(lesson_id: models.ID, token: Token):
        return store.command(token, lambda db, a: flow.detail(db, a, lesson_id))

    @api.post("/v1/lessons/{lesson_id}/leaves", status_code=201)
    def leave(lesson_id: models.ID, data: models.Leave, token: Token):
        return store.command(token, lambda db, a: flow.create_leave(db, a, lesson_id, data.model_dump()))

    @api.post("/v1/leaves/{leave_id}/cancel")
    def cancel(leave_id: models.ID, data: models.CancelLeave, token: Token):
        return store.command(token, lambda db, a: flow.cancel_leave(db, a, leave_id, data.reason))

    @api.post("/v1/device/attendance")
    def attendance(data: models.AttendanceEvent, token: Token):
        return store.command(token, lambda db, a: flow.ingest_attendance(db, a, data.model_dump()))

    @api.post("/v1/device/balance")
    def balance(data: models.BalanceEvent, token: Token):
        return store.command(token, lambda db, a: flow.ingest_balance(db, a, data.model_dump()))

    @api.post("/v1/device/attendance/{attendance_id}/revoke")
    def revoke(attendance_id: models.ID, data: models.RevokeEvent, token: Token):
        return store.command(token, lambda db, a: flow.revoke(db, a, attendance_id, data.model_dump()))

    @api.put("/v1/me/notification-preferences")
    def preferences(data: models.Preferences, token: Token):
        return store.command(token, lambda db, a: flow.preferences(db, a, data.preferences))

    @api.get("/v1/me/notification-preferences")
    def get_preferences(token: Token):
        def run(db, a):
            store.require_user(a)
            values = {kind: False for kind in flow_types(a)}
            values.update({r[0]: bool(r[1]) for r in db.execute("SELECT event_type,enabled FROM notification_preferences WHERE org_id=? AND user_id=?", (a["org_id"], a["id"]))})
            return {"test_only": True, "preferences": values}
        return store.command(token, run)

    def flow_types(a):
        from .workflows import PARENT_TYPES, TEACHER_TYPES, ADMIN_TYPES
        return set().union(*(types for role, types in (("parent", PARENT_TYPES), ("teacher", TEACHER_TYPES), ("admin", ADMIN_TYPES)) if role in a["roles"]))

    @api.post("/v1/lessons/{lesson_id}/absence-notices")
    def absence(lesson_id: models.ID, data: models.AbsenceNotices, token: Token):
        return store.command(token, lambda db, a: flow.absence_notices(db, a, lesson_id, data.student_ids))

    @api.put("/v1/lessons/{lesson_id}/content")
    def content(lesson_id: models.ID, data: models.Content, token: Token):
        return store.command(token, lambda db, a: flow.content(db, a, lesson_id, data.model_dump()))

    @api.get("/v1/lessons/{lesson_id}/content")
    def get_content(lesson_id: models.ID, token: Token):
        def run(db, a):
            store.require_user(a)
            row = db.execute("SELECT * FROM lesson_content WHERE org_id=? AND lesson_id=?", (a["org_id"], lesson_id)).fetchone()
            if not row:
                raise Problem(404, "not_found", "未找到内容")
            if "admin" in a["roles"] or "teacher" in a["roles"] and store.entity(db, a, "lessons", lesson_id)["class_id"] in store.class_ids(db, a):
                flow.lesson(db, a, lesson_id)
            else:
                linked = db.execute("SELECT 1 FROM lesson_members m JOIN test_parent_links p ON p.org_id=m.org_id AND p.student_id=m.student_id WHERE m.org_id=? AND m.lesson_id=? AND p.user_id=? AND (?='roster' OR EXISTS(SELECT 1 FROM attendance x WHERE x.org_id=m.org_id AND x.lesson_id=m.lesson_id AND x.student_id=m.student_id AND x.revoked=0))", (a["org_id"], lesson_id, a["id"], row["audience"])).fetchone()
                if "parent" not in a["roles"] or row["queued_at"] is None or not linked:
                    raise Problem(403, "content_forbidden", "此身份无权查看本节内容")
            return {"lesson_id": lesson_id, "body": row["body"], "audience": row["audience"], "publish_after_class": bool(row["publish_after_class"]), "published_in_test": row["queued_at"] is not None, "test_only": True}
        return store.command(token, run)

    @api.get("/v1/reports/weekly")
    def weekly(token: Token, week_start: Annotated[str, Query(max_length=10)]):
        return store.command(token, lambda db, a: flow.weekly(db, a, week_start))

    @api.get("/v1/me/reports/weekly")
    def own_weekly(token: Token, week_start: Annotated[str, Query(max_length=10)]):
        return store.command(token, lambda db, a: flow.weekly(db, a, week_start, own=True))

    @api.post("/v1/admin/student-activity", status_code=201)
    def activity(data: models.Activity, token: Token):
        def run(db, a):
            store.require_user(a, "admin")
            student = store.entity(db, a, "students", data.student_id)
            occurred = flow.event_time(data.occurred_at)
            old = db.execute("SELECT * FROM student_activity WHERE org_id=? AND source_ref=?", (a["org_id"], data.source_ref)).fetchone()
            if old:
                if (old["student_id"], old["kind"], old["occurred_at"]) != (data.student_id, data.kind, occurred):
                    raise Problem(409, "activity_changed", "相同记录编号不能改为其他内容")
                return {"id": old["id"], "duplicate": True, "hours_adjusted": False}
            activity_id = uid()
            course = store.entity(db, a, "classes", student["class_id"])
            db.execute("INSERT INTO student_activity VALUES(?,?,?,?,?,?,?)", (activity_id, a["org_id"], student["id"], data.kind, occurred, data.source_ref, course["name"]))
            store.audit(db, a, "student_activity_recorded", activity_id, kind=data.kind, student_id=student["id"])
            return {"id": activity_id, "duplicate": False, "hours_adjusted": False}
        return store.command(token, run)

    @api.get("/v1/reports/monthly")
    def monthly(token: Token, month: Annotated[str, Query(max_length=7)]):
        return store.command(token, lambda db, a: flow.monthly(db, a, month))

    @api.put("/v1/admin/notification-schedule")
    def schedule(data: models.Schedule, token: Token):
        return store.command(token, lambda db, a: jobs.configure(db, a, data.model_dump()))

    @api.get("/v1/admin/notification-schedule")
    def get_schedule(token: Token):
        return store.command(token, jobs.configuration)

    @api.post("/v1/admin/jobs/tick")
    def tick(token: Token):
        return store.command(token, jobs.tick)

    @api.post("/v1/test/simulate")
    def simulate(token: Token):
        return store.command(token, flow.simulate)

    def notices(db, a, after, limit, admin):
        store.require_user(a, "admin" if admin else None)
        # Cursor scans are bounded independently of revoked/hidden recipient rows.
        rows = list(db.execute("SELECT * FROM outbox WHERE org_id=? AND id>?" + ("" if admin else " AND recipient_id=?") + " ORDER BY id LIMIT ?", (a["org_id"], after, limit + 1) if admin else (a["org_id"], after, a["id"], limit + 1)))
        items = []
        for row in rows[:limit]:
            payload = json.loads(row["payload"])
            if admin or flow.recipient_allowed(db, a["org_id"], a["id"], row["notice_type"], row["student_id"], row["class_id"], payload["data"]):
                recipient = store.entity(db, a, "users", row["recipient_id"])
                items.append({key: row[key] for key in ("id", "notice_type", "status", "created_at", "expires_at", "simulated_at", "cancel_reason")} | {"payload": payload})
                items[-1]["recipient_name"] = recipient["display_name"]
        return {"items": items, "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None, "wechat_sent": False, "test_only": True}

    @api.get("/v1/me/notifications")
    def mine(token: Token, after: Cursor = "", limit: Page = 40):
        return store.command(token, lambda db, a: notices(db, a, after, limit, False))

    @api.get("/v1/admin/outbox")
    def outbox(token: Token, after: Cursor = "", limit: Page = 40):
        return store.command(token, lambda db, a: notices(db, a, after, limit, True))
