"""Confirmed lessons, mirrored device events and simulation-only notifications."""

from datetime import date, datetime, timedelta, timezone
import json

from .models import aware_time
from .security import token_digest
from .store import Problem, uid

SCHOOL_TZ = timezone(timedelta(hours=8))
PARENT_TYPES = {"attendance", "low_hours_parent", "lesson_content", "absence_parent"}
TEACHER_TYPES = {"absence_teacher", "low_hours_teacher", "weekly_teacher"}
ADMIN_TYPES = {"weekly_admin", "monthly_admin"}


def epoch(value):
    return int(aware_time(value).timestamp())


def local_day(timestamp):
    return datetime.fromtimestamp(timestamp, SCHOOL_TZ).date()


def rate(actual, expected):
    return (200 * actual + expected) // (2 * expected) if expected else None


class Workflows:
    def __init__(self, store):
        self.store = store

    def lesson(self, db, actor, lesson_id):
        lesson = self.store.entity(db, actor, "lessons", lesson_id)
        self.store.require_user(actor)
        self.store.require_class(db, actor, lesson["class_id"])
        return lesson

    def member(self, db, actor, lesson_id, student_id):
        if not db.execute("SELECT 1 FROM lesson_members WHERE org_id=? AND lesson_id=? AND student_id=?", (actor["org_id"], lesson_id, student_id)).fetchone():
            raise Problem(409, "not_in_roster", "学生不在本次课的确认名单中")

    def create_lesson(self, db, actor, data):
        self.store.require_user(actor, "admin")
        self.store.entity(db, actor, "classes", data["class_id"])
        teacher = self.store.entity(db, actor, "users", data["teacher_id"])
        if not teacher["active"] or not db.execute("SELECT 1 FROM teacher_classes t JOIN roles r ON r.org_id=t.org_id AND r.user_id=t.user_id AND r.role='teacher' WHERE t.org_id=? AND t.class_id=? AND t.user_id=?", (actor["org_id"], data["class_id"], data["teacher_id"])).fetchone():
            raise Problem(409, "teacher_not_assigned", "本次任课老师必须已获本班授权")
        start, end = epoch(data["starts_at"]), epoch(data["ends_at"])
        if local_day(start) != local_day(end):
            raise Problem(422, "overnight_not_supported", "本测试版暂不接受跨午夜课程")
        members = data["student_ids"]
        if members is None:
            members = [r[0] for r in db.execute("SELECT id FROM students WHERE org_id=? AND class_id=? AND active=1 ORDER BY id LIMIT 501", (actor["org_id"], data["class_id"]))]
        if len(members) > 500:
            raise Problem(422, "roster_limit", "本测试版单次名单上限500人")
        for student_id in members:
            student = self.store.entity(db, actor, "students", student_id)
            if student["class_id"] != data["class_id"] or not student["active"]:
                raise Problem(409, "invalid_roster", "名单须为本班在用学生，历史导入需另行核对")
        lesson_id = uid()
        db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?)", (lesson_id, actor["org_id"], data["class_id"], data["teacher_id"], start, end, self.store.clock()))
        for student_id in members:
            db.execute("INSERT INTO lesson_members VALUES(?,?,?)", (actor["org_id"], lesson_id, student_id))
        self.store.audit(db, actor, "lesson_snapshot_created", lesson_id, expected=len(members))
        return {"id": lesson_id, "expected": len(members), "roster_is_snapshot": True, "starts_at": start, "ends_at": end}

    def detail(self, db, actor, lesson_id):
        lesson = self.lesson(db, actor, lesson_id)
        members = list(db.execute("SELECT s.id,s.name FROM lesson_members m JOIN students s ON s.org_id=m.org_id AND s.id=m.student_id WHERE m.org_id=? AND m.lesson_id=? ORDER BY s.id", (actor["org_id"], lesson_id)))
        arrived = {r[0] for r in db.execute("SELECT student_id FROM attendance WHERE org_id=? AND lesson_id=? AND revoked=0", (actor["org_id"], lesson_id))}
        leaves = {r[0] for r in db.execute("SELECT student_id FROM leaves WHERE org_id=? AND lesson_id=? AND status='approved'", (actor["org_id"], lesson_id))}
        absent = [dict(row) for row in members if row["id"] not in arrived and row["id"] not in leaves]
        return {"id": lesson_id, "class_id": lesson["class_id"], "teacher_id": lesson["teacher_id"],
                "starts_at": lesson["starts_at"], "ends_at": lesson["ends_at"], "expected": len(members),
                "arrived": len(arrived), "leave_count": len(leaves), "rate_percent": rate(len(arrived), len(members)),
                "not_arrived_total": len(members) - len(arrived), "unexcused": absent,
                "leave_students": [dict(row) for row in members if row["id"] in leaves],
                "arrived_students": [dict(row) for row in members if row["id"] in arrived]}

    def create_leave(self, db, actor, lesson_id, data):
        self.lesson(db, actor, lesson_id)
        self.member(db, actor, lesson_id, data["student_id"])
        if db.execute("SELECT 1 FROM attendance WHERE org_id=? AND lesson_id=? AND student_id=? AND revoked=0", (actor["org_id"], lesson_id, data["student_id"])).fetchone():
            raise Problem(409, "already_arrived", "本次已签到，不能再请假")
        leave_id = uid()
        db.execute("INSERT INTO leaves(id,org_id,lesson_id,student_id,status,reason,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (leave_id, actor["org_id"], lesson_id, data["student_id"], "approved", data["reason"], actor["id"], self.store.clock()))
        self.store.audit(db, actor, "leave_approved", leave_id, lesson_id=lesson_id, student_id=data["student_id"])
        return {"id": leave_id, "status": "approved", "counts_as_expected": True, "counts_as_arrived": False, "hours_deducted": False}

    def cancel_leave(self, db, actor, leave_id, reason):
        row = db.execute("SELECT * FROM leaves WHERE org_id=? AND id=?", (actor["org_id"], leave_id)).fetchone()
        if not row:
            raise Problem(404, "not_found", "未找到请假记录")
        self.lesson(db, actor, row["lesson_id"])
        if row["status"] != "approved":
            raise Problem(409, "already_cancelled", "请假已撤销")
        db.execute("UPDATE leaves SET status='cancelled',cancelled_by=?,cancel_reason=?,cancelled_at=? WHERE org_id=? AND id=?", (actor["id"], reason, self.store.clock(), actor["org_id"], leave_id))
        self.store.audit(db, actor, "leave_cancelled", leave_id)
        return {"status": "cancelled", "counts_as_expected": True, "automatic_checkin": False, "hours_deducted": False}

    @staticmethod
    def role_for(notice_type):
        return "parent" if notice_type in PARENT_TYPES else "teacher" if notice_type in TEACHER_TYPES else "admin"

    def preferences(self, db, actor, data):
        self.store.require_user(actor)
        for event_type in data:
            if self.role_for(event_type) not in actor["roles"]:
                raise Problem(403, "preference_forbidden", "此身份无权启用该通知类型")
        for event_type, enabled in data.items():
            db.execute("INSERT INTO notification_preferences VALUES(?,?,?,?,?) ON CONFLICT(org_id,user_id,event_type) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at", (actor["org_id"], actor["id"], event_type, int(enabled), self.store.clock()))
            if not enabled:
                db.execute("UPDATE outbox SET status='cancelled',cancel_reason='preference_disabled' WHERE org_id=? AND recipient_id=? AND notice_type=? AND status='pending_simulation'", (actor["org_id"], actor["id"], event_type))
        self.store.audit(db, actor, "test_notification_preferences", actor["id"], preferences=data)
        return {"test_only": True, "preferences": data}

    def recipient_allowed(self, db, org, recipient, notice_type, student_id="", class_id="", payload=None):
        role = self.role_for(notice_type)
        allowed = db.execute("SELECT 1 FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id JOIN notification_preferences p ON p.org_id=u.org_id AND p.user_id=u.id AND p.event_type=? AND p.enabled=1 WHERE u.org_id=? AND u.id=? AND u.active=1 AND r.role=?", (notice_type, org, recipient, role)).fetchone()
        if not allowed:
            return False
        if role == "parent":
            return bool(db.execute("SELECT 1 FROM test_parent_links WHERE org_id=? AND user_id=? AND student_id=?", (org, recipient, student_id)).fetchone())
        if role == "teacher":
            class_ids = [class_id] if class_id else (payload or {}).get("class_ids", [])
            return bool(class_ids) and all(db.execute("SELECT 1 FROM teacher_classes WHERE org_id=? AND user_id=? AND class_id=?", (org, recipient, current)).fetchone() for current in class_ids)
        return True

    def enqueue(self, db, org, notice_type, origin_id, recipient, payload, *, student_id="", class_id="", due=None):
        now = self.store.clock()
        due = now if due is None else due
        if now < due or now >= due + 86400 or not self.recipient_allowed(db, org, recipient, notice_type, student_id, class_id, payload):
            return False
        preference = db.execute("SELECT updated_at FROM notification_preferences WHERE org_id=? AND user_id=? AND event_type=?", (org, recipient, notice_type)).fetchone()
        if not preference or preference[0] > due:
            return False
        if notice_type in PARENT_TYPES and db.execute("SELECT created_at FROM test_parent_links WHERE org_id=? AND user_id=? AND student_id=?", (org, recipient, student_id)).fetchone()[0] > due:
            return False
        encoded = json.dumps({"channel": "simulation", "identity_verification": "test_only", "data": payload}, ensure_ascii=True)
        if len(encoded.encode()) > 65536:
            db.execute("INSERT INTO notice_skips VALUES(?,?,1) ON CONFLICT(org_id,reason) DO UPDATE SET count=count+1", (org, "payload_limit"))
            return False
        if db.execute("SELECT 1 FROM outbox WHERE org_id=? AND notice_type=? AND origin_id=? AND recipient_id=? AND student_id=?", (org, notice_type, origin_id, recipient, student_id)).fetchone():
            return False
        db.execute("UPDATE outbox SET status='expired',cancel_reason='expired' WHERE org_id=? AND status='pending_simulation' AND expires_at<=?", (org, now))
        if db.execute("SELECT count(*) FROM outbox WHERE org_id=? AND status='pending_simulation'", (org,)).fetchone()[0] >= 2000:
            db.execute("INSERT INTO notice_skips VALUES(?,?,1) ON CONFLICT(org_id,reason) DO UPDATE SET count=count+1", (org, "queue_capacity"))
            return False
        db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid(), org, notice_type, origin_id, recipient, student_id, class_id, encoded,
                   "pending_simulation", due + 86400, now, None, None))
        return True

    def parent_notices(self, db, org, notice_type, origin_id, student_id, class_id, payload, due=None):
        parents = [r[0] for r in db.execute("SELECT user_id FROM test_parent_links WHERE org_id=? AND student_id=?", (org, student_id))]
        return sum(self.enqueue(db, org, notice_type, origin_id, parent, payload, student_id=student_id, class_id=class_id, due=due) for parent in parents)

    def device_event(self, db, actor, kind, data, operation):
        if actor["kind"] != "device":
            raise Problem(403, "device_required", "只接受已授权设备事件")
        digest = token_digest(json.dumps({"kind": kind, "data": data}, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
        old = db.execute("SELECT * FROM inbound_events WHERE org_id=? AND device_id=? AND event_ref=?", (actor["org_id"], actor["id"], data["event_ref"])).fetchone()
        if old:
            if old["payload_digest"] != digest:
                raise Problem(409, "event_changed", "同一事件编号不能对应不同内容")
            return json.loads(old["response_json"]) | {"duplicate": True}
        response = operation()
        db.execute("INSERT INTO inbound_events VALUES(?,?,?,?,?,?)", (actor["org_id"], actor["id"], data["event_ref"], digest, json.dumps(response, sort_keys=True), self.store.clock()))
        return response | {"duplicate": False}

    def apply_balance(self, db, actor, student, data):
        self.store.require_class(db, actor, student["class_id"])
        if student["source_device_id"] not in (None, actor["id"]):
            raise Problem(409, "different_source_device", "学生余额属于另一设备来源，不能覆盖")
        changed = data["student_revision"] > student["source_revision"]
        if changed:
            db.execute("UPDATE students SET remaining_units=?,source_revision=?,source_device_id=? WHERE org_id=? AND id=?", (data["remaining_units"], data["student_revision"], actor["id"], actor["org_id"], student["id"]))
        return changed

    def low_hours(self, db, actor, student, data, eligible, changed):
        if not changed:
            return 0
        row = db.execute("SELECT low_hours_units FROM notification_schedule WHERE org_id=?", (actor["org_id"],)).fetchone()
        threshold = row[0] if row else 1500
        if data["remaining_units"] > threshold:
            db.execute("INSERT INTO low_hours_state VALUES(?,?,0) ON CONFLICT(org_id,student_id) DO UPDATE SET active=0", (actor["org_id"], student["id"]))
            db.execute("UPDATE outbox SET status='cancelled',cancel_reason='balance_replenished' WHERE org_id=? AND student_id=? AND notice_type IN ('low_hours_parent','low_hours_teacher') AND status='pending_simulation'", (actor["org_id"], student["id"]))
            return 0
        state = db.execute("SELECT active FROM low_hours_state WHERE org_id=? AND student_id=?", (actor["org_id"], student["id"])).fetchone()
        if not eligible or state and state[0]:
            return 0
        db.execute("INSERT INTO low_hours_state VALUES(?,?,1) ON CONFLICT(org_id,student_id) DO UPDATE SET active=1", (actor["org_id"], student["id"]))
        payload = {"student_id": student["id"], "student_name": student["name"], "class_id": student["class_id"], "remaining_units": data["remaining_units"], "threshold_units": threshold}
        origin = actor["id"] + ":" + data["event_ref"]
        due = epoch(data["occurred_at"])
        count = self.parent_notices(db, actor["org_id"], "low_hours_parent", origin, student["id"], student["class_id"], payload, due)
        teachers = [r[0] for r in db.execute("SELECT user_id FROM teacher_classes WHERE org_id=? AND class_id=?", (actor["org_id"], student["class_id"]))]
        count += sum(self.enqueue(db, actor["org_id"], "low_hours_teacher", origin, teacher, payload, student_id=student["id"], class_id=student["class_id"], due=due) for teacher in teachers)
        return count

    def event_time(self, value):
        occurred = epoch(value)
        if occurred > self.store.clock() + 60 or not 2000 <= aware_time(value).year <= 2100:
            raise Problem(422, "invalid_event_time", "事件时间不能明显超前或超出支持范围")
        return occurred

    def ingest_attendance(self, db, actor, data):
        if actor["kind"] != "device":
            raise Problem(403, "device_required", "只接受已授权设备事件")
        current_lesson = self.store.entity(db, actor, "lessons", data["lesson_id"])
        self.store.require_class(db, actor, current_lesson["class_id"])
        current_student = self.store.entity(db, actor, "students", data["student_id"])
        if current_student["source_device_id"] not in (None, actor["id"]):
            raise Problem(409, "different_source_device", "学生余额属于另一设备来源")
        def run():
            lesson = self.store.entity(db, actor, "lessons", data["lesson_id"])
            self.store.require_class(db, actor, lesson["class_id"])
            self.member(db, actor, lesson["id"], data["student_id"])
            student = self.store.entity(db, actor, "students", data["student_id"])
            occurred = self.event_time(data["occurred_at"]) if data["occurred_at"] is not None else lesson["starts_at"]
            if data["occurred_at"] is None and occurred > self.store.clock() + 60:
                raise Problem(422, "future_makeup", "不能补签尚未开始的课次")
            if local_day(occurred) != local_day(lesson["starts_at"]):
                raise Problem(409, "lesson_date_mismatch", "签到须对应本次实际上课日期")
            if db.execute("SELECT 1 FROM leaves WHERE org_id=? AND lesson_id=? AND student_id=? AND status='approved'", (actor["org_id"], lesson["id"], student["id"])).fetchone():
                raise Problem(409, "leave_active", "本次已请假，请先明确撤假再签到")
            attendance_id = uid()
            # Balances are snapshots from the device. Never subtract lesson hours a second time.
            changed = self.apply_balance(db, actor, student, data)
            db.execute("INSERT INTO attendance VALUES(?,?,?,?,?,?,?,?,?,0)", (attendance_id, actor["org_id"], lesson["id"], student["id"], actor["id"], occurred, data["consumed_units"], data["remaining_units"], data["source"]))
            eligible = data["source"] == "live" and self.store.clock() - occurred <= 86400
            count = 0
            if eligible:
                course = self.store.entity(db, actor, "classes", lesson["class_id"])
                count += self.parent_notices(db, actor["org_id"], "attendance", attendance_id, student["id"], lesson["class_id"],
                    {"student_name": student["name"], "class_name": course["name"], "occurred_at": occurred, "consumed_units": data["consumed_units"], "remaining_units": data["remaining_units"]}, due=occurred)
            count += self.low_hours(db, actor, student, data, eligible, changed)
            self.store.audit(db, actor, "attendance_mirrored", attendance_id, lesson_id=lesson["id"], student_id=student["id"], balance_applied=changed, occurred_at_known=data["occurred_at"] is not None)
            return {"id": attendance_id, "balance_applied": changed, "simulation_notices_queued": count, "wechat_sent": False, "hours_deducted_by_server": False}
        return self.device_event(db, actor, "attendance", data, run)

    def ingest_balance(self, db, actor, data):
        if actor["kind"] != "device":
            raise Problem(403, "device_required", "只接受已授权设备事件")
        current_student = self.store.entity(db, actor, "students", data["student_id"])
        self.store.require_class(db, actor, current_student["class_id"])
        if current_student["source_device_id"] not in (None, actor["id"]):
            raise Problem(409, "different_source_device", "学生余额属于另一设备来源")
        def run():
            student = self.store.entity(db, actor, "students", data["student_id"])
            occurred = self.event_time(data["occurred_at"])
            changed = self.apply_balance(db, actor, student, data)
            count = self.low_hours(db, actor, student, data, data["source"] == "live" and self.store.clock() - occurred <= 86400, changed)
            self.store.audit(db, actor, "balance_mirrored", student["id"], revision=data["student_revision"], applied=changed)
            return {"balance_applied": changed, "simulation_notices_queued": count, "wechat_sent": False, "hours_deducted_by_server": False}
        return self.device_event(db, actor, "balance", data, run)

    def revoke(self, db, actor, attendance_id, data):
        if actor["kind"] != "device":
            raise Problem(403, "device_required", "只接受已授权设备事件")
        current = db.execute("SELECT * FROM attendance WHERE org_id=? AND id=?", (actor["org_id"], attendance_id)).fetchone()
        if not current:
            raise Problem(404, "not_found", "未找到签到")
        current_lesson = self.store.entity(db, actor, "lessons", current["lesson_id"])
        self.store.require_class(db, actor, current_lesson["class_id"])
        if current["device_id"] != actor["id"]:
            raise Problem(403, "source_device_required", "只有原设备可撤销")
        def run():
            row = db.execute("SELECT * FROM attendance WHERE org_id=? AND id=?", (actor["org_id"], attendance_id)).fetchone()
            if not row:
                raise Problem(404, "not_found", "未找到签到")
            lesson = self.store.entity(db, actor, "lessons", row["lesson_id"])
            self.store.require_class(db, actor, lesson["class_id"])
            if row["device_id"] != actor["id"]:
                raise Problem(403, "source_device_required", "只有原设备可提交本签到撤销")
            db.execute("UPDATE attendance SET revoked=1 WHERE org_id=? AND id=?", (actor["org_id"], attendance_id))
            db.execute("UPDATE outbox SET status=CASE WHEN status='pending_simulation' THEN 'cancelled' ELSE status END,cancel_reason='attendance_revoked' WHERE org_id=? AND origin_id=?", (actor["org_id"], attendance_id))
            self.store.audit(db, actor, "attendance_revoked", attendance_id)
            return {"revoked": True, "automatic_refund": False, "balance_snapshot_required": True}
        return self.device_event(db, actor, "revoke:" + attendance_id, data, run)

    def absence_notices(self, db, actor, lesson_id, student_ids):
        lesson = self.lesson(db, actor, lesson_id)
        if not lesson["starts_at"] + 300 <= self.store.clock() <= lesson["ends_at"] + 86400:
            raise Problem(409, "absence_window", "开课五分钟后且未过期时才可确认未到通知")
        detail = self.detail(db, actor, lesson_id)
        absent = {row["id"] for row in detail["unexcused"]}
        if not set(student_ids) <= absent:
            raise Problem(409, "not_missing", "名单有已到、已请假或非本次应到学生，未生成通知")
        count = 0
        for student_id in student_ids:
            count += self.parent_notices(db, actor["org_id"], "absence_parent", lesson_id, student_id, lesson["class_id"], {"lesson_id": lesson_id, "student_id": student_id, "starts_at": lesson["starts_at"]}, due=lesson["starts_at"] + 300)
        self.store.audit(db, actor, "absence_simulation_confirmed", lesson_id, student_ids=student_ids)
        return {"simulation_notices_queued": count, "wechat_sent": False}

    def content(self, db, actor, lesson_id, data):
        self.lesson(db, actor, lesson_id)
        old = db.execute("SELECT queued_at FROM lesson_content WHERE org_id=? AND lesson_id=?", (actor["org_id"], lesson_id)).fetchone()
        if old and old[0] is not None:
            raise Problem(409, "content_already_queued", "内容已生成通知，不能无留痕覆盖，请另行更正")
        db.execute("INSERT INTO lesson_content VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(org_id,lesson_id) DO UPDATE SET body=excluded.body,audience=excluded.audience,publish_after_class=excluded.publish_after_class,edited_by=excluded.edited_by,updated_at=excluded.updated_at", (actor["org_id"], lesson_id, data["body"], data["audience"], int(data["publish_after_class"]), actor["id"], self.store.clock()))
        self.store.audit(db, actor, "lesson_content_saved", lesson_id, publish_after_class=data["publish_after_class"], audience=data["audience"])
        return {"saved": True, "scheduled_for_simulation": data["publish_after_class"], "wechat_sent": False}

    def simulate(self, db, actor):
        self.store.require_user(actor, "admin")
        counts = {"simulated": 0, "cancelled": 0, "expired": 0, "wechat_sent": False}
        rows = list(db.execute("SELECT * FROM outbox WHERE org_id=? AND status='pending_simulation' ORDER BY created_at,id LIMIT 200", (actor["org_id"],)))
        for row in rows:
            payload = json.loads(row["payload"])["data"]
            if row["expires_at"] <= self.store.clock():
                status, reason = "expired", "expired"
            elif not self.recipient_allowed(db, row["org_id"], row["recipient_id"], row["notice_type"], row["student_id"], row["class_id"], payload) or not self.notice_current(db, row, payload):
                status, reason = "cancelled", "authorization_or_consent_changed"
            else:
                status, reason = "simulated", None
            db.execute("UPDATE outbox SET status=?,cancel_reason=?,simulated_at=? WHERE org_id=? AND id=?", (status, reason, self.store.clock() if status == "simulated" else None, actor["org_id"], row["id"]))
            counts[status] += 1
        self.store.audit(db, actor, "simulation_tick", actor["id"], **counts)
        return counts

    def notice_current(self, db, row, payload):
        """Recheck mutable classroom facts immediately before simulated delivery."""
        org, kind = row["org_id"], row["notice_type"]
        if kind == "attendance":
            return bool(db.execute("SELECT 1 FROM attendance WHERE org_id=? AND id=? AND revoked=0", (org, row["origin_id"])).fetchone())
        if kind in {"low_hours_parent", "low_hours_teacher"}:
            student = db.execute("SELECT active,remaining_units FROM students WHERE org_id=? AND id=?", (org, row["student_id"])).fetchone()
            return bool(student and student["active"] and student["remaining_units"] <= payload["threshold_units"])
        if kind in {"absence_parent", "absence_teacher"}:
            missing = [r[0] for r in db.execute("SELECT m.student_id FROM lesson_members m WHERE m.org_id=? AND m.lesson_id=? AND NOT EXISTS(SELECT 1 FROM attendance a WHERE a.org_id=m.org_id AND a.lesson_id=m.lesson_id AND a.student_id=m.student_id AND a.revoked=0) AND NOT EXISTS(SELECT 1 FROM leaves l WHERE l.org_id=m.org_id AND l.lesson_id=m.lesson_id AND l.student_id=m.student_id AND l.status='approved') ORDER BY m.student_id", (org, row["origin_id"]))]
            return row["student_id"] in missing if kind == "absence_parent" else len(missing) == payload["missing_count"] and missing[:20] == [s["id"] for s in payload["missing_students"]]
        if kind == "lesson_content" and payload["audience"] == "arrived":
            return bool(db.execute("SELECT 1 FROM attendance WHERE org_id=? AND lesson_id=? AND student_id=? AND revoked=0", (org, row["origin_id"], row["student_id"])).fetchone())
        return True

    def weekly(self, db, actor, week_start, *, own=False):
        self.store.require_user(actor, "teacher" if own else "admin")
        try:
            start = date.fromisoformat(week_start)
        except ValueError:
            raise Problem(422, "invalid_week", "请输入周一日期") from None
        if start.weekday() != 0 or not 2000 <= start.year <= 2100:
            raise Problem(422, "invalid_week", "周报起始日期须为周一")
        low = int(datetime.combine(start, datetime.min.time(), SCHOOL_TZ).timestamp())
        high = low + 7 * 86400
        scope = " AND teacher_id=? AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=lessons.org_id AND t.class_id=lessons.class_id AND t.user_id=?)" if own else ""
        rows = list(db.execute("SELECT * FROM lessons WHERE org_id=? AND starts_at>=? AND starts_at<?" + scope + " ORDER BY starts_at,id LIMIT 1001", (actor["org_id"], low, high, actor["id"], actor["id"]) if own else (actor["org_id"], low, high)))
        if len(rows) > 1000:
            raise Problem(422, "report_limit", "测试版周报课次超过上限，请按范围拆分")
        allowed = {row[0] for row in db.execute("SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?", (actor["org_id"], actor["id"]))} if own else self.store.class_ids(db, actor)
        if own:
            rows = [row for row in rows if row["teacher_id"] == actor["id"] and row["class_id"] in allowed]
        completed = [row for row in rows if row["ends_at"] <= self.store.clock()]
        expected_total = sum(db.execute("SELECT count(*) FROM lesson_members WHERE org_id=? AND lesson_id=?", (actor["org_id"], row["id"])).fetchone()[0] for row in completed)
        if expected_total > 5000:
            raise Problem(422, "report_limit", "测试版周报应到人次超过上限，请按范围拆分")
        sessions = [self.detail(db, actor, row["id"]) for row in completed]
        groups = {"classes": {}, "teachers": {}}
        for session in sessions:
            for group, key in (("classes", "class_id"), ("teachers", "teacher_id")):
                entry = groups[group].setdefault(session[key], {"id": session[key], "expected": 0, "arrived": 0, "leaves": 0})
                entry["expected"] += session["expected"]
                entry["arrived"] += session["arrived"]
                entry["leaves"] += session["leave_count"]
        for group in groups.values():
            for entry in group.values():
                entry["rate_percent"] = rate(entry["arrived"], entry["expected"])
        for entry in groups["classes"].values():
            entry["name"] = self.store.entity(db, actor, "classes", entry["id"])["name"]
        for entry in groups["teachers"].values():
            entry["name"] = self.store.entity(db, actor, "users", entry["id"])["display_name"]
        expected, arrived = sum(s["expected"] for s in sessions), sum(s["arrived"] for s in sessions)
        threshold_row = db.execute("SELECT low_hours_units FROM notification_schedule WHERE org_id=?", (actor["org_id"],)).fetchone()
        threshold = threshold_row[0] if threshold_row else 1500
        scope = " AND EXISTS(SELECT 1 FROM teacher_classes tc WHERE tc.org_id=s.org_id AND tc.class_id=s.class_id AND tc.user_id=?)" if own else ""
        low_students = list(db.execute("SELECT s.id,s.name,s.class_id,s.remaining_units,c.name AS class_name FROM students s JOIN classes c ON c.org_id=s.org_id AND c.id=s.class_id WHERE s.org_id=? AND s.active=1 AND s.remaining_units<=?" + scope + " ORDER BY s.remaining_units,s.id LIMIT 1001", (actor["org_id"], threshold, actor["id"]) if own else (actor["org_id"], threshold)))
        low_items = []
        for row in low_students[:1000]:
            teachers = [dict(t) for t in db.execute("SELECT u.id,u.display_name FROM teacher_classes tc JOIN users u ON u.org_id=tc.org_id AND u.id=tc.user_id WHERE tc.org_id=? AND tc.class_id=? AND u.active=1 ORDER BY u.id", (actor["org_id"], row["class_id"]))]
            low_items.append(dict(row) | {"teachers": teachers})
        return {"week_start": start.isoformat(), "expected": expected, "arrived": arrived, "rate_percent": rate(arrived, expected),
                "leave_count": sum(s["leave_count"] for s in sessions), "classes": list(groups["classes"].values()),
                "teachers": list(groups["teachers"].values()), "sessions": sessions, "low_hours": low_items,
                "low_hours_truncated": len(low_students) > 1000, "scope": "confirmed_lesson_snapshots_only", "pending_lessons": sum(row["ends_at"] > self.store.clock() for row in rows)}

    def monthly(self, db, actor, month):
        self.store.require_user(actor, "admin")
        try:
            start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=SCHOOL_TZ)
            if start.strftime("%Y-%m") != month or not 2000 <= start.year <= 2100:
                raise ValueError
        except ValueError:
            raise Problem(422, "invalid_month", "请输入YYYY-MM月份") from None
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        rows = list(db.execute("SELECT a.id,a.student_id,a.kind,a.occurred_at,a.class_name,s.name FROM student_activity a JOIN students s ON s.org_id=a.org_id AND s.id=a.student_id WHERE a.org_id=? AND a.occurred_at>=? AND a.occurred_at<? ORDER BY a.occurred_at,a.id LIMIT 5001", (actor["org_id"], int(start.timestamp()), int(end.timestamp()))))
        if len(rows) > 5000:
            raise Problem(422, "report_limit", "测试版月报活动数超过上限")
        return {"month": month, "new_students": len({row["student_id"] for row in rows if row["kind"] == "new"}),
                "renewal_students": len({row["student_id"] for row in rows if row["kind"] == "renewal"}),
                "renewal_events": sum(row["kind"] == "renewal" for row in rows), "items": [dict(row) for row in rows],
                "final_period": self.store.clock() >= int(end.timestamp()), "does_not_adjust_hours": True}
