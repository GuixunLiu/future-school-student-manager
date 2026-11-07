"""Explicit class assignment with durable, non-financial tablet metadata delivery."""
import hmac
import json

from business.store import Problem,uid
from .approvals import digest,encoded,label
from .delivery import DeliveryStore, deferred_ids
from .migration_guard import require_not_held
from .policy import require,workspace

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_class_versions (
 org_id TEXT NOT NULL,class_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>=0),
 PRIMARY KEY(org_id,class_id),FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_class_commands (
 org_id TEXT NOT NULL,actor_id TEXT NOT NULL,command_ref TEXT NOT NULL,digest TEXT NOT NULL,result_json TEXT NOT NULL,
 PRIMARY KEY(org_id,actor_id,command_ref),FOREIGN KEY(org_id,actor_id) REFERENCES users(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_lesson_teacher_snapshots (
 org_id TEXT NOT NULL,lesson_id TEXT NOT NULL,class_id TEXT NOT NULL,teachers_json TEXT NOT NULL,captured_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,lesson_id),FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_metadata_clients (
 org_id TEXT NOT NULL,device_id TEXT NOT NULL,generation TEXT NOT NULL,protocol INTEGER NOT NULL CHECK(protocol=1),updated_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id,generation),FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_class_teacher_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,org_id TEXT NOT NULL,device_id TEXT NOT NULL,generation TEXT NOT NULL,
 class_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,receipt_token TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','conflict','applied')),acknowledged_at INTEGER,
 UNIQUE(org_id,device_id,generation,class_id,revision),FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_class_command_no_update BEFORE UPDATE ON management_class_commands BEGIN SELECT RAISE(ABORT,'immutable class command'); END;
CREATE TRIGGER IF NOT EXISTS management_class_command_no_delete BEFORE DELETE ON management_class_commands BEGIN SELECT RAISE(ABORT,'immutable class command'); END;
CREATE TRIGGER IF NOT EXISTS management_teacher_snapshot_no_update BEFORE UPDATE ON management_lesson_teacher_snapshots BEGIN SELECT RAISE(ABORT,'immutable teacher snapshot'); END;
CREATE TRIGGER IF NOT EXISTS management_teacher_snapshot_no_delete BEFORE DELETE ON management_lesson_teacher_snapshots BEGIN SELECT RAISE(ABORT,'immutable teacher snapshot'); END;
CREATE TRIGGER IF NOT EXISTS management_class_event_no_update BEFORE UPDATE OF seq,org_id,device_id,generation,class_id,revision,payload,sha256,receipt_token ON management_class_teacher_events BEGIN SELECT RAISE(ABORT,'immutable class event'); END;
CREATE TRIGGER IF NOT EXISTS management_class_event_no_delete BEFORE DELETE ON management_class_teacher_events BEGIN SELECT RAISE(ABORT,'immutable class event'); END;
'''


class ClassTeacherStore(DeliveryStore):
    def initialize_class_teachers(self):
        db=self.connect()
        try:db.executescript('BEGIN IMMEDIATE;\n'+SCHEMA+'\nCOMMIT;')
        finally:db.close()

    @staticmethod
    def teachers(db,org,class_id):
        return [dict(r) for r in db.execute('SELECT u.id,u.display_name FROM teacher_classes t JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id WHERE t.org_id=? AND t.class_id=? ORDER BY u.id',(org,class_id))]

    @staticmethod
    def targets(db,org,class_id):
        return [dict(r) for r in db.execute('''SELECT a.device_id,a.generation,m.protocol FROM device_classes c
          JOIN management_device_access a ON a.org_id=c.org_id AND a.device_id=c.device_id
          LEFT JOIN management_metadata_clients m ON m.org_id=a.org_id AND m.device_id=a.device_id AND m.generation=a.generation
          WHERE c.org_id=? AND c.class_id=? AND a.enabled=1 ORDER BY a.device_id''',(org,class_id))]

    def class_list(self,token,role,*,q='',before=None,limit=50):
        if not isinstance(q,str) or len(q)>80 or type(limit)is not int or not 1<=limit<=100 or before is not None and (not isinstance(before,str) or len(before)>80):raise Problem(422,'invalid_filter','班级查询条件不正确')
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'classes.read',scope=self.scope(db,actor),class_id=None)
            where=['c.org_id=?'];args=[actor['org_id']]
            if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_initializations'").fetchone() and db.execute('SELECT 1 FROM management_initializations WHERE org_id=?',(actor['org_id'],)).fetchone():
                # Retained server-only trial/old classes are history, not current
                # tablet classes. Never infer current status from old names.
                where.append("EXISTS(SELECT 1 FROM management_source_links l WHERE l.org_id=c.org_id AND l.kind='class' AND l.server_id=c.id)")
            if q.strip():where.append('(instr(c.name,?)>0 OR instr(c.course_name,?)>0)');args.extend([q.strip()]*2)
            if before:where.append('c.id<?');args.append(before)
            rows=db.execute('''SELECT c.id,c.name,c.course_name,COALESCE(v.revision,0) AS revision,
              (SELECT COUNT(*) FROM students s WHERE s.org_id=c.org_id AND s.class_id=c.id AND s.active=1) AS students
              FROM classes c LEFT JOIN management_class_versions v ON v.org_id=c.org_id AND v.class_id=c.id
              WHERE '''+' AND '.join(where)+' ORDER BY c.id DESC LIMIT ?',(*args,limit+1)).fetchall()
            items=[]
            for r in rows[:limit]:
                targets=self.targets(db,actor['org_id'],r['id'])
                pending=db.execute("SELECT COUNT(*) FROM management_class_teacher_events WHERE org_id=? AND class_id=? AND status!='applied'",(actor['org_id'],r['id'])).fetchone()[0]
                conflicts=db.execute("SELECT COUNT(*) FROM management_class_teacher_events WHERE org_id=? AND class_id=? AND status='conflict'",(actor['org_id'],r['id'])).fetchone()[0]
                items.append(dict(r,teachers=self.teachers(db,actor['org_id'],r['id']),tablet_bound=bool(targets),tablet_ready=bool(targets) and all(t['protocol']==1 for t in targets),pending_deliveries=pending,conflict_deliveries=conflicts))
            candidates=[dict(r) for r in db.execute("SELECT u.id,u.display_name FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.active=1 AND r.role='teacher' ORDER BY u.display_name,u.id LIMIT 1001",(actor['org_id'],))] if selected=='admin' else []
            if len(candidates)>1000:raise Problem(503,'teacher_scope_exceeded','教师人数较多，请分段核对')
            return dict(items=items,teachers=candidates,next_before=items[-1]['id'] if len(rows)>limit else None,read_only=selected!='admin')
        return self.command(token,run)

    def assign_teacher(self,token,role,*,class_id,teacher_id,expected_revision,reason,command_ref):
        class_id=label(class_id,80);teacher_id=label(teacher_id,80);reason=label(reason);command_ref=label(command_ref,80)
        if type(expected_revision)is not int or expected_revision<0:raise Problem(422,'invalid_revision','班级版本不正确')
        hashed=digest(dict(class_id=class_id,teacher_id=teacher_id,revision=expected_revision,reason=reason))
        def run(db,actor):
            org=actor['org_id'];require(actor,workspace(actor,role),'classes.manage');require_not_held(db,org,self.clock())
            prior=db.execute('SELECT digest,result_json FROM management_class_commands WHERE org_id=? AND actor_id=? AND command_ref=?',(org,actor['id'],command_ref)).fetchone()
            if prior:
                if prior['digest']!=hashed:raise Problem(409,'idempotency_conflict','同一操作编号不能用于不同任课内容')
                return json.loads(prior['result_json'])|dict(duplicate=True)
            c=db.execute('SELECT id,name FROM classes WHERE org_id=? AND id=?',(org,class_id)).fetchone()
            if not c:raise Problem(404,'class_missing','未找到本机构班级')
            if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_initializations'").fetchone() and db.execute('SELECT 1 FROM management_initializations WHERE org_id=?',(org,)).fetchone() and not db.execute("SELECT 1 FROM management_source_links WHERE org_id=? AND kind='class' AND server_id=?",(org,class_id)).fetchone():
                raise Problem(409,'class_not_current','该班级是保留的原服务器资料，不是当前平板班级')
            teacher=db.execute("SELECT u.id,u.display_name FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.id=? AND u.active=1 AND r.role='teacher'",(org,teacher_id)).fetchone()
            if not teacher:raise Problem(409,'teacher_missing','请选择本机构启用中的教师')
            v=db.execute('SELECT revision FROM management_class_versions WHERE org_id=? AND class_id=?',(org,class_id)).fetchone();revision=v[0] if v else 0
            if revision!=expected_revision:raise Problem(409,'class_changed','班级任课已更新，请刷新核对')
            old=self.teachers(db,org,class_id)
            if len(old)!=1:raise Problem(409,'class_teacher_review_required','原任课不是一位教师，需要先核对交接')
            if old[0]['id']==teacher_id:raise Problem(409,'teacher_unchanged','任课教师没有变化')
            targets=self.targets(db,org,class_id)
            if not targets:raise Problem(409,'class_device_missing','该班尚无平板接收授权，请先核对班级接入')
            if any(t['protocol']!=1 for t in targets):raise Problem(409,'tablet_update_required','请先更新并连接平板，使其能接收任课变更')
            local=db.execute("SELECT local_id FROM management_source_links WHERE org_id=? AND kind='class' AND server_id=?",(org,class_id)).fetchone() if targets else None
            if targets and not local:raise Problem(409,'class_mapping_missing','班级与平板编号尚未核对')
            # Freeze each started lesson BEFORE replacing current permissions.
            # Historical reports retain the teacher responsible at lesson time.
            for row in db.execute('SELECT DISTINCT lesson_id FROM management_attendance WHERE org_id=? AND class_id=? AND starts_at<=?',(org,class_id,self.clock())).fetchall():
                db.execute('INSERT OR IGNORE INTO management_lesson_teacher_snapshots VALUES(?,?,?,?,?)',(org,row['lesson_id'],class_id,encoded(old),self.clock()))
            db.execute('DELETE FROM teacher_classes WHERE org_id=? AND class_id=?',(org,class_id));db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(org,teacher_id,class_id))
            db.execute('INSERT INTO management_class_versions VALUES(?,?,?) ON CONFLICT(org_id,class_id) DO UPDATE SET revision=excluded.revision',(org,class_id,revision+1))
            for target in targets:
                event=dict(protocol=1,kind='class_teacher',event_id=uid(),org_id=org,class_id=class_id,local_class_id=local[0],class_name=c['name'],previous_teacher=old[0]['display_name'],teacher=teacher['display_name'],previous_revision=revision,revision=revision+1)
                wire=encoded(event);db.execute("INSERT INTO management_class_teacher_events(org_id,device_id,generation,class_id,revision,payload,sha256,receipt_token,status) VALUES(?,?,?,?,?,?,?,?,'pending')",(org,target['device_id'],target['generation'],class_id,revision+1,wire,digest(event),uid()))
            result=dict(class_id=class_id,teacher_id=teacher_id,revision=revision+1,pending_deliveries=len(targets),balances_changed=False,students_changed=False,duplicate=False)
            self.record_audit(db,actor,'admin','class_teacher_changed',class_id,previous_teacher_ids=[t['id'] for t in old],teacher_id=teacher_id,reason=reason)
            db.execute('INSERT INTO management_class_commands VALUES(?,?,?,?,?)',(org,actor['id'],command_ref,hashed,encoded(result)))
            return result
        return self.command(token,run)

    def metadata_ready(self,token,generation):
        def run(db,actor):
            self.access(db,actor,generation);require_not_held(db,actor['org_id'],self.clock())
            db.execute('INSERT INTO management_metadata_clients VALUES(?,?,?,1,?) ON CONFLICT(org_id,device_id,generation) DO UPDATE SET updated_at=excluded.updated_at',(actor['org_id'],actor['id'],generation,self.clock()))
            return dict(org_id=actor['org_id'],device_id=actor['id'],generation=generation,class_teacher_protocol=1)
        return self.command(token,run)

    def metadata_next(self,token,generation,defer=''):
        deferred=deferred_ids(defer)
        def run(db,actor):
            self.access(db,actor,generation);require_not_held(db,actor['org_id'],self.clock())
            r=db.execute("""SELECT e.* FROM management_class_teacher_events e JOIN device_classes c
                ON c.org_id=e.org_id AND c.device_id=e.device_id AND c.class_id=e.class_id
                WHERE e.org_id=? AND e.device_id=? AND e.generation=? AND e.status!='applied'
                AND e.class_id NOT IN (SELECT value FROM json_each(?)) ORDER BY e.seq LIMIT 1""",
                (actor['org_id'],actor['id'],generation,deferred)).fetchone()
            return dict(org_id=actor['org_id'],device_id=actor['id'],generation=generation,item=None if not r else dict(event_json=r['payload'],sha256=r['sha256'],receipt_token=r['receipt_token'],status=r['status']))
        return self.command(token,run)

    def metadata_ack(self,token,generation,*,class_id,revision,receipt_token,sha256,status):
        if type(revision)is not int or revision<1 or status not in ('applied','conflict') or any(not isinstance(v,str) for v in (class_id,receipt_token,sha256)):raise Problem(422,'invalid_receipt','任课同步回执无效')
        def run(db,actor):
            self.access(db,actor,generation);require_not_held(db,actor['org_id'],self.clock())
            args=(actor['org_id'],actor['id'],generation,class_id,revision)
            r=db.execute('''SELECT e.* FROM management_class_teacher_events e JOIN device_classes c ON c.org_id=e.org_id AND c.device_id=e.device_id AND c.class_id=e.class_id
              WHERE e.org_id=? AND e.device_id=? AND e.generation=? AND e.class_id=? AND e.revision=?''',args).fetchone()
            if not r:raise Problem(404,'delivery_missing','没有属于本平板的任课交付')
            if not hmac.compare_digest(r['receipt_token'],receipt_token) or not hmac.compare_digest(r['sha256'],sha256):raise Problem(409,'receipt_mismatch','任课回执与交付不符')
            if r['status']=='applied' and status!='applied':raise Problem(409,'already_applied','已保存的任课不能退回冲突状态')
            # Do not allow a forged later acknowledgement to skip an earlier event.
            if db.execute("SELECT 1 FROM management_class_teacher_events WHERE org_id=? AND device_id=? AND generation=? AND seq<? AND status!='applied'",(*args[:3],r['seq'])).fetchone():raise Problem(409,'delivery_order','请先保存上一条任课变更')
            duplicate=r['status']==status
            if not duplicate:db.execute('UPDATE management_class_teacher_events SET status=?,acknowledged_at=? WHERE seq=?',(status,self.clock(),r['seq']))
            return dict(class_id=class_id,revision=revision,status=status,duplicate=duplicate)
        return self.command(token,run)
