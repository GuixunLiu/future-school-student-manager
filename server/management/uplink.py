"""Canonical check-ins from the one bound writer device, candidate only.

The tablet has already persisted its own deduction. This transaction advances
the server ledger once; its receipt must never be treated as a new local debit.
No arbitrary balance replacement, history replay, or local leave approval.
"""
import json
from datetime import datetime

from business.store import Problem
from .approvals import digest, encoded, label
from .deadlines import TZ
from .migration_guard import require_not_held

SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_uploads (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, generation TEXT NOT NULL,
 event_ref TEXT NOT NULL, digest TEXT NOT NULL, student_id TEXT NOT NULL,
 lesson_id TEXT NOT NULL, receipt TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id,generation,event_ref),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(org_id,student_id) REFERENCES management_balances(org_id,student_id)
);
CREATE TRIGGER IF NOT EXISTS management_upload_no_update
 BEFORE UPDATE ON management_uploads BEGIN SELECT RAISE(ABORT,'immutable upload'); END;
CREATE TRIGGER IF NOT EXISTS management_upload_no_delete
 BEFORE DELETE ON management_uploads BEGIN SELECT RAISE(ABORT,'immutable upload'); END;
'''


class UplinkOperations:
    def upload_attendance(self, token, generation, *, event_ref, student_id, lesson_id,
                          attendance_revision, balance_revision, previous_balance_units,
                          consumed_units, remaining_units, occurred_at):
        event_ref = label(event_ref, 80)
        numbers = (attendance_revision, balance_revision, previous_balance_units,
                   consumed_units, remaining_units, occurred_at)
        if (any(type(v) is not int for v in numbers) or min(attendance_revision, balance_revision, occurred_at) < 0
                or consumed_units <= 0 or occurred_at > 253402185599
                or max(abs(v) for v in numbers) > 253402185599):
            raise Problem(422, 'invalid_checkin', '签到版本、时间或扣减单位无效')
        payload = dict(student_id=student_id, lesson_id=lesson_id, attendance_revision=attendance_revision,
                       balance_revision=balance_revision, previous_balance_units=previous_balance_units,
                       consumed_units=consumed_units, remaining_units=remaining_units, occurred_at=occurred_at)
        hashed = digest(payload)
        def run(db, actor):
            self.access(db, actor, generation)
            require_not_held(db,actor['org_id'],self.clock())
            fact = self.fact(db, actor['org_id'], lesson_id, student_id)
            binding = db.execute('''SELECT b.* FROM management_device_students b
              JOIN device_classes c ON c.org_id=b.org_id AND c.device_id=b.device_id AND c.class_id=b.class_id
              WHERE b.org_id=? AND b.device_id=? AND b.student_id=? AND b.class_id=?''',
              (actor['org_id'], actor['id'], student_id, fact['class_id'])).fetchone()
            if not binding:
                raise Problem(403, 'checkin_scope', '此平板不再具有该学生或班级的同步授权')
            old = db.execute('''SELECT * FROM management_uploads
              WHERE org_id=? AND device_id=? AND generation=? AND event_ref=?''',
              (actor['org_id'], actor['id'], generation, event_ref)).fetchone()
            if old:
                if old['digest'] != hashed:
                    raise Problem(409, 'idempotency_conflict', '同一签到编号不能用于不同内容')
                return json.loads(old['receipt']) | {'duplicate': True}
            if (fact['revision'] != attendance_revision or fact['status'] != 'missing' or not fact['expected']):
                raise Problem(409, 'attendance_changed', '服务器签到或请假已变化，请保留平板记录并核对')
            balance = db.execute('SELECT * FROM management_balances WHERE org_id=? AND student_id=?',
                                 (actor['org_id'], student_id)).fetchone()
            if (balance['revision'] != balance_revision or balance['units'] != previous_balance_units
                    or consumed_units != fact['charge_units'] or remaining_units != balance['units'] - fact['charge_units']):
                raise Problem(409, 'balance_changed', '服务器余额或扣减规则已变化，本次未覆盖课时')
            if occurred_at > self.clock() + 120:
                raise Problem(409, 'device_clock', '签到时间超前，请核对平板时间')
            if datetime.fromtimestamp(occurred_at, TZ).date() != datetime.fromtimestamp(fact['starts_at'], TZ).date():
                raise Problem(409, 'lesson_day', '签到日期与已确认课次不同，历史补签请走网页审批')
            # Device-authored events require no second approval or debit on the
            # tablet. Pending teacher requests will fail their stale revision.
            db.execute("UPDATE management_attendance SET status='arrived',revision=revision+1 WHERE org_id=? AND lesson_id=? AND student_id=?",
                       (actor['org_id'], lesson_id, student_id))
            db.execute('UPDATE management_balances SET units=?,revision=revision+1 WHERE org_id=? AND student_id=?',
                       (remaining_units, actor['org_id'], student_id))
            receipt = dict(protocol=1, org_id=actor['org_id'], device_id=actor['id'], generation=generation,
                           event_ref=event_ref, digest=hashed, student_id=student_id, lesson_id=lesson_id,
                           accepted=True, duplicate=False, status='arrived',
                           attendance_revision=attendance_revision + 1, balance_revision=balance_revision + 1,
                           balance_units=remaining_units, apply_local_debit=False)
            db.execute('INSERT INTO management_uploads VALUES(?,?,?,?,?,?,?,?,?)',
                       (actor['org_id'], actor['id'], generation, event_ref, hashed, student_id, lesson_id, encoded(receipt), self.clock()))
            self.record_audit(db, actor, 'device', 'device_checkin_accepted', event_ref,
                              student_id=student_id, lesson_id=lesson_id, occurred_at=occurred_at, consumed_units=consumed_units)
            return receipt
        return self.command(token, run)
