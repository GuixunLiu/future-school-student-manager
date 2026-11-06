"""Operator-triggered, bounded simulation jobs. No timer or network sender."""

from datetime import datetime, timedelta

from .models import Schedule
from .store import Problem
from .workflows import SCHOOL_TZ


class Jobs:
    def __init__(self, flow):
        self.flow, self.store = flow, flow.store

    def configuration(self, db, actor):
        self.store.require_user(actor, "admin")
        row = db.execute("SELECT * FROM notification_schedule WHERE org_id=?", (actor["org_id"],)).fetchone()
        return {"test_only": True, "automatic_worker_running": False, "settings": dict(row) if row else Schedule().model_dump() | {"enabled_since": None}}

    def configure(self, db, actor, data):
        self.store.require_user(actor, "admin")
        old = db.execute("SELECT * FROM notification_schedule WHERE org_id=?", (actor["org_id"],)).fetchone()
        # Reconfiguring timing starts a new eligible window; never backfill old jobs.
        same = old and all(old[key] == value for key, value in data.items())
        since = old["enabled_since"] if same else self.store.clock()
        db.execute("INSERT INTO notification_schedule VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(org_id) DO UPDATE SET enabled=excluded.enabled,enabled_since=excluded.enabled_since,weekly_weekday=excluded.weekly_weekday,weekly_hour=excluded.weekly_hour,weekly_minute=excluded.weekly_minute,monthly_delay_minutes=excluded.monthly_delay_minutes,low_hours_units=excluded.low_hours_units", (actor["org_id"], int(data["enabled"]), since, data["weekly_weekday"], data["weekly_hour"], data["weekly_minute"], data["monthly_delay_minutes"], data["low_hours_units"]))
        self.store.audit(db, actor, "simulation_schedule_configured", actor["org_id"], **data)
        return self.configuration(db, actor)

    def tick(self, db, actor):
        self.store.require_user(actor, "admin")
        org, now = actor["org_id"], self.store.clock()
        settings = db.execute("SELECT * FROM notification_schedule WHERE org_id=?", (org,)).fetchone()
        counts = {"simulation_notices_queued": 0, "wechat_sent": False, "automatic_worker_running": False}
        if not settings or not settings["enabled"]:
            return counts | {"enabled": False}
        since = settings["enabled_since"]
        lessons = list(db.execute("SELECT * FROM lessons WHERE org_id=? AND starts_at<=? AND ends_at>=? ORDER BY starts_at,id LIMIT 501", (org, now, now - 86400)))
        if len(lessons) > 500:
            raise Problem(422, "job_limit", "测试版单次作业最多处理500节课")
        for lesson in lessons:
            detail = self.flow.detail(db, actor, lesson["id"])
            due = lesson["starts_at"] + 300
            if since <= due <= now <= lesson["ends_at"] + 86400 and detail["unexcused"]:
                payload = {"lesson_id": lesson["id"], "class_id": lesson["class_id"], "missing_count": len(detail["unexcused"]), "missing_students": detail["unexcused"][:20], "details_available": True}
                counts["simulation_notices_queued"] += self.flow.enqueue(db, org, "absence_teacher", lesson["id"], lesson["teacher_id"], payload, class_id=lesson["class_id"], due=due)
            content = db.execute("SELECT * FROM lesson_content WHERE org_id=? AND lesson_id=? AND publish_after_class=1 AND queued_at IS NULL", (org, lesson["id"])).fetchone()
            if content and lesson["ends_at"] <= now:
                due = max(lesson["ends_at"], content["updated_at"])
                if since <= due <= now <= due + 86400:
                    members = detail["arrived_students"] if content["audience"] == "arrived" else [dict(r) for r in db.execute("SELECT student_id AS id FROM lesson_members WHERE org_id=? AND lesson_id=?", (org, lesson["id"]))]
                    for student in members:
                        counts["simulation_notices_queued"] += self.flow.parent_notices(db, org, "lesson_content", lesson["id"], student["id"], lesson["class_id"], {"lesson_id": lesson["id"], "body": content["body"], "audience": content["audience"]}, due)
                    db.execute("UPDATE lesson_content SET queued_at=? WHERE org_id=? AND lesson_id=?", (now, org, lesson["id"]))
        today = datetime.fromtimestamp(now, SCHOOL_TZ)
        monday = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        recipients = list(db.execute("SELECT u.id,r.role FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.active=1 AND r.role IN ('admin','teacher') ORDER BY u.id,r.role LIMIT 501", (org,)))
        if len(recipients) > 500:
            raise Problem(422, "job_limit", "测试版周报接收身份超过500个")
        for start in (monday - timedelta(days=7), monday):
            low, high = int(start.timestamp()), int((start + timedelta(days=7)).timestamp())
            cutoff = int((start + timedelta(days=settings["weekly_weekday"], hours=settings["weekly_hour"], minutes=settings["weekly_minute"])).timestamp())
            for person in recipients:
                own = person["role"] == "teacher"
                notice_type = "weekly_teacher" if own else "weekly_admin"
                eligible = db.execute("SELECT 1 FROM notification_preferences WHERE org_id=? AND user_id=? AND event_type=? AND enabled=1", (org, person["id"], notice_type)).fetchone()
                if not eligible:
                    continue
                user = {"kind": "user", "id": person["id"], "org_id": org, "roles": {person["role"]}}
                scope = " AND teacher_id=? AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=lessons.org_id AND t.class_id=lessons.class_id AND t.user_id=?)" if own else ""
                last = db.execute("SELECT max(ends_at) FROM lessons WHERE org_id=? AND starts_at>=? AND starts_at<?" + scope, (org, low, high, person["id"], person["id"]) if own else (org, low, high)).fetchone()[0]
                if last is None:
                    continue
                due = max(cutoff, last)
                if not since <= due <= now <= due + 86400:
                    continue
                report = self.flow.weekly(db, user, start.date().isoformat(), own=own)
                missing = [s for lesson in report["sessions"] for s in lesson["unexcused"]]
                payload = {key: report[key] for key in ("week_start", "expected", "arrived", "rate_percent", "leave_count", "scope")}
                payload.update({"class_ids": [r["id"] for r in report["classes"]], "missing_students": missing[:20], "missing_person_sessions": len(missing), "low_hours_count": len(report["low_hours"]), "low_hours_truncated": report["low_hours_truncated"], "details_available": True})
                counts["simulation_notices_queued"] += self.flow.enqueue(db, org, notice_type, start.date().isoformat(), person["id"], payload, due=due)
        month_end = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        due = int(month_end.timestamp()) + settings["monthly_delay_minutes"] * 60
        if since <= due <= now <= due + 86400:
            month = (month_end - timedelta(days=1)).strftime("%Y-%m")
            report = self.flow.monthly(db, actor, month)
            payload = {key: report[key] for key in ("month", "new_students", "renewal_students", "renewal_events", "final_period")}
            payload["details_available"] = True
            for person in recipients:
                if person["role"] == "admin":
                    counts["simulation_notices_queued"] += self.flow.enqueue(db, org, "monthly_admin", month, person["id"], payload, due=due)
        self.store.audit(db, actor, "simulation_jobs_tick", org, **counts)
        return counts | {"enabled": True}
