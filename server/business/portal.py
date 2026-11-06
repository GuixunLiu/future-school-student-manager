"""Local operator UI and bounded authorized reads needed by the workbench."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, Query
from fastapi.responses import Response

from . import models
from .store import Problem
from .workflows import SCHOOL_TZ, Workflows


def install_portal(api, store, bearer):
    Token = Annotated[str, Depends(bearer)]
    Cursor = Annotated[str, Query(pattern=r"^(|[0-9a-f]{32})$")]
    Page = Annotated[int, Query(ge=1, le=100)]
    flow = Workflows(store)
    web = Path(__file__).with_name("web")

    def asset(name, media_type):
        return Response((web / name).read_bytes(), media_type=media_type)

    @api.get("/", include_in_schema=False)
    def home():
        return asset("index.html", "text/html")

    @api.get("/workspace.js", include_in_schema=False)
    def script():
        return asset("workspace.js", "text/javascript")

    @api.get("/workspace.css", include_in_schema=False)
    def stylesheet():
        return asset("workspace.css", "text/css")

    @api.get("/v1/lessons")
    def lessons(token: Token, day: Annotated[str, Query(max_length=10)], scope: Literal["authorized", "teaching"] = "authorized", after: Cursor = "", limit: Page = 40):
        def run(db, a):
            store.require_user(a)
            if not a["roles"] & {"teacher", "admin"} or scope == "teaching" and "teacher" not in a["roles"]:
                raise Problem(403, "forbidden", "此身份不能查看教师课堂")
            try:
                parsed = date.fromisoformat(day)
                if parsed.isoformat() != day or not 2000 <= parsed.year <= 2100:
                    raise ValueError
            except ValueError:
                raise Problem(422, "invalid_date", "请输入有效日期") from None
            start = int(datetime.combine(parsed, datetime.min.time(), SCHOOL_TZ).timestamp())
            values = [a["org_id"], start, start + 86400, after]
            restriction = ""
            if scope == "teaching":
                restriction = " AND l.teacher_id=? AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=l.org_id AND t.class_id=l.class_id AND t.user_id=?)"
                values.extend([a["id"], a["id"]])
            elif "admin" not in a["roles"]:
                restriction = " AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=l.org_id AND t.class_id=l.class_id AND t.user_id=?)"
                values.append(a["id"])
            values.append(limit + 1)
            rows = list(db.execute("SELECT l.*,c.name AS class_name,c.course_name,u.display_name AS teacher_name,(SELECT count(*) FROM lesson_members m WHERE m.org_id=l.org_id AND m.lesson_id=l.id) AS expected,(SELECT count(*) FROM attendance x WHERE x.org_id=l.org_id AND x.lesson_id=l.id AND x.revoked=0) AS arrived,(SELECT count(*) FROM leaves v WHERE v.org_id=l.org_id AND v.lesson_id=l.id AND v.status='approved') AS leave_count FROM lessons l JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id JOIN users u ON u.org_id=l.org_id AND u.id=l.teacher_id WHERE l.org_id=? AND l.starts_at>=? AND l.starts_at<? AND l.id>?" + restriction + " ORDER BY l.id LIMIT ?", values))
            return {"items": [dict(r) for r in rows[:limit]], "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None, "server_time": store.clock()}
        return store.command(token, run)

    @api.get("/v1/admin/classes/{class_id}/teachers")
    def assignments(class_id: models.ID, token: Token):
        def run(db, a):
            store.require_user(a, "admin")
            store.entity(db, a, "classes", class_id)
            return {"class_id": class_id, "teacher_ids": [r[0] for r in db.execute("SELECT user_id FROM teacher_classes WHERE org_id=? AND class_id=? ORDER BY user_id", (a["org_id"], class_id))]}
        return store.command(token, run)

    @api.get("/v1/lessons/{lesson_id}/workspace")
    def workspace(lesson_id: models.ID, token: Token):
        def run(db, a):
            detail = flow.detail(db, a, lesson_id)
            course = store.entity(db, a, "classes", detail["class_id"])
            teacher = store.entity(db, a, "users", detail["teacher_id"])
            rows = list(db.execute("SELECT s.id,s.name,s.external_ref,s.remaining_units,(SELECT count(*) FROM attendance x WHERE x.org_id=m.org_id AND x.lesson_id=m.lesson_id AND x.student_id=m.student_id AND x.revoked=0) AS arrived FROM lesson_members m JOIN students s ON s.org_id=m.org_id AND s.id=m.student_id WHERE m.org_id=? AND m.lesson_id=? ORDER BY s.name,s.id", (a["org_id"], lesson_id)))
            leaves = list(db.execute("SELECT v.id,v.student_id,v.reason,v.created_at,COALESCE(u.display_name,d.name || '（平板代办）','原登记账号') AS recorded_by FROM leaves v LEFT JOIN users u ON u.org_id=v.org_id AND u.id=v.recorded_by LEFT JOIN devices d ON d.org_id=v.org_id AND d.id=v.recorded_by WHERE v.org_id=? AND v.lesson_id=? AND v.status='approved' ORDER BY v.created_at,v.id", (a["org_id"], lesson_id)))
            content = db.execute("SELECT body,audience,publish_after_class,queued_at FROM lesson_content WHERE org_id=? AND lesson_id=?", (a["org_id"], lesson_id)).fetchone()
            now = store.clock()
            return detail | {"class_name": course["name"], "course_name": course["course_name"], "teacher_name": teacher["display_name"], "members": [dict(r) for r in rows], "leaves": [dict(r) for r in leaves], "content": dict(content) if content else None, "server_time": now, "absence_window_open": detail["starts_at"] + 300 <= now <= detail["ends_at"] + 86400}
        return store.command(token, run)
