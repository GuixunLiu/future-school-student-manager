"""Original deleted-class history -> sealed copy -> actual paged enrollment."""
from contextlib import closing
import copy
import json
import sqlite3
import unittest

from business.store import Problem
from management.delivery import DeliveryStore
from management.history_boundary import load
import test_management_initialization as fixture


class HistoryBoundaryTests(unittest.TestCase):
    node=fixture.InitializationTests.node
    run_copy=fixture.InitializationTests.run_copy
    open_copy=fixture.InitializationTests.open_copy

    def setUp(self):
        fixture.InitializationTests.setUp(self)
        self.run_copy()
        self.provision()

    def provision(self):
        with closing(self.open_copy()) as db:self.boundary=load(db,self.org)
        self.store=DeliveryStore(self.target/'rehearsal.sqlite',lambda:self.now)
        self.owner=self.store.login('fictional','owner','Fictional initialization test password 2026!','fictional-boundary')['access_token']
        self.device=self.store.command(self.owner,lambda db,actor:self.store.create_device(db,actor,dict(name='虚构隔离平板',class_ids=[c['server_id'] for c in self.plan['classes']])))
        local_classes={c['server_id']:c['local_id'] for c in self.plan['classes']}
        self.mapping=dict(protocol=1,org_id=self.org,device_id=self.device['id'],generation='fictional-boundary',classes=self.plan['classes'],
            students=[dict(server_id=s['server_id'],local_id=s['local_id'],external_ref=s['external_ref'],name=s['name'],class_id=s['current_class_id'],class_local_id=local_classes[s['current_class_id']]) for s in self.plan['students'] if s['status']=='active'],
            lessons=[dict(lesson_id='future-'+c['server_id'],class_id=c['server_id'],starts_at=self.boundary['canonical_from']+14*3600,charge_units=100,
                          student_ids=[s['server_id'] for s in self.plan['students'] if s['status']=='active' and s['current_class_id']==c['server_id']]) for c in self.plan['classes']])
        with self.store.transaction() as db:
            db.execute('INSERT INTO management_device_access VALUES(?,?,?,?,?)',(self.org,self.device['id'],self.mapping['generation'],1,self.now+86400))
            for s in self.mapping['students']:
                db.execute('INSERT INTO management_device_students VALUES(?,?,?,?)',(self.org,s['server_id'],self.device['id'],s['class_id']))
            for lesson in self.mapping['lessons']:
                for student in lesson['student_ids']:
                    db.execute("INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,'missing',0)",(self.org,lesson['lesson_id'],student,lesson['class_id'],lesson['starts_at']))

    def project(self,**changes):
        value=dict(raw=self.source_raw,mapping=self.mapping,boundary=self.boundary,now=self.now)|changes
        return self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8')),crypto=require('crypto'),raw=v.raw,capture={format:'future-school-native-capture-v1',sha256:crypto.createHash('sha256').update(raw).digest('hex'),bytes:Buffer.byteLength(raw),captured_at:v.now,payload_base64:Buffer.from(raw).toString('base64')};console.log(JSON.stringify(require('./server/ops/build-canonical-manifest.cjs').buildNativeBoundarySegmented(capture,v.mapping,v.boundary,'fictional-review',v.now)));",value)

    def source_body(self):
        p=self.project()
        return p,{section:[row for page in p['pages'] if page['section']==section for row in page['rows']] for section in ('classes','students','facts')}

    def direct(self,source=None,baseline=None):
        return self.store.review_enrollment(self.owner,'admin',device_id=self.device['id'],generation=self.mapping['generation'],
            baseline_sha256=baseline or self.boundary['baseline_sha256'],source_captured_at=self.now,source=source or self.source_body()[1])

    def test_deleted_class_history_seals_and_new_facts_complete_node_python_paged_review(self):
        p,source=self.source_body()
        self.assertTrue(p['metadata']['legacy_read_only']);self.assertFalse(p['metadata']['legacy_replayed'])
        self.assertTrue(all(f['starts_at']>=self.boundary['canonical_from'] and f['status']=='missing' and f['charge_units']==100 for f in source['facts']))
        with self.store.transaction() as db:before=[tuple(r) for r in db.execute('SELECT * FROM management_balances ORDER BY student_id')]
        begin=self.store.begin_enrollment_batch(self.owner,'admin',**{k:v for k,v in p['start'].items() if k!='workspace_role'})
        for page in reversed(p['pages']):self.store.put_enrollment_page(self.owner,batch_id=begin['batch_id'],role=page['workspace_role'],**{k:v for k,v in page.items() if k!='workspace_role'})
        result=self.store.finalize_enrollment_batch(self.owner,'admin',batch_id=begin['batch_id'])
        self.assertEqual(result['students'],len(self.mapping['students']));self.assertEqual(result['facts'],len(self.mapping['students']))
        self.assertTrue(self.store.finalize_enrollment_batch(self.owner,'admin',batch_id=begin['batch_id'])['duplicate'])
        self.store.authorize_enrollment(self.owner,'admin',review_id=result['review_id'],profile_id='3'*32,nonce='4'*32)
        self.assertFalse(self.store.enrollment_plan(self.device['device_token'],self.mapping['generation'])['enabled'])
        with self.store.transaction() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM management_balances ORDER BY student_id')])
            recorded=json.loads(db.execute('SELECT source_map FROM management_enrollment_reviews').fetchone()[0])
            self.assertEqual(recorded['history_boundary'],self.boundary)
            self.assertEqual(json.loads(db.execute("SELECT payload FROM management_legacy_rows WHERE section='attendance'").fetchone()[0])['classId'],99)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_uploads').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_outbox').fetchone()[0],0)

    def test_whole_scope_stable_local_ids_and_baseline_are_rechecked_on_server(self):
        _,source=self.source_body()
        for change in (lambda s:s['students'].pop(),lambda s:s['students'][0].update(local_id=999),lambda s:s['classes'][0].update(local_id=999),lambda s:s.update(facts=[])):
            bad=copy.deepcopy(source);change(bad)
            with self.assertRaises(Problem):self.direct(bad)
        with self.assertRaises(Problem):self.direct(baseline='1'*64)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_reviews').fetchone()[0],0)

    def test_legacy_lesson_and_cross_day_relabelling_refused(self):
        _,source=self.source_body();source['facts'][0]['starts_at']=self.boundary['canonical_from']-1
        with self.assertRaises(Problem):self.direct(source)
        self.now=self.boundary['canonical_from']
        with self.assertRaises(Problem):self.direct(self.source_body_before_expiry())

    def source_body_before_expiry(self):
        # Keep a valid old source but vary only the server/capture clock.
        balances={s['server_id']:s['opening_balance_units'] for s in self.plan['students']}
        return dict(classes=self.mapping['classes'],students=[s|dict(balance_units=balances[s['server_id']]) for s in self.mapping['students']],
                    facts=[dict(lesson_id=l['lesson_id'],student_id=s,status='missing',expected=True,charge_units=100,starts_at=l['starts_at']) for l in self.mapping['lessons'] for s in l['student_ids']])

    def test_corrupted_history_after_staging_invalidates_proof_without_replay(self):
        result=self.direct();self.store.authorize_enrollment(self.owner,'admin',review_id=result['review_id'],profile_id='3'*32,nonce='4'*32)
        with self.store.transaction() as db:
            db.execute('DROP TRIGGER management_legacy_rows_no_update')
            db.execute("UPDATE management_legacy_rows SET payload='{}' WHERE section='attendance'")
        with self.assertRaises(Problem) as error:self.store.enrollment_plan(self.device['device_token'],self.mapping['generation'])
        self.assertEqual(error.exception.code,'history_boundary_differs')

    def test_old_strict_manifest_still_rejects_unmapped_history(self):
        result=self.node("const v=JSON.parse(require('fs').readFileSync(0,'utf8')),crypto=require('crypto'),capture={format:'future-school-live-capture-v1',sha256:crypto.createHash('sha256').update(v.raw).digest('hex'),bytes:Buffer.byteLength(v.raw),captured_at:v.now};try{require('./server/ops/build-canonical-manifest.cjs').buildSegmented(v.raw,v.mapping,capture,'legacy-path',v.now);console.log(JSON.stringify({ok:true}));}catch(e){console.log(JSON.stringify({code:e.code}));}",dict(raw=self.source_raw,mapping=self.mapping,now=self.now))
        self.assertEqual(result['code'],'unmapped_history')


class WholeSchoolBoundaryTests(unittest.TestCase):
    node=HistoryBoundaryTests.node
    run_copy=HistoryBoundaryTests.run_copy
    open_copy=HistoryBoundaryTests.open_copy
    provision=HistoryBoundaryTests.provision
    project=HistoryBoundaryTests.project
    source_body=HistoryBoundaryTests.source_body

    def setUp(self):
        fixture.InitializationTests.setUp(self)
        fixture.InitializationTests.test_whole_school_366_active_and_43_classes_copy_without_replaying_history(self)
        self.provision()

    def test_whole_366_43_history_boundary_and_actual_paged_review(self):
        self.assertEqual(len(self.mapping['students']),366);self.assertEqual(len(self.mapping['classes']),43)
        HistoryBoundaryTests.test_deleted_class_history_seals_and_new_facts_complete_node_python_paged_review(self)


if __name__=='__main__':unittest.main()
