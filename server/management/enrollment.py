"""Explicit baseline-bound enrollment proofs. Never infers or grants a device.

Migration tooling must provision proofs AFTER reconciliation and current admin
authorization. plan() is a device-only read, not an activation command.
"""
import json
import re
from business.store import Problem,uid
from .approvals import digest,encoded
from .policy import workspace,require
from .segmented_enrollment import SegmentedEnrollmentOperations,SCHEMA as SEGMENTED_SCHEMA

SCHEMA = '''
CREATE TABLE IF NOT EXISTS management_enrollment_proofs (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, generation TEXT NOT NULL,
 profile_id TEXT NOT NULL, nonce TEXT NOT NULL, baseline_sha256 TEXT NOT NULL, server_sha256 TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id,generation), UNIQUE(org_id,profile_id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id)
);
CREATE TABLE IF NOT EXISTS management_enrollment_reviews (
 id TEXT PRIMARY KEY,org_id TEXT NOT NULL,device_id TEXT NOT NULL,generation TEXT NOT NULL,
 baseline_sha256 TEXT NOT NULL,server_sha256 TEXT NOT NULL,source_map TEXT NOT NULL,
 reviewed_by TEXT NOT NULL,source_captured_at INTEGER NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(org_id,reviewed_by) REFERENCES users(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_review_no_update
 BEFORE UPDATE ON management_enrollment_reviews BEGIN SELECT RAISE(ABORT,'immutable review'); END;
CREATE TRIGGER IF NOT EXISTS management_review_no_delete
 BEFORE DELETE ON management_enrollment_reviews BEGIN SELECT RAISE(ABORT,'immutable review'); END;
'''


SCHEMA+=SEGMENTED_SCHEMA

class EnrollmentOperations(SegmentedEnrollmentOperations):
    def review_enrollment(self,token,role,*,device_id,generation,baseline_sha256,source_captured_at,source):
        if not isinstance(baseline_sha256,str) or not re.fullmatch('[a-f0-9]{64}',baseline_sha256):
            raise Problem(422,'invalid_baseline','资料摘要无效')
        def run(db,actor):
            return self._record_enrollment_review(db,actor,role=role,device_id=device_id,generation=generation,
                baseline_sha256=baseline_sha256,source_captured_at=source_captured_at,source=source)
        return self.command(token,run)

    def _record_enrollment_review(self,db,actor,*,role,device_id,generation,baseline_sha256,source_captured_at,source):
        selected=workspace(actor,role);require(actor,selected,'settings.manage')
        if type(source_captured_at)is not int or source_captured_at<0 or source_captured_at>self.clock()+120 or self.clock()-source_captured_at>=900:
            raise Problem(409,'source_not_fresh','资料采集时间已过期或超前，请重新读取平板当前资料')
        device=db.execute('SELECT * FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',
                          (actor['org_id'],device_id,self.clock())).fetchone()
        if not device:raise Problem(403,'device_not_granted','没有可核对的设备授权')
        self.access(db,dict(kind='device',org_id=actor['org_id'],id=device_id),generation)
        def refuse():raise Problem(409,'migration_source_differs','平板清单与服务器资料不一致，请保留差异并核对')
        def rows(key,fields,limit):
            value=source.get(key)
            if not isinstance(value,list) or len(value)>limit:refuse()
            for row in value:
                if not isinstance(row,dict) or set(row)!=set(fields.split()):refuse()
            return value
        if not isinstance(source,dict) or set(source)!={'students','classes','facts'}:refuse()
        students=rows('students','server_id local_id external_ref name class_id class_local_id balance_units',2000)
        classes=rows('classes','server_id local_id name teacher_id teacher_name',1000)
        facts=rows('facts','lesson_id student_id status expected charge_units starts_at',10000)
        if not students or not classes:refuse()
        def local_ids(value):
            ids=[r['local_id'] for r in value]
            if any(type(x)is not int or not 0<x<=9007199254740991 for x in ids) or len(set(ids))!=len(ids):refuse()
            names=[r['server_id'] for r in value]
            if any(not isinstance(x,str) or not x for x in names) or len(set(names))!=len(names):refuse()
        local_ids(students);local_ids(classes)
        bound=list(db.execute('SELECT * FROM management_device_students WHERE org_id=? AND device_id=? ORDER BY student_id',
                              (actor['org_id'],device_id)))
        if {r['server_id'] for r in students}!={r['student_id'] for r in bound}:refuse()
        class_map={r['server_id']:r for r in classes}
        if set(class_map)!={r['class_id'] for r in bound}:refuse()
        bound_map={r['student_id']:r['class_id'] for r in bound}
        for c in classes:
            remote=db.execute('SELECT * FROM classes WHERE org_id=? AND id=?',(actor['org_id'],c['server_id'])).fetchone()
            teachers=list(db.execute('''SELECT u.id,u.display_name FROM teacher_classes t JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id
              JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher'
              WHERE t.org_id=? AND t.class_id=? AND u.active=1''',(actor['org_id'],c['server_id'])))
            if not remote or remote['name']!=c['name'] or len(teachers)!=1 or teachers[0]['id']!=c['teacher_id'] or teachers[0]['display_name']!=c['teacher_name']:refuse()
            if not db.execute('SELECT 1 FROM device_classes WHERE org_id=? AND device_id=? AND class_id=?',(actor['org_id'],device_id,c['server_id'])).fetchone():refuse()
        refs=[]
        for s in students:
            remote=db.execute('''SELECT s.*,b.units FROM students s JOIN management_balances b ON b.org_id=s.org_id AND b.student_id=s.id
              WHERE s.org_id=? AND s.id=?''',(actor['org_id'],s['server_id'])).fetchone()
            if (not remote or not remote['active'] or s['class_id']!=bound_map[s['server_id']] or remote['class_id']!=s['class_id']
                or s['class_local_id']!=class_map[s['class_id']]['local_id'] or type(s['class_local_id'])is not int
                or remote['name']!=s['name'] or not s['external_ref'] or remote['external_ref']!=s['external_ref']
                or type(s['balance_units'])is not int or remote['units']!=s['balance_units']):refuse()
            refs.append(s['external_ref'])
        if len(set(refs))!=len(refs):refuse()
        actual=[dict(r) for r in db.execute('''SELECT a.lesson_id,a.student_id,a.status,a.expected,a.charge_units,a.starts_at FROM management_attendance a
          JOIN management_device_students b ON b.org_id=a.org_id AND b.student_id=a.student_id
          WHERE b.org_id=? AND b.device_id=? ORDER BY a.lesson_id,a.student_id''',(actor['org_id'],device_id))]
        for f in facts:
            if type(f['expected'])is not bool or type(f['charge_units'])is not int or type(f['starts_at'])is not int:refuse()
        normalized=[dict(f,expected=int(f['expected'])) for f in facts]
        if sorted(encoded(r) for r in normalized)!=sorted(encoded(r) for r in actual):refuse()
        checksum=self.enrollment_ledger_digest(db,actor['org_id'],device_id)
        key=uid();expires=min(self.clock()+900,source_captured_at+900)
        db.execute('INSERT INTO management_enrollment_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (key,actor['org_id'],device_id,generation,baseline_sha256,checksum,encoded(source),actor['id'],source_captured_at,self.clock(),expires))
        self.record_audit(db,actor,selected,'enrollment_reviewed',key,device_id=device_id,students=len(students),classes=len(classes),facts=len(facts))
        return dict(review_id=key,baseline_sha256=baseline_sha256,server_sha256=checksum,expires_at=expires,
                    ready_for_enrollment=True,students=len(students),classes=len(classes),facts=len(facts))

    def authorize_enrollment(self,token,role,*,review_id,profile_id,nonce):
        if any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{32}',v) for v in (profile_id,nonce)):
            raise Problem(422,'invalid_enrollment','设备申请标识无效')
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            review=db.execute('SELECT * FROM management_enrollment_reviews WHERE org_id=? AND id=?',(actor['org_id'],review_id)).fetchone()
            if not review or review['expires_at']<=self.clock():raise Problem(409,'review_expired','资料核对已失效，请重新核对')
            device=db.execute('SELECT * FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',(actor['org_id'],review['device_id'],self.clock())).fetchone()
            if not device:raise Problem(403,'device_not_granted','设备授权已失效')
            self.access(db,dict(kind='device',id=device['id'],org_id=actor['org_id']),review['generation'])
            if review['server_sha256']!=self.enrollment_ledger_digest(db,actor['org_id'],device['id']):
                raise Problem(409,'migration_baseline_changed','核对后资料已变化，本次未授权')
            grant=db.execute('SELECT * FROM management_device_access WHERE org_id=? AND device_id=?',(actor['org_id'],device['id'])).fetchone()
            expiry=min(review['expires_at'],grant['expires_at'],device['expires_at'])
            old=db.execute('SELECT * FROM management_enrollment_proofs WHERE org_id=? AND device_id=? AND generation=?',(actor['org_id'],device['id'],review['generation'])).fetchone()
            if old:
                if (old['profile_id'],old['nonce'],old['baseline_sha256'],old['server_sha256'])!=(profile_id,nonce,review['baseline_sha256'],review['server_sha256']):
                    raise Problem(409,'enrollment_exists','该代次已有其他连接授权，不覆盖原授权')
                if old['expires_at']<=self.clock():
                    db.execute('UPDATE management_enrollment_proofs SET expires_at=? WHERE org_id=? AND device_id=? AND generation=?',
                               (expiry,actor['org_id'],device['id'],review['generation']))
                    self.record_audit(db,actor,selected,'enrollment_renewed',profile_id,review_id=review_id,previous_expiry=old['expires_at'],expires_at=expiry)
                    return dict(profile_id=profile_id,expires_at=expiry,staged_only=True,duplicate=False,renewed=True)
                return dict(profile_id=profile_id,expires_at=old['expires_at'],staged_only=True,duplicate=True)
            if db.execute('SELECT 1 FROM management_enrollment_proofs WHERE org_id=? AND profile_id=?',(actor['org_id'],profile_id)).fetchone():
                raise Problem(409,'profile_id_used','连接标识已使用')
            db.execute('INSERT INTO management_enrollment_proofs VALUES(?,?,?,?,?,?,?,?,?)',
                       (actor['org_id'],device['id'],review['generation'],profile_id,nonce,review['baseline_sha256'],review['server_sha256'],expiry,self.clock()))
            self.record_audit(db,actor,selected,'enrollment_authorized',profile_id,review_id=review_id,device_id=device['id'],staged_only=True)
            return dict(profile_id=profile_id,expires_at=expiry,staged_only=True,duplicate=False)
        return self.command(token,run)

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
        teachers=[dict(r) for r in db.execute('''SELECT DISTINCT t.class_id,t.user_id,u.display_name,u.active,
          EXISTS(SELECT 1 FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher') AS teacher_role FROM teacher_classes t
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
