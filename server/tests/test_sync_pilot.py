"""Current extension integration with fully fictional students and a fresh database."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from business.store import Store
from business.workflows import SCHOOL_TZ
from serve_sync_pilot_fixture import build, PASSWORD


class SyncPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=Path(cls.temp.name)/"fixture.sqlite"
        cls.clock=int(datetime(2026,10,27,16,0,tzinfo=SCHOOL_TZ).timestamp())
        cls.app=build(cls.path,lambda:cls.clock,allow_test_client=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_minimal_scoped_plan_archived_exclusion_and_durations(self):
        with TestClient(self.app) as c:
            def request(method,url,token=None,data=None,status=200):
                r=c.request(method,url,headers={"Authorization":"Bearer "+token} if token else {},**({"json":data} if data is not None else {}))
                self.assertEqual(r.status_code,status,r.text);return r.json()
            def login(name):return request("POST","/v1/auth/login",data={"organization":"usb-fixture","login":name,"password":PASSWORD})["access_token"]
            owner=login("usb-owner");teacher=login("usb-teacher")
            self.assertEqual(request("GET","/v1/me",owner)["roles"],["admin","teacher"])
            all_classes=request("GET","/v1/classes",owner)["items"];allowed=request("GET","/v1/classes",teacher)["items"]
            self.assertEqual(len(all_classes),2);self.assertEqual(len(allowed),1);self.assertEqual(len(request("GET","/v1/students",teacher)["items"]),2)
            request("GET","/v1/admin/archives",teacher,status=403)
            device=request("POST","/v1/admin/devices",owner,{"name":"虚构设备","class_ids":[allowed[0]["id"]]},201)
            request("PUT","/v1/admin/devices/"+device["id"]+"/sync-access",owner,{"enabled":True,"allow_local_leaves":True})
            plan=request("GET","/v1/device/sync-plan",device["device_token"])
            self.assertEqual(len(plan["students"]),2);self.assertEqual(len(plan["classes"]),1);self.assertEqual(len(plan["lessons"]),1)
            self.assertNotIn("ARCH-USB",str(plan));self.assertNotIn("26020001",str(plan));self.assertEqual(plan["lessons"][0]["ends_at"]-plan["lessons"][0]["starts_at"],7200)
            with closing(Store(self.path).connect()) as db:
                self.assertEqual(sorted(r[0] for r in db.execute("SELECT duration_minutes FROM class_schedule_templates")),[90,120])
                self.assertEqual(db.execute("SELECT interval_weeks FROM class_schedule_templates WHERE duration_minutes=120").fetchone()[0],2)
                self.assertEqual(db.execute("SELECT remaining_units FROM archived_students").fetchone()[0],2300)
                self.assertEqual(db.execute("SELECT count(*) FROM student_activity").fetchone()[0],0)

    def test_refuses_existing_database_and_midnight_fixture(self):
        with self.assertRaises(FileExistsError):build(self.path,lambda:self.clock)
        with tempfile.TemporaryDirectory() as d:
            midnight=int(datetime(2026,10,27,0,3,tzinfo=SCHOOL_TZ).timestamp())
            with self.assertRaises(ValueError):build(Path(d)/"midnight.sqlite",lambda:midnight)


if __name__=="__main__":unittest.main()
