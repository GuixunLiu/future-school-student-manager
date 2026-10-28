"""Disposable USB integration fixture; synthetic data, no production credentials."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from business.api import create_app
from business.store import Store
from business.workflows import SCHOOL_TZ

TEST_PASSWORD = "Fictional USB-only passphrase 2026!"


class LoseFirstReceipt:
    """Return a transient failure AFTER one committed event, to verify exact retry."""
    def __init__(self, app):
        self.app, self.lost = app, False

    async def __call__(self, scope, receive, send):
        lose = scope.get("path") == "/v1/device/events" and scope.get("method") == "POST" and not self.lost
        async def outgoing(message):
            if lose and message["type"] == "http.response.start" and message["status"] == 200:
                self.lost = True
                message["status"] = 503
            await send(message)
        await self.app(scope, receive, outgoing)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--clock-start", type=int, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    clock = lambda: args.clock_start + int(time.monotonic() - started)
    Store.bootstrap(args.database, "usb-fixture", "usb-owner", "虚构USB管理员", TEST_PASSWORD, clock)
    app = create_app(args.database, clock, allow_test_client=True)
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        def call(method, path, data=None, token=None):
            r=client.request(method,path,headers={"Authorization":"Bearer "+token} if token else {}, **({"json":data} if data is not None else {}))
            if not r.is_success:
                raise RuntimeError("Fictional USB setup failed")
            return r.json()
        token=call("POST","/v1/auth/login",{"organization":"usb-fixture","login":"usb-owner","password":TEST_PASSWORD})["access_token"]
        teacher=call("POST","/v1/admin/people",{"login":"usb-teacher","display_name":"虚构USB老师","password":TEST_PASSWORD,"roles":["teacher"]},token)["id"]
        course=call("POST","/v1/admin/classes",{"name":"同步联调一班","course_name":"虚构同步课程"},token)["id"]
        call("PUT",f"/v1/admin/classes/{course}/teachers",{"teacher_ids":[teacher]},token)
        for number in ("26010001","26010002"):
            call("POST","/v1/admin/students",{"class_id":course,"name":"联调同名学生","external_ref":number,"remaining_units":2000},token)
        local=datetime.fromtimestamp(clock(),SCHOOL_TZ)
        start=local.replace(hour=0 if local.hour<12 else 12,minute=0,second=0,microsecond=0)
        call("POST","/v1/admin/lessons",{"class_id":course,"teacher_id":teacher,"starts_at":start.isoformat(),"ends_at":(start+timedelta(hours=11,minutes=59)).isoformat()},token)
    app=create_app(args.database,clock)
    app.add_middleware(LoseFirstReceipt)
    uvicorn.run(app,host="127.0.0.1",port=args.port,access_log=False,proxy_headers=False,server_header=False,ws="none")


if __name__=="__main__":main()
