"""Activation, exact retry after business and pause/resume without restoration."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from business.store import Problem
from management.approvals import digest
import test_management_migration as fixture


class ActivationTests(unittest.TestCase):
    setUp=fixture.MigrationTests.setUp
    source=fixture.MigrationTests.source
    review=fixture.MigrationTests.review
    authorize=fixture.MigrationTests.authorize
    prepared_args=fixture.MigrationTests.prepared_args
    prepare=fixture.MigrationTests.prepare
    cancel=fixture.MigrationTests.cancel
    upload=fixture.MigrationTests.upload

    def offer(self,**changes):
        held=self.prepare()
        with self.store.transaction() as db:source=json.loads(db.execute('SELECT source_map FROM management_enrollment_reviews').fetchone()[0])
        self.args=dict(hold_id=held['hold_id'],bootstrap_sha256='b'*64,source_map_sha256=digest(source),reason='虚构两端启用',command_ref='offer-1')|changes
        self.held=held;self.offered=self.store.offer_activation(self.f.tokens['owner'],'admin',**self.args)
        return self.offered

    def plan(self):return self.store.activation_plan(self.token,'fixture-generation',profile_id='a'*32)

    def commit(self,**changes):
        p=self.plan()
        args={k:p[k] for k in ('activation_id','profile_id','nonce','bootstrap_sha256','source_map_sha256')}
        return self.store.commit_activation(self.token,'fixture-generation',**(args|changes))

    def change(self,action,**changes):
        status=self.store.activation_status(self.f.tokens['owner'],'admin',self.offered['activation_id'])
        args=dict(activation_id=status['activation_id'],action=action,expected_revision=status['revision'],ledger_sha256=status['ledger_sha256'],reason='虚构核对后'+action,command_ref=action+'-1')|changes
        return self.store.change_activation(self.f.tokens['owner'],'admin',**args)

    def test_offer_is_dormant_until_device_saved_commit_then_old_hold_never_cancels(self):
        before=self.f.state();offer=self.offer();self.assertEqual(offer['phase'],'offered')
        self.assertTrue(self.store.offer_activation(self.f.tokens['owner'],'admin',**self.args)['duplicate'])
        with self.assertRaises(Problem):self.store.poll(self.token,'fixture-generation')
        result=self.commit();self.assertEqual(result['phase'],'active');self.assertFalse(result['balances_changed'])
        self.assertEqual(before,self.f.state());self.assertIsNone(self.store.poll(self.token,'fixture-generation')['item'])
        with self.assertRaises(Problem) as error:self.cancel(self.held['hold_id'])
        self.assertEqual(error.exception.code,'activation_recovery_required')
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM management_migration_holds').fetchone()[0],'held')

    def test_lost_commit_response_retry_after_web_business_never_reapplies_balance(self):
        self.offer();p=self.plan();self.commit();request=self.f.submit();self.f.decide(request['id']);before=self.f.state()
        args={k:p[k] for k in ('activation_id','profile_id','nonce','bootstrap_sha256','source_map_sha256')}
        self.assertTrue(self.store.commit_activation(self.token,'fixture-generation',**args)['duplicate'])
        self.assertEqual(before,self.f.state());self.assertEqual(self.f.state()[1],(850,1))
        with self.store.transaction() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM management_audit WHERE action='activation_committed'").fetchone()[0],1)

    def test_pause_resume_after_business_preserves_new_balances_and_old_mirror_fence(self):
        self.offer();self.commit();request=self.f.submit();self.f.decide(request['id']);before=self.f.state()
        paused=self.change('pause');self.assertEqual(paused['phase'],'paused')
        for call in (lambda:self.store.poll(self.token,'fixture-generation'),self.upload,self.commit):
            with self.assertRaises(Problem) as error:call()
            self.assertEqual(error.exception.code,'migration_in_progress')
        resumed=self.change('resume');self.assertEqual(resumed['phase'],'active');self.assertFalse(resumed['balances_restored'])
        self.assertEqual(before,self.f.state());self.assertIsNotNone(self.store.poll(self.token,'fixture-generation')['item'])
        with self.assertRaises(Problem):self.cancel(self.held['hold_id'])

    def test_revoked_device_stops_further_approval_effects_until_reconciled(self):
        self.offer();self.commit();request=self.f.submit();before=self.f.state()
        with self.store.transaction() as db:db.execute('UPDATE management_device_access SET enabled=0')
        with self.assertRaises(Problem) as error:self.f.decide(request['id'])
        self.assertEqual(error.exception.code,'migration_in_progress');self.assertEqual(before,self.f.state())

    def test_renewal_updates_only_matching_active_grant_and_can_retry(self):
        offer=self.offer();self.commit()
        expiry=offer['active_until']
        with self.store.transaction() as db:
            db.execute('UPDATE devices SET expires_at=?',(expiry,))
            db.execute('UPDATE management_device_access SET expires_at=?',(expiry,))
        before=self.f.state()
        args=dict(activation_id=offer['activation_id'],expected_expiry=expiry,
                  expires_at=expiry+86400,reason='续期验收',command_ref='renew-1')
        with self.assertRaises(Problem):
            self.store.renew_active_writer(self.f.tokens['teacher'],**args)
        with self.assertRaises(Problem):
            self.store.renew_active_writer(self.f.tokens['owner'],**(args|{'expected_expiry':expiry+1}))
        self.assertFalse(self.store.renew_active_writer(self.f.tokens['owner'],**args)['duplicate'])
        self.assertTrue(self.store.renew_active_writer(self.f.tokens['owner'],**args)['duplicate'])
        self.f.now=expiry+1
        self.assertIsNone(self.store.poll(self.token,'fixture-generation')['item'])
        self.assertEqual(self.f.state(),before)

    def test_wrong_profile_digest_nonce_device_and_role_cannot_activate(self):
        self.offer()
        for changes in (dict(profile_id='f'*32),dict(nonce='f'*32),dict(bootstrap_sha256='f'*64),dict(source_map_sha256='f'*64)):
            with self.assertRaises(Problem):self.commit(**changes)
        with self.assertRaises(Problem):self.store.offer_activation(self.f.tokens['teacher'],'teacher',**(self.args|dict(command_ref='teacher-offer')))
        with self.assertRaises(Problem):self.store.activation_plan(self.f.tokens['owner'],'fixture-generation',profile_id='a'*32)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT phase FROM management_activations').fetchone()[0],'offered')

    def test_expired_offer_or_revoked_admin_keeps_all_writes_paused(self):
        self.offer()
        with self.store.transaction() as db:db.execute("DELETE FROM roles WHERE user_id=? AND role='admin'",(self.f.owner,))
        with self.assertRaises(Problem):self.commit()
        with self.store.transaction() as db:db.execute("INSERT INTO roles VALUES(?,?,'admin')",(self.f.org,self.f.owner))
        self.f.now=self.offered['offer_expires_at']
        with self.assertRaises(Problem) as error:self.plan()
        self.assertEqual(error.exception.code,'activation_offer_expired')
        with self.assertRaises(Problem):self.cancel(self.held['hold_id'])
        with self.assertRaises(Problem):self.store.poll(self.token,'fixture-generation')

    def test_changed_data_before_commit_and_stale_recovery_do_not_restore(self):
        self.offer()
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=999')
        with self.assertRaises(Problem):self.commit()
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=1000')
        self.commit();self.change('pause')
        status=self.store.activation_status(self.f.tokens['owner'],'admin',self.offered['activation_id'])
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=900,revision=1')
        with self.assertRaises(Problem):self.change('resume',ledger_sha256=status['ledger_sha256'])
        self.assertEqual(self.f.state()[1],(900,1))
        self.assertEqual(self.change('resume')['phase'],'active');self.assertEqual(self.f.state()[1],(900,1))

    def test_audit_failure_rolls_back_device_commit_and_concurrent_retry_commits_once(self):
        self.offer()
        with self.store.transaction() as db:db.execute("CREATE TRIGGER test_fail_activation BEFORE INSERT ON management_audit WHEN NEW.action='activation_committed' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):self.commit()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT phase FROM management_activations').fetchone()[0],'offered');db.execute('DROP TRIGGER test_fail_activation')
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.commit(),range(2)))
        self.assertEqual(sum(r['duplicate'] for r in results),1)

    def test_active_expiry_revoked_device_and_wrong_generation_cannot_resume_writes(self):
        self.offer();self.commit()
        with self.store.transaction() as db:db.execute('UPDATE management_activations SET active_until=?',(self.f.now,))
        with self.assertRaises(Problem):self.store.poll(self.token,'fixture-generation')
        with self.store.transaction() as db:db.execute('UPDATE management_activations SET active_until=9999');db.execute('UPDATE devices SET active=0 WHERE id=?',(self.device['id'],))
        with self.assertRaises(Problem):self.plan()

    def test_http_current_role_device_commit_and_unknown_fields(self):
        self.offer();headers={'Authorization':'Bearer '+self.token}
        response=self.client.get('/v1/management/device/activation',params=dict(generation='fixture-generation',profile_id='a'*32),headers=headers)
        self.assertEqual(response.status_code,200,response.text);p=response.json()
        body={k:p[k] for k in ('generation','activation_id','profile_id','nonce','bootstrap_sha256','source_map_sha256')}
        self.assertEqual(self.client.post('/v1/management/device/activation-commit',json=body|dict(org_id=self.f.org),headers=headers).status_code,422)
        response=self.client.post('/v1/management/device/activation-commit',json=body,headers=headers);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['phase'],'active')


if __name__=='__main__':unittest.main()
