import copy,unittest
from business.store import Problem
import test_management_delivery as fixture


class EnrollmentReviewTests(unittest.TestCase):
    setUp=fixture.DeliveryTests.setUp
    def source(self):
        return dict(classes=[dict(server_id='class-a',local_id=2,name='虚构班甲',teacher_id='teacher',teacher_name='虚构人员')],
                    students=[dict(server_id='student',local_id=1,external_ref='TEST',name='虚构学生',class_id='class-a',class_local_id=2,balance_units=1000)],
                    facts=[dict(lesson_id='lesson',student_id='student',status='missing',expected=True,charge_units=150,starts_at=1900)])
    def review(self,source=None,who='owner',role='admin'):
        return self.store.review_enrollment(self.f.tokens[who],role,device_id=self.device['id'],generation='fixture-generation',baseline_sha256='c'*64,source_captured_at=self.f.now,source=source or self.source())
    def authorize(self,key,**changes):
        return self.store.authorize_enrollment(self.f.tokens['owner'],'admin',**(dict(review_id=key,profile_id='a'*32,nonce='b'*32)|changes))

    def test_review_authorization_and_plan_complete_without_any_debit_or_grant(self):
        before=self.f.state();review=self.review();self.assertTrue(review['ready_for_enrollment'])
        result=self.authorize(review['review_id']);self.assertTrue(result['staged_only']);self.assertFalse(result['duplicate'])
        self.assertTrue(self.authorize(review['review_id'])['duplicate'])
        plan=self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertEqual(plan['baseline_sha256'],'c'*64);self.assertFalse(plan['enabled']);self.assertTrue(plan['activation_required'])
        self.assertEqual(before,self.f.state())
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM devices').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_proofs').fetchone()[0],1)

    def test_wrong_identity_balance_teacher_missing_history_and_duplicate_ids_stop_review(self):
        changes=[lambda s:s['students'][0].update(name='同名误匹配'),lambda s:s['students'][0].update(balance_units=999),
                 lambda s:s['students'][0].update(external_ref='OTHER'),lambda s:s['classes'][0].update(teacher_id='other-teacher'),
                 lambda s:s.update(facts=[]),lambda s:s['facts'][0].update(expected=1),
                 lambda s:s['students'].append(copy.deepcopy(s['students'][0])),lambda s:s['students'][0].update(local_id=True)]
        for change in changes:
            source=self.source();change(source)
            with self.assertRaises(Problem):self.review(source)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_reviews').fetchone()[0],0)
        self.assertEqual(self.f.state()[:2],(('missing',1,0),(1000,0)))

    def test_changed_ledger_or_expired_review_cannot_issue_proof(self):
        review=self.review()
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=900,revision=1')
        with self.assertRaises(Problem) as error:self.authorize(review['review_id'])
        self.assertEqual(error.exception.code,'migration_baseline_changed')
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=1000,revision=0')
        self.f.now+=900
        with self.assertRaises(Problem) as error:self.authorize(review['review_id'])
        self.assertEqual(error.exception.code,'review_expired')

    def test_wrong_workspace_revoked_scope_and_conflicting_profile_refused(self):
        with self.assertRaises(Problem):self.review(who='teacher',role='teacher')
        review=self.review();self.authorize(review['review_id'])
        with self.assertRaises(Problem):self.authorize(review['review_id'],profile_id='d'*32)
        with self.store.transaction() as db:db.execute('DELETE FROM device_classes WHERE device_id=?',(self.device['id'],))
        with self.assertRaises(Problem):self.review()

    def test_revoked_teacher_role_invalidates_review_before_authorization(self):
        review=self.review()
        with self.store.transaction() as db:db.execute("DELETE FROM roles WHERE user_id='teacher' AND role='teacher'")
        with self.assertRaises(Problem) as error:self.authorize(review['review_id'])
        self.assertEqual(error.exception.code,'migration_baseline_changed')

    def test_review_and_proof_audit_failures_roll_back(self):
        with self.store.transaction() as db:db.execute("CREATE TRIGGER fail_review_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):self.review()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_reviews').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_review_audit')
        review=self.review()
        with self.store.transaction() as db:db.execute("CREATE TRIGGER fail_proof_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):self.authorize(review['review_id'])
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_proofs').fetchone()[0],0)

    def test_http_review_then_authorize_and_student_token_cannot_impersonate_admin(self):
        headers={'Authorization':'Bearer '+self.f.tokens['owner']}
        body=dict(workspace_role='admin',device_id=self.device['id'],generation='fixture-generation',baseline_sha256='c'*64,source_captured_at=self.f.now,source=self.source())
        r=self.client.post('/v1/management/enrollment-reviews',headers=headers,json=body);self.assertEqual(r.status_code,200,r.text)
        approval=dict(workspace_role='admin',review_id=r.json()['review_id'],profile_id='a'*32,nonce='b'*32)
        r=self.client.post('/v1/management/enrollment-authorizations',headers=headers,json=approval);self.assertEqual(r.status_code,200,r.text)
        approval['org_id']=self.f.org
        self.assertEqual(self.client.post('/v1/management/enrollment-authorizations',headers=headers,json=approval).status_code,422)
        self.assertEqual(self.client.post('/v1/management/enrollment-reviews',headers={'Authorization':'Bearer '+self.token},json=body).status_code,403)

    def test_old_or_future_capture_cannot_be_relabelled_as_a_fresh_review(self):
        for captured in (self.f.now-900,self.f.now+121,True):
            with self.assertRaises(Problem) as error:
                self.store.review_enrollment(self.f.tokens['owner'],'admin',device_id=self.device['id'],generation='fixture-generation',baseline_sha256='c'*64,source_captured_at=captured,source=self.source())
            self.assertEqual(error.exception.code,'source_not_fresh')

    def test_expired_staging_proof_requires_fresh_review_and_renews_without_widening_scope(self):
        first=self.review();self.authorize(first['review_id']);self.f.now+=900
        with self.assertRaises(Problem):self.authorize(first['review_id'])
        fresh=self.review();renewed=self.authorize(fresh['review_id'])
        self.assertTrue(renewed['renewed']);self.assertGreater(renewed['expires_at'],self.f.now)
        self.assertTrue(self.authorize(fresh['review_id'])['duplicate'])
        self.assertEqual(self.store.enrollment_plan(self.token,'fixture-generation')['baseline_sha256'],'c'*64)
