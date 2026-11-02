import copy
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from business.store import Problem
from management.api import create_app
from management.deadlines import TZ
from management.delivery import DeliveryStore
import test_management_rules as rules


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.f=rules.ApprovalTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.now=int(datetime(2026,11,2,14,tzinfo=TZ).timestamp())
        self.store=DeliveryStore(self.f.path,lambda:self.f.now);self.store.initialize()
        self.tokens={who:self.store.login('fictional',who,self.f.password,'report-'+who)['access_token'] for who in ('owner','teacher','other-teacher','principal','assessor')}
        self.saved=dict(start='2026-10-19',end='2026-10-25',name='全部',expected=2.571428571,arrived=1,leave_count=1,rate='38.9%',fallback=True,needs_review=True,
          classes=[dict(name='已删除旧班',expected=2.571428571,arrived=1,leave_count=1,rate='38.9%',needs_review=True)],teachers=[dict(name='旧教师',expected=2.571428571,arrived=1,leave_count=1,rate='38.9%')])
        self.data=dict(classes=[dict(local_id='11',name='虚构班甲')],source_captured_at='2026-11-02T06:00:00Z',weeks=[self.saved],lessons=[dict(class_local_id='11',class_name='虚构班甲',teacher='甲教师',date='2026-10-24',expected=2,arrived=1,leave_count=1),dict(class_local_id='12',class_name='不应给甲教师看的班',teacher='乙教师',date='2026-10-24',expected=3,arrived=3,leave_count=0)])
        for i,l in enumerate(self.data['lessons']):l.update(key=l['class_local_id']+':'+l['date'],members=[dict(student_local_id=str(i+1),name='旧申请学员'+str(i+1))])
        with self.store.transaction() as db:
            db.execute('CREATE TABLE web_dataset(id INTEGER PRIMARY KEY,data_json TEXT,source_sha256 TEXT,imported_at INTEGER)')
            db.execute('INSERT INTO web_dataset VALUES(1,?,?,?)',(json.dumps(self.data),'f'*64,self.f.now))
            db.execute('CREATE TABLE web_class_links(local_id TEXT PRIMARY KEY,remote_id TEXT)')
            db.execute('INSERT INTO web_class_links VALUES(?,?)',('11','class-a'));db.execute('INSERT INTO web_class_links VALUES(?,?)',('12','class-b'))
            db.execute('CREATE TABLE web_makeup_requests(id TEXT PRIMARY KEY,org_id TEXT,lesson_key TEXT,student_local_id TEXT,reason TEXT,status TEXT,created_at INTEGER)')
            for i,l in enumerate(self.data['lessons']):db.execute('INSERT INTO web_makeup_requests VALUES(?,?,?,?,?,?,?)',('legacy-'+str(i),self.f.org,l['key'],str(i+1),'原补签申请'+str(i),'pending',self.f.now-100))
            db.execute('INSERT INTO web_makeup_requests VALUES(?,?,?,?,?,?,?)',('unmapped',self.f.org,'removed-class:2026-01-01','999','不猜测已删除课堂归属','pending',self.f.now-200))
            # This teacher has the SAME display name as another teacher.
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(self.f.org,'other-teacher','class-b'))
            db.execute('INSERT INTO students VALUES(?,?,?,?,?,?,?,?,?)',('other-student',self.f.org,'class-b','其他学生','OTHER',1000,1,None,0))
            db.execute('INSERT INTO management_balances VALUES(?,?,?,0)',(self.f.org,'other-student',1000))
            for key,student,cls,status in [('new-a','student','class-a','leave'),('new-b','other-student','class-b','arrived')]:
                db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,?,0)',(self.f.org,key,student,cls,int(datetime(2026,11,7,14,tzinfo=TZ).timestamp()),status))

    def report(self,who='owner',role='admin',**changes):return self.store.weekly_report(self.tokens[who],role,**(dict(start='2026-10-19')|changes))

    def test_exact_legacy_totals_and_deleted_class_are_preserved_read_only(self):
        result=self.report();r=result['sections'][0]
        self.assertTrue(r['original_totals_preserved']);expected=copy.deepcopy(self.saved);expected['classes'][0]['historical_class_name']=True
        self.assertEqual(r['report'],expected);self.assertFalse(result['balances_changed'])
        with self.store.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0]),self.data)

    def test_teacher_selected_scope_never_returns_school_totals_or_other_classes(self):
        result=self.report('teacher','teacher');r=result['sections'][0]['report']
        self.assertEqual((r['expected'],r['arrived'],r['leave_count']),(2,1,1));self.assertNotIn('不应给甲教师看的班',json.dumps(result,ensure_ascii=False));self.assertFalse(result['sections'][0]['original_totals_preserved'])
        with self.assertRaises(Problem):self.report('teacher','admin')

    def test_principal_and_assessor_can_read_but_do_not_acquire_other_workspace(self):
        for who in ('principal','assessor'):
            self.assertEqual(self.report(who,who)['sections'][0]['report']['expected'],self.saved['expected'])
            with self.assertRaises(Problem):self.report(who,'admin')

    def test_transition_week_separates_sources_and_leave_stays_in_denominator(self):
        cut=int(datetime(2026,11,3,tzinfo=TZ).timestamp())
        with patch('management.reports.history_boundary',return_value=dict(canonical_from_day='2026-11-03',canonical_from=cut)):
            result=self.report(start='2026-11-02');self.assertTrue(result['transition_week']);self.assertFalse(result['totals_merged'])
            r=result['sections'][0]['report'];self.assertEqual((r['expected'],r['arrived'],r['leave_count'],r['rate']),(2,1,1,'50%'))
            self.assertEqual(len(r['teachers']),2)  # Equal names must not merge identities.
            own=self.report('teacher','teacher',start='2026-11-02')['sections'][0]['report'];self.assertEqual((own['expected'],own['arrived'],own['leave_count']),(1,0,1))

    def test_real_http_route_requires_auth_selected_role_and_canonical_week(self):
        client=TestClient(create_app(self.f.path,lambda:self.f.now,allow_test_client=True),base_url='http://127.0.0.1:18087');self.addCleanup(client.close)
        path='/v1/management/weekly-reports?workspace_role=admin&start=2026-10-19'
        self.assertEqual(client.get(path).status_code,401)
        response=client.get(path,headers={'Authorization':'Bearer '+self.tokens['owner']});self.assertEqual(response.status_code,200,response.text)
        with self.assertRaises(Problem):self.report(start='2026-10-20')

    def test_old_requests_are_visible_paginated_read_only_and_scoped_even_for_dual_role_owner(self):
        def read(who,role,**kw):return self.store.legacy_requests(self.tokens[who],role,**kw)
        first=read('owner','admin',limit=1);self.assertIsNotNone(first['next_before']);self.assertTrue(first['legacy_read_only']);self.assertFalse(first['balances_changed'])
        all_ids=[];cursor=None
        while True:
            page=read('owner','admin',limit=1,before=cursor);all_ids += [r['id'] for r in page['items']];cursor=page['next_before']
            if cursor is None:break
        self.assertEqual(set(all_ids),{'legacy-0','legacy-1','unmapped'})
        for who,role in [('teacher','teacher'),('assessor','assessor')]:self.assertEqual([r['id'] for r in read(who,role)['items']],['legacy-0'])
        with self.assertRaises(Problem):read('principal','principal')
        with self.store.transaction() as db:
            db.execute('INSERT INTO roles VALUES(?,?,?)',(self.f.org,self.f.owner,'teacher'));db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(self.f.org,self.f.owner,'class-a'))
        # Refresh credentials after role changes, matching normal authorization behavior.
        self.tokens['owner']=self.store.login('fictional','owner',self.f.password,'report-dual')['access_token']
        self.assertEqual([r['id'] for r in read('owner','teacher')['items']],['legacy-0'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM web_makeup_requests WHERE status='pending'").fetchone()[0],3)
            self.assertEqual(db.execute('SELECT units FROM management_balances WHERE student_id=?',('student',)).fetchone()[0],1000)

