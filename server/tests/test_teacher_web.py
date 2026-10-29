import copy
from datetime import datetime
import sqlite3
import unittest
from fastapi.testclient import TestClient
from teacher_web.api import create_app,install_data,TZ
import test_scoped_pilot as fixture


class TeacherWebTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ScopedPilotTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=self.f.database;self.now=int(datetime(2026,10,31,16,30,tzinfo=TZ).timestamp());self.clock=lambda:self.now
        members=[{'student_local_id':'22','name':'虚构学生','external_ref':'TEST001','remaining_units':-150,'expected':True,'status':'missing','attendance_time':'','attendance_method':''}]
        summary={'name':'全校','expected':1,'arrived':0,'leave_count':0,'rate':'0%','needs_review':False}
        self.data={'format':'future-school-teacher-web-dataset','schema_version':1,'history_import_is_read_only':True,'balances_charged':False,'wechat_enabled':False,'source_sha256':'f'*64,'source_captured_at':'虚构快照时间','as_of_date':'2026-10-31','source_records':1,'classes':[{'local_id':'11','name':'虚构提高班','course_name':'虚构课程','teacher':'虚构教师'}],
          'lessons':[{'key':'11:2026-10-31','class_local_id':'11','class_name':'虚构提高班','teacher':'虚构教师','date':'2026-10-31','kind':'regular','start_time':'14:00','end_time':'16:00','expected':1,'arrived':0,'leave_count':0,'needs_review':False,'members':members}],
          'weeks':[{'start':'2026-10-26','end':'2026-11-01','offset':0,**summary,'fallback':False,'classes':[dict(summary,name='虚构提高班')],'teachers':[dict(summary,name='虚构教师')]},{'start':'2026-10-19','end':'2026-10-25','offset':-1,**dict(summary,arrived=1,rate='100%'),'fallback':False,'classes':[],'teachers':[]}]}
        install_data(self.db,self.data,clock=self.clock);self.app=create_app(self.db,self.clock,allow_test_client=True)

    def client(self):return TestClient(self.app,base_url='http://127.0.0.1:18086')
    def login(self,c,name='fictional-teacher'):
        r=c.post('/v1/auth/login',json={'organization':'fictional','login':name,'password':fixture.PASSWORD});self.assertEqual(r.status_code,200);return {'Authorization':'Bearer '+r.json()['access_token']}
    def request(self):return {'lesson_key':'11:2026-10-31','student_local_id':'22','reason':'虚构核对补签','request_ref':'4'*32,'expected_source_sha256':'f'*64,'confirmed':True}

    def test_owner_today_roster_and_historical_week(self):
        with self.client() as c:
            h=self.login(c);day=c.get('/v1/web/days',headers=h).json();self.assertEqual(day['date'],'2026-10-31');self.assertEqual(len(day['lessons']),1)
            self.assertEqual(day['lessons'][0]['members'][0]['status'],'missing')
            old=c.get('/v1/web/weeks?start=2026-10-19',headers=h).json();self.assertEqual(old['report']['arrived'],1)
            self.assertEqual(c.get('/v1/web/weeks?start=2020-01-01',headers=h).status_code,404)

    def test_other_teacher_sees_no_other_class_and_no_admin_report(self):
        with self.client() as c:
            h=self.login(c,'other-teacher');self.assertEqual(c.get('/v1/web/days',headers=h).json()['lessons'],[])
            self.assertEqual(c.get('/v1/web/weeks',headers=h).status_code,403)
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).status_code,403)

    def test_assigned_teacher_can_use_own_class_but_not_admin_weekly_report(self):
        person='8'*32;org=fixture.fixture.ORG
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO users SELECT ?,org_id,?,?,password_hash,active,created_at FROM users WHERE id=?',(person,'ordinary-teacher','虚构普通教师',fixture.fixture.OWNER))
            db.execute('INSERT INTO roles VALUES(?,?,?)',(org,person,'teacher'))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(org,person,fixture.fixture.CLASS))
        with self.client() as c:
            h=self.login(c,'ordinary-teacher');self.assertEqual(len(c.get('/v1/web/days',headers=h).json()['lessons']),1)
            self.assertEqual(c.get('/v1/web/weeks',headers=h).status_code,403)
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).status_code,201)

    def test_makeup_is_persistent_idempotent_request_without_charge_or_arrival(self):
        with self.client() as c:
            h=self.login(c);r=c.post('/v1/web/makeup-requests',headers=h,json=self.request());self.assertEqual(r.status_code,201,r.text);first=r.json();self.assertFalse(first['hours_deducted']);self.assertFalse(first['attendance_changed'])
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).json()['id'],first['id'])
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()|{'request_ref':'5'*32}).json()['id'],first['id'])
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()|{'reason':'另一个请求'}).status_code,409)
        with self.client() as c:
            h=self.login(c);rows=c.get('/v1/web/requests',headers=h).json()['items'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'pending')
            day=c.get('/v1/web/days',headers=h).json()['lessons'][0];self.assertEqual(day['arrived'],0);self.assertEqual(day['members'][0]['makeup_request']['id'],first['id'])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT remaining_units FROM students WHERE id=?',(fixture.fixture.STUDENT,)).fetchone()[0],-150)
            self.assertEqual(db.execute('SELECT count(*) FROM attendance WHERE lesson_id=?',(self.f.scope['lesson_id'],)).fetchone()[0],0)

    def test_confirmation_staleness_future_and_signed_leave_rejected(self):
        with self.client() as c:
            h=self.login(c)
            for payload,code in [(self.request()|{'confirmed':False},422),(self.request()|{'expected_source_sha256':'a'*64},409),(self.request()|{'student_local_id':'999'},409)]:self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=payload).status_code,code)
            self.now-=86400;self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).status_code,409);self.now+=86400
        for status in ['arrived','leave']:
            updated=copy.deepcopy(self.data);updated['lessons'][0]['members'][0]['status']=status;install_data(self.db,updated,clock=self.clock)
            with self.client() as c:
                h=self.login(c);self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).status_code,409)

    def test_new_tablet_snapshot_resolves_pending_request_without_invented_charge(self):
        with self.client() as c:h=self.login(c);self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request()).status_code,201)
        updated=copy.deepcopy(self.data);updated['source_sha256']='e'*64;updated['lessons'][0]['members'][0]['status']='arrived';updated['lessons'][0]['arrived']=1;install_data(self.db,updated,clock=self.clock)
        with self.client() as c:
            h=self.login(c);self.assertEqual(c.get('/v1/web/requests',headers=h).json()['items'][0]['status'],'observed_arrived')

    def test_old_mutations_host_and_unauthenticated_access_are_not_available(self):
        with self.client() as c:
            self.assertEqual(c.get('/v1/web/days').status_code,401);h=self.login(c)
            self.assertEqual(c.post('/v1/admin/devices',headers=h,json={}).status_code,404)
            self.assertEqual(c.post('/v1/device/events',headers=h,json={}).status_code,404)
            self.assertEqual(c.get('/v1/web/days',headers={**h,'Host':'evil.example'}).status_code,403)
            self.assertEqual(c.post('/v1/web/makeup-requests',headers={**h,'Origin':'https://evil.example'},json=self.request()).status_code,403)

    def test_import_rejects_changed_identity_and_teacher(self):
        for mutate in [lambda d:d['classes'][0].update(teacher='其他老师'),lambda d:d['lessons'][0]['members'][0].update(name='其他学生')]:
            updated=copy.deepcopy(self.data);mutate(updated)
            with self.assertRaises(ValueError):install_data(self.db,updated,clock=self.clock)


if __name__=='__main__':unittest.main()
