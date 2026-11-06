from concurrent.futures import ThreadPoolExecutor
import unittest

from business.store import Problem
import test_management_delivery as fixture


class UplinkTests(unittest.TestCase):
    setUp = fixture.DeliveryTests.setUp

    def payload(self, **change):
        return dict(generation='fixture-generation', event_ref='local-checkin-1', student_id='student',
                    lesson_id='lesson', attendance_revision=0, balance_revision=0,
                    previous_balance_units=1000, consumed_units=150, remaining_units=850, occurred_at=1950) | change

    def upload(self, **change):
        return self.store.upload_attendance(self.token, **self.payload(**change))

    def test_offline_retry_concurrent_retry_and_lost_receipt_debit_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: self.upload(), range(2)))
        self.assertEqual(sum(not r['duplicate'] for r in result), 1)
        self.assertTrue(self.upload()['duplicate'])
        self.assertFalse(result[0]['apply_local_debit'])
        self.assertEqual(self.f.state(), (('arrived', 1, 1), (850, 1), 0))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_uploads').fetchone()[0], 1)

    def test_upload_and_web_approval_race_has_one_winner_and_one_debit(self):
        key = self.f.submit()['id']
        def run(operation):
            try:
                return operation()
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [self.upload, lambda: self.f.decide(key)]))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn('attendance_changed', results)
        self.assertEqual(self.f.state()[:2], (('arrived', 1, 1), (850, 1)))

    def test_web_leave_and_other_lesson_balance_change_never_overwritten(self):
        request = self.f.submit('leave')
        self.f.decide(request['id'])
        with self.assertRaises(Problem) as error:
            self.upload()
        self.assertEqual(error.exception.code, 'attendance_changed')
        self.assertEqual(self.f.state()[:2], (('leave', 1, 1), (1000, 0)))

    def test_units_clock_scope_generation_and_reused_reference_fail_closed(self):
        for changes in ({'consumed_units':100,'remaining_units':900}, {'remaining_units':999},
                        {'occurred_at':3000}, {'attendance_revision':True}, {'generation':'stale'},
                        {'balance_revision':1}):
            with self.assertRaises(Problem):
                self.upload(**changes)
        self.assertEqual(self.f.state()[:2], (('missing', 1, 0), (1000, 0)))
        self.upload()
        with self.assertRaises(Problem) as error:
            self.upload(occurred_at=1951)
        self.assertEqual(error.exception.code, 'idempotency_conflict')
        with self.store.transaction() as db:
            db.execute('DELETE FROM device_classes WHERE device_id=?', (self.device['id'],))
        with self.assertRaises(Problem):
            self.upload()  # even an identical old retry needs current authorization

    def test_audit_failure_rolls_back_and_api_rejects_named_user(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_upload_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.upload()
        self.assertEqual(self.f.state()[:2], (('missing', 1, 0), (1000, 0)))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_uploads').fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_upload_audit')
        headers = {'Authorization':'Bearer ' + self.f.tokens['owner']}
        path = '/v1/management/device/checkin'
        self.assertEqual(self.client.post(path, headers=headers, json=self.payload()).status_code, 403)
        headers = {'Authorization':'Bearer ' + self.token}
        response = self.client.post(path, headers=headers, json=self.payload())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['balance_units'], 850)
        self.assertFalse(response.json()['apply_local_debit'])


if __name__ == '__main__':
    unittest.main()
