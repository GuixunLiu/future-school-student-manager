from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest

from fastapi.testclient import TestClient
from business.store import Problem
from management.api import create_app
from management.delivery import DeliveryStore as AssessmentStore
import test_management_rules as fixtures


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ApprovalTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = AssessmentStore(self.f.path, lambda: self.f.now)
        self.store.initialize()
        self.period = self.store.open_period(self.f.tokens['owner'], 'admin', kind='monthly_plan', starts_on='1970-01-01', owner_ids=['teacher', 'other-teacher'], command_ref='open')
        self.key = self.period['materials'][0]['id']
        self.counter = 0

    def change(self, action, who='teacher', key=None, **kw):
        key = key or self.key
        role = 'admin' if who == 'owner' else 'teacher' if who == 'other-teacher' else who
        with self.store.transaction() as db:
            revision = self.store.material(db, self.f.org, key)['revision']
        self.counter += 1
        args = dict(material_id=key, action=action, expected_revision=revision, command_ref='change-' + str(self.counter))
        args.update(kw)
        return self.store.change_material(self.f.tokens[who], role, **args)

    def read(self, who='teacher'):
        role = 'admin' if who == 'owner' else 'teacher' if who == 'other-teacher' else who
        return self.store.read_material(self.f.tokens[who], role, self.key)

    def submit(self):
        self.change('save', content='虚构计划\n第二行')
        return self.change('submit')

    def test_deadline_exactly_locks_without_nightly_job(self):
        self.assertEqual(self.period['due_at'], 3600)
        self.f.now = 3599
        self.change('save', content='截止前草稿')
        self.f.now = 3600
        with self.assertRaises(Problem) as e:
            self.change('submit')
        self.assertEqual(e.exception.code, 'material_locked')
        self.assertEqual(self.read()['status'], 'overdue')
        self.assertFalse(self.read()['editable'])

    def test_return_after_deadline_needs_admin_unlock_and_resubmission_is_late(self):
        first = self.submit()
        self.assertEqual(first['status'], 'submitted')
        self.f.now = 3601
        returned = self.change('return', who='assessor', reason='补充课堂记录')
        self.assertEqual(returned['state'], 'returned')
        self.assertFalse(returned['editable'])
        self.assertEqual(self.read()['return_reason'], '补充课堂记录')
        with self.assertRaises(Problem):
            self.change('unlock', who='assessor', reason='越权测试', unlocked_until=4000)
        self.change('unlock', who='owner', reason='允许补充材料', unlocked_until=4000)
        self.change('save', content='补充后的完整材料')
        self.f.now = 3700
        result = self.change('submit')
        self.assertEqual(result['status'], 'late')
        self.assertIsNone(result['unlocked_until'])
        versions = self.read()['versions']
        self.assertEqual([r['late'] for r in versions], [0, 1])
        self.assertEqual(versions[0]['content'], '虚构计划\n第二行')
        self.assertEqual(versions[1]['submitted_at'], 3700)

    def test_unlock_expires_and_requires_reason(self):
        self.f.now = 3600
        for until in (3600, True, 3600 + 32 * 86400):
            with self.assertRaises(Problem):
                self.change('unlock', who='owner', reason='原因', unlocked_until=until)
        with self.assertRaises(Problem):
            self.change('unlock', who='owner', reason=' ', unlocked_until=4000)
        self.change('unlock', who='owner', reason='允许延后', unlocked_until=4000)
        self.f.now = 4000
        with self.assertRaises(Problem):
            self.change('save', content='恰好过期')
        with self.store.transaction() as db:
            rows = db.execute("SELECT detail FROM management_audit WHERE action='assessment_unlock'").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn('teacher', rows[0]['detail'])

    def test_deadline_rule_change_only_affects_new_period(self):
        self.store.configure_rule(self.f.tokens['owner'], 'admin', kind='monthly_plan', rule={'frequency': 'monthly', 'day': 1, 'hour': 10}, expected_revision=0, command_ref='rule')
        self.assertEqual(self.read()['due_at'], 3600)
        result = self.store.open_period(self.f.tokens['owner'], 'admin', kind='monthly_plan', starts_on='1970-02-01', owner_ids=['teacher'], command_ref='feb')
        from datetime import datetime
        from management.deadlines import TZ
        self.assertEqual(datetime.fromtimestamp(result['due_at'], TZ).hour, 10)
        with self.assertRaises(Problem):
            self.store.configure_rule(self.f.tokens['owner'], 'admin', kind='monthly_plan', rule={'frequency': 'monthly', 'day': 1, 'hour': 11}, expected_revision=0, command_ref='stale-rule')

    def test_teacher_scope_global_read_and_no_admin_teacher_submission(self):
        self.submit()
        for who in ('owner', 'principal', 'assessor'):
            self.assertEqual(self.read(who)['owner_id'], 'teacher')
        with self.assertRaises(Problem):
            self.read('other-teacher')
        for who in ('owner', 'principal', 'assessor', 'other-teacher'):
            with self.assertRaises(Problem):
                self.change('save', who=who, content='越权内容')
        with self.assertRaises(Problem):
            self.change('return', who='teacher', reason='自行退回')

    def test_submit_retries_and_concurrent_updates_do_not_duplicate_history(self):
        self.change('save', content='只应提交一次')
        args = dict(material_id=self.key, action='submit', expected_revision=1, command_ref='same-submit')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.change_material(self.f.tokens['teacher'], 'teacher', **args), range(2)))
        self.assertEqual(sum(r['duplicate'] for r in results), 1)
        self.assertEqual(len(self.read()['versions']), 1)
        with self.assertRaises(Problem):
            self.store.change_material(self.f.tokens['teacher'], 'teacher', **{**args, 'action': 'save', 'content': '替换内容'})
        with self.assertRaises(Problem):
            self.change('return', who='owner', reason='旧版本', expected_revision=1)

    def test_manual_score_tracks_version_no_unprovided_formula(self):
        row = self.submit()
        args = dict(material_id=self.key, expected_revision=row['revision'], value='87.50', basis='按已公布的评分表人工核算', command_ref='score')
        for who, role in (('teacher', 'teacher'), ('owner', 'admin'), ('principal', 'principal')):
            with self.assertRaises(Problem):
                self.store.record_score(self.f.tokens[who], role, **args)
        self.assertEqual(self.store.record_score(self.f.tokens['assessor'], 'assessor', **args)['method'], 'manual')
        self.assertTrue(self.store.record_score(self.f.tokens['assessor'], 'assessor', **args)['duplicate'])
        self.assertTrue(self.read()['scores'][0]['current'])
        self.change('return', who='assessor', reason='补充证据')
        self.assertFalse(self.read()['scores'][0]['current'])
        self.change('save', content='新的证据')
        self.change('submit')
        self.assertFalse(self.read()['scores'][0]['current'])
        with self.assertRaises(Problem):
            self.store.record_score(self.f.tokens['assessor'], 'assessor', **{**args, 'command_ref': 'stale-score'})

    def test_invalid_score_empty_content_and_period_validation(self):
        with self.assertRaises(Problem):
            self.change('submit')
        self.submit()
        for score in ('NaN', 'Infinity', '1.234', '1e999', '1e999999999', '1234567'):
            with self.assertRaises(Problem):
                self.store.record_score(self.f.tokens['assessor'], 'assessor', material_id=self.key, expected_revision=2, value=score, basis='测试', command_ref='invalid-' + score)
        for fields in ({'kind': 'weekly_plan', 'starts_on': '1970-01-01'}, {'owner_ids': ['principal']}, {'owner_ids': ['teacher', 'teacher']}, {'kind': 'enrolment_list'}):
            args = dict(kind='monthly_plan', starts_on='1970-02-01', owner_ids=['teacher'], command_ref='invalid-period')
            args.update(fields)
            with self.assertRaises(Problem):
                self.store.open_period(self.f.tokens['owner'], 'admin', **args)

    def test_immutable_history_and_audit_failure_rolls_back(self):
        self.submit()
        for table in ('assessment_versions', 'management_audit'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db:
                    db.execute('DELETE FROM ' + table)
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'simulated audit storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.change('return', who='owner', reason='测试回滚')
        self.assertEqual(self.read()['state'], 'submitted')

    def test_revoked_role_cannot_replay_successful_mutation(self):
        self.change('save', content='材料', command_ref='known')
        with self.store.transaction() as db:
            db.execute("DELETE FROM roles WHERE user_id='teacher'")
        with self.assertRaises(Problem):
            self.store.change_material(self.f.tokens['teacher'], 'teacher', material_id=self.key, action='save', expected_revision=0, command_ref='known', content='材料')

    def test_http_roundtrip_scope_and_realtime_status(self):
        with TestClient(create_app(self.f.path, lambda: self.f.now, allow_test_client=True), base_url='http://127.0.0.1:18087') as client:
            headers = lambda who: {'Authorization': 'Bearer ' + self.f.tokens[who]}
            for who, role, count in (('teacher', 'teacher', 1), ('assessor', 'assessor', 2), ('owner', 'admin', 2), ('principal', 'principal', 2)):
                r = client.get('/v1/management/materials?workspace_role=' + role, headers=headers(who))
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(len(r.json()['items']), count)
            path = '/v1/management/materials/' + self.key
            body = dict(workspace_role='teacher', action='save', expected_revision=0, command_ref='http-save', content='网页保存材料')
            r = client.post(path, headers=headers('teacher'), json=body)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['revision'], 1)
            body.update(action='submit', expected_revision=1, command_ref='http-submit')
            del body['content']
            self.assertEqual(client.post(path, headers=headers('teacher'), json=body).status_code, 200)
            self.assertEqual(client.get(path + '?workspace_role=teacher', headers=headers('other-teacher')).status_code, 403)
            self.assertEqual(client.post(path, headers=headers('teacher'), json={**body, 'due_at': 99999}).status_code, 422)
            self.f.now = 3600
            others = client.get('/v1/management/materials?workspace_role=assessor', headers=headers('assessor')).json()['items']
            self.assertEqual({r['status'] for r in others}, {'submitted', 'overdue'})


if __name__ == '__main__':
    unittest.main()
