"""Transactional approval core for the next server-authoritative release.

Not installed by the live mirror. Canonical records must first be populated by
an explicit reconciled migration; raw mirror snapshots cannot overwrite them.
No delivery acknowledgement is inferred from an approval or outbox insert.
"""
import hashlib
import json

from business.store import Problem, Store, uid
from .policy import Scope, require, workspace
from .migration_guard import require_not_held

SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_roles (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('principal','assessor')),
 PRIMARY KEY(org_id,user_id,role),
 FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_approval_scopes (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL, class_id TEXT NOT NULL,
 PRIMARY KEY(org_id,user_id,class_id),
 FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_balances (
 org_id TEXT NOT NULL, student_id TEXT NOT NULL,
 units INTEGER NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
 PRIMARY KEY(org_id,student_id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_attendance (
 org_id TEXT NOT NULL, lesson_id TEXT NOT NULL, student_id TEXT NOT NULL,
 class_id TEXT NOT NULL, starts_at INTEGER NOT NULL,
 expected INTEGER NOT NULL CHECK(expected IN (0,1)),
 charge_units INTEGER NOT NULL CHECK(charge_units>0),
 status TEXT NOT NULL CHECK(status IN ('missing','leave','arrived')),
 revision INTEGER NOT NULL CHECK(revision>=0),
 PRIMARY KEY(org_id,lesson_id,student_id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id),
 FOREIGN KEY(org_id,student_id) REFERENCES management_balances(org_id,student_id)
);
CREATE TABLE IF NOT EXISTS management_requests (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, lesson_id TEXT NOT NULL,
 student_id TEXT NOT NULL, class_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('leave','makeup')),
 requested_by TEXT NOT NULL, request_ref TEXT NOT NULL, digest TEXT NOT NULL,
 reason TEXT NOT NULL, expected_revision INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
 created_at INTEGER NOT NULL, decided_at INTEGER, decided_by TEXT,
 decision_reason TEXT, decision_ref TEXT, decision_digest TEXT,
 FOREIGN KEY(org_id,requested_by) REFERENCES users(org_id,id),
 UNIQUE(org_id,requested_by,request_ref)
);
CREATE UNIQUE INDEX IF NOT EXISTS management_pending_attendance
 ON management_requests(org_id,lesson_id,student_id) WHERE status='pending';
CREATE UNIQUE INDEX IF NOT EXISTS management_decision_ref
 ON management_requests(org_id,decided_by,decision_ref) WHERE decision_ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS management_outbox (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 org_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
 payload TEXT NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(request_id) REFERENCES management_requests(id)
);
CREATE TABLE IF NOT EXISTS management_audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, workspace_role TEXT NOT NULL, action TEXT NOT NULL,
 entity_id TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS management_audit_no_update
 BEFORE UPDATE ON management_audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS management_audit_no_delete
 BEFORE DELETE ON management_audit BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS management_outbox_no_update
 BEFORE UPDATE ON management_outbox BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS management_outbox_no_delete
 BEFORE DELETE ON management_outbox BEGIN SELECT RAISE(ABORT,'immutable event'); END;
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def label(value, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise Problem(422, 'invalid_text', '请填写有效内容')
    return value.strip()


class ApprovalStore(Store):
    def initialize(self):
        # Explicit candidate-install operation, never run by the live old app.
        db = self.connect()
        try:
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
        finally:
            db.close()

    def authenticate(self, db, token):
        actor = super().authenticate(db, token)
        if actor['kind'] == 'user':
            actor['roles'].update(r[0] for r in db.execute(
                'SELECT role FROM management_roles WHERE org_id=? AND user_id=?',
                (actor['org_id'], actor['id'])))
        return actor

    def scope(self, db, actor):
        args = actor['org_id'], actor['id']
        return Scope(
            frozenset(r[0] for r in db.execute('SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?', args)),
            frozenset(r[0] for r in db.execute('SELECT class_id FROM management_approval_scopes WHERE org_id=? AND user_id=?', args)),
        )

    def record_audit(self, db, actor, role, action, entity, **detail):
        db.execute('INSERT INTO management_audit(org_id,actor_id,workspace_role,action,entity_id,detail,created_at) VALUES(?,?,?,?,?,?,?)',
                   (actor['org_id'], actor['id'], role, action, entity, encoded(detail), self.clock()))

    def fact(self, db, org, lesson, student):
        row = db.execute('SELECT * FROM management_attendance WHERE org_id=? AND lesson_id=? AND student_id=?',
                         (org, lesson, student)).fetchone()
        if not row:
            raise Problem(404, 'lesson_student_missing', '未找到已核对的课堂学生记录')
        return row

    @staticmethod
    def receipt(row, duplicate=False):
        return {'id': row['id'], 'status': row['status'], 'duplicate': duplicate,
                'effective': row['status'] == 'approved',
                'tablet_delivery': 'pending' if row['status'] == 'approved' else 'not_created'}

    def submit(self, token, role, *, lesson_id, student_id, kind, reason, request_ref, expected_revision):
        reason, request_ref = label(reason), label(request_ref, 80)
        if kind not in {'leave', 'makeup'} or type(expected_revision) is not int or expected_revision < 0:
            raise Problem(422, 'invalid_request', '申请类型或资料版本不正确')
        payload = dict(lesson=lesson_id, student=student_id, kind=kind, reason=reason, revision=expected_revision)
        hashed = digest(payload)
        def run(db, actor):
            selected = workspace(actor, role)
            if lesson_id.startswith('legacy:'):
                from .historical import prepare_request
                prepare_request(self,db,actor,selected,lesson_id,student_id)
            fact = self.fact(db, actor['org_id'], lesson_id, student_id)
            require(actor, selected, 'attendance.request', scope=self.scope(db, actor),
                    target_org=fact['org_id'], class_id=fact['class_id'])
            old = db.execute('SELECT * FROM management_requests WHERE org_id=? AND requested_by=? AND request_ref=?',
                             (actor['org_id'], actor['id'], request_ref)).fetchone()
            if old:
                if old['digest'] != hashed:
                    raise Problem(409, 'idempotency_conflict', '同一申请编号不能用于不同内容')
                return self.receipt(old, True)
            if fact['revision'] != expected_revision:
                raise Problem(409, 'attendance_changed', '签到资料已更新，请刷新后核对')
            if not fact['expected'] or fact['status'] != 'missing':
                raise Problem(409, 'attendance_conflict', '仅能为应到且未签到、未请假的学生申请')
            if kind == 'makeup' and self.clock() < fact['starts_at']:
                raise Problem(409, 'future_makeup', '课程尚未开始，不能补签')
            if db.execute("SELECT 1 FROM management_requests WHERE org_id=? AND lesson_id=? AND student_id=? AND status='pending'",
                          (actor['org_id'], lesson_id, student_id)).fetchone():
                raise Problem(409, 'pending_request', '这名学生已有待审批申请')
            key = uid()
            db.execute('INSERT INTO management_requests(id,org_id,lesson_id,student_id,class_id,kind,requested_by,request_ref,digest,reason,expected_revision,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (key, actor['org_id'], lesson_id, student_id, fact['class_id'], kind, actor['id'], request_ref, hashed, reason, expected_revision, 'pending', self.clock()))
            self.record_audit(db, actor, selected, 'attendance_requested', key, kind=kind)
            return self.receipt(db.execute('SELECT * FROM management_requests WHERE id=?', (key,)).fetchone())
        return self.command(token, run)

    def decide(self, token, role, *, request_id, approve, reason, decision_ref):
        return self.command(token,lambda db,actor:self.decide_in_transaction(db,actor,role,
            request_id=request_id,approve=approve,reason=reason,decision_ref=decision_ref))

    def decide_in_transaction(self, db, actor, role, *, request_id, approve, reason, decision_ref):
        """Share the same checked decision inside atomic legacy-request adoption."""
        if type(approve) is not bool:
            raise Problem(422, 'invalid_decision', '请选择审批结果')
        reason, decision_ref = label(reason), label(decision_ref, 80)
        hashed = digest(dict(request=request_id, approve=approve, reason=reason))
        selected = workspace(actor, role)
        row = db.execute('SELECT * FROM management_requests WHERE org_id=? AND id=?',
                         (actor['org_id'], request_id)).fetchone()
        if not row:
            raise Problem(404, 'request_missing', '未找到申请')
        require(actor, selected, 'approvals.decide', scope=self.scope(db, actor),
                target_org=row['org_id'], class_id=row['class_id'])
        prior = db.execute('SELECT * FROM management_requests WHERE org_id=? AND decided_by=? AND decision_ref=?',
                           (actor['org_id'], actor['id'], decision_ref)).fetchone()
        if prior:
            if prior['decision_digest'] != hashed:
                raise Problem(409, 'idempotency_conflict', '同一审批编号不能用于不同内容')
            return self.receipt(prior, True)
        if row['status'] != 'pending':
            raise Problem(409, 'already_decided', '申请已处理，请刷新查看结果')
        now = self.clock()
        if approve:
            require_not_held(db,actor['org_id'],self.clock())
            fact = self.fact(db, actor['org_id'], row['lesson_id'], row['student_id'])
            if fact['class_id'] != row['class_id'] or fact['revision'] != row['expected_revision'] or fact['status'] != 'missing' or not fact['expected']:
                raise Problem(409, 'attendance_changed', '申请后课堂记录已变化，需重新核对；本次未生效')
            if row['kind'] == 'makeup' and now < fact['starts_at']:
                raise Problem(409, 'future_makeup', '课程尚未开始，不能补签')
            historical=None
            if row['lesson_id'].startswith('legacy:'):
                from .historical import approve_context
                historical=approve_context(db,actor['org_id'],row['lesson_id'],row['student_id'],now)
            balance = db.execute('SELECT * FROM management_balances WHERE org_id=? AND student_id=?',
                                 (actor['org_id'], row['student_id'])).fetchone()
            delta = -fact['charge_units'] if row['kind'] == 'makeup' else 0
            status = 'arrived' if row['kind'] == 'makeup' else 'leave'
            db.execute('UPDATE management_attendance SET status=?,revision=revision+1 WHERE org_id=? AND lesson_id=? AND student_id=?',
                       (status, actor['org_id'], row['lesson_id'], row['student_id']))
            if delta:
                db.execute('UPDATE management_balances SET units=units+?,revision=revision+1 WHERE org_id=? AND student_id=?',
                           (delta, actor['org_id'], row['student_id']))
            # Device must apply this event once against BOTH expected revisions.
            # It must not perform its ordinary local attendance deduction again.
            event = dict(protocol=1, org_id=actor['org_id'], event_id=uid(), request_id=row['id'],
                         lesson_id=row['lesson_id'], student_id=row['student_id'], class_id=row['class_id'],
                         starts_at=fact['starts_at'], charge_units=fact['charge_units'],
                         reason=row['reason'], decided_by_name=actor['display_name'],
                         status=status, expected=bool(fact['expected']),
                         previous_attendance_revision=fact['revision'], attendance_revision=fact['revision'] + 1,
                         previous_balance_revision=balance['revision'], balance_revision=balance['revision'] + bool(delta),
                         previous_balance_units=balance['units'],
                         balance_units=balance['units'] + delta, balance_delta=delta,
                         decided_by=actor['id'], effective_at=now)
            if historical:event['historical']=historical
            db.execute('INSERT INTO management_outbox(id,org_id,request_id,payload,created_at) VALUES(?,?,?,?,?)',
                       (event['event_id'], actor['org_id'], row['id'], encoded(event), now))
            if historical:db.execute('INSERT INTO management_historical_events VALUES(?,?,?)',(event['event_id'],actor['org_id'],historical['delivery_class_id']))
        db.execute('UPDATE management_requests SET status=?,decided_at=?,decided_by=?,decision_reason=?,decision_ref=?,decision_digest=? WHERE id=?',
                   ('approved' if approve else 'rejected', now, actor['id'], reason, decision_ref, hashed, row['id']))
        self.record_audit(db, actor, selected, 'attendance_approved' if approve else 'attendance_rejected', row['id'], reason=reason)
        return self.receipt(db.execute('SELECT * FROM management_requests WHERE id=?', (row['id'],)).fetchone())
