"""Disposable v0.5 fixture: archives, scoped teachers and 120/90-minute lessons. Never packaged."""

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time

sys.path.insert(0, os.environ.get("FUTURE_SCHOOL_FIXTURE_RUNTIME") or str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import uvicorn
from business.store import Store, uid
from business.workflows import SCHOOL_TZ
from business_next.archive import initialize
from business_next.api import create_app
from serve_tablet_fixture import LoseFirstReceipt

PASSWORD = "Fictional USB-only passphrase 2026!"


class FixtureClient:
    """Small in-process ASGI bootstrap client; requires no extra cloud test dependency."""
    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, method, path, *, headers, **kwargs):
        async def send_request():
            body = json.dumps(kwargs["json"]).encode() if "json" in kwargs else b""
            fields = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
            fields += [(b"host", b"127.0.0.1"), (b"content-length", str(len(body)).encode())]
            if body:
                fields.append((b"content-type", b"application/json"))
            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                     "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                     "raw_path": path.encode(), "query_string": b"", "root_path": "",
                     "headers": fields, "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 80)}
            messages, received = [], False
            async def receive():
                nonlocal received
                if received:
                    await asyncio.Event().wait()
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                messages.append(message)
            await self.app(scope, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            return SimpleNamespace(is_success=200 <= status < 300, json=lambda: json.loads(raw))
        return asyncio.run(send_request())


def build(database, clock, *, allow_test_client=False):
    store = Store.bootstrap(database, "usb-fixture", "usb-owner", "虚构USB管理员", PASSWORD, clock)
    initialize(database)
    app = create_app(database, clock, allow_test_client=True, review_only=False)
    with FixtureClient(app) as client:
        def call(method, path, data=None, token=None):
            r = client.request(method, path, headers={"Authorization": "Bearer " + token} if token else {}, **({"json": data} if data is not None else {}))
            if not r.is_success:
                raise RuntimeError("Fictional isolated setup failed: " + path)
            return r.json()
        def login():
            return call("POST", "/v1/auth/login", {"organization": "usb-fixture", "login": "usb-owner", "password": PASSWORD})["access_token"]
        token = login()
        owner = call("GET", "/v1/me", token=token)
        call("PATCH", "/v1/admin/people/" + owner["id"], {"roles": ["admin", "teacher"], "expected_roles": ["admin"], "active": True}, token)
        token = login()
        teacher = call("POST", "/v1/admin/people", {"login": "usb-teacher", "display_name": "虚构USB老师", "password": PASSWORD, "roles": ["teacher"]}, token)["id"]
        other = call("POST", "/v1/admin/people", {"login": "usb-other", "display_name": "虚构另一老师", "password": PASSWORD, "roles": ["teacher"]}, token)["id"]
        local = datetime.fromtimestamp(clock(), SCHOOL_TZ)
        start = local.replace(second=0, microsecond=0) - timedelta(minutes=7)
        if start.date() != local.date() or (start + timedelta(minutes=120)).date() != local.date():
            raise ValueError("Run this physical fixture away from midnight; do not alter the device clock")
        for name, course, person, duration, numbers in (
                ("同步联调一班", "信息学奥赛提高班", teacher, 120, ("26010001", "26010002")),
                ("同步隔离另一班", "虚构基础课程", other, 90, ("26020001",))):
            key = call("POST", "/v1/admin/classes", {"name": name, "course_name": course}, token)["id"]
            call("PUT", "/v1/admin/classes/" + key + "/teachers", {"teacher_ids": [person, owner["id"]] if duration == 120 else [person]}, token)
            for number in numbers:
                call("POST", "/v1/admin/students", {"class_id": key, "name": "联调同名学生" if duration == 120 else "虚构另一班学生", "external_ref": number, "remaining_units": 2000 if duration == 120 else 3000}, token)
            end = start + timedelta(minutes=duration)
            call("POST", "/v1/admin/lessons", {"class_id": key, "teacher_id": person, "starts_at": start.isoformat(), "ends_at": end.isoformat()}, token)
            with store.transaction() as db:
                db.execute("INSERT INTO class_schedule_templates VALUES(?,?,?,?,?,?,?,?,?,?,?)", (owner["org_id"], key, local.isoweekday(), 2 if duration == 120 else 1, local.date().isoformat() if duration == 120 else "", start.strftime("%H:%M"), end.strftime("%H:%M"), duration, 0, local.date().isoformat(), "虚构USB老师" if duration == 120 else "虚构另一老师"))
        with store.transaction() as db:
            db.execute("INSERT INTO archived_students VALUES(?,?,?,?,?,?,?)", (uid(), owner["org_id"], "虚构长期不来", "ARCH-USB", 2300, "", int(clock())))
        call("POST", "/v1/auth/logout", token=token)
    result = create_app(database, clock, allow_test_client=allow_test_client, review_only=False)
    @result.get("/fixture/identity")
    def fixture_identity():
        return {"synthetic_fixture": True, "fixture_kind": "sync-validation-v0.5", "real_database_opened": False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--clock-start", type=int, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    app = build(args.database, lambda: args.clock_start + int(time.monotonic() - started))
    app.add_middleware(LoseFirstReceipt)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False, server_header=False, ws="none")


if __name__ == "__main__":
    main()
