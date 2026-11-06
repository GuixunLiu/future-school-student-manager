from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from business.store import Problem, Store
from management.approvals import ApprovalStore
from management.deadlines import DEFAULTS, TZ, Rule, deadline, may_edit, submission_status
from management.policy import Scope, permits, workspace


class PermissionsTests(unittest.TestCase):
    def actor(self, *roles):
        return {'kind': 'user', 'id': 'person', 'org_id': 'school', 'roles': set(roles)}

    def test_dual_role_never_inherits_admin_while_teaching(self):
        actor = self.actor('admin', 'teacher')
        with self.assertRaises(Problem):
            workspace(actor)
        scope = Scope(frozenset({'mine'}))
        self.assertTrue(permits(actor, 'teacher', 'attendance.request', scope=scope, class_id='mine'))
        self.assertFalse(permits(actor, 'teacher', 'attendance.request', scope=scope, class_id='other'))
        self.assertFalse(permits(actor, 'teacher', 'approvals.decide', class_id='mine'))
        self.assertFalse(permits(actor, 'teacher', 'accounts.manage'))
        self.assertTrue(permits(actor, 'admin', 'accounts.manage'))
        self.assertFalse(permits(actor, 'admin', 'submissions.submit', owner_id='person'))
        self.assertTrue(permits(actor, 'teacher', 'submissions.submit', owner_id='person'))
        self.assertFalse(permits(actor, 'teacher', 'submissions.read', owner_id='other'))

    def test_assessor_global_materials_scoped_decisions_no_raw_edits(self):
        actor = self.actor('assessor')
        scope = Scope(approval_classes=frozenset({'assigned'}))
        for action in ('statistics.read', 'submissions.read', 'submissions.export', 'scores.record', 'scores.calculate'):
            self.assertTrue(permits(actor, 'assessor', action))
        for action in ('students.manage', 'hours.manage', 'classes.manage', 'classes.read', 'accounts.manage', 'audit.read', 'submissions.unlock'):
            self.assertFalse(permits(actor, 'assessor', action))
        self.assertTrue(permits(actor, 'assessor', 'approvals.decide', scope=scope, class_id='assigned'))
        self.assertFalse(permits(actor, 'assessor', 'approvals.decide', scope=scope, class_id='other'))

    def test_principal_exports_require_explicit_policy_and_all_roles_respect_tenant(self):
        principal = self.actor('principal')
        self.assertTrue(permits(principal, None, 'statistics.read'))
        self.assertFalse(permits(principal, None, 'data.export'))
        self.assertTrue(permits(principal, None, 'data.export', principal_export_allowed=True))
        for role in ('principal', 'admin', 'teacher', 'assessor'):
            self.assertFalse(permits(self.actor(role), role, 'statistics.read', target_org='other'))
            self.assertFalse(permits(self.actor(role), role, 'unknown.permission'))
        with self.assertRaises(Problem):
            workspace({'kind': 'device', 'roles': {'admin'}})
        with self.assertRaises(Problem):
            workspace(principal, 'admin')


class DeadlineTests(unittest.TestCase):
    def local(self, value):
        return datetime.fromtimestamp(value, TZ).strftime('%Y-%m-%d %H:%M')

    def test_defaults_calendar_and_month_ends(self):
        self.assertEqual(self.local(deadline(date(2026, 10, 26), DEFAULTS['weekly_plan'])), '2026-10-27 09:00')
        self.assertEqual(self.local(deadline(date(2026, 10, 26), DEFAULTS['weekly_report'])), '2026-11-01 18:00')
        self.assertEqual(self.local(deadline(date(2026, 11, 1), DEFAULTS['monthly_plan'])), '2026-11-01 09:00')
        for year, expected in ((2028, '2028-02-29 18:00'), (2027, '2027-02-28 18:00')):
            self.assertEqual(self.local(deadline(date(year, 2, 1), DEFAULTS['monthly_report'])), expected)
        self.assertEqual(self.local(deadline(date(2026, 12, 28), DEFAULTS['weekly_report'])), '2027-01-03 18:00')

    def test_exact_boundary_lock_late_and_expiring_unlock(self):
        due = 1000
        self.assertTrue(may_edit(now=999, due=due))
        self.assertFalse(may_edit(now=1000, due=due))
        self.assertTrue(may_edit(now=1001, due=due, unlocked_until=1100))
        self.assertFalse(may_edit(now=1100, due=due, unlocked_until=1100))
        self.assertEqual(submission_status(now=999, due=due, submitted_at=None), 'upcoming')
        self.assertEqual(submission_status(now=1000, due=due, submitted_at=None), 'overdue')
        self.assertEqual(submission_status(now=2000, due=due, submitted_at=999), 'submitted')
        self.assertEqual(submission_status(now=2000, due=due, submitted_at=1000), 'late')
        self.assertEqual(submission_status(now=2000, due=due, submitted_at=1001), 'late')

    def test_configuration_and_invalid_periods(self):
        rule = Rule('monthly', 31, 10, 30)
        self.assertEqual(self.local(deadline(date(2027, 2, 1), rule)), '2027-02-28 10:30')
        for params in [('weekly', True, 9), ('weekly', 7, 9), ('monthly', 0, 9), ('monthly', 'last', 24)]:
            with self.assertRaises(ValueError):
                Rule(*params)
        with self.assertRaises(ValueError):
            deadline(date(2026, 10, 27), DEFAULTS['weekly_plan'])
        with self.assertRaises(ValueError):
            deadline(date(2026, 10, 29), DEFAULTS['monthly_plan'])


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fictional.sqlite'
        self.now = 2000
        self.password = 'fictional-approval-test-only'
        base = Store.bootstrap(self.path, 'fictional', 'owner', '虚构管理员', self.password, lambda: self.now)
        self.store = ApprovalStore(self.path, lambda: self.now)
        self.store.initialize()
        with base.transaction() as db:
            owner = db.execute('SELECT * FROM users').fetchone()
            self.owner, self.org = owner['id'], owner['org_id']
            for who in ('teacher', 'assessor', 'principal', 'other-teacher'):
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)', (who, self.org, who, '虚构人员', owner['password_hash'], 1, self.now))
            db.execute('INSERT INTO roles VALUES(?,?,?)', (self.org, 'teacher', 'teacher'))
            db.execute('INSERT INTO roles VALUES(?,?,?)', (self.org, 'other-teacher', 'teacher'))
            for who in ('assessor', 'principal'):
                db.execute('INSERT INTO management_roles VALUES(?,?,?)', (self.org, who, who))
            db.execute('INSERT INTO classes VALUES(?,?,?,?,?)', ('class-a', self.org, '虚构班甲', '虚构课程', self.now))
            db.execute('INSERT INTO classes VALUES(?,?,?,?,?)', ('class-b', self.org, '虚构班乙', '虚构课程', self.now))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)', (self.org, 'teacher', 'class-a'))
            db.execute('INSERT INTO management_approval_scopes VALUES(?,?,?)', (self.org, 'assessor', 'class-a'))
            db.execute('INSERT INTO students VALUES(?,?,?,?,?,?,?,?,?)', ('student', self.org, 'class-a', '虚构学生', 'TEST', 1000, 1, None, 0))
            db.execute('INSERT INTO management_balances VALUES(?,?,?,?)', (self.org, 'student', 1000, 0))
            db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,?,?,?,?)',
                       (self.org, 'lesson', 'student', 'class-a', 1900, 1, 150, 'missing', 0))
        self.tokens = {who: base.login('fictional', who, self.password, who)['access_token']
                       for who in ('owner', 'teacher', 'assessor', 'principal', 'other-teacher')}

    def submit(self, kind='makeup', **overrides):
        args = dict(lesson_id='lesson', student_id='student', kind=kind, reason='虚构核对', request_ref='request-one', expected_revision=0)
        args.update(overrides)
        return self.store.submit(self.tokens['teacher'], 'teacher', **args)

    def decide(self, key, who='assessor', **overrides):
        args = dict(request_id=key, approve=True, reason='虚构审批确认', decision_ref='decision-one')
        args.update(overrides)
        return self.store.decide(self.tokens[who], 'admin' if who == 'owner' else who, **args)

    def state(self):
        with self.store.transaction() as db:
            return (db.execute('SELECT status,expected,revision FROM management_attendance').fetchone()[:],
                    db.execute('SELECT units,revision FROM management_balances').fetchone()[:],
                    db.execute('SELECT COUNT(*) FROM management_outbox').fetchone()[0])

    def test_approved_makeup_effective_before_device_ack_and_retry_does_not_charge(self):
        first = self.submit()
        self.assertEqual(self.state(), (('missing', 1, 0), (1000, 0), 0))
        self.assertTrue(self.submit()['duplicate'])
        result = self.decide(first['id'])
        self.assertTrue(result['effective'])
        self.assertEqual(result['tablet_delivery'], 'pending')
        self.assertEqual(self.state(), (('arrived', 1, 1), (850, 1), 1))
        self.assertTrue(self.decide(first['id'])['duplicate'])
        self.assertEqual(self.state(), (('arrived', 1, 1), (850, 1), 1))
        with self.store.transaction() as db:
            event = json.loads(db.execute('SELECT payload FROM management_outbox').fetchone()[0])
            self.assertEqual(event['balance_delta'], -150)
            self.assertEqual(event['previous_attendance_revision'], 0)
            self.assertEqual(event['balance_units'], 850)
            # Original snapshot/legacy balances remain untouched by candidate core.
            self.assertEqual(db.execute('SELECT remaining_units FROM students').fetchone()[0], 1000)

    def test_leave_preserves_expected_count_and_balance(self):
        key = self.submit('leave')['id']
        self.decide(key)
        self.assertEqual(self.state(), (('leave', 1, 1), (1000, 0), 1))

    def test_rejection_is_audited_without_attendance_change(self):
        key = self.submit()['id']
        self.assertEqual(self.decide(key, approve=False)['status'], 'rejected')
        self.assertEqual(self.state(), (('missing', 1, 0), (1000, 0), 0))
        self.assertEqual(self.submit(request_ref='second')['status'], 'pending')

    def test_scopes_and_identity_checked_again_at_decision(self):
        key = self.submit()['id']
        for who in ('teacher', 'principal', 'other-teacher'):
            with self.assertRaises(Problem) as cm:
                self.decide(key, who=who)
            self.assertEqual(cm.exception.status, 403)
        with self.store.transaction() as db:
            db.execute('DELETE FROM management_approval_scopes')
        with self.assertRaises(Problem):
            self.decide(key)
        self.assertEqual(self.decide(key, who='owner')['status'], 'approved')

    def test_changed_or_already_arrived_fact_cannot_be_approved(self):
        key = self.submit()['id']
        with self.store.transaction() as db:
            db.execute("UPDATE management_attendance SET status='arrived',revision=revision+1")
        with self.assertRaises(Problem) as cm:
            self.decide(key)
        self.assertEqual(cm.exception.code, 'attendance_changed')
        self.assertEqual(self.state(), (('arrived', 1, 1), (1000, 0), 0))
        self.assertEqual(self.decide(key, approve=False)['status'], 'rejected')

    def test_reused_refs_conflicting_pending_and_future_makeup(self):
        key = self.submit()['id']
        for changes in ({'reason': '不同内容'}, {'request_ref': 'second', 'kind': 'leave'}):
            with self.assertRaises(Problem):
                self.submit(**changes)
        self.decide(key)
        with self.assertRaises(Problem):
            self.decide(key, approve=False)
        with self.assertRaises(Problem):
            self.decide(key, decision_ref='another')

    def test_future_makeup_denied_but_leave_allowed(self):
        with self.store.transaction() as db:
            db.execute('UPDATE management_attendance SET starts_at=3000')
        with self.assertRaises(Problem):
            self.submit()
        key = self.submit('leave')['id']
        self.assertEqual(self.decide(key)['status'], 'approved')

    def test_disabled_user_and_concurrent_retries(self):
        key = self.submit()['id']
        with self.store.transaction() as db:
            db.execute("UPDATE users SET active=0 WHERE id='assessor'")
        with self.assertRaises(Problem) as cm:
            self.decide(key)
        self.assertEqual(cm.exception.status, 401)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.decide(key, who='owner'), range(2)))
        self.assertEqual(sum(r['duplicate'] for r in results), 1)
        self.assertEqual(self.state(), (('arrived', 1, 1), (850, 1), 1))

    def test_outbox_failure_rolls_back_approval_and_balance(self):
        key = self.submit()['id']
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fixture_fail_outbox BEFORE INSERT ON management_outbox BEGIN SELECT RAISE(ABORT,'fictional failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.decide(key)
        self.assertEqual(self.state(), (('missing', 1, 0), (1000, 0), 0))
        self.assertEqual(self.submit()['status'], 'pending')

    def test_audit_cannot_be_rewritten_or_deleted(self):
        self.submit()
        for sql in ("UPDATE management_audit SET action='hidden'", 'DELETE FROM management_audit'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as db:
                    db.execute(sql)


if __name__ == '__main__':
    unittest.main()
