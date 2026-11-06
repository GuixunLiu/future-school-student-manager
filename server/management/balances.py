"""Scoped, optimistic and immutable server-side opening-balance corrections."""
from business.store import Problem, uid
from .approvals import digest, encoded, label
from .migration_guard import require_not_held
from .policy import require, workspace


SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_balance_adjustments (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, student_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, workspace_role TEXT NOT NULL CHECK(workspace_role IN ('admin','teacher')),
 command_ref TEXT NOT NULL, digest TEXT NOT NULL,
 previous_units INTEGER NOT NULL, units INTEGER NOT NULL,
 previous_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 reason TEXT NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(org_id,actor_id,command_ref),
 FOREIGN KEY(org_id,student_id) REFERENCES management_balances(org_id,student_id),
 FOREIGN KEY(org_id,actor_id) REFERENCES users(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_balance_adjustments_no_update
 BEFORE UPDATE ON management_balance_adjustments BEGIN SELECT RAISE(ABORT,'immutable balance adjustment'); END;
CREATE TRIGGER IF NOT EXISTS management_balance_adjustments_no_delete
 BEFORE DELETE ON management_balance_adjustments BEGIN SELECT RAISE(ABORT,'immutable balance adjustment'); END;
'''


class BalanceOperations:
    def adjust_balance(self, token, role, *, student_id, units, expected_revision, reason, command_ref):
        reason, command_ref = label(reason), label(command_ref, 80)
        if (type(units) is not int or not -1000000 <= units <= 10000000
                or type(expected_revision) is not int or expected_revision < 0):
            raise Problem(422, 'invalid_balance', '课时余额或资料版本不正确')
        hashed = digest(dict(student_id=student_id, units=units,
                             expected_revision=expected_revision, reason=reason))
        def run(db, actor):
            selected = workspace(actor, role)
            student = db.execute('SELECT * FROM students WHERE org_id=? AND id=?',
                                 (actor['org_id'], student_id)).fetchone()
            if not student:
                raise Problem(404, 'student_missing', '未找到学生资料')
            require(actor, selected, 'hours.manage', scope=self.scope(db, actor),
                    target_org=student['org_id'], class_id=student['class_id'])
            prior = db.execute('''SELECT * FROM management_balance_adjustments
              WHERE org_id=? AND actor_id=? AND command_ref=?''',
              (actor['org_id'], actor['id'], command_ref)).fetchone()
            if prior:
                if prior['digest'] != hashed:
                    raise Problem(409, 'idempotency_conflict', '同一操作编号不能用于不同余额修改')
                return dict(student_id=prior['student_id'], units=prior['units'],
                            revision=prior['revision'], duplicate=True, audited=True)
            require_not_held(db, actor['org_id'], self.clock())
            balance = db.execute('SELECT * FROM management_balances WHERE org_id=? AND student_id=?',
                                 (actor['org_id'], student_id)).fetchone()
            if not balance:
                raise Problem(409, 'balance_missing', '该学生尚无期初余额，请先完成迁移核对')
            if balance['revision'] != expected_revision:
                raise Problem(409, 'balance_changed', '余额已更新，请刷新后重新核对')
            if balance['units'] == units:
                raise Problem(409, 'balance_unchanged', '新余额与当前余额相同')
            revision = expected_revision + 1
            db.execute('''UPDATE management_balances SET units=?,revision=?
              WHERE org_id=? AND student_id=? AND revision=?''',
              (units, revision, actor['org_id'], student_id, expected_revision))
            key = uid()
            db.execute('INSERT INTO management_balance_adjustments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (key, actor['org_id'], student_id, actor['id'], selected, command_ref,
                        hashed, balance['units'], units, expected_revision, revision,
                        reason, self.clock()))
            self.record_audit(db, actor, selected, 'balance_adjusted', student_id,
                              adjustment_id=key, command_ref=command_ref, reason=reason,
                              previous_units=balance['units'], units=units,
                              previous_revision=expected_revision, revision=revision)
            return dict(student_id=student_id, units=units, revision=revision,
                        duplicate=False, audited=True,
                        tablet_sync='server_authoritative_refresh_required')
        return self.command(token, run)
