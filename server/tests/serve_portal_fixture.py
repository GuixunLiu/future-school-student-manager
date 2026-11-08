"""Fictional browser-test server only. Never packaged or connected to a real device."""

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import uvicorn
from business.api import create_app
from business.store import Store
from business.workflows import SCHOOL_TZ

PASSWORD = "Fictional browser-only passphrase 2026!"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    initial = int(datetime(2026, 10, 25, 9, tzinfo=SCHOOL_TZ).timestamp())
    clock = [initial]
    Store.bootstrap(args.database, "school-ui", "owner-ui", "演示管理员", PASSWORD, lambda: clock[0])
    app = create_app(args.database, lambda: clock[0], allow_test_client=True)
    with TestClient(app) as client:
        def call(method, path, token=None, data=None):
            r = client.request(method, path, headers={"Authorization": "Bearer " + token} if token else {}, **({"json": data} if data is not None else {}))
            if r.status_code >= 400:
                raise RuntimeError("Fictional fixture initialization failed: " + path)
            return r.json()
        def login(name):
            return call("POST", "/v1/auth/login", data={"organization": "school-ui", "login": name, "password": PASSWORD})["access_token"]
        owner = login("owner-ui")
        people = {}
        for name, label, roles in (("teacher-ui", "演示林老师", ["teacher"]), ("dual-ui", "演示周老师", ["teacher", "admin"]), ("parent-ui", "演示家长", ["parent"])):
            people[name] = call("POST", "/v1/admin/people", owner, {"login": name, "display_name": label, "roles": roles, "password": PASSWORD})["id"]
        classes = []
        for name, course, teacher in (("信息学奥赛 · 周日上午1", "信息学奥赛", "teacher-ui"), ("创意绘画 · 周日上午", "创意绘画", "dual-ui")):
            c = call("POST", "/v1/admin/classes", owner, {"name": name, "course_name": course})["id"]
            call("PUT", f"/v1/admin/classes/{c}/teachers", owner, {"teacher_ids": [people[teacher]]})
            classes.append(c)
        students = []
        for i, name in enumerate(("演示同名学生", "演示同名学生", "演示张同学", "演示赵同学")):
            students.append(call("POST", "/v1/admin/students", owner, {"class_id": classes[0], "name": name, "external_ref": "DEMO-10"+str(i), "remaining_units": 2000-i*400})["id"])
        call("POST", "/v1/admin/students", owner, {"class_id": classes[1], "name": "演示另一班学生", "external_ref": "OTHER-201", "remaining_units": 900})
        call("POST", "/v1/test/parent-links", owner, {"parent_id": people["parent-ui"], "student_id": students[1]})
        parent = login("parent-ui")
        call("PUT", "/v1/me/notification-preferences", parent, {"preferences": {"absence_parent": True, "lesson_content": True}})
        teacher = login("teacher-ui")
        call("PUT", "/v1/me/notification-preferences", teacher, {"preferences": {"absence_teacher": True, "weekly_teacher": True}})
        # One currently teaching lesson, another completed lesson for a weighted report.
        lessons = []
        for index, (c, t) in enumerate(((classes[0], people["teacher-ui"]), (classes[1], people["dual-ui"]))):
            lessons.append(call("POST", "/v1/admin/lessons", owner, {"class_id": c, "teacher_id": t, "starts_at": "2026-10-25T10:00:00+08:00", "ends_at": "2026-10-25T11:30:00+08:00"})["id"])
        call("POST", "/v1/admin/lessons", owner, {"class_id": classes[0], "teacher_id": people["teacher-ui"], "starts_at": "2026-10-24T10:00:00+08:00", "ends_at": "2026-10-24T11:30:00+08:00"})
        device = call("POST", "/v1/admin/devices", owner, {"name": "演示设备", "class_ids": classes})["device_token"]
        clock[0] = int(datetime(2026, 10, 25, 10, 7, tzinfo=SCHOOL_TZ).timestamp())
        call("POST", "/v1/device/attendance", device, {"event_ref": "fixture-live", "lesson_id": lessons[0], "student_id": students[0], "occurred_at": "2026-10-25T10:01:00+08:00", "consumed_units": 100, "remaining_units": 1900, "student_revision": 1, "source": "live"})
    # Browser HTTP requests use real loopback addresses, never the TestClient exception.
    app = create_app(args.database, lambda: clock[0])
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False, server_header=False, ws="none")


if __name__ == "__main__":
    main()
