"""Explicit admission of a sealed full copy, still blocked pending device commit.

The rehearsal marker is never rewritten. Original history/account tables stay
intact, and future missing facts cost nothing until an actual approved arrival.
"""
import json

from business.store import Problem, uid
from .approvals import digest, encoded, label
from .history_boundary import load as boundary_for
from .policy import workspace, require

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_runtime_admissions (
 org_id TEXT PRIMARY KEY, initialization_id TEXT NOT NULL UNIQUE,
 id TEXT NOT NULL UNIQUE, baseline_sha256 TEXT NOT NULL,
 schedule_sha256 TEXT NOT NULL, schedule_json TEXT NOT NULL,
 source_captured_at INTEGER NOT NULL, created_by TEXT NOT NULL,
 created_at INTEGER NOT NULL, command_ref TEXT NOT NULL, reason TEXT NOT NULL,
 FOREIGN KEY(org_id,created_by) REFERENCES users(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_runtime_admission_no_update BEFORE UPDATE ON management_runtime_admissions
 BEGIN SELECT RAISE(ABORT,'immutable runtime admission'); END;
CREATE TRIGGER IF NOT EXISTS management_runtime_admission_no_delete BEFORE DELETE ON management_runtime_admissions
 BEGIN SELECT RAISE(ABORT,'immutable runtime admission'); END;
'''


def runtime_admitted(db,org):
    """Startup admission, NOT an activation or mutable-balance checksum."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if 'management_runtime_admissions' not in tables:return False
    row=db.execute('SELECT * FROM management_runtime_admissions WHERE org_id=?',(org,)).fetchone()
    if not row:return False
    boundary=boundary_for(db,org)
    schedule=json.loads(row['schedule_json'])
    if (not boundary or row['initialization_id']!=boundary['initialization_id']
        or row['baseline_sha256']!=boundary['baseline_sha256'] or digest(schedule)!=row['schedule_sha256']
        or schedule.get('boundary_sha256')!=boundary['boundary_sha256']):
        raise ValueError('Explicit runtime admission does not match sealed history')
    return True


class StagingOperations:
    def stage_runtime(self,token,role,*,schedule,source_captured_at,reason,command_ref):
        reason=label(reason);command_ref=label(command_ref,80)
        def refuse():raise Problem(409,'runtime_admission_differs','新课次、已封存资料或期初不一致，未切换服务')
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            if len(encoded(schedule).encode())>8*1024*1024:refuse()
            hashed=digest(schedule)
            old=db.execute('SELECT * FROM management_runtime_admissions WHERE org_id=?',(actor['org_id'],)).fetchone()
            if old:
                if (old['created_by'],old['command_ref'],old['schedule_sha256'],old['reason'],old['source_captured_at'])!=(actor['id'],command_ref,hashed,reason,source_captured_at):refuse()
                return dict(admission_id=old['id'],duplicate=True,activated=False,balances_changed=False)
            now=self.clock();boundary=boundary_for(db,actor['org_id'])
            if (type(source_captured_at)is not int or source_captured_at>now+120 or now-source_captured_at>=900
                or not boundary or now>=boundary['canonical_from'] or source_captured_at>=boundary['canonical_from']):
                raise Problem(409,'source_not_fresh','请在同轮读取平板当前资料，不沿用过期连接核对')
            if (not isinstance(schedule,dict) or schedule.get('format')!='future-school-canonical-schedule-v1'
                or schedule.get('org_id')!=actor['org_id'] or schedule.get('initialization_id')!=boundary['initialization_id']
                or schedule.get('baseline_sha256')!=boundary['baseline_sha256'] or schedule.get('boundary_sha256')!=boundary['boundary_sha256']
                or schedule.get('canonical_from')!=boundary['canonical_from'] or type(schedule.get('until'))is not int
                or not boundary['canonical_from']<schedule['until']<=boundary['canonical_from']+14*86400
                or schedule.get('offline_only')is not True or schedule.get('balances_changed')is not False or schedule.get('history_replayed')is not False):refuse()
            source=schedule.get('source')
            if not isinstance(source,dict) or set(source)!={'classes','students','facts'} or schedule.get('source_sha256')!=digest(source):refuse()
            for table in ('management_attendance','management_requests','management_outbox','management_uploads','management_device_access','management_migration_holds','management_activations'):
                if db.execute('SELECT 1 FROM '+table+' WHERE org_id=? LIMIT 1',(actor['org_id'],)).fetchone():refuse()
            openings={r['student_id']:r for r in db.execute('SELECT * FROM management_openings WHERE org_id=?',(actor['org_id'],))}
            balances={r['student_id']:r for r in db.execute('SELECT * FROM management_balances WHERE org_id=?',(actor['org_id'],))}
            if set(openings)!=set(balances) or any((r['units'],r['revision'])!=(balances[k]['units'],balances[k]['revision']) for k,r in openings.items()):refuse()
            links={(r['kind'],r['local_id']):r['server_id'] for r in db.execute('SELECT * FROM management_source_links WHERE org_id=?',(actor['org_id'],))}
            def rows(key,fields,limit):
                values=source.get(key)
                if not isinstance(values,list) or len(values)>limit or any(not isinstance(r,dict) or set(r)!=set(fields.split()) for r in values):refuse()
                return values
            classes=rows('classes','server_id local_id name teacher_id teacher_name',1000)
            students=rows('students','server_id local_id external_ref name class_id class_local_id balance_units',2000)
            facts=rows('facts','lesson_id student_id status expected charge_units starts_at',10000)
            class_map={r['server_id']:r for r in classes};student_map={r['server_id']:r for r in students}
            if len(class_map)!=len(classes) or len(student_map)!=len(students) or set(student_map)!=set(openings):refuse()
            if set(class_map)!={r['class_id'] for r in openings.values()}:refuse()
            for c in classes:
                remote=db.execute('SELECT name FROM classes WHERE org_id=? AND id=?',(actor['org_id'],c['server_id'])).fetchone()
                teachers=list(db.execute("SELECT u.id,u.display_name FROM teacher_classes t JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher' WHERE t.org_id=? AND t.class_id=? AND u.active=1",(actor['org_id'],c['server_id'])))
                if (type(c['local_id'])is not int or links.get(('class',c['local_id']))!=c['server_id'] or not remote or remote['name']!=c['name']
                    or len(teachers)!=1 or (teachers[0]['id'],teachers[0]['display_name'])!=(c['teacher_id'],c['teacher_name'])):refuse()
            for s in students:
                r=db.execute('SELECT * FROM students WHERE org_id=? AND id=?',(actor['org_id'],s['server_id'])).fetchone();o=openings[s['server_id']]
                if (type(s['local_id'])is not int or links.get(('student',s['local_id']))!=s['server_id'] or not r or not r['active']
                    or (r['name'],r['external_ref'],r['class_id'])!=(s['name'],s['external_ref'],s['class_id'])
                    or s['class_id']!=o['class_id'] or s['class_local_id']!=class_map[s['class_id']]['local_id']
                    or type(s['balance_units'])is not int or s['balance_units']!=o['units']):refuse()
            seen=set();days=set();lessons={}
            for f in facts:
                s=student_map.get(f['student_id']);key=(f['lesson_id'],f['student_id'])
                if (not s or not isinstance(f['lesson_id'],str) or len(f['lesson_id'])!=32 or any(c not in '0123456789abcdef' for c in f['lesson_id'])
                    or f['status']!='missing' or f['expected']is not True or type(f['charge_units'])is not int or f['charge_units']!=100
                    or type(f['starts_at'])is not int or not boundary['canonical_from']<=f['starts_at']<schedule['until'] or key in seen):refuse()
                day_key=(s['server_id'],(f['starts_at']+28800)//86400)
                if day_key in days:refuse()
                current=(s['class_id'],f['starts_at'])
                if lessons.setdefault(f['lesson_id'],current)!=current:refuse()
                seen.add(key);days.add(day_key)
            for f in facts:
                db.execute("INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,'missing',0)",(actor['org_id'],f['lesson_id'],f['student_id'],student_map[f['student_id']]['class_id'],f['starts_at']))
            key=uid()
            db.execute('INSERT INTO management_runtime_admissions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(actor['org_id'],boundary['initialization_id'],key,boundary['baseline_sha256'],hashed,encoded(schedule),source_captured_at,actor['id'],now,command_ref,reason))
            self.record_audit(db,actor,selected,'runtime_staged',key,reason=reason,lessons=len(lessons),facts=len(facts),balances_changed=False)
            return dict(admission_id=key,duplicate=False,activated=False,balances_changed=False,lessons=len(lessons),facts=len(facts),device_commit_required=True)
        return self.command(token,run)
