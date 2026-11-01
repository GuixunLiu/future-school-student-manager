"""Candidate-only durable delivery. No implicit grant or live mirror cutover.

An explicit, reconciled migration must provision a generation and exact student
bindings. Only one canonical writer device is bound to a student. Poll returns
one oldest unacknowledged event, preventing a client cursor from skipping it.
Acknowledgements report device persistence; they NEVER mutate a balance.
"""
import hashlib
import hmac
import json
import re

from business.store import Problem, uid
from .assessments import AssessmentStore
from .uplink import UplinkOperations, SCHEMA as UPLINK_SCHEMA

SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_device_access (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, generation TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), expires_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id),
 FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_device_students (
 org_id TEXT NOT NULL, student_id TEXT NOT NULL, device_id TEXT NOT NULL,
 class_id TEXT NOT NULL,
 PRIMARY KEY(org_id,student_id),
 FOREIGN KEY(org_id,student_id) REFERENCES management_balances(org_id,student_id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_deliveries (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, generation TEXT NOT NULL,
 event_id TEXT NOT NULL, receipt_token TEXT NOT NULL, sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','conflict','applied')),
 conflict_code TEXT, offered_at INTEGER NOT NULL, acknowledged_at INTEGER,
 PRIMARY KEY(org_id,device_id,generation,event_id),
 FOREIGN KEY(event_id) REFERENCES management_outbox(id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id)
);
'''
CONFLICTS = frozenset({'mapping_changed', 'local_changes', 'attendance_changed',
                      'balance_changed', 'invalid_event', 'storage_failed'})


class DeliveryStore(UplinkOperations, AssessmentStore):
    def delivery_status(self, db, org, request_id, request_status):
        if request_status != 'approved':
            return 'not_created'
        row = db.execute('''SELECT d.status FROM management_requests r
          JOIN management_outbox o ON o.request_id=r.id AND o.org_id=r.org_id
          JOIN management_device_students b ON b.org_id=r.org_id AND b.student_id=r.student_id AND b.class_id=r.class_id
          JOIN management_device_access a ON a.org_id=b.org_id AND a.device_id=b.device_id
          JOIN devices v ON v.org_id=a.org_id AND v.id=a.device_id
          JOIN device_classes c ON c.org_id=b.org_id AND c.device_id=b.device_id AND c.class_id=b.class_id
          LEFT JOIN management_deliveries d ON d.org_id=a.org_id AND d.device_id=a.device_id
            AND d.generation=a.generation AND d.event_id=o.id
          WHERE r.org_id=? AND r.id=? AND a.enabled=1 AND a.expires_at>? AND v.active=1 AND v.expires_at>?''',
          (org, request_id, self.clock(), self.clock())).fetchone()
        return (row['status'] or 'pending') if row else 'unassigned'

    def initialize(self):
        super().initialize()
        db = self.connect()
        try:
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\n' + UPLINK_SCHEMA + '\nCOMMIT;')
        finally:
            db.close()

    def access(self, db, actor, generation):
        if actor['kind'] != 'device':
            raise Problem(403, 'device_required', '仅已绑定的平板可接收同步结果')
        row = db.execute('SELECT * FROM management_device_access WHERE org_id=? AND device_id=?',
                         (actor['org_id'], actor['id'])).fetchone()
        if not row or not row['enabled'] or row['expires_at'] <= self.clock() or row['generation'] != generation:
            raise Problem(403, 'delivery_not_granted', '平板同步授权未启用、已到期或已更换，请重新核对')

    def eligible_event(self, db, actor, event_id=None):
        # Recheck current binding and device scope on EVERY poll/ack, including
        # duplicate acknowledgements after a class/identity authorization change.
        rows = db.execute('''SELECT o.* FROM management_outbox o
          JOIN management_requests r ON r.id=o.request_id AND r.org_id=o.org_id
          JOIN management_device_students b ON b.org_id=r.org_id AND b.student_id=r.student_id AND b.class_id=r.class_id
          JOIN device_classes c ON c.org_id=b.org_id AND c.device_id=b.device_id AND c.class_id=b.class_id
          WHERE o.org_id=? AND b.device_id=? AND (? IS NULL OR o.id=?) ORDER BY o.seq''',
          (actor['org_id'], actor['id'], event_id, event_id))
        return rows

    def poll(self, token, generation):
        def run(db, actor):
            self.access(db, actor, generation)
            # SQL excludes completed delivery, so an institution's complete
            # history is not materialized into memory on every poll.
            event = db.execute('''SELECT o.* FROM management_outbox o
              JOIN management_requests r ON r.id=o.request_id AND r.org_id=o.org_id
              JOIN management_device_students b ON b.org_id=r.org_id AND b.student_id=r.student_id AND b.class_id=r.class_id
              JOIN device_classes c ON c.org_id=b.org_id AND c.device_id=b.device_id AND c.class_id=b.class_id
              WHERE o.org_id=? AND b.device_id=? AND NOT EXISTS (
                SELECT 1 FROM management_deliveries d WHERE d.org_id=o.org_id AND d.device_id=?
                  AND d.generation=? AND d.event_id=o.id AND d.status='applied')
              ORDER BY o.seq LIMIT 1''', (actor['org_id'], actor['id'], actor['id'], generation)).fetchone()
            base = dict(org_id=actor['org_id'], device_id=actor['id'], generation=generation)
            if not event:
                return base | {'item': None}
            sha = hashlib.sha256(event['payload'].encode('utf-8')).hexdigest()
            db.execute('''INSERT OR IGNORE INTO management_deliveries
              (org_id,device_id,generation,event_id,receipt_token,sha256,status,offered_at)
              VALUES(?,?,?,?,?,?,'pending',?)''',
              (actor['org_id'], actor['id'], generation, event['id'], uid(), sha, self.clock()))
            delivery = db.execute('SELECT * FROM management_deliveries WHERE org_id=? AND device_id=? AND generation=? AND event_id=?',
                                  (actor['org_id'], actor['id'], generation, event['id'])).fetchone()
            return base | {'item': {'event_json': event['payload'], 'sha256': sha,
                'receipt_token': delivery['receipt_token'], 'status': delivery['status'],
                'conflict_code': delivery['conflict_code']}}
        return self.command(token, run)

    def acknowledge(self, token, generation, *, event_id, receipt_token, sha256,
                    status, attendance_revision=None, balance_revision=None, balance_units=None,
                    conflict_code=None):
        if (status not in {'applied', 'conflict'} or not isinstance(sha256, str) or not isinstance(receipt_token, str)
                or not re.fullmatch('[0-9a-f]{64}', sha256) or not re.fullmatch('[0-9a-f]{32}', receipt_token)):
            raise Problem(422, 'invalid_receipt', '同步回执无效')
        if status == 'conflict' and conflict_code not in CONFLICTS:
            raise Problem(422, 'invalid_conflict', '同步冲突类型无效')
        def run(db, actor):
            self.access(db, actor, generation)
            event = self.eligible_event(db, actor, event_id).fetchone()
            row = db.execute('SELECT * FROM management_deliveries WHERE org_id=? AND device_id=? AND generation=? AND event_id=?',
                             (actor['org_id'], actor['id'], generation, event_id)).fetchone()
            if not event or not row:
                raise Problem(404, 'delivery_missing', '没有已交付给本平板的同步记录')
            if not hmac.compare_digest(row['receipt_token'], receipt_token) or not hmac.compare_digest(row['sha256'], sha256):
                raise Problem(409, 'receipt_mismatch', '同步回执与交付内容不符')
            payload = json.loads(event['payload'])
            if status == 'applied' and (any(type(v) is not int for v in (attendance_revision, balance_revision, balance_units)) or
                (attendance_revision, balance_revision, balance_units) !=
                (payload['attendance_revision'], payload['balance_revision'], payload['balance_units'])):
                raise Problem(409, 'receipt_mismatch', '平板保存后的资料版本与审批结果不符')
            if row['status'] == 'applied':
                if status != 'applied':
                    raise Problem(409, 'receipt_already_applied', '该结果已经保存，不能退回未应用状态')
                return {'event_id': event_id, 'status': 'applied', 'duplicate': True}
            duplicate = row['status'] == status and row['conflict_code'] == conflict_code
            if not duplicate:
                db.execute('''UPDATE management_deliveries SET status=?,conflict_code=?,acknowledged_at=?
                  WHERE org_id=? AND device_id=? AND generation=? AND event_id=?''',
                  (status, conflict_code if status == 'conflict' else None, self.clock(), actor['org_id'], actor['id'], generation, event_id))
                self.record_audit(db, actor, 'device', 'delivery_' + status, event_id,
                                  generation=generation, conflict_code=conflict_code if status == 'conflict' else None)
            return {'event_id': event_id, 'status': status, 'duplicate': duplicate}
        return self.command(token, run)
