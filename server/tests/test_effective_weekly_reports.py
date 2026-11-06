"""Approved corrections update weekly totals; confirmed makeup resolves its cause."""
import copy
import hashlib
import json
import unittest
from management.approvals import encoded
from test_historical_requests import HistoricalTests

class EffectiveWeeklyTests(unittest.TestCase):
    def setUp(self):
        self.h=HistoricalTests();self.h.setUp();self.addCleanup(self.h.doCleanups);self.store=self.h.store;self.f=self.h.base
        group=dict(name='全部',expected=1,arrived=0,leave_count=0,rate='0%',needs_review=False)
        self.data=copy.deepcopy(self.h.original);self.data['weeks']=[dict(group,start='2026-10-26',end='2026-11-01',fallback=False,classes=[dict(group,name='原历史班')],teachers=[dict(group,name='原任教师')])]
        self.save()
    def save(self):
        with self.store.transaction() as db:db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(self.data),))
    def report(self,role='admin'):
        return self.store.weekly_report(self.h.tokens['owner' if role=='admin' else 'teacher'],role,start='2026-10-26')
    def test_pending_does_not_count_approved_makeup_updates_all_three_scopes_once(self):
        r=self.h.submit();self.assertEqual(self.report()['sections'][0]['report']['arrived'],0);self.h.decide(r['id']);self.h.decide(r['id'])
        section=self.report()['sections'][0];self.assertTrue(section['includes_approved_corrections']);self.assertEqual(section['original_report']['arrived'],0)
        for group in [section['report'],section['report']['classes'][0],section['report']['teachers'][0]]:self.assertEqual((group['expected'],group['arrived'],group['rate']),(1,1,'100%'))
        teacher=self.report('teacher')['sections'][0]['report'];self.assertEqual((teacher['arrived'],teacher['rate']),(1,'100%'))
        self.assertEqual(self.report()['sections'][-1]['makeup_count'],1);self.assertTrue(self.report()['sections'][-1]['included_in_report'])
        with self.store.transaction() as db:self.assertEqual(json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0]),self.data)
    def test_approved_leave_updates_leave_totals_and_keeps_expected_denominator(self):
        r=self.h.submit(kind='leave');self.h.decide(r['id']);report=self.report()['sections'][0]['report'];self.assertEqual((report['expected'],report['arrived'],report['leave_count'],report['rate']),(1,0,1,'0%'))
    def temporary(self):
        l=copy.deepcopy(self.data['lessons'][0]);l.update(key='11:2026-11-01',date='2026-11-01',expected=0,arrived=1,needs_review=True,members=[dict(student_local_id='2',name='虚构临时补课学生',expected=False,status='arrived')]);self.data['lessons'].append(l)
        for g in [self.data['weeks'][0],*self.data['weeks'][0]['classes'],*self.data['weeks'][0]['teachers']]:g.update(arrived=1,rate='待核对',needs_review=True)
        self.save();return l
    def confirm(self,l,checksum=None):
        with self.store.transaction() as db:
            raw=db.execute('SELECT data_json FROM web_dataset').fetchone()[0]
            detail=dict(format='future-school-temporary-makeup-review-v1',source_sha256=checksum or hashlib.sha256(raw.encode()).hexdigest(),expected_student_ids=['2'])
            db.execute('INSERT INTO management_audit(org_id,actor_id,workspace_role,action,entity_id,detail,created_at) VALUES(?,?,?,?,?,?,?)',(self.f.org,self.f.owner,'admin','historical_temporary_makeup_confirmed','legacy:'+l['key'],encoded(detail),self.f.now))
    def test_confirmed_individual_makeup_adds_one_expected_and_clears_only_resolved_flag(self):
        l=self.temporary();self.assertEqual(self.report()['sections'][0]['report']['rate'],'待核对');self.confirm(l)
        r=self.h.submit();self.h.decide(r['id']);section=self.report()['sections'][0]
        for group in [section['report'],section['report']['classes'][0],section['report']['teachers'][0]]:self.assertEqual((group['expected'],group['arrived'],group['rate'],group['needs_review']),(2,2,'100%',False))
        self.assertEqual(section['review_issues'],[]);self.assertEqual(section['original_report']['rate'],'待核对')
        day=self.store.attendance_day(self.h.tokens['owner'],'admin',on='2026-11-01');self.assertEqual((day['lessons'][0]['expected'],day['lessons'][0]['arrived']),(1,1))
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT units FROM management_balances').fetchone()[0],900)
    def test_wrong_source_confirmation_does_not_clear_flag_or_add_expected(self):
        l=self.temporary();self.confirm(l,'0'*64);section=self.report()['sections'][0];self.assertEqual(section['report']['expected'],1);self.assertEqual(section['report']['rate'],'待核对');self.assertIn('临时补课',section['review_issues'][0]['reason'])
    def test_other_unresolved_session_and_incomplete_baseline_cannot_be_silently_cleared(self):
        l=self.temporary();other=copy.deepcopy(l);other.update(key='11:2026-10-30',date='2026-10-30');self.data['lessons'].append(other)
        for g in [self.data['weeks'][0],*self.data['weeks'][0]['classes'],*self.data['weeks'][0]['teachers']]:g['arrived']=2
        self.save();self.confirm(l);section=self.report()['sections'][0];self.assertEqual(section['report']['rate'],'待核对');self.assertEqual(len(section['review_issues']),1);self.assertEqual(section['review_issues'][0]['date'],'2026-10-30')

if __name__=='__main__':unittest.main()
