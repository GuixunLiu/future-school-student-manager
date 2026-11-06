import unittest
from business.store import Problem
import test_management_delivery as fixture


class EnrollmentTests(unittest.TestCase):
    setUp=fixture.DeliveryTests.setUp

    def proof(self):
        with self.store.transaction() as db:
            checksum=self.store.enrollment_ledger_digest(db,self.f.org,self.device['id'])
            db.execute('INSERT INTO management_enrollment_proofs VALUES(?,?,?,?,?,?,?,?,?)',
                       (self.f.org,self.device['id'],'fixture-generation','a'*32,'b'*32,'c'*64,checksum,8000,self.f.now))

    def test_missing_proof_never_grants_or_enrolls_and_users_cannot_fetch(self):
        with self.assertRaises(Problem) as error:
            self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertEqual(error.exception.code,'migration_proof_required')
        self.proof()
        with self.assertRaises(Problem):
            self.store.enrollment_plan(self.f.tokens['owner'],'fixture-generation')
        with self.assertRaises(Problem):
            self.store.enrollment_plan(self.token,'old-generation')

    def test_bound_plan_is_readonly_staged_and_expiry_is_shortest_grant(self):
        self.proof()
        with self.store.transaction() as db:
            db.execute('UPDATE management_device_access SET expires_at=7000')
        result=self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertFalse(result['enabled']);self.assertTrue(result['activation_required'])
        self.assertEqual(result['expires_at'],7000)
        self.assertEqual(result['baseline_sha256'],'c'*64)
        self.assertEqual(result['nonce'],'b'*32)
        self.assertEqual(result['profile_id'],'a'*32)
        self.assertNotIn('token',result)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_uploads').fetchone()[0],0)
        self.assertEqual(self.f.state()[:2],(('missing',1,0),(1000,0)))

    def test_expired_revoked_or_empty_scope_refuses_staging(self):
        self.proof()
        with self.store.transaction() as db:
            db.execute('UPDATE management_enrollment_proofs SET expires_at=?',(self.f.now,))
        with self.assertRaises(Problem):self.store.enrollment_plan(self.token,'fixture-generation')
        with self.store.transaction() as db:
            db.execute('UPDATE management_enrollment_proofs SET expires_at=8000')
            db.execute('DELETE FROM management_device_students')
        with self.assertRaises(Problem):self.store.enrollment_plan(self.token,'fixture-generation')

    def test_plan_http_auth_and_tenant_generation_are_server_derived(self):
        self.proof()
        path='/v1/management/device/plan?generation=fixture-generation'
        self.assertEqual(self.client.get(path).status_code,401)
        response=self.client.get(path,headers={'Authorization':'Bearer '+self.token})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['org_id'],self.f.org)
        self.assertEqual(response.json()['device_id'],self.device['id'])
        with self.store.transaction() as db:db.execute('UPDATE management_device_access SET enabled=0')
        self.assertEqual(self.client.get(path,headers={'Authorization':'Bearer '+self.token}).status_code,403)

    def test_new_server_checkin_invalidates_previously_verified_baseline(self):
        self.proof()
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=900,revision=1')
        with self.assertRaises(Problem) as error:self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertEqual(error.exception.code,'migration_baseline_changed')

    def test_teacher_change_or_student_identity_change_invalidates_proof(self):
        self.proof()
        with self.store.transaction() as db:db.execute("UPDATE students SET name='已改名' WHERE id='student'")
        with self.assertRaises(Problem) as error:self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertEqual(error.exception.code,'migration_baseline_changed')
        with self.store.transaction() as db:
            db.execute("UPDATE students SET name='虚构学生' WHERE id='student'")
            db.execute("DELETE FROM teacher_classes WHERE user_id='teacher'")
        with self.assertRaises(Problem) as error:self.store.enrollment_plan(self.token,'fixture-generation')
        self.assertEqual(error.exception.code,'migration_baseline_changed')
