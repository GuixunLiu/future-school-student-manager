"""Missing historical facts, approvals and current balance exactly once."""
import copy
from datetime import datetime
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from business.store import Problem
from management.api import create_app
from management.approvals import digest
from management.deadlines import TZ
from management import historical
from test_management_delivery import DeliveryTests


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.f=DeliveryTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.base=self.f.f;self.store=self.f.store
        self.base.now=int(datetime(2026,11,3,10,tzinfo=TZ).timestamp());historical.initialize(self.base.path)
        header=dict(format='future-school-history-boundary-v1',org_id=self.base.org,canonical_from_day='2026-11-03',canonical_from=int(datetime(2026,11,3,tzinfo=TZ).timestamp()),baseline_sha256='a'*64)
        self.boundary=header|dict(boundary_sha256=digest(header))
        for name in ('management.historical.boundary_for','management.reports.history_boundary'):
            p=patch(name,return_value=self.boundary);p.start();self.addCleanup(p.stop)
        self.lid='legacy:11:2026-10-31';self.original=dict(lessons=[dict(key='11:2026-10-31',date='2026-10-31',class_local_id='11',class_name='原历史班',teacher='原任教师',start_time='14:00',end_time='15:30',expected=1,arrived=0,leave_count=0,members=[dict(student_local_id='1',name='虚构学生',external_ref='TEST',expected=True,status='missing',remaining_units=1000)])],weeks=[])
        with self.store.transaction() as db:
            db.execute('UPDATE devices SET expires_at=?',(self.base.now+86400,));db.execute('UPDATE management_device_access SET expires_at=?',(self.base.now+86400,))
            db.execute('CREATE TABLE management_source_links(org_id TEXT,kind TEXT,local_id INTEGER,server_id TEXT)')
            db.executemany('INSERT INTO management_source_links VALUES(?,?,?,?)',[(self.base.org,'class',11,'class-a'),(self.base.org,'student',1,'student')])
            db.execute('CREATE TABLE management_initializations(org_id TEXT,phase TEXT,archive_json TEXT)');db.execute('INSERT INTO management_initializations VALUES(?,?,?)',(self.base.org,'fixture',json.dumps(dict(sections=dict(attendance=[],leaves=[])))))
            db.execute('CREATE TABLE web_dataset(id INTEGER PRIMARY KEY,data_json TEXT)');db.execute('INSERT INTO web_dataset VALUES(1,?)',(json.dumps(self.original),))
            db.execute('INSERT INTO management_historical_clients VALUES(?,?,?,1)',(self.base.org,self.f.device['id'],'fixture-generation'))
        self.tokens={who:self.store.login('fictional',who,self.base.password,'historical-'+who)['access_token'] for who in ('owner','teacher','assessor','principal','other-teacher')}
        self.client=TestClient(create_app(self.base.path,lambda:self.base.now,allow_test_client=True),base_url='http://127.0.0.1:18087');self.addCleanup(self.client.close)

    def submit(self,**kw):return self.store.submit(self.tokens['teacher'],'teacher',**(dict(lesson_id=self.lid,student_id='student',kind='makeup',reason='核对历史未签',request_ref='history-one',expected_revision=0)|kw))
    def decide(self,key,**kw):return self.store.decide(self.tokens['owner'],'admin',**(dict(request_id=key,approve=True,reason='历史课堂核对通过',decision_ref='approve-history')|kw))
    def day(self):return self.store.attendance_day(self.tokens['teacher'],'teacher',on='2026-10-31')
    def raw(self):
        with self.store.transaction() as db:return db.execute('SELECT data_json FROM web_dataset').fetchone()[0]

    def test_read_only_get_exposes_both_request_eligibility_without_creating_facts(self):
        before=self.base.state();row=self.day()['items'][0];self.assertTrue(row['can_request']);self.assertEqual(row['request_student_id'],'student');self.assertEqual(row['revision'],0);self.assertTrue(row['balance_is_current']);self.assertEqual(self.base.state(),before)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_historical_facts').fetchone()[0],0)

    def test_makeup_pending_then_approval_changes_current_balance_once_and_preserves_original(self):
        raw=self.raw();r=self.submit();self.assertTrue(self.submit()['duplicate']);self.assertTrue(self.day()['items'][0]['has_pending_request'])
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],1000)
        self.decide(r['id']);self.assertTrue(self.decide(r['id'])['duplicate']);row=self.day()['items'][0];self.assertEqual((row['status'],row['balance_units']),('arrived',900));self.assertFalse(row['can_request']);self.assertIsNotNone(row['corrected_at']);self.assertEqual(self.raw(),raw)
        item=self.store.poll(self.f.token,'fixture-generation')['item'];e=json.loads(item['event_json']);self.assertEqual(e['historical']['history_boundary'],self.boundary);self.assertEqual(e['balance_delta'],-100)
        ack=dict(event_id=e['event_id'],receipt_token=item['receipt_token'],sha256=item['sha256'],status='applied',attendance_revision=1,balance_revision=1,balance_units=900)
        self.store.acknowledge(self.f.token,'fixture-generation',**ack);self.assertTrue(self.store.acknowledge(self.f.token,'fixture-generation',**ack)['duplicate']);self.assertEqual(self.raw(),raw)
        report=self.store.weekly_report(self.tokens['owner'],'admin',start='2026-10-26');self.assertEqual(report['sections'][-1]['makeup_count'],1)

    def test_historical_leave_keeps_expected_and_never_changes_balance(self):
        r=self.submit(kind='leave');self.decide(r['id']);row=self.day()['items'][0];self.assertEqual((row['status'],row['balance_units']),('leave',1000));self.assertEqual(self.day()['lessons'][0]['expected'],1)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT revision FROM management_balances').fetchone()[0],0)

    def test_scope_forgery_and_original_arrival_or_leave_refuse_without_admission(self):
        for who in ('other-teacher','principal'):
            with self.assertRaises(Problem):self.store.submit(self.tokens[who],'teacher' if who=='other-teacher' else who,lesson_id=self.lid,student_id='student',kind='leave',reason='无权',request_ref='forged',expected_revision=0)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_historical_facts').fetchone()[0],0)
        with self.assertRaises(Problem):self.submit(lesson_id='legacy:forged')
        for section,record in [('attendance',dict(studentId=1,date='2026-10-31')),('leaves',dict(studentId=1,date='2026-10-31',status='approved'))]:
            with self.store.transaction() as db:db.execute('UPDATE management_initializations SET archive_json=?',(json.dumps(dict(sections=dict(attendance=[record] if section=='attendance' else [],leaves=[record] if section=='leaves' else []))),))
            with self.assertRaises(Problem):self.submit()

    def test_old_tablet_or_changed_source_blocks_approval_and_keeps_request_pending(self):
        r=self.submit()
        with self.store.transaction() as db:db.execute('DELETE FROM management_historical_clients')
        with self.assertRaises(Problem):self.decide(r['id'])
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT status FROM management_requests').fetchone()[0],'pending');self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],1000)
            db.execute('INSERT INTO management_historical_clients VALUES(?,?,?,1)',(self.base.org,self.f.device['id'],'fixture-generation'));db.execute("UPDATE web_dataset SET data_json=data_json||' '")
        with self.assertRaises(Problem):self.decide(r['id'])

    def test_transferred_student_delivers_to_current_class_but_keeps_old_class_snapshot(self):
        with self.store.transaction() as db:
            db.execute("UPDATE students SET class_id='class-b' WHERE id='student'");db.execute("UPDATE management_device_students SET class_id='class-b'");db.execute('INSERT INTO device_classes VALUES(?,?,?)',(self.base.org,self.f.device['id'],'class-b'))
        r=self.submit();self.decide(r['id']);item=self.store.poll(self.f.token,'fixture-generation')['item'];self.assertIsNotNone(item);e=json.loads(item['event_json']);self.assertEqual(e['class_id'],'class-a');self.assertEqual(e['historical']['delivery_class_id'],'class-b')

    def test_duplicate_same_day_arrival_blocks_second_deduction(self):
        r=self.submit()
        with self.store.transaction() as db:db.execute("INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,'arrived',1)",(self.base.org,'other-same-day','student','class-a',int(datetime(2026,10,31,16,tzinfo=TZ).timestamp())))
        with self.assertRaises(Problem):self.decide(r['id'])
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],1000)

    def test_http_historical_submission_and_native_capability_require_proper_identity(self):
        h={'Authorization':'Bearer '+self.tokens['teacher']};r=self.client.post('/v1/management/requests',headers=h,json=dict(workspace_role='teacher',lesson_id=self.lid,student_id='student',kind='leave',reason='历史请假',request_ref='http-history',expected_revision=0));self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(self.client.post('/v1/management/device/history-ready',headers=h,json=dict(generation='fixture-generation')).status_code,403)
        r=self.client.post('/v1/management/device/history-ready',headers={'Authorization':'Bearer '+self.f.token},json=dict(generation='fixture-generation'));self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['historical_protocol'],1)


if __name__=='__main__':unittest.main()
