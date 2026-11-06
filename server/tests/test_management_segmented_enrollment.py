import copy,json,os,shutil,sqlite3,subprocess,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from business.store import Problem
from management.approvals import digest
import test_management_enrollment_review as fixture

class SegmentedEnrollmentTests(unittest.TestCase):
    setUp=fixture.EnrollmentReviewTests.setUp
    source=fixture.EnrollmentReviewTests.source

    def package(self,source=None):
        source=source or self.source()
        manifest={k:dict(rows=len(v),pages=1 if v else 0,sha256=digest(v)) for k,v in source.items()}
        return dict(device_id=self.device['id'],generation='fixture-generation',baseline_sha256='c'*64,
                    source_captured_at=self.f.now,manifest=manifest,command_ref='review-one')

    def begin(self,**changes):
        return self.store.begin_enrollment_batch(self.f.tokens['owner'],'admin',**(self.package()|changes))

    def pages(self,batch,source=None):
        for section,rows in (source or self.source()).items():
            if rows:self.store.put_enrollment_page(self.f.tokens['owner'],'admin',batch_id=batch,section=section,page_index=0,rows=rows,sha256=digest(rows))

    def finish(self,batch):return self.store.finalize_enrollment_batch(self.f.tokens['owner'],'admin',batch_id=batch)

    def test_missing_pages_retry_restart_and_atomic_complete_review_without_business_changes(self):
        before=self.f.state();b=self.begin();self.assertTrue(self.begin()['duplicate'])
        with self.assertRaises(Problem) as e:self.finish(b['batch_id'])
        self.assertEqual(e.exception.code,'review_pages_missing');self.pages(b['batch_id']);self.pages(b['batch_id'])
        result=self.finish(b['batch_id']);self.assertFalse(result['duplicate']);self.assertTrue(self.finish(b['batch_id'])['duplicate'])
        self.assertEqual(before,self.f.state())
        # A fresh store/process sees the same persistent completion, not a new review.
        old=self.store;self.store=type(old)(self.f.path,lambda:self.f.now)
        self.assertEqual(self.finish(b['batch_id'])['review_id'],result['review_id'])
        proof=self.store.authorize_enrollment(self.f.tokens['owner'],'admin',review_id=result['review_id'],profile_id='a'*32,nonce='b'*32)
        self.assertTrue(proof['staged_only']);self.assertFalse(self.store.enrollment_plan(self.token,'fixture-generation')['enabled'])

    def test_changed_page_section_hash_and_reference_cannot_replace_evidence(self):
        b=self.begin()['batch_id'];self.pages(b);rows=copy.deepcopy(self.source()['students']);rows[0]['balance_units']=999
        with self.assertRaises(Problem) as e:self.store.put_enrollment_page(self.f.tokens['owner'],'admin',batch_id=b,section='students',page_index=0,rows=rows,sha256=digest(rows))
        self.assertEqual(e.exception.code,'review_page_reused')
        with self.assertRaises(Problem):self.begin(baseline_sha256='d'*64)
        with self.store.transaction() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE management_enrollment_pages SET sha256='wrong'")
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM management_enrollment_pages')
        other=self.package();other['manifest']['students']['sha256']='f'*64;other['command_ref']='bad-section'
        bad=self.store.begin_enrollment_batch(self.f.tokens['owner'],'admin',**other)['batch_id'];self.pages(bad)
        with self.assertRaises(Problem) as e:self.finish(bad)
        self.assertEqual(e.exception.code,'review_section_digest')

    def test_expiry_source_freshness_current_ledger_and_teacher_revocation_are_rechecked(self):
        for captured in (self.f.now-900,self.f.now+121,True):
            with self.assertRaises(Problem):self.begin(source_captured_at=captured)
        b=self.begin()['batch_id'];self.pages(b)
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=999')
        with self.assertRaises(Problem) as e:self.finish(b)
        self.assertEqual(e.exception.code,'migration_baseline_changed')
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=1000');db.execute("DELETE FROM roles WHERE user_id='teacher'")
        with self.assertRaises(Problem):self.finish(b)
        self.f.now+=900
        with self.assertRaises(Problem) as e:self.finish(b)
        self.assertEqual(e.exception.code,'review_batch_expired')

    def test_owner_workspace_actor_and_device_authentication_cannot_be_borrowed(self):
        b=self.begin()['batch_id']
        with self.store.transaction() as db:db.execute('INSERT INTO roles VALUES(?,?,?)',(self.f.org,'other-teacher','admin'));db.execute('INSERT INTO roles VALUES(?,?,?)',(self.f.org,self.f.owner,'teacher'))
        with self.assertRaises(Problem):self.store.begin_enrollment_batch(self.f.tokens['owner'],'teacher',**(self.package()|dict(command_ref='teacher-mode')))
        with self.assertRaises(Problem) as e:self.store.finalize_enrollment_batch(self.f.tokens['other-teacher'],'admin',batch_id=b)
        self.assertEqual(e.exception.status,404)
        with self.assertRaises(Problem):self.store.begin_enrollment_batch(self.token,'admin',**self.package())
        with self.store.transaction() as db:db.execute('UPDATE devices SET active=0')
        with self.assertRaises(Problem):self.finish(b)

    def test_final_audit_failure_rolls_back_both_review_and_batch_receipt(self):
        b=self.begin()['batch_id'];self.pages(b)
        with self.store.transaction() as db:db.execute("CREATE TRIGGER test_fail_batch_audit BEFORE INSERT ON management_audit WHEN NEW.action='enrollment_batch_finalized' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.finish(b)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_reviews').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state,review_id FROM management_enrollment_batches').fetchone()[:],('open',None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_pages').fetchone()[0],3)
            db.execute('DROP TRIGGER test_fail_batch_audit')
        self.assertTrue(self.finish(b)['ready_for_enrollment'])

    def test_page_limits_private_fields_and_exact_original_reconciliation_remain_enforced(self):
        b=self.begin()['batch_id'];source=self.source();source['students'][0]['name']='Different identity'
        self.pages(b,source)
        with self.assertRaises(Problem):self.finish(b)  # Section hash already differs; no partial success.
        malformed=copy.deepcopy(self.source()['students']);malformed[0]['face']='PRIVATE'
        with self.assertRaises(Problem):self.store.put_enrollment_page(self.f.tokens['owner'],'admin',batch_id=b,section='students',page_index=0,rows=malformed,sha256=digest(malformed))
        with self.assertRaises(Problem):self.store.put_enrollment_page(self.f.tokens['owner'],'admin',batch_id=b,section='students',page_index=True,rows=self.source()['students'],sha256=digest(self.source()['students']))
        source=self.source();source['students'][0]['balance_units']=999;params=self.package(source);params['command_ref']='balanced-but-wrong';other=self.store.begin_enrollment_batch(self.f.tokens['owner'],'admin',**params)['batch_id'];self.pages(other,source)
        with self.assertRaises(Problem) as e:self.finish(other)
        self.assertEqual(e.exception.code,'migration_source_differs')

    def test_parallel_finalize_produces_one_immutable_review(self):
        b=self.begin()['batch_id'];self.pages(b)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.finish(b),range(2)))
        self.assertEqual(len({r['review_id'] for r in results}),1);self.assertEqual(sum(r['duplicate'] for r in results),1)
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_reviews').fetchone()[0],1)

    def test_begin_audit_failure_has_no_orphan_batch_and_open_batch_count_is_bounded(self):
        with self.store.transaction() as db:db.execute("CREATE TRIGGER test_fail_start BEFORE INSERT ON management_audit WHEN NEW.action='enrollment_batch_started' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.begin()
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_enrollment_batches').fetchone()[0],0);db.execute('DROP TRIGGER test_fail_start')
        for n in range(3):self.begin(command_ref='batch-'+str(n))
        with self.assertRaises(Problem) as e:self.begin(command_ref='batch-over-limit')
        self.assertEqual(e.exception.code,'review_batch_limit')

    def test_empty_fact_section_and_declared_scope_limits_do_not_require_fake_rows(self):
        with self.store.transaction() as db:db.execute('DELETE FROM management_attendance')
        source=self.source();source['facts']=[];b=self.store.begin_enrollment_batch(self.f.tokens['owner'],'admin',**self.package(source))['batch_id'];self.pages(b,source)
        self.assertEqual(self.finish(b)['facts'],0)
        malformed=self.package(source);malformed['manifest']['students']['rows']=2001;malformed['manifest']['students']['pages']=21
        with self.assertRaises(Problem):self.store.begin_enrollment_batch(self.f.tokens['owner'],'admin',**malformed)

    def test_366_students_43_classes_node_pack_to_real_http_pages_respects_existing_16kb_limit(self):
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        if not node:self.skipTest('Node runtime required')
        source=self.source();classes=[source['classes'][0]];students=[source['students'][0]];facts=[source['facts'][0]]
        with self.store.transaction() as db:
            for i in range(1,43):
                cid='class-full-'+str(i);name='虚构全校班'+str(i)
                db.execute('INSERT INTO classes VALUES(?,?,?,?,?)',(cid,self.f.org,name,'虚构课程',self.f.now));db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(self.f.org,'teacher',cid));db.execute('INSERT INTO device_classes VALUES(?,?,?)',(self.f.org,self.device['id'],cid))
                classes.append(dict(server_id=cid,local_id=100+i,name=name,teacher_id='teacher',teacher_name='虚构人员'))
            for i in range(1,366):
                sid='student-full-'+str(i);c=classes[i%43];ref='F'+str(i).zfill(5);name='虚构学生'+str(i)+('😀' if i==1 else '')
                db.execute('INSERT INTO students VALUES(?,?,?,?,?,?,?,?,?)',(sid,self.f.org,c['server_id'],name,ref,1000,1,None,0));db.execute('INSERT INTO management_balances VALUES(?,?,?,?)',(self.f.org,sid,1000,0));db.execute('INSERT INTO management_device_students VALUES(?,?,?,?)',(self.f.org,sid,self.device['id'],c['server_id']));db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,?,?,?,?)',(self.f.org,'lesson-full',sid,c['server_id'],1900,1,100,'missing',0))
                students.append(dict(server_id=sid,local_id=1000+i,external_ref=ref,name=name,class_id=c['server_id'],class_local_id=c['local_id'],balance_units=1000));facts.append(dict(lesson_id='lesson-full',student_id=sid,status='missing',expected=True,charge_units=100,starts_at=1900))
        source=dict(classes=classes,students=students,facts=facts)
        body=dict(workspace_role='admin',device_id=self.device['id'],generation='fixture-generation',baseline_sha256='c'*64,source_captured_at=self.f.now,source=source)
        headers={'Authorization':'Bearer '+self.f.tokens['owner']}
        self.assertEqual(self.client.post('/v1/management/enrollment-reviews',headers=headers,json=body).status_code,413)
        script="const fs=require('fs');const b=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(require('./server/ops/pack-enrollment-review.cjs').pack(b,'whole-school')));"
        packed=json.loads(subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[2],input=json.dumps(body),encoding='utf8',capture_output=True,check=True).stdout)
        before=self.f.state();r=self.client.post('/v1/management/enrollment-batches',headers=headers,json=packed['start']);self.assertEqual(r.status_code,200,r.text);key=r.json()['batch_id']
        for page in reversed(packed['pages']):
            self.assertLessEqual(len(json.dumps(page,ensure_ascii=True).encode()),16384)
            r=self.client.post('/v1/management/enrollment-batches/'+key+'/pages',headers=headers,json=page);self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/v1/management/enrollment-batches/'+key+'/finalize',headers=headers,json=dict(workspace_role='admin'));self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['students'],366);self.assertEqual(r.json()['classes'],43);self.assertEqual(r.json()['facts'],366);self.assertEqual(before,self.f.state())
        self.assertEqual(self.client.post('/v1/management/enrollment-batches',headers={'Authorization':'Bearer '+self.token},json=packed['start']).status_code,403)

if __name__=='__main__':unittest.main()
