"""Explicit baseline-bound enrollment proofs. Never infers or grants a device.

Migration tooling must provision proofs AFTER reconciliation and current admin
authorization. plan() is a device-only read, not an activation command.
"""
from business.store import Problem
from .approvals import digest

SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_enrollment_proofs (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, generation TEXT NOT NULL,
 profile_id TEXT NOT NULL, nonce TEXT NOT NULL, baseline_sha256 TEXT NOT NULL, server_sha256 TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id,generation), UNIQUE(org_id,profile_id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id)
);
'''


class EnrollmentOperations:
    def enrollment_ledger_digest(self, db, org, device):
        args=(org,device)
        bindings=[dict(r) for r in db.execute('SELECT student_id,class_id FROM management_device_students WHERE org_id=? AND device_id=? ORDER BY student_id',args)]
        balances=[dict(r) for r in db.execute('''SELECT b.student_id,b.units,b.revision FROM management_balances b
          JOIN management_device_students s ON s.org_id=b.org_id AND s.student_id=b.student_id
          WHERE s.org_id=? AND s.device_id=? ORDER BY b.student_id''',args)]
        facts=[dict(r) for r in db.execute('''SELECT a.lesson_id,a.student_id,a.class_id,a.starts_at,a.expected,a.charge_units,a.status,a.revision
          FROM management_attendance a JOIN management_device_students s ON s.org_id=a.org_id AND s.student_id=a.student_id
          WHERE s.org_id=? AND s.device_id=? ORDER BY a.lesson_id,a.student_id''',args)]
        scopes=[r[0] for r in db.execute('SELECT class_id FROM device_classes WHERE org_id=? AND device_id=? ORDER BY class_id',args)]
        students=[dict(r) for r in db.execute('''SELECT s.id,s.class_id,s.name,s.external_ref,s.active FROM students s
          JOIN management_device_students b ON b.org_id=s.org_id AND b.student_id=s.id
          WHERE b.org_id=? AND b.device_id=? ORDER BY s.id''',args)]
        classes=[dict(r) for r in db.execute('''SELECT DISTINCT c.id,c.name,c.course_name FROM classes c
          JOIN management_device_students b ON b.org_id=c.org_id AND b.class_id=c.id
          WHERE b.org_id=? AND b.device_id=? ORDER BY c.id''',args)]
        teachers=[dict(r) for r in db.execute('''SELECT DISTINCT t.class_id,t.user_id,u.display_name,u.active FROM teacher_classes t
          JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id
          JOIN management_device_students b ON b.org_id=t.org_id AND b.class_id=t.class_id
          WHERE b.org_id=? AND b.device_id=? ORDER BY t.class_id,t.user_id''',args)]
        return digest(dict(bindings=bindings,balances=balances,facts=facts,scopes=scopes,students=students,classes=classes,teachers=teachers))

    def enrollment_plan(self, token, generation):
        def run(db, actor):
            self.access(db, actor, generation)
            row = db.execute('''SELECT p.*,v.expires_at AS device_expiry,a.expires_at AS grant_expiry
              FROM management_enrollment_proofs p JOIN devices v ON v.org_id=p.org_id AND v.id=p.device_id
              JOIN management_device_access a ON a.org_id=p.org_id AND a.device_id=p.device_id AND a.generation=p.generation
              WHERE p.org_id=? AND p.device_id=? AND p.generation=?''',
              (actor['org_id'], actor['id'], generation)).fetchone()
            if not row or row['expires_at'] <= self.clock():
                raise Problem(403, 'migration_proof_required', '未找到有效的资料核对授权，不启用新同步')
            bindings = db.execute('''SELECT COUNT(*) FROM management_device_students b JOIN device_classes c
              ON c.org_id=b.org_id AND c.device_id=b.device_id AND c.class_id=b.class_id WHERE b.org_id=? AND b.device_id=?''',
                                  (actor['org_id'], actor['id'])).fetchone()[0]
            total=db.execute('SELECT COUNT(*) FROM management_device_students WHERE org_id=? AND device_id=?',
                             (actor['org_id'],actor['id'])).fetchone()[0]
            if not bindings or bindings!=total:
                raise Problem(403, 'migration_binding_required', '尚未核对设备学生范围')
            if row['server_sha256'] != self.enrollment_ledger_digest(db,actor['org_id'],actor['id']):
                raise Problem(409,'migration_baseline_changed','核对后服务器资料发生变化，需重新核对基线')
            return dict(protocol=1, mode='canonical_enrollment_staging', org_id=actor['org_id'], device_id=actor['id'],
                        generation=generation, profile_id=row['profile_id'], nonce=row['nonce'],
                        baseline_sha256=row['baseline_sha256'], expires_at=min(row['expires_at'], row['device_expiry'], row['grant_expiry']),
                        enabled=False, activation_required=True)
        return self.command(token, run)
