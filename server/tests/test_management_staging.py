"""Full-copy admission uses real App calendar; staging is never activation."""
from contextlib import closing
import copy
import json
import unittest
from unittest.mock import patch

from business.store import Problem
from management.api import create_app
from management.approvals import digest
from management.delivery import DeliveryStore
from management.history_boundary import load
from management.migration_guard import require_not_held
import test_management_initialization as fixture


class StagingTests(unittest.TestCase):
    node=fixture.InitializationTests.node
    run_copy=fixture.InitializationTests.run_copy
    open_copy=fixture.InitializationTests.open_copy

    def setUp(self):
        fixture.InitializationTests.setUp(self);self.run_copy()
        with closing(self.open_copy()) as db:self.boundary=load(db,self.org)
        self.store=DeliveryStore(self.target/'rehearsal.sqlite',lambda:self.now)
        self.owner=self.store.login('fictional','owner','Fictional initialization test password 2026!','fixture-stage')['access_token']
        self.schedule=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(require('./server/ops/build-canonical-schedule.cjs').build(v.raw,v.plan,v.boundary,{now:v.now})));",dict(raw=self.source_raw,plan=self.plan,boundary=self.boundary,now=self.now))

    def stage(self,**changes):
        return self.store.stage_runtime(self.owner,'admin',**(dict(schedule=self.schedule,source_captured_at=self.now,reason='虚构已核对独立工作库',command_ref='stage-1')|changes))

    def snapshot(self):
        with self.store.transaction() as db:
            return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('users','roles','students','web_makeup_requests','management_balances','management_legacy_rows','management_openings','management_initializations')}

    def test_explicit_fresh_admission_retains_history_accounts_balances_and_keeps_writes_blocked(self):
        before=self.snapshot()
        with self.assertRaises(ValueError):create_app(self.store.path,lambda:self.now,allow_test_client=True)
        result=self.stage();self.assertFalse(result['activated']);self.assertFalse(result['balances_changed']);self.assertGreater(result['facts'],0)
        self.assertEqual(before,self.snapshot());self.assertTrue(self.stage()['duplicate'])
        app=create_app(self.store.path,lambda:self.now,allow_test_client=True);self.assertIsNotNone(app)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT DISTINCT charge_units FROM management_attendance').fetchall()[0][0],100)
            with self.assertRaises(Problem):require_not_held(db,self.org,self.now)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_activations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_device_access').fetchone()[0],0)

    def test_stale_source_wrong_identity_and_nonstandard_charge_leave_no_partial_admission(self):
        for change in (lambda s:s.update(baseline_sha256='f'*64),lambda s:s['source']['students'][0].update(balance_units=1),
                       lambda s:s['source']['classes'][0].update(teacher_name='Other'),lambda s:s['source']['facts'][0].update(charge_units=150),
                       lambda s:s['source']['facts'][0].update(starts_at=self.boundary['canonical_from']-1)):
            bad=copy.deepcopy(self.schedule);change(bad);bad['source_sha256']=digest(bad['source'])
            with self.assertRaises(Problem):self.stage(schedule=bad)
        for captured in (self.now-900,self.now+121):
            with self.assertRaises(Problem):self.stage(source_captured_at=captured)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_attendance').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_runtime_admissions').fetchone()[0],0)

    def test_immutable_admission_and_changed_retry_do_not_replace_facts(self):
        self.stage()
        with self.assertRaises(Problem):self.stage(command_ref='another')
        with self.store.transaction() as db:
            with self.assertRaises(Exception):db.execute("UPDATE management_runtime_admissions SET reason='changed'")
        before=self.snapshot()
        with self.store.transaction() as db:db.execute("UPDATE management_attendance SET status='arrived',revision=1")
        self.assertTrue(self.stage()['duplicate']);self.assertEqual(before,self.snapshot())
        with self.store.transaction() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM management_attendance WHERE status='missing'").fetchone()[0],0)

    def test_audit_failure_rolls_back_all_facts_and_staging_and_prior_device_scope_refuses(self):
        with patch.object(self.store,'record_audit',side_effect=RuntimeError('fictional disk failure')):
            with self.assertRaises(RuntimeError):self.stage()
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_attendance').fetchone()[0],0)
        self.store.command(self.owner,lambda db,actor:self.store.create_device(db,actor,dict(name='虚构旧设备',class_ids=[self.plan['classes'][0]['server_id']])))
        # A bare legacy device identity does not grant canonical access.
        result=self.stage();self.assertFalse(result['activated'])

    def test_full_copy_schedule_enrollment_bootstrap_activation_then_new_business_uses_one_ledger(self):
        before=self.snapshot();self.stage();source=self.schedule['source'];generation='5'*32
        device=self.store.command(self.owner,lambda db,actor:self.store.create_device(db,actor,dict(name='虚构正式待启用平板',class_ids=[c['server_id'] for c in source['classes']])))
        with self.store.transaction() as db:
            expiry=self.now+30*86400
            db.execute('UPDATE devices SET expires_at=? WHERE id=?',(expiry,device['id']))
            db.execute('INSERT INTO management_device_access VALUES(?,?,?,?,?)',(self.org,device['id'],generation,1,expiry))
            for s in source['students']:db.execute('INSERT INTO management_device_students VALUES(?,?,?,?)',(self.org,s['server_id'],device['id'],s['class_id']))
        review=self.store.review_enrollment(self.owner,'admin',device_id=device['id'],generation=generation,baseline_sha256=self.boundary['baseline_sha256'],source_captured_at=self.now,source=source)
        self.store.authorize_enrollment(self.owner,'admin',review_id=review['review_id'],profile_id='6'*32,nonce='7'*32)
        held=self.store.prepare_migration(self.owner,'admin',review_id=review['review_id'],profile_id='6'*32,reason='虚构双端确认前隔离',command_ref='hold-1',accept_database_mirror_pause=True)
        with self.store.transaction() as db:recorded=json.loads(db.execute('SELECT source_map FROM management_enrollment_reviews WHERE id=?',(review['review_id'],)).fetchone()[0])
        profile=dict(org_id=self.org,device_id=device['id'],generation=generation,profile_id='6'*32,baseline_sha256=self.boundary['baseline_sha256'])
        bootstrap=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(require('./server/ops/build-canonical-bootstrap.cjs').build(v.raw,v.source,v.profile)));",dict(raw=self.source_raw,source=recorded,profile=profile))
        offered=self.store.offer_activation(self.owner,'admin',hold_id=held['hold_id'],bootstrap_sha256=bootstrap['bootstrap_sha256'],source_map_sha256=digest(recorded),reason='虚构双端启用',command_ref='offer-1')
        with self.assertRaises(Problem):self.store.poll(device['device_token'],generation)
        plan=self.store.activation_plan(device['device_token'],generation,profile_id='6'*32)
        commit={k:plan[k] for k in ('activation_id','profile_id','nonce','bootstrap_sha256','source_map_sha256')}
        self.store.commit_activation(device['device_token'],generation,**commit)
        self.assertEqual(before,self.snapshot());self.assertIsNone(self.store.poll(device['device_token'],generation)['item'])
        # Only a new classroom arrival may debit; earlier sealed history never runs.
        fact=source['facts'][0];self.now=fact['starts_at']+1
        self.owner=self.store.login('fictional','owner','Fictional initialization test password 2026!','fixture-later-lesson')['access_token']
        request=self.store.submit(self.owner,'admin',lesson_id=fact['lesson_id'],student_id=fact['student_id'],kind='makeup',reason='虚构实际到课补签',request_ref='makeup-1',expected_revision=0)
        self.store.decide(self.owner,'admin',request_id=request['id'],approve=True,reason='虚构已核实到课',decision_ref='decision-1')
        with self.store.transaction() as db:
            units=db.execute('SELECT units,revision FROM management_balances WHERE org_id=? AND student_id=?',(self.org,fact['student_id'])).fetchone()
            original=next(s['balance_units'] for s in source['students'] if s['server_id']==fact['student_id'])
            self.assertEqual(tuple(units),(original-100,1))
        self.assertTrue(self.store.commit_activation(device['device_token'],generation,**commit)['duplicate'])
        item=self.store.poll(device['device_token'],generation)['item'];self.assertIsNotNone(item)
        event=json.loads(item['event_json']);self.assertEqual(event['balance_units'],original-100)
        self.store.acknowledge(device['device_token'],generation,event_id=event['event_id'],receipt_token=item['receipt_token'],sha256=item['sha256'],status='applied',attendance_revision=event['attendance_revision'],balance_revision=event['balance_revision'],balance_units=event['balance_units'])
        self.assertIsNone(self.store.poll(device['device_token'],generation)['item'])
        self.assertEqual(before['management_legacy_rows'],self.snapshot()['management_legacy_rows'])
        # Restarting the real API with a sealed admission preserves later business.
        self.assertIsNotNone(create_app(self.store.path,lambda:self.now,allow_test_client=True))

