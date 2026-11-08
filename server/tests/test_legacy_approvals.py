"""Old pending requests become reviewable without altering/replaying originals."""
import copy
import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from business.store import Problem
from management import legacy_approvals as legacy
from management.api import create_app
from test_historical_requests import HistoricalTests

KEY='a'*32

class LegacyApprovalTests(unittest.TestCase):
    def setUp(self):
        self.h=HistoricalTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.store=self.h.store;self.f=self.h.base;legacy.initialize(self.f.path)
        with self.store.transaction() as db:
            db.execute('CREATE TABLE web_makeup_requests(id TEXT PRIMARY KEY,org_id TEXT,lesson_key TEXT,student_local_id TEXT,reason TEXT,status TEXT,created_at INTEGER)')
            db.execute('INSERT INTO web_makeup_requests VALUES(?,?,?,?,?,?,?)',(KEY,self.f.org,self.h.lid[7:],'1','原教师确认到课申请','pending',self.f.now-86400))
        self.client=TestClient(create_app(self.f.path,lambda:self.f.now,allow_test_client=True),base_url='http://127.0.0.1:18087');self.addCleanup(self.client.close)

    def decide(self,**changes):
        return legacy.decide(self.store,self.h.tokens['owner'],'admin',**(dict(legacy_id=KEY,approve=True,reason='原记录核对通过',decision_ref='legacy-one')|changes))
    def rows(self,who='owner',role='admin'):return self.store.legacy_requests(self.h.tokens[who],role)
    def counts(self):
        with self.store.transaction() as db:
            return {n:db.execute('SELECT COUNT(*) FROM '+n).fetchone()[0] for n in ['management_legacy_decisions','management_requests','management_historical_facts','management_outbox']}

    def test_get_exposes_review_buttons_only_in_approval_workspace_and_never_adopts(self):
        before=self.f.state();r=self.rows();self.assertFalse(r['legacy_read_only']);self.assertTrue(r['items'][0]['can_approve']);self.assertTrue(r['items'][0]['can_reject']);self.assertEqual(self.f.state(),before)
        self.assertFalse(self.rows('teacher','teacher')['items'][0]['can_decide']);self.assertTrue(self.rows('assessor','assessor')['items'][0]['can_decide'])

    def test_approve_preserves_original_then_canonical_debits_and_delivers_once(self):
        r=self.decide();self.assertEqual(r['status'],'approved');self.assertTrue(self.decide()['duplicate']);self.assertEqual(self.counts(),dict(management_legacy_decisions=1,management_requests=1,management_historical_facts=1,management_outbox=1))
        row=self.rows()['items'][0];self.assertEqual(row['status'],'approved');self.assertEqual(row['legacy_status'],'pending');self.assertFalse(row['requires_review']);self.assertEqual(row['tablet_delivery'],'pending')
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],900);self.assertEqual(db.execute('SELECT status FROM web_makeup_requests').fetchone()[0],'pending');self.assertEqual(json.loads(db.execute('SELECT source_json FROM management_legacy_decisions').fetchone()[0])['reason'],'原教师确认到课申请')
        event=json.loads(self.store.poll(self.h.f.token,'fixture-generation')['item']['event_json']);self.assertEqual(event['request_id'],r['request_id']);self.assertEqual(event['historical']['date'],'2026-10-31');self.assertEqual(event['balance_delta'],-100)
        with self.assertRaises(Problem):self.decide(reason='同编号不同内容')

    def test_reject_preserves_reason_without_fact_debit_or_outbox(self):
        r=self.decide(approve=False);self.assertEqual(r['status'],'rejected');self.assertEqual(self.counts(),dict(management_legacy_decisions=1,management_requests=0,management_historical_facts=0,management_outbox=0));self.assertFalse(self.rows()['items'][0]['requires_review'])
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],1000)

    def test_existing_pending_and_approved_makeup_are_reused_instead_of_charged_again(self):
        existing=self.h.submit();r=self.decide();self.assertEqual(r['request_id'],existing['id']);self.assertEqual(self.counts()['management_outbox'],1)
        with self.store.transaction() as db:db.execute('INSERT INTO web_makeup_requests SELECT ?,org_id,lesson_key,student_local_id,reason,status,created_at FROM web_makeup_requests WHERE id=?',('b'*32,KEY))
        self.assertTrue(self.rows()['items'][0]['already_effective']);second=self.decide(legacy_id='b'*32,decision_ref='old-duplicate')
        self.assertEqual(second['request_id'],existing['id']);self.assertEqual(self.counts()['management_outbox'],1)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],900)

    def test_existing_leave_is_never_silently_changed_into_makeup(self):
        self.h.submit(kind='leave');before=self.f.state();row=self.rows()['items'][0];self.assertFalse(row['can_approve']);self.assertIn('请假',row['approval_blocked_reason'])
        with self.assertRaises(Problem):self.decide()
        self.assertEqual(self.f.state(),before);self.decide(approve=False)

    def test_teacher_cannot_approve_and_scoped_assessor_can_adopt_without_raw_edit_permission(self):
        with self.assertRaises(Problem):legacy.decide(self.store,self.h.tokens['teacher'],'teacher',legacy_id=KEY,approve=True,reason='无权',decision_ref='bad')
        r=legacy.decide(self.store,self.h.tokens['assessor'],'assessor',legacy_id=KEY,approve=True,reason='管辖原申请核对',decision_ref='scope-one');self.assertEqual(r['status'],'approved')
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT status FROM web_makeup_requests').fetchone()[0],'pending')

    def test_unknown_or_archived_source_blocks_approve_but_admin_can_reject(self):
        with self.store.transaction() as db:db.execute("UPDATE web_makeup_requests SET lesson_key='unknown'")
        row=self.rows()['items'][0];self.assertTrue(row['can_reject']);self.assertFalse(row['can_approve'])
        with self.assertRaises(Problem):self.decide()
        with self.assertRaises(Problem):legacy.decide(self.store,self.h.tokens['assessor'],'assessor',legacy_id=KEY,approve=False,reason='无范围',decision_ref='bad')
        self.decide(approve=False)

    def test_old_completed_request_is_never_replayed(self):
        with self.store.transaction() as db:db.execute("UPDATE web_makeup_requests SET status='approved'")
        before=self.f.state()
        with self.assertRaises(Problem):self.decide()
        self.assertEqual(self.f.state(),before);self.assertFalse(self.rows()['items'][0]['can_decide'])

    def test_all_writes_rollback_if_final_source_decision_cannot_persist(self):
        before=self.f.state();original=self.store.record_audit
        def fail(db,actor,role,action,*args,**kw):
            if action=='legacy_makeup_approved':raise RuntimeError('fictional storage failure')
            return original(db,actor,role,action,*args,**kw)
        with patch.object(self.store,'record_audit',side_effect=fail):
            with self.assertRaises(RuntimeError):self.decide()
        self.assertEqual(self.f.state(),before);self.assertEqual(self.counts()['management_legacy_decisions'],0)

    def test_source_change_after_decision_does_not_allow_idempotent_replay(self):
        self.decide(approve=False)
        with self.store.transaction() as db:db.execute("UPDATE web_makeup_requests SET reason='外部来源变化'")
        with self.assertRaises(Problem):self.decide(approve=False)

    def test_new_http_route_requires_authentication_scope_and_handles_old_id(self):
        url='/v1/management/legacy-requests/'+KEY+'/decision';body=dict(workspace_role='admin',approve=True,reason='核对旧申请',decision_ref='http-old')
        self.assertEqual(self.client.post(url,json=body).status_code,401)
        r=self.client.post(url,json=body,headers={'Authorization':'Bearer '+self.h.tokens['teacher']});self.assertEqual(r.status_code,403)
        r=self.client.post(url,json=body,headers={'Authorization':'Bearer '+self.h.tokens['owner']});self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'approved')

if __name__=='__main__':unittest.main()
