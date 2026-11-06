import unittest
import hashlib,json
from concurrent.futures import ThreadPoolExecutor
from business.store import Problem
from teacher_web import mirror_store
import test_management_enrollment_review as fixture
import test_management_uplink as uplink


class MigrationTests(unittest.TestCase):
    setUp=fixture.EnrollmentReviewTests.setUp
    source=fixture.EnrollmentReviewTests.source
    review=fixture.EnrollmentReviewTests.review
    authorize=fixture.EnrollmentReviewTests.authorize

    def prepared_args(self):
        review=self.review();self.authorize(review['review_id'])
        return dict(review_id=review['review_id'],profile_id='a'*32,reason='虚构切换前核对',command_ref='prepare-1',accept_database_mirror_pause=True)
    def prepare(self,args=None,who='owner',role='admin'):
        return self.store.prepare_migration(self.f.tokens[who],role,**(args or self.prepared_args()))
    def cancel(self,key,**changes):
        return self.store.cancel_migration(self.f.tokens['owner'],'admin',**(dict(hold_id=key,reason='虚构取消准备',command_ref='cancel-1')|changes))
    def upload(self):
        return self.store.upload_attendance(self.token,**uplink.UplinkTests.payload(self))
    def denied(self,call,code):
        with self.assertRaises(Problem) as error:call()
        self.assertEqual(error.exception.code,code)

    def test_prepare_pause_and_cancel_are_metadata_only_and_retries_do_not_reenter(self):
        before=self.f.state();args=self.prepared_args();held=self.prepare(args)
        self.assertTrue(held['writes_paused']);self.assertFalse(held['activated']);self.assertFalse(held['automatic_release'])
        self.assertEqual(held['mirror_pause_scope'],'database');self.assertTrue(self.prepare(args)['duplicate'])
        self.assertEqual(before,self.f.state())
        cancelled=self.cancel(held['hold_id']);self.assertEqual(cancelled['state'],'cancelled');self.assertFalse(cancelled['writes_paused'])
        self.assertTrue(self.cancel(held['hold_id'])['duplicate']);self.assertEqual(self.prepare(args)['state'],'cancelled')
        self.assertEqual(before,self.f.state());self.assertIsNone(self.store.poll(self.token,'fixture-generation')['item'])

    def test_hold_blocks_upload_download_ack_and_approval_but_rejection_is_allowed(self):
        request=self.f.submit();held=self.prepare();before=self.f.state()
        for call in (self.upload,lambda:self.store.poll(self.token,'fixture-generation'),lambda:self.f.decide(request['id']),
                     lambda:self.store.acknowledge(self.token,'fixture-generation',event_id='event',receipt_token='a'*32,sha256='b'*64,status='applied',attendance_revision=1,balance_revision=1,balance_units=850)):
            self.denied(call,'migration_in_progress')
        self.assertEqual(before,self.f.state())
        self.assertEqual(self.f.decide(request['id'],approve=False)['status'],'rejected')
        self.cancel(held['hold_id']);self.upload();self.assertEqual(self.f.state()[1],(850,1))

    def test_proof_expiry_does_not_automatically_restore_old_writes(self):
        held=self.prepare();self.f.now+=901
        self.denied(self.upload,'migration_in_progress');self.denied(lambda:self.store.poll(self.token,'fixture-generation'),'migration_in_progress')
        self.assertEqual(self.cancel(held['hold_id'])['state'],'cancelled')

    def test_changed_frozen_ledger_preserves_hold_and_does_not_restore_balances(self):
        held=self.prepare()
        with self.store.transaction() as db:db.execute('UPDATE management_balances SET units=999,revision=1')
        self.denied(lambda:self.cancel(held['hold_id']),'migration_frozen_data_changed')
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM management_migration_holds').fetchone()[0],'held')
        self.assertEqual(self.f.state()[1],(999,1))

    def test_unselected_class_change_also_prevents_unsafe_database_wide_resume(self):
        held=self.prepare()
        with self.store.transaction() as db:
            original=db.execute("SELECT name FROM classes WHERE id='class-b'").fetchone()[0]
            db.execute("UPDATE classes SET name='changed' WHERE id='class-b'")
        self.denied(lambda:self.cancel(held['hold_id']),'migration_frozen_data_changed')
        with self.store.transaction() as db:
            db.execute("UPDATE classes SET name=? WHERE id='class-b'",(original,))
            db.execute("UPDATE users SET display_name='changed-teacher' WHERE id='teacher'")
        self.denied(lambda:self.cancel(held['hold_id']),'migration_frozen_data_changed')

    def test_actual_old_mirror_same_database_is_fenced_until_unchanged_cancellation(self):
        mirror_store.initialize(self.f.path);mirror_device='d'*32;mirror_token='wxd_'+'B'*43
        summary=dict(name='fictional',expected=0,arrived=0,leave_count=0,rate='0%',needs_review=False)
        data=dict(format='future-school-teacher-web-dataset',schema_version=1,as_of_date='1970-01-01',source_captured_at='1970-01-01',source_records=0,
                  classes=[],students=[],weeks=[summary|dict(offset=0,start='1969-12-29',end='1970-01-04',fallback=False,classes=[],teachers=[])],lessons=[],
                  history_import_is_read_only=True,balances_charged=False,wechat_enabled=False)
        def payload(n):return json.dumps(dict(protocol=1,device_id=mirror_device,revision=n,request_ref=format(n,'032x'),data=data)).encode()
        with self.store.transaction() as db:
            db.execute('CREATE TABLE web_dataset(id INTEGER PRIMARY KEY,data_json TEXT,source_sha256 TEXT,imported_at INTEGER)')
            db.execute("INSERT INTO web_dataset VALUES(1,'{}','initial',0)")
            db.execute('INSERT INTO web_mirror_devices VALUES(?,?,?,?,?,?,?,?)',(mirror_device,self.f.org,hashlib.sha256(mirror_token.encode()).hexdigest(),1,9999,0,None,None))
        mirror_store.receive(self.f.path,mirror_token,payload(1),self.f.now);held=self.prepare();before=self.f.state()
        for n in (1,2):
            with self.assertRaises(mirror_store.MirrorError) as error:mirror_store.receive(self.f.path,mirror_token,payload(n),self.f.now)
            self.assertEqual(error.exception.code,'migration_in_progress')
        self.cancel(held['hold_id']);self.assertEqual(mirror_store.receive(self.f.path,mirror_token,payload(2),self.f.now)['revision'],2)
        self.assertEqual(before,self.f.state())

    def test_wrong_role_scope_reused_reference_and_missing_confirmation_are_rejected(self):
        args=self.prepared_args()
        self.denied(lambda:self.prepare(args,who='teacher',role='teacher'),'permission_denied')
        self.denied(lambda:self.prepare(args|{'accept_database_mirror_pause':False}),'mirror_pause_confirmation_required')
        held=self.prepare(args)
        self.denied(lambda:self.prepare(args|{'reason':'different'}),'idempotency_conflict')
        self.denied(lambda:self.prepare(args|{'command_ref':'other'}),'migration_already_held')
        self.denied(lambda:self.cancel('other-org-id'),'migration_missing')
        self.cancel(held['hold_id']);self.denied(lambda:self.cancel(held['hold_id'],reason='different'),'idempotency_conflict')

    def test_changed_or_expired_review_cannot_pause(self):
        args=self.prepared_args()
        with self.store.transaction() as db:db.execute("UPDATE roles SET role='admin' WHERE user_id='teacher' AND role='teacher'")
        self.denied(lambda:self.prepare(args),'migration_baseline_changed')
        self.f.now+=900;self.denied(lambda:self.prepare(args),'review_expired')
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_migration_holds').fetchone()[0],0)

    def test_audit_failures_roll_back_both_prepare_and_cancel(self):
        args=self.prepared_args()
        with self.store.transaction() as db:db.execute("CREATE TRIGGER fail_hold BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'test');END")
        with self.assertRaises(Exception):self.prepare(args)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_migration_holds').fetchone()[0],0);db.execute('DROP TRIGGER fail_hold')
        held=self.prepare(args)
        with self.store.transaction() as db:db.execute("CREATE TRIGGER fail_hold BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'test');END")
        with self.assertRaises(Exception):self.cancel(held['hold_id'])
        with self.store.transaction() as db:self.assertEqual(db.execute('SELECT state FROM management_migration_holds').fetchone()[0],'held')

    def test_prepare_and_upload_race_cannot_both_succeed(self):
        args=self.prepared_args()
        def attempt(call):
            try:return call()
            except Problem as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[lambda:self.prepare(args),self.upload]))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertTrue('migration_baseline_changed' in results or 'migration_in_progress' in results)
        self.assertIn(self.f.state()[1],[(1000,0),(850,1)])

    def test_http_admin_only_prepare_status_cancel_and_extra_org_field_denied(self):
        args=self.prepared_args();headers={'Authorization':'Bearer '+self.f.tokens['owner']};body=args|{'workspace_role':'admin'}
        url='/v1/management/migration-holds'
        bad=self.client.post(url,headers=headers,json=body|{'org_id':'other'});self.assertEqual(bad.status_code,422)
        response=self.client.post(url,headers=headers,json=body);self.assertEqual(response.status_code,200,response.text);key=response.json()['hold_id']
        self.assertEqual(self.client.get(url+'?workspace_role=admin',headers=headers).json()['items'][0]['hold_id'],key)
        device={'Authorization':'Bearer '+self.token};self.assertEqual(self.client.get(url+'?workspace_role=admin',headers=device).status_code,403)
        other={'Authorization':'Bearer '+self.f.tokens['teacher']};self.assertEqual(self.client.get(url+'?workspace_role=teacher',headers=other).status_code,403)
        response=self.client.post(url+'/'+key+'/cancel',headers=headers,json=dict(workspace_role='admin',reason='虚构取消',command_ref='http-cancel'))
        self.assertEqual(response.status_code,200,response.text);self.assertFalse(response.json()['activated'])
