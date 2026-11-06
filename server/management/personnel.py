"""Explicitly installed personnel management for the next admin update.

Does not change students, balances, device grants or class assignments. Existing
teaching names cannot change until the class metadata delivery path is ready.
"""
import hashlib
import json
import secrets

from business.security import identifier, verify_password, ITERATIONS
from business.store import Problem, uid
from .approvals import digest, encoded, label
from .delivery import DeliveryStore
from .policy import ROLES, require, workspace

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_staff_versions (
 org_id TEXT NOT NULL,user_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>=0),
 PRIMARY KEY(org_id,user_id),FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_staff_commands (
 org_id TEXT NOT NULL,actor_id TEXT NOT NULL,command_ref TEXT NOT NULL,
 digest TEXT NOT NULL,password_proof TEXT,result_json TEXT NOT NULL,created_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,actor_id,command_ref),FOREIGN KEY(org_id,actor_id) REFERENCES users(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_staff_command_no_update BEFORE UPDATE ON management_staff_commands
 BEGIN SELECT RAISE(ABORT,'immutable personnel command'); END;
CREATE TRIGGER IF NOT EXISTS management_staff_command_no_delete BEFORE DELETE ON management_staff_commands
 BEGIN SELECT RAISE(ABORT,'immutable personnel command'); END;
'''


def password_proof(value):
    # Keep the owner's explicit six-character password policy here; do not
    # weaken the frozen bootstrap password validator or log raw passwords.
    if not isinstance(value,str) or not 6<=len(value)<=128 or len(value.encode())>1024:
        raise Problem(422,'invalid_password','密码需为6至128位')
    salt=secrets.token_hex(16)
    return 'pbkdf2_sha256$'+str(ITERATIONS)+'$'+salt+'$'+hashlib.pbkdf2_hmac('sha256',value.encode(),bytes.fromhex(salt),ITERATIONS).hex()


class PersonnelStore(DeliveryStore):
    def initialize_personnel(self):
        """Explicit candidate migration; never called during an HTTP request."""
        db=self.connect()
        try:db.executescript('BEGIN IMMEDIATE;\n'+SCHEMA+'\nCOMMIT;')
        finally:db.close()

    @staticmethod
    def staff_record(db,org,user_id):
        user=db.execute('''SELECT u.id,u.login,u.display_name,u.active,COALESCE(v.revision,0) AS revision FROM users u
          LEFT JOIN management_staff_versions v ON v.org_id=u.org_id AND v.user_id=u.id WHERE u.org_id=? AND u.id=?''',(org,user_id)).fetchone()
        if not user:raise Problem(404,'staff_missing','未找到此人员')
        base={r[0] for r in db.execute('SELECT role FROM roles WHERE org_id=? AND user_id=?',(org,user_id))}
        extra={r[0] for r in db.execute('SELECT role FROM management_roles WHERE org_id=? AND user_id=?',(org,user_id))}
        held=(base|extra)&ROLES
        if not held:raise Problem(404,'staff_missing','此账号不是工作人员账号')
        return dict(user,active=bool(user['active']),roles=sorted(held),
            teaching_class_ids=[r[0] for r in db.execute('SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=? ORDER BY class_id',(org,user_id))],
            approval_class_ids=[r[0] for r in db.execute('SELECT class_id FROM management_approval_scopes WHERE org_id=? AND user_id=? ORDER BY class_id',(org,user_id))])

    def staff_list(self,token,role,*,q='',before=None,limit=50):
        if not isinstance(q,str) or len(q)>80 or type(limit)is not int or not 1<=limit<=100 or before is not None and (not isinstance(before,str) or len(before)>80):
            raise Problem(422,'invalid_filter','人员查询条件不正确')
        def run(db,actor):
            require(actor,workspace(actor,role),'accounts.manage')
            where=['u.org_id=?',"(EXISTS(SELECT 1 FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role IN ('admin','teacher')) OR EXISTS(SELECT 1 FROM management_roles m WHERE m.org_id=u.org_id AND m.user_id=u.id))"]
            args=[actor['org_id']]
            if q.strip():where.append('(instr(u.login,?)>0 OR instr(u.display_name,?)>0)');args.extend([q.strip(),q.strip()])
            if before:where.append('u.id<?');args.append(before)
            rows=db.execute('SELECT u.id FROM users u WHERE '+' AND '.join(where)+' ORDER BY u.id DESC LIMIT ?',(*args,limit+1)).fetchall()
            items=[self.staff_record(db,actor['org_id'],r['id']) for r in rows[:limit]]
            classes=[dict(r) for r in db.execute('SELECT id,name FROM classes WHERE org_id=? ORDER BY name,id LIMIT 1001',(actor['org_id'],))]
            if len(classes)>1000:raise Problem(503,'class_scope_exceeded','班级较多，请分段核对审批范围')
            return dict(items=items,classes=classes,next_before=items[-1]['id'] if len(rows)>limit else None,selected_role='admin')
        return self.command(token,run)

    def staff_save(self,token,role,*,user_id=None,login,display_name,roles,active,approval_class_ids,
                   expected_revision=None,password=None,reason,command_ref):
        try:login=identifier(login)
        except (AttributeError,ValueError):raise Problem(422,'invalid_login','账号需为3至64位字母、数字、点、下划线或连字符')
        display_name=label(display_name,80);reason=label(reason,200);command_ref=label(command_ref,80)
        if (not isinstance(roles,list) or not 1<=len(roles)<=4 or any(not isinstance(v,str) or v not in ROLES for v in roles) or len(set(roles))!=len(roles)
            or type(active)is not bool or not isinstance(approval_class_ids,list) or len(approval_class_ids)>1000
            or any(not isinstance(v,str) or not 1<=len(v)<=80 for v in approval_class_ids) or len(set(approval_class_ids))!=len(approval_class_ids)
            or approval_class_ids and 'assessor' not in roles or user_id is not None and (not isinstance(user_id,str) or not 1<=len(user_id)<=80)
            or user_id is not None and (type(expected_revision)is not int or expected_revision<0)
            or user_id is None and expected_revision is not None):
            raise Problem(422,'invalid_personnel','身份、审批范围或资料版本不正确')
        if password is not None and (not isinstance(password,str) or not 6<=len(password)<=128 or len(password.encode())>1024):
            raise Problem(422,'invalid_password','密码需为6至128位')
        if user_id is None and password is None:raise Problem(422,'password_required','新增人员需要设置初始密码')
        hashed=digest(dict(user_id=user_id,login=login,name=display_name,roles=sorted(roles),active=active,
            scopes=sorted(approval_class_ids),revision=expected_revision,password_supplied=password is not None,reason=reason))
        def run(db,actor):
            require(actor,workspace(actor,role),'accounts.manage')
            org=actor['org_id']
            prior=db.execute('SELECT * FROM management_staff_commands WHERE org_id=? AND actor_id=? AND command_ref=?',(org,actor['id'],command_ref)).fetchone()
            if prior:
                if prior['digest']!=hashed or password is not None and not verify_password(password,prior['password_proof']):
                    raise Problem(409,'idempotency_conflict','同一操作编号不能用于不同内容')
                return json.loads(prior['result_json'])|dict(duplicate=True)
            previous=self.staff_record(db,org,user_id) if user_id is not None else None
            if previous and previous['revision']!=expected_revision:raise Problem(409,'staff_changed','人员资料已更新，请刷新后重新核对')
            existing=db.execute('SELECT id FROM users WHERE org_id=? AND login=?',(org,login)).fetchone()
            if existing and existing['id']!=user_id:raise Problem(409,'login_exists','这个账号已被使用')
            for class_id in approval_class_ids:
                if not db.execute('SELECT 1 FROM classes WHERE org_id=? AND id=?',(org,class_id)).fetchone():raise Problem(404,'class_missing','审批范围中有不存在或无权操作的班级')
            if previous and previous['teaching_class_ids']:
                if not active or 'teacher' not in roles:raise Problem(409,'teaching_handoff_required','该教师仍有任课班级，请先完成任课交接')
                if previous['display_name']!=display_name:raise Problem(409,'teaching_name_sync_required','任课教师姓名需要与班级资料一并同步，当前请保留姓名')
            if previous and previous['active'] and 'admin' in previous['roles'] and (not active or 'admin' not in roles):
                count=db.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.active=1 AND r.role='admin'",(org,)).fetchone()[0]
                if count<=1:raise Problem(409,'last_admin','需保留至少一名启用中的管理员')
            proof=password_proof(password) if password is not None else None
            key=user_id or uid()
            if previous:
                db.execute('UPDATE users SET login=?,display_name=?,active=? WHERE org_id=? AND id=?',(login,display_name,int(active),org,key))
                if proof:db.execute('UPDATE users SET password_hash=? WHERE org_id=? AND id=?',(proof,org,key))
            else:db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(key,org,login,display_name,proof,int(active),self.clock()))
            # Parent roles/links, teacher assignments and all class/device data
            # remain intact. Only the four selected staff identities change.
            db.execute("DELETE FROM roles WHERE org_id=? AND user_id=? AND role IN ('admin','teacher')",(org,key))
            db.execute('DELETE FROM management_roles WHERE org_id=? AND user_id=?',(org,key))
            for value in sorted(roles):
                db.execute('INSERT INTO '+('roles' if value in {'admin','teacher'} else 'management_roles')+' VALUES(?,?,?)',(org,key,value))
            db.execute('DELETE FROM management_approval_scopes WHERE org_id=? AND user_id=?',(org,key))
            for class_id in sorted(approval_class_ids):db.execute('INSERT INTO management_approval_scopes VALUES(?,?,?)',(org,key,class_id))
            revision=(previous['revision']+1) if previous else 0
            db.execute('INSERT INTO management_staff_versions VALUES(?,?,?) ON CONFLICT(org_id,user_id) DO UPDATE SET revision=excluded.revision',(org,key,revision))
            if previous:db.execute('DELETE FROM sessions WHERE org_id=? AND user_id=?',(org,key))
            self.record_audit(db,actor,'admin','staff_updated' if previous else 'staff_created',key,
                roles=sorted(roles),active=active,approval_class_ids=sorted(approval_class_ids),password_reset=proof is not None,reason=reason)
            result=dict(id=key,revision=revision,duplicate=False,created=previous is None,
                sessions_revoked=previous is not None,force_relogin=key==actor['id'],students_changed=False,balances_changed=False)
            db.execute('INSERT INTO management_staff_commands VALUES(?,?,?,?,?,?,?)',(org,actor['id'],command_ref,hashed,proof,encoded(result),self.clock()))
            return result
        return self.command(token,run)
