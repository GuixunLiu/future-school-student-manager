"""Scoped historical detail and current lesson cards. Fictional records only."""
import copy
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from management.api import create_app
from management.deadlines import TZ
from test_management_reports import ReportTests


class ClassroomTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ReportTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f;self.store=self.fixture.store;self.tokens=self.fixture.tokens
        self.f.now=int(datetime(2026,11,7,15,tzinfo=TZ).timestamp())
        self.tokens={who:self.store.login('fictional',who,self.f.password,'classroom-'+who)['access_token'] for who in self.tokens}
        self.boundary=dict(canonical_from_day='2026-11-03',canonical_from=int(datetime(2026,11,3,tzinfo=TZ).timestamp()))
        p=patch('management.reports.history_boundary',return_value=self.boundary);p.start();self.addCleanup(p.stop)
        self.data=copy.deepcopy(self.fixture.data)
        lesson=self.data['lessons'][0];lesson.update(start_time='14:00',end_time='15:30',expected=3,arrived=1,leave_count=1)
        lesson['members']=[dict(student_local_id=str(i+1),name=name,external_ref='H00'+str(i),remaining_units=1000,
            expected=True,status=status,attendance_time='14:03' if status=='arrived' else '',attendance_method='人脸签到' if status=='arrived' else '',makeup=False)
            for i,(name,status) in enumerate([('历史到课学生','arrived'),('历史请假学生','leave'),('历史未到学生','missing')])]
        with self.store.transaction() as db:
            db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(self.data),))
            for i,status in [(2,'missing'),(3,'arrived')]:
                sid='current-'+str(i)
                db.execute('INSERT INTO students VALUES(?,?,?,?,?,?,?,?,?)',(sid,self.f.org,'class-a','当前学生'+str(i),'C00'+str(i),1000,1,None,0))
                db.execute('INSERT INTO management_balances VALUES(?,?,?,0)',(self.f.org,sid,1000))
                db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,?,0)',(self.f.org,'new-a',sid,'class-a',int(datetime(2026,11,7,14,tzinfo=TZ).timestamp()),status))
        self.client=TestClient(create_app(self.f.path,lambda:self.f.now,allow_test_client=True),base_url='http://127.0.0.1:18087');self.addCleanup(self.client.close)

    def get(self,who='teacher',role='teacher',**kw):
        return self.client.get('/v1/management/attendance',params=dict(workspace_role=role,on='2026-10-24')|kw,headers={'Authorization':'Bearer '+self.tokens[who]})

    def test_historical_students_time_method_and_balances_are_read_only(self):
        before=self.f.state()
        data=self.get().json();self.assertTrue(data['read_only']);self.assertEqual(len(data['lessons']),1)
        self.assertEqual((data['lessons'][0]['expected'],data['lessons'][0]['arrived'],data['lessons'][0]['missing'],data['lessons'][0]['leave_count']),(3,1,1,1))
        arrived=next(v for v in data['items'] if v['status']=='arrived')
        self.assertEqual((arrived['attendance_time'],arrived['attendance_method']),('14:03','人脸签到'))
        self.assertTrue(all(v['read_only'] and v['charge_units'] is None for v in data['items']))
        self.assertEqual(self.f.state(),before)
        with self.store.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0]),self.data)

    def test_history_pagination_filters_and_lesson_do_not_leak_other_teachers(self):
        pages=[];cursor=None
        while True:
            r=self.get(limit=1,**({'before':cursor} if cursor else {}));self.assertEqual(r.status_code,200,r.text);data=r.json();pages+=data['items'];cursor=data['next_before']
            if cursor is None:break
        self.assertEqual(len({v['student_id'] for v in pages}),3)
        self.assertEqual(len(self.get(status='missing').json()['items']),1)
        self.assertEqual(self.get(q='H000').json()['items'][0]['student_name'],'历史到课学生')
        data=self.get(lesson_id='legacy:12:2026-10-24').json();self.assertEqual(data['items'],[])
        self.assertNotIn('不应给甲教师看的班',json.dumps(data,ensure_ascii=False))
        self.assertEqual(len(self.get('owner','admin').json()['lessons']),2)
        self.assertEqual(self.get('assessor','assessor').status_code,403)
        self.assertEqual(self.get(role='admin').status_code,403)
        self.assertEqual(self.client.get('/v1/management/attendance',params={'on':'2026-10-24','workspace_role':'teacher'}).status_code,401)

    def test_canonical_cards_count_whole_lesson_despite_member_filters_and_pagination(self):
        data=self.get(on='2026-11-07',limit=1,status='missing',q='C00').json()
        self.assertFalse(data['read_only']);self.assertEqual(len(data['lessons']),1)
        row=data['lessons'][0];self.assertEqual((row['expected'],row['arrived'],row['missing'],row['leave_count']),(3,1,1,1))
        self.assertEqual(len(data['items']),1);self.assertEqual(data['items'][0]['student_no'],'C002')
        self.assertFalse(data['items'][0]['read_only'])
        self.assertEqual(self.get(on='2026-11-07',lesson_id='new-b').json()['items'],[])
        self.assertEqual(len(self.get('owner','admin',on='2026-11-07').json()['lessons']),2)

    def test_deleted_class_is_not_granted_by_matching_teacher_name_or_same_display_name(self):
        data=copy.deepcopy(self.data);data['lessons'][0]['class_local_id']='unknown';data['lessons'][0]['teacher']='甲教师'
        with self.store.transaction() as db:db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(data),))
        self.assertEqual(self.get().json()['items'],[])
        self.assertEqual(len(self.get('owner','admin').json()['lessons']),2)
        self.assertEqual(len(self.get('other-teacher','teacher').json()['lessons']),1)

    def test_boundary_is_exact_and_invalid_date_status_limit_are_rejected(self):
        self.assertTrue(self.get(on='2026-11-02').json()['read_only'])
        self.assertFalse(self.get(on='2026-11-03').json()['read_only'])
        for args in [dict(on='2026-02-30'),dict(on='20261107'),dict(status='forged'),dict(limit=101),dict(before=0),dict(q='x'*81)]:
            self.assertEqual(self.get(**args).status_code,422)


if __name__=='__main__':unittest.main()
