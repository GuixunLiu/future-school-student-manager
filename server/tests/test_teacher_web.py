import copy
from datetime import datetime
import sqlite3
import unittest
from fastapi.testclient import TestClient
from teacher_web.api import create_app,install_data,TZ
import test_scoped_pilot as fixture


class RoleClient(TestClient):
    workspace_role=None
    def request(self,method,url,**kwargs):
        if method.upper()=='GET' and str(url).startswith('/v1/web/') and self.workspace_role and 'workspace_role=' not in str(url):
            url=str(url)+('&' if '?' in str(url) else '?')+'workspace_role='+self.workspace_role
        return super().request(method,url,**kwargs)


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

    def client(self):return RoleClient(self.app,base_url='http://127.0.0.1:18086')
    def login(self,c,name='fictional-teacher'):
        r=c.post('/v1/auth/login',json={'organization':'fictional','login':name,'password':fixture.PASSWORD});self.assertEqual(r.status_code,200);c.workspace_role='admin' if name=='fictional-teacher' else 'teacher';return {'Authorization':'Bearer '+r.json()['access_token']}
    def request(self,role='admin'):return {'lesson_key':'11:2026-10-31','student_local_id':'22','reason':'虚构核对补签','request_ref':'4'*32,'expected_source_sha256':'f'*64,'confirmed':True,'workspace_role':role}

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

    def test_historical_class_names_annotated_without_removing_history_or_rounding_totals(self):
        updated=copy.deepcopy(self.data);old=updated['weeks'][1]
        old.update(expected=18/7,arrived=1,fallback=True)
        old['classes']=[{'name':'虚构旧班名','expected':18/7,'arrived':1,'leave_count':0,'rate':'38.9%','needs_review':False}]
        install_data(self.db,updated,clock=self.clock)
        with self.client() as c:
            h=self.login(c);report=c.get('/v1/web/weeks?start=2026-10-19',headers=h).json()['report']
            self.assertEqual(report['expected'],18/7);self.assertEqual(report['classes'][0]['expected'],18/7)
            self.assertTrue(report['classes'][0]['historical_class_name']);self.assertEqual(report['arrived'],1)
            current=c.get('/v1/web/weeks',headers=h).json()['report'];self.assertFalse(current['classes'][0]['historical_class_name'])
            self.assertEqual(c.get('/health/ready').json()['report_display_version'],2)
        with sqlite3.connect(self.db) as db:
            stored=db.execute('SELECT data_json FROM web_dataset').fetchone()[0]
            self.assertNotIn('historical_class_name',stored)

    def test_assigned_teacher_can_use_own_class_but_not_admin_weekly_report(self):
        person='8'*32;org=fixture.fixture.ORG
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO users SELECT ?,org_id,?,?,password_hash,active,created_at FROM users WHERE id=?',(person,'ordinary-teacher','虚构普通教师',fixture.fixture.OWNER))
            db.execute('INSERT INTO roles VALUES(?,?,?)',(org,person,'teacher'))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(org,person,fixture.fixture.CLASS))
        with self.client() as c:
            h=self.login(c,'ordinary-teacher');self.assertEqual(len(c.get('/v1/web/days',headers=h).json()['lessons']),1)
            self.assertEqual(c.get('/v1/web/weeks',headers=h).status_code,403)
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=self.request('teacher')).status_code,201)

    def test_dual_role_requires_choice_and_teacher_mode_cannot_use_admin_scope(self):
        updated=copy.deepcopy(self.data)
        updated['classes'].append({'local_id':'12','name':'虚构另一班','course_name':'虚构课程','teacher':'虚构另班老师'})
        lesson=copy.deepcopy(updated['lessons'][0]);lesson.update(key='12:2026-10-31',class_local_id='12',class_name='虚构另一班',teacher='虚构另班老师')
        lesson['members']=[dict(lesson['members'][0],student_local_id='23',name='虚构另一学生',external_ref='OTHER001')];updated['lessons'].append(lesson)
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(fixture.fixture.ORG,'class','12',fixture.OTHER_C,'b'*64))
            db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(fixture.fixture.ORG,'student','23',fixture.OTHER_S,'b'*64))
        db.close();install_data(self.db,updated,clock=self.clock)
        with self.client() as c:
            h=self.login(c);c.workspace_role=None
            self.assertEqual(c.get('/v1/web/days',headers=h).status_code,409)
            missing=self.request();missing.pop('workspace_role');self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=missing).status_code,409)
            c.workspace_role='teacher'
            self.assertEqual([l['class_local_id'] for l in c.get('/v1/web/days',headers=h).json()['lessons']],['11'])
            self.assertEqual(c.get('/v1/web/weeks',headers=h).status_code,403)
            target=self.request('teacher')|{'lesson_key':'12:2026-10-31','student_local_id':'23'}
            self.assertEqual(c.post('/v1/web/makeup-requests',headers=h,json=target).status_code,403)
            c.workspace_role='admin';self.assertEqual(len(c.get('/v1/web/days',headers=h).json()['lessons']),2)
            self.assertEqual(c.get('/v1/web/weeks',headers=h).status_code,200)

    def test_single_role_cannot_select_administrator_and_can_default_to_teacher(self):
        with self.client() as c:
            h=self.login(c,'other-teacher');c.workspace_role=None;self.assertEqual(c.get('/v1/web/days',headers=h).json()['workspace_role'],'teacher')
            c.workspace_role='admin';self.assertEqual(c.get('/v1/web/days',headers=h).status_code,403)
            self.assertEqual(c.get('/v1/web/requests',headers=h).status_code,403)

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
