"""Fictional scoped balance corrections with immutable audit evidence."""
import json
import sqlite3
import unittest

from fastapi.testclient import TestClient

from business.store import Problem
from management.api import create_app
from management.delivery import DeliveryStore
from test_management_rules import ApprovalTests


class BalanceAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = DeliveryStore(self.fixture.path, lambda: self.fixture.now)
        self.store.initialize()

    def adjust(self, who='teacher', role=None, **changes):
        args = dict(student_id='student', units=1200, expected_revision=0,
                    reason='虚构期初余额核对', command_ref='balance-command-1')
        args.update(changes)
        return self.store.adjust_balance(self.fixture.tokens[who], role or who, **args)

    def balance(self):
        with self.store.transaction() as db:
            return db.execute('SELECT units,revision FROM management_balances').fetchone()[:]

    def test_teacher_adjusts_own_class_with_complete_immutable_audit(self):
        result = self.adjust()
        self.assertEqual((result['units'], result['revision'], result['audited']), (1200, 1, True))
        self.assertEqual(self.balance(), (1200, 1))
        self.assertEqual(result['tablet_sync'], 'server_authoritative_refresh_required')
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM management_balance_adjustments').fetchone()
            self.assertEqual((row['previous_units'], row['units'], row['previous_revision'], row['revision']),
                             (1000, 1200, 0, 1))
            self.assertEqual(row['actor_id'], 'teacher')
            self.assertEqual(row['reason'], '虚构期初余额核对')
            audit = json.loads(db.execute("SELECT detail FROM management_audit WHERE action='balance_adjusted'").fetchone()[0])
            self.assertEqual(audit['previous_units'], 1000)
            self.assertEqual(audit['units'], 1200)
            self.assertEqual(audit['command_ref'], 'balance-command-1')
        for statement in ("UPDATE management_balance_adjustments SET reason='hidden'",
                          'DELETE FROM management_balance_adjustments'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as transaction:
                    transaction.execute(statement)

    def test_out_of_scope_and_non_manager_roles_cannot_adjust(self):
        for who, role in (('other-teacher', 'teacher'), ('assessor', 'assessor'), ('principal', 'principal')):
            with self.subTest(who=who):
                with self.assertRaises(Problem) as error:
                    self.adjust(who, role)
                self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.balance(), (1000, 0))
        self.assertEqual(self.adjust('owner', 'admin', command_ref='admin-adjustment')['units'], 1200)

    def test_idempotency_stale_revision_and_noop_are_safe(self):
        first = self.adjust()
        self.assertFalse(first['duplicate'])
        self.assertTrue(self.adjust()['duplicate'])
        self.assertEqual(self.balance(), (1200, 1))
        with self.assertRaises(Problem) as error:
            self.adjust(units=1300)
        self.assertEqual(error.exception.code, 'idempotency_conflict')
        with self.assertRaises(Problem) as error:
            self.adjust(units=1300, command_ref='stale-command')
        self.assertEqual(error.exception.code, 'balance_changed')
        with self.assertRaises(Problem) as error:
            self.adjust(units=1200, expected_revision=1, command_ref='noop-command')
        self.assertEqual(error.exception.code, 'balance_unchanged')
        self.assertEqual(self.balance(), (1200, 1))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM management_balance_adjustments').fetchone()[0], 1)

    def test_audit_failure_rolls_back_balance_and_adjustment(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_balance_audit_failure BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'fictional audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.adjust()
        self.assertEqual(self.balance(), (1000, 0))
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM management_balance_adjustments').fetchone()[0], 0)

    def test_http_route_exposes_revision_and_rejects_forged_scope(self):
        app = create_app(self.fixture.path, lambda: self.fixture.now, allow_test_client=True)
        with TestClient(app, base_url='http://127.0.0.1:18087') as client:
            path = '/v1/management/students/student/balance'
            body = dict(workspace_role='teacher', units=1250, expected_revision=0,
                        reason='虚构网页余额调整', command_ref='http-balance-1')
            response = client.post(path, headers={'Authorization': 'Bearer ' + self.fixture.tokens['teacher']}, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['revision'], 1)
            body['workspace_role'] = 'admin'
            response = client.post(path, headers={'Authorization': 'Bearer ' + self.fixture.tokens['teacher']}, json=body)
            self.assertEqual(response.status_code, 403)
            attendance = client.get('/v1/management/attendance?on=1970-01-01&workspace_role=teacher',
                                    headers={'Authorization': 'Bearer ' + self.fixture.tokens['teacher']})
            row = attendance.json()['items'][0]
            self.assertEqual((row['balance_units'], row['balance_revision']), (1250, 1))
            self.assertTrue(row['can_adjust_balance'])


if __name__ == '__main__':
    unittest.main()
