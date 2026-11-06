import importlib.util
from pathlib import Path
import sqlite3
import unittest

from fastapi.testclient import TestClient
from business.security import password_hash
import test_single_class_stage as fixture

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("scoped_runtime",ROOT/"ops/scoped-pilot.py")
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
PASSWORD="Fictional scoped pilot password 2026!"
OTHER_T,OTHER_C,OTHER_S,OTHER_L,OTHER_LEAVE=[c*32 for c in "abcde"]


class ScopedPilotTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SingleClassStageTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        db=sqlite3.connect(self.f.source);encoded=password_hash(PASSWORD)
        db.execute("UPDATE users SET password_hash=?",(encoded,))
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)",(OTHER_T,fixture.ORG,"other-teacher","虚构另班老师",encoded,1,1))
        db.execute("INSERT INTO roles VALUES(?,?,?)",(fixture.ORG,OTHER_T,"teacher"))
        db.execute("INSERT INTO classes VALUES(?,?,?,?,?)",(OTHER_C,fixture.ORG,"虚构另一班","虚构课程",1))
        db.execute("INSERT INTO teacher_classes VALUES(?,?,?)",(fixture.ORG,OTHER_T,OTHER_C))
        db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)",(OTHER_S,fixture.ORG,OTHER_C,"虚构另一学生","OTHER001",2000))
        db.execute("INSERT INTO lessons VALUES(?,?,?,?,?,?,?)",(OTHER_L,fixture.ORG,OTHER_C,OTHER_T,1793407200,1793414400,1))
        db.execute("INSERT INTO lesson_members VALUES(?,?,?)",(fixture.ORG,OTHER_L,OTHER_S))
        db.execute("INSERT INTO leaves(id,org_id,lesson_id,student_id,status,reason,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",(OTHER_LEAVE,fixture.ORG,OTHER_L,OTHER_S,"approved","虚构旧请假",OTHER_T,1))
        db.commit();db.close();self.receipt=self.f.prepare();self.source=self.f.output/"pilot-offline.sqlite";self.database=self.f.root/"scoped.sqlite"
        self.before=fixture.stage.checksum(self.source)
        self.scope=runtime.prepare_database(self.source,self.database,self.f.plan,self.receipt,fixture.stage,"fictional-teacher",clock=lambda:fixture.NOW)
        self.api=runtime.build_app(self.database,self.f.plan,self.scope,fixture.stage,clock=lambda:fixture.NOW,allow_test_client=True)

    def client(self):return TestClient(self.api,base_url="http://127.0.0.1:18085")

    def login(self,client,name="fictional-teacher"):
        r=client.post("/v1/auth/login",json={"organization":"fictional","login":name,"password":PASSWORD})
        self.assertEqual(r.status_code,200);return {"Authorization":"Bearer "+r.json()["access_token"]}

    def test_confirmed_lesson_created_without_charging_or_touching_source(self):
        self.assertEqual(fixture.stage.checksum(self.source),self.before)
        db=sqlite3.connect(self.database)
        self.assertEqual(db.execute("SELECT count(*) FROM lessons WHERE class_id=?",(fixture.CLASS,)).fetchone()[0],1)
        self.assertEqual(db.execute("SELECT remaining_units FROM students WHERE id=?",(fixture.STUDENT,)).fetchone()[0],-150)
        self.assertEqual(db.execute("SELECT count(*) FROM attendance WHERE lesson_id=?",(self.scope["lesson_id"],)).fetchone()[0],0)
        self.assertEqual(db.execute("SELECT count(*) FROM lesson_members WHERE lesson_id=?",(self.scope["lesson_id"],)).fetchone()[0],1);db.close()
        with self.assertRaises(FileExistsError):runtime.prepare_database(self.source,self.database,self.f.plan,self.receipt,fixture.stage,"fictional-teacher",clock=lambda:fixture.NOW)

    def test_leave_cancel_after_refresh_keeps_denominator_and_balance(self):
        with self.client() as c:
            h=self.login(c);lid=self.scope["lesson_id"]
            self.assertEqual(c.get('/pilot/context',headers=h).json()["expected"],1)
            r=c.post('/v1/lessons/'+lid+'/leaves',json={"student_id":fixture.STUDENT,"reason":"虚构事假"},headers=h)
            self.assertEqual(r.status_code,201)
            row=c.get('/pilot/leaves',headers=h).json()["items"][0]
            self.assertEqual(row["id"],r.json()["id"])
            detail=c.get('/v1/lessons/'+lid,headers=h).json();self.assertEqual((detail["expected"],detail["arrived"],detail["leave_count"],detail["rate_percent"]),(1,0,1,0))
            self.assertEqual(c.post('/v1/leaves/'+row['id']+'/cancel',json={"reason":"虚构撤假"},headers=h).status_code,200)
            self.assertEqual(c.get('/pilot/leaves',headers=h).json()["items"],[])
            self.assertEqual(c.get('/pilot/students',headers=h).json()["items"][0]["remaining_units"],-150)
            self.assertEqual(c.get('/health/ready').status_code,200)

    def test_draft_saved_but_publishing_refused(self):
        with self.client() as c:
            h=self.login(c);url='/v1/lessons/'+self.scope['lesson_id']+'/content'
            data={"body":"虚构本节课学习循环。","audience":"arrived","publish_after_class":False}
            self.assertEqual(c.put(url,json=data,headers=h).status_code,200)
            self.assertEqual(c.get(url,headers=h).json()['body'],data['body'])
            self.assertEqual(c.put(url,json={**data,'publish_after_class':True},headers=h).status_code,403)
            self.assertEqual(c.get('/health/ready').status_code,200)

    def test_admin_cannot_write_other_class_or_wrong_member(self):
        with self.client() as c:
            h=self.login(c)
            for method,url,data in [('POST','/v1/lessons/'+OTHER_L+'/leaves',{'student_id':OTHER_S,'reason':'虚构'}),('PUT','/v1/lessons/'+OTHER_L+'/content',{'body':'虚构','publish_after_class':False}),('POST','/v1/leaves/'+OTHER_LEAVE+'/cancel',{'reason':'虚构'}),('POST','/v1/lessons/'+self.scope['lesson_id']+'/leaves',{'student_id':OTHER_S,'reason':'虚构'})]:
                self.assertEqual(c.request(method,url,json=data,headers=h).status_code,403,url)
        db=sqlite3.connect(self.database);self.assertEqual(db.execute('SELECT status FROM leaves WHERE id=?',(OTHER_LEAVE,)).fetchone()[0],'approved');db.close()

    def test_teacher_without_selected_assignment_cannot_read_context_or_write(self):
        with self.client() as c:
            h=self.login(c,'other-teacher')
            for url in ('/pilot/context','/pilot/students','/pilot/leaves'):self.assertEqual(c.get(url,headers=h).status_code,403)
            self.assertEqual(c.post('/v1/lessons/'+self.scope['lesson_id']+'/leaves',json={'student_id':fixture.STUDENT,'reason':'虚构'},headers=h).status_code,403)

    def test_all_new_legacy_device_admin_and_notification_writes_are_denied(self):
        with self.client() as c:
            h=self.login(c)
            paths=[('POST','/v1/device/events'),('POST','/v1/device/attendance'),('POST','/v1/device/balance'),('POST','/v1/device/attendance/'+'f'*32+'/revoke'),('POST','/v1/admin/devices'),('PUT','/v1/admin/devices/'+'f'*32+'/sync-access'),('POST','/v1/admin/lessons'),('PATCH','/v1/admin/people/'+fixture.OWNER),('PUT','/v1/me/notification-preferences'),('POST','/v1/admin/jobs/tick'),('POST','/v1/test/parent-links'),('POST','/v1/lessons/'+self.scope['lesson_id']+'/absence-notices')]
            for method,url in paths:self.assertEqual(c.request(method,url,json={},headers=h).status_code,403,url)
            self.assertEqual(c.post('/v1/lessons/'+self.scope['lesson_id']+'/leaves',json={'student_id':fixture.STUDENT,'reason':'虚构'}).status_code,401)

    def test_baseline_tamper_stops_writes_and_health(self):
        db=sqlite3.connect(self.database);db.execute('UPDATE students SET remaining_units=0 WHERE id=?',(fixture.STUDENT,));db.commit();db.close()
        with self.client() as c:
            h=self.login(c)
            self.assertEqual(c.get('/health/ready').status_code,503)
            self.assertEqual(c.post('/v1/lessons/'+self.scope['lesson_id']+'/leaves',json={'student_id':fixture.STUDENT,'reason':'虚构'},headers=h).status_code,503)

    def test_host_origin_and_student_list_do_not_bypass_existing_guards(self):
        with self.client() as c:
            h=self.login(c)
            self.assertEqual(c.get('/pilot/students',headers=h).json()['items'][0]['id'],fixture.STUDENT)
            self.assertEqual(len(c.get('/pilot/students',headers=h).json()['items']),1)
            self.assertEqual(c.get('/pilot/context',headers={**h,'Host':'evil.example'}).status_code,403)
            self.assertEqual(c.post('/v1/lessons/'+self.scope['lesson_id']+'/leaves',headers={**h,'Origin':'https://evil.example'},json={'student_id':fixture.STUDENT,'reason':'虚构'}).status_code,403)


if __name__=='__main__':unittest.main()
