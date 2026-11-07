import json
import unittest

from fastapi.testclient import TestClient
from business.store import Problem
from management.api import create_app
from management.delivery import DeliveryStore
import test_management_rules as fixture_module


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture_module.ApprovalTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = DeliveryStore(self.f.path, lambda: self.f.now)
        self.store.initialize()
        self.device = self.store.command(self.f.tokens['owner'], lambda db, a: self.store.create_device(
            db, a, {'name': '虚构平板', 'class_ids': ['class-a']}))
        self.token = self.device['device_token']
        with self.store.transaction() as db:
            db.execute('INSERT INTO management_device_access VALUES(?,?,?,?,?)',
                       (self.f.org, self.device['id'], 'fixture-generation', 1, 9999))
            db.execute('INSERT INTO management_device_students VALUES(?,?,?,?)',
                       (self.f.org, 'student', self.device['id'], 'class-a'))
        self.client = TestClient(create_app(self.f.path, lambda: self.f.now, allow_test_client=True),
                                 base_url='http://127.0.0.1:18087')
        self.addCleanup(self.client.close)

    def approve(self, kind='makeup'):
        result = self.f.submit(kind)
        self.f.decide(result['id'])
        return result

    def poll(self):
        return self.store.poll(self.token, 'fixture-generation')

    def receipt(self, offered=None, **override):
        item = (offered or self.poll())['item']
        e = json.loads(item['event_json'])
        return dict(generation='fixture-generation', event_id=e['event_id'],
                    sha256=item['sha256'], receipt_token=item['receipt_token'], status='applied',
                    attendance_revision=e['attendance_revision'], balance_revision=e['balance_revision'],
                    balance_units=e['balance_units']) | override

    def test_lost_poll_ack_retry_and_no_second_deduction(self):
        self.assertIsNone(self.poll()['item'])
        self.approve()
        first = self.poll()
        self.assertEqual(first, self.poll())
        e = json.loads(first['item']['event_json'])
        self.assertEqual(e['org_id'], self.f.org)
        self.assertEqual(e['previous_balance_units'], 1000)
        expected = self.f.state()
        receipt = self.receipt(first)
        self.assertFalse(self.store.acknowledge(self.token, **receipt)['duplicate'])
        self.assertTrue(self.store.acknowledge(self.token, **receipt)['duplicate'])
        self.assertIsNone(self.poll()['item'])
        self.assertEqual(self.f.state(), expected)

    def test_conflict_is_not_delivery_and_stays_first_until_persisted(self):
        self.approve()
        item = self.poll()
        args = self.receipt(item, status='conflict', conflict_code='local_changes')
        self.store.acknowledge(self.token, **args)
        self.assertTrue(self.store.acknowledge(self.token, **args)['duplicate'])
        pending = self.poll()['item']
        self.assertEqual(pending['status'], 'conflict')
        self.assertEqual(pending['receipt_token'], item['item']['receipt_token'])
        self.store.acknowledge(self.token, **self.receipt(item))
        with self.assertRaises(Problem):
            self.store.acknowledge(self.token, **args)
        self.assertIsNone(self.poll()['item'])

    def test_deferral_does_not_acknowledge_or_hide_the_event_from_future_polls(self):
        self.approve()
        original=self.poll()
        headers={'Authorization':'Bearer '+self.token}
        endpoint='/v1/management/device/next?generation=fixture-generation'
        filtered=self.client.get(endpoint+'&defer=student',headers=headers)
        self.assertEqual(filtered.status_code,200,filtered.text)
        self.assertIsNone(filtered.json()['item'])
        self.assertEqual(self.poll(),original)
        self.assertEqual(self.client.get(endpoint+'&defer=student,,other',headers=headers).status_code,422)

    def test_disabled_expired_changed_generation_and_personal_accounts_denied(self):
        for token, gen in [(self.f.tokens['owner'], 'fixture-generation'), (self.token, 'wrong')]:
            with self.assertRaises(Problem):
                self.store.poll(token, gen)
        self.approve()
        receipt = self.receipt()
        with self.store.transaction() as db:
            db.execute('UPDATE management_device_access SET enabled=0')
        with self.assertRaises(Problem):
            self.store.acknowledge(self.token, **receipt)
        with self.store.transaction() as db:
            db.execute('UPDATE management_device_access SET enabled=1, expires_at=?', (self.f.now,))
        with self.assertRaises(Problem):
            self.poll()

    def test_wrong_proof_or_balance_cannot_advance_and_scope_rechecked(self):
        self.approve()
        receipt = self.receipt()
        for change in [{'sha256':'0'*64}, {'receipt_token':'0'*32}, {'balance_units':999},
                       {'attendance_revision':True}, {'receipt_token':'错误'}, {'event_id':'unoffered'}]:
            with self.assertRaises(Problem):
                self.store.acknowledge(self.token, **(receipt | change))
        self.assertEqual(self.poll()['item']['status'], 'pending')
        with self.store.transaction() as db:
            db.execute('DELETE FROM device_classes WHERE device_id=?', (self.device['id'],))
        with self.assertRaises(Problem):
            self.store.acknowledge(self.token, **receipt)
        self.assertIsNone(self.poll()['item'])

    def test_sequential_lessons_leave_then_makeup_share_balance_revision(self):
        self.approve('leave')
        with self.store.transaction() as db:
            db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,?,?,?,?)',
                       (self.f.org, 'next-lesson', 'student', 'class-a', 1950, 1, 100, 'missing', 0))
        request = self.f.submit(lesson_id='next-lesson', request_ref='next-request')
        self.f.decide(request['id'], decision_ref='next-decision')
        first = self.poll()
        e = json.loads(first['item']['event_json'])
        self.assertEqual((e['status'], e['balance_revision']), ('leave', 0))
        self.store.acknowledge(self.token, **self.receipt(first))
        second = self.poll()
        e = json.loads(second['item']['event_json'])
        self.assertEqual((e['status'], e['balance_units'], e['balance_delta']), ('arrived', 900, -100))
        self.store.acknowledge(self.token, **self.receipt(second))
        self.assertIsNone(self.poll()['item'])

    def test_http_device_scope_and_forged_body(self):
        self.approve()
        headers = {'Authorization': 'Bearer ' + self.token}
        r = self.client.get('/v1/management/device/next?generation=fixture-generation', headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        receipt = self.receipt(r.json())
        r = self.client.post('/v1/management/device/receipt', headers=headers, json=receipt | {'org_id':self.f.org})
        self.assertEqual(r.status_code, 422)
        r = self.client.post('/v1/management/device/receipt', headers=headers, json=receipt)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'applied')
        r = self.client.get('/v1/management/attendance?workspace_role=admin&on=1970-01-01', headers=headers)
        self.assertEqual(r.status_code, 403)

    def test_request_list_distinguishes_effective_and_device_saved(self):
        key = self.approve()['id']
        def status():
            response = self.client.get('/v1/management/requests?workspace_role=teacher',
                headers={'Authorization': 'Bearer ' + self.f.tokens['teacher']})
            self.assertEqual(response.status_code, 200, response.text)
            item = next(r for r in response.json()['items'] if r['id'] == key)
            self.assertEqual(item['status'], 'approved')
            return item['tablet_delivery']
        self.assertEqual(status(), 'pending')
        receipt = self.receipt()
        self.store.acknowledge(self.token, **(receipt | {'status':'conflict','conflict_code':'local_changes'}))
        self.assertEqual(status(), 'conflict')
        self.store.acknowledge(self.token, **receipt)
        self.assertEqual(status(), 'applied')
        with self.store.transaction() as db:
            db.execute('UPDATE management_device_access SET enabled=0')
        self.assertEqual(status(), 'unassigned')


if __name__ == '__main__':
    unittest.main()
