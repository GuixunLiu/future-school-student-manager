"""Explicitly delegated tablet synchronization. Never authenticates a named teacher."""

from datetime import datetime, timedelta
import json
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, ValidationError

from . import models
from .store import Problem, uid
from .workflows import SCHOOL_TZ, Workflows, local_day


class Grant(models.Input):
    enabled: bool
    allow_local_leaves: bool = False
    expected_enabled: bool | None = None
    expected_allow_local_leaves: bool | None = None


class Envelope(models.Input):
    kind: Literal["attendance", "balance", "revoke", "leave", "cancel_leave"]
    payload: dict


class LeaveEvent(models.Input):
    event_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    lesson_id: models.ID
    student_id: models.ID
    local_leave_ref: str = Field(pattern=r"^[0-9]{1,20}$")
    occurred_at: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=200)
    operator_label: str = Field(min_length=1, max_length=40)


class CancelEvent(models.Input):
    event_ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    leave_id: models.ID
    occurred_at: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=200)
    operator_label: str = Field(min_length=1, max_length=40)


class Revoke(models.RevokeEvent):
    attendance_id: models.ID


def sync_grant(db, org, device_id):
    row = db.execute("SELECT metadata,created_at FROM audit WHERE org_id=? AND entity_id=? AND action='device_sync_grant' ORDER BY rowid DESC LIMIT 1", (org, device_id)).fetchone()
    return json.loads(row[0]) | {"granted_at": row[1]} if row else {"enabled": False, "allow_local_leaves": False, "granted_at": None}


def install_device_sync(api, store, bearer):
    Token = Annotated[str, Depends(bearer)]
    flow = Workflows(store)

    def require_device(db, a):
        if a["kind"] != "device":
            raise Problem(403, "device_required", "需要已授权的平板设备")
        grant = sync_grant(db, a["org_id"], a["id"])
        if not grant["enabled"]:
            raise Problem(403, "sync_not_granted", "管理员尚未启用这台平板的同步权限")
        return grant

    @api.get("/v1/admin/devices")
    def devices(token: Token):
        def run(db, a):
            store.require_user(a, "admin")
            rows = list(db.execute("SELECT id,name,active,expires_at FROM devices WHERE org_id=? ORDER BY id LIMIT 101", (a["org_id"],)))
            if len(rows) > 100:
                raise Problem(422, "device_limit", "联调版最多展示100台设备，请先核对设备范围")
            return {"items": [dict(r) | {"sync_access": sync_grant(db, a["org_id"], r["id"])} for r in rows]}
        return store.command(token, run)

    @api.put("/v1/admin/devices/{device_id}/sync-access")
    def access(device_id: models.ID, data: Grant, token: Token):
        def run(db, a):
            store.require_user(a, "admin")
            store.entity(db, a, "devices", device_id)
            old = sync_grant(db, a["org_id"], device_id)
            if data.expected_enabled is not None and data.expected_enabled != old["enabled"] or data.expected_allow_local_leaves is not None and data.expected_allow_local_leaves != old["allow_local_leaves"]:
                raise Problem(409, "device_grant_changed", "设备授权已被更新，请刷新核对")
            store.audit(db, a, "device_sync_grant", device_id, enabled=data.enabled, allow_local_leaves=data.allow_local_leaves)
            return {"id": device_id, **sync_grant(db, a["org_id"], device_id)}
        return store.command(token, run)

    @api.get("/v1/device/sync-plan")
    def plan(token: Token):
        def run(db, a):
            grant = require_device(db, a)
            classes = list(db.execute("SELECT c.id,c.name,c.course_name FROM classes c JOIN device_classes d ON d.org_id=c.org_id AND d.class_id=c.id WHERE c.org_id=? AND d.device_id=? ORDER BY c.id", (a["org_id"], a["id"])))
            students = list(db.execute("SELECT s.id,s.name,s.external_ref,s.class_id,s.remaining_units,s.source_revision,s.source_device_id FROM students s JOIN device_classes d ON d.org_id=s.org_id AND d.class_id=s.class_id WHERE s.org_id=? AND d.device_id=? AND s.active=1 ORDER BY s.id LIMIT 2001", (a["org_id"], a["id"])))
            day = local_day(store.clock())
            start = int(datetime.combine(day - timedelta(days=14), datetime.min.time(), SCHOOL_TZ).timestamp())
            lessons = list(db.execute("SELECT l.id,l.class_id,l.starts_at,l.ends_at FROM lessons l JOIN device_classes d ON d.org_id=l.org_id AND d.class_id=l.class_id WHERE l.org_id=? AND d.device_id=? AND l.starts_at>=? AND l.starts_at<? ORDER BY l.starts_at,l.id LIMIT 501", (a["org_id"], a["id"], start, start + 46 * 86400)))
            if len(students) > 2000 or len(lessons) > 500:
                raise Problem(422, "plan_limit", "平板授权范围超过联调上限，请按班级拆分")
            roster_count = sum(db.execute("SELECT count(*) FROM lesson_members WHERE org_id=? AND lesson_id=?", (a["org_id"], r["id"])).fetchone()[0] for r in lessons)
            if roster_count > 10000:
                raise Problem(422, "plan_limit", "确认名单过多，请缩小联调范围")
            return {"protocol_version": 1, "org_id": a["org_id"], "device_id": a["id"], "server_time": store.clock(), "access": grant,
                    "classes": [dict(r) for r in classes], "students": [dict(r) for r in students],
                    "lessons": [dict(r) | {"student_ids": [m[0] for m in db.execute("SELECT student_id FROM lesson_members WHERE org_id=? AND lesson_id=? ORDER BY student_id", (a["org_id"], r["id"]))]} for r in lessons], "wechat_sending_enabled": False}
        return store.command(token, run)

    @api.post("/v1/device/events")
    def event(data: Envelope, token: Token):
        def run(db, a):
            grant = require_device(db, a)
            try:
                cls = {"attendance": models.AttendanceEvent, "balance": models.BalanceEvent, "revoke": Revoke, "leave": LeaveEvent, "cancel_leave": CancelEvent}[data.kind]
                payload = cls.model_validate(data.payload).model_dump()
            except (ValidationError, ValueError):
                raise Problem(422, "invalid_event", "同步事件字段不正确，未保存") from None
            if data.kind == "attendance":
                return flow.ingest_attendance(db, a, payload)
            if data.kind == "balance":
                return flow.ingest_balance(db, a, payload)
            if data.kind == "revoke":
                attendance_id = payload.pop("attendance_id")
                return flow.revoke(db, a, attendance_id, payload)
            if not grant["allow_local_leaves"]:
                raise Problem(403, "leave_delegation_required", "管理员尚未允许同步平板代办请假")
            try:
                occurred = flow.event_time(payload["occurred_at"])
            except ValueError:
                raise Problem(422, "invalid_event_time", "请假时间格式不正确") from None
            if data.kind == "leave":
                lesson = store.entity(db, a, "lessons", payload["lesson_id"])
                store.require_class(db, a, lesson["class_id"])
                flow.member(db, a, lesson["id"], payload["student_id"])
                def apply():
                    if db.execute("SELECT 1 FROM attendance WHERE org_id=? AND lesson_id=? AND student_id=? AND revoked=0", (a["org_id"], lesson["id"], payload["student_id"])).fetchone():
                        raise Problem(409, "already_arrived", "服务器已签到，请先核对差异")
                    leave_id = uid()
                    db.execute("INSERT INTO leaves(id,org_id,lesson_id,student_id,status,reason,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (leave_id, a["org_id"], lesson["id"], payload["student_id"], "approved", payload["reason"], a["id"], occurred))
                    store.audit(db, a, "device_leave_mirrored", leave_id, local_leave_ref=payload["local_leave_ref"], operator_label=payload["operator_label"], identity_verification="device_delegation_only")
                    return {"id": leave_id, "hours_deducted_by_server": False, "wechat_sent": False, "teacher_identity_verified": False}
                return flow.device_event(db, a, "leave", payload, apply)
            row = db.execute("SELECT * FROM leaves WHERE org_id=? AND id=?", (a["org_id"], payload["leave_id"])).fetchone()
            if not row:
                raise Problem(404, "not_found", "未找到原请假")
            lesson = store.entity(db, a, "lessons", row["lesson_id"])
            store.require_class(db, a, lesson["class_id"])
            if row["recorded_by"] != a["id"] or not db.execute("SELECT 1 FROM audit WHERE org_id=? AND entity_id=? AND actor_id=? AND action='device_leave_mirrored'", (a["org_id"], row["id"], a["id"])).fetchone():
                raise Problem(403, "source_device_required", "平板只能撤销本机同步的代办请假")
            def cancel():
                if row["status"] == "cancelled":
                    store.audit(db, a, "device_leave_cancellation_observed", row["id"], operator_label=payload["operator_label"])
                    return {"id": row["id"], "cancelled": True, "already_cancelled": True, "hours_deducted_by_server": False, "wechat_sent": False}
                if occurred < row["created_at"]:
                    raise Problem(409, "invalid_cancellation_time", "撤销时间不能早于请假登记")
                db.execute("UPDATE leaves SET status='cancelled',cancelled_by=?,cancel_reason=?,cancelled_at=? WHERE org_id=? AND id=?", (a["id"], payload["reason"], occurred, a["org_id"], row["id"]))
                store.audit(db, a, "device_leave_cancelled", row["id"], operator_label=payload["operator_label"], identity_verification="device_delegation_only")
                return {"id": row["id"], "cancelled": True, "hours_deducted_by_server": False, "wechat_sent": False}
            return flow.device_event(db, a, "cancel_leave", payload, cancel)
        def accept(db, a):
            result = run(db, a)
            return result | {"accepted": True, "device_id": a["id"], "event_ref": data.payload["event_ref"], "kind": data.kind}
        return store.command(token, accept)
