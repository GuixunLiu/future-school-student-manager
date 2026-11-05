"""Two-end activation; old mirror stays fenced, recovery never restores balances."""
import json
import re
from business.store import Problem,uid
from .approvals import digest,encoded,label
from .policy import workspace,require

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_activations (
 id TEXT PRIMARY KEY,org_id TEXT NOT NULL,hold_id TEXT NOT NULL UNIQUE,
 device_id TEXT NOT NULL,generation TEXT NOT NULL,profile_id TEXT NOT NULL,
 baseline_sha256 TEXT NOT NULL,server_sha256 TEXT NOT NULL,source_map_sha256 TEXT NOT NULL,
 bootstrap_sha256 TEXT NOT NULL,nonce TEXT NOT NULL,offer_expires_at INTEGER NOT NULL,
 active_until INTEGER NOT NULL,created_by TEXT NOT NULL,created_at INTEGER NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('offered','active','paused')),revision INTEGER NOT NULL CHECK(revision>=0),committed_at INTEGER,
 FOREIGN KEY(hold_id) REFERENCES management_migration_holds(id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(org_id,created_by) REFERENCES users(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_activation_commands (
 org_id TEXT NOT NULL,actor_id TEXT NOT NULL,command_ref TEXT NOT NULL,
 digest TEXT NOT NULL,activation_id TEXT NOT NULL,response TEXT NOT NULL,
 PRIMARY KEY(org_id,actor_id,command_ref),FOREIGN KEY(activation_id) REFERENCES management_activations(id)
);
CREATE TRIGGER IF NOT EXISTS management_activation_command_no_update BEFORE UPDATE ON management_activation_commands
 BEGIN SELECT RAISE(ABORT,'immutable activation command'); END;
CREATE TRIGGER IF NOT EXISTS management_activation_command_no_delete BEFORE DELETE ON management_activation_commands
 BEGIN SELECT RAISE(ABORT,'immutable activation command'); END;
'''


def checksum(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise Problem(422,'invalid_activation_digest','启用资料摘要无效')


class ActivationOperations:
    def renew_active_writer(self, token, *, activation_id, expected_expiry, expires_at, reason, command_ref):
        reason, command_ref = label(reason), label(command_ref, 80)
        if type(expected_expiry) is not int or type(expires_at) is not int or expires_at <= expected_expiry:
            raise Problem(422,'invalid_activation_expiry','续期必须延长当前授权')
        hashed=digest(dict(activation_id=activation_id,expected_expiry=expected_expiry,expires_at=expires_at,reason=reason))
        def run(db,actor):
            require(actor,'admin','settings.manage')
            prior=self._activation_prior(db,actor,command_ref,hashed)
            if prior:return prior
            now=self.clock()
            if not now < expected_expiry < expires_at <= now+90*86400:
                raise Problem(422,'invalid_activation_expiry','只能续期未到期授权，最远续至90天后')
            row=self._activation_row(db,actor['org_id'],activation_id)
            org,device,generation=row['org_id'],row['device_id'],row['generation']
            hold=db.execute("SELECT state FROM management_migration_holds WHERE id=? AND org_id=?",(row['hold_id'],org)).fetchone()
            grant=db.execute('SELECT generation,enabled,expires_at FROM management_device_access WHERE org_id=? AND device_id=?',(org,device)).fetchone()
            hardware=db.execute('SELECT active,expires_at FROM devices WHERE org_id=? AND id=?',(org,device)).fetchone()
            if (row['phase']!='active' or row['active_until']!=expected_expiry or not hold or hold['state']!='held'
                or not grant or tuple(grant)!=(generation,1,expected_expiry)
                or not hardware or tuple(hardware)!=(1,expected_expiry)):
                raise Problem(409,'activation_changed','当前平板授权、期限或启用状态与核对结果不一致')
            db.execute('UPDATE devices SET expires_at=? WHERE org_id=? AND id=?',(expires_at,org,device))
            db.execute('UPDATE management_device_access SET expires_at=? WHERE org_id=? AND device_id=? AND generation=?',(expires_at,org,device,generation))
            db.execute('UPDATE management_activations SET active_until=?,revision=revision+1 WHERE id=?',(expires_at,row['id']))
            result=dict(activation_id=row['id'],previous_expiry=expected_expiry,expires_at=expires_at,duplicate=False)
            db.execute('INSERT INTO management_activation_commands VALUES(?,?,?,?,?,?)',(org,actor['id'],command_ref,hashed,row['id'],encoded(result)))
            self.record_audit(db,actor,'admin','activation_renewed',row['id'],reason=reason,previous_expiry=expected_expiry,expires_at=expires_at)
            return result
        return self.command(token,run)

    @staticmethod
    def _activation_row(db,org,key):
        row=db.execute('SELECT * FROM management_activations WHERE org_id=? AND id=?',(org,key)).fetchone()
        if not row:raise Problem(404,'activation_missing','未找到本机构启用记录')
        return row

    @staticmethod
    def _activation_prior(db,actor,ref,hashed):
        old=db.execute('SELECT * FROM management_activation_commands WHERE org_id=? AND actor_id=? AND command_ref=?',(actor['org_id'],actor['id'],ref)).fetchone()
        if old and old['digest']!=hashed:raise Problem(409,'idempotency_conflict','同一启用或恢复编号不能改变内容')
        return json.loads(old['response'])|dict(duplicate=True) if old else None

    def _activation_device(self,db,actor,generation,row):
        self.access(db,actor,generation)
        if actor['id']!=row['device_id'] or generation!=row['generation']:raise Problem(403,'activation_device_differs','此连接不属于当前平板')
        hold=db.execute('SELECT * FROM management_migration_holds WHERE id=? AND org_id=?',(row['hold_id'],row['org_id'])).fetchone()
        if not hold or hold['state']!='held':raise Problem(409,'activation_barrier_missing','迁移写入隔离未保持')
        if row['active_until']<=self.clock():raise Problem(403,'activation_expired','同步启用授权已到期')
        if row['phase']=='paused':raise Problem(409,'migration_in_progress','同步已暂停，本机记录保留，等待管理员核对')
        if row['phase']=='offered':
            if row['offer_expires_at']<=self.clock():raise Problem(409,'activation_offer_expired','本次连接核对已过期，未开放新写入')
            admin=db.execute("SELECT 1 FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin' WHERE u.org_id=? AND u.id=? AND u.active=1",(row['org_id'],row['created_by'])).fetchone()
            if not admin or self.enrollment_ledger_digest(db,row['org_id'],row['device_id'])!=row['server_sha256'] or self.migration_snapshot_digest(db,row['org_id'])!=hold['frozen_sha256']:
                raise Problem(409,'migration_baseline_changed','核对后的资料或授权已变，未启用同步')

    def activation_status(self,token,role,activation_id):
        def run(db,actor):
            require(actor,workspace(actor,role),'settings.manage');row=self._activation_row(db,actor['org_id'],activation_id)
            return dict(activation_id=row['id'],phase=row['phase'],revision=row['revision'],ledger_sha256=self.enrollment_ledger_digest(db,row['org_id'],row['device_id']),old_mirror_writable=False,balances_restored=False)
        return self.command(token,run)

    def offer_activation(self,token,role,*,hold_id,bootstrap_sha256,source_map_sha256,reason,command_ref):
        checksum(bootstrap_sha256);checksum(source_map_sha256);reason=label(reason);command_ref=label(command_ref,80)
        hashed=digest(dict(action='offer',hold_id=hold_id,bootstrap_sha256=bootstrap_sha256,source_map_sha256=source_map_sha256,reason=reason))
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            prior=self._activation_prior(db,actor,command_ref,hashed)
            if prior:return prior
            hold=db.execute("SELECT * FROM management_migration_holds WHERE org_id=? AND id=? AND state='held'",(actor['org_id'],hold_id)).fetchone()
            if not hold:raise Problem(409,'activation_hold_required','请先完成迁移暂停核对')
            if db.execute('SELECT 1 FROM management_activations WHERE hold_id=?',(hold_id,)).fetchone():raise Problem(409,'activation_exists','此暂停已有启用记录，请核对原记录')
            proof=db.execute('SELECT * FROM management_enrollment_proofs WHERE org_id=? AND device_id=? AND generation=? AND profile_id=?',(actor['org_id'],hold['device_id'],hold['generation'],hold['profile_id'])).fetchone()
            review=db.execute('SELECT * FROM management_enrollment_reviews WHERE id=? AND org_id=?',(hold['review_id'],actor['org_id'])).fetchone()
            if not proof or not review or min(proof['expires_at'],review['expires_at'],hold['proof_expires_at'])<=self.clock():raise Problem(409,'review_expired','本次资料核对授权已过期')
            device=db.execute('SELECT * FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',(actor['org_id'],hold['device_id'],self.clock())).fetchone()
            if not device:raise Problem(403,'device_not_granted','平板授权已失效')
            self.access(db,dict(kind='device',org_id=actor['org_id'],id=device['id']),hold['generation'])
            all_students={r[0] for r in db.execute('SELECT student_id FROM management_balances WHERE org_id=?',(actor['org_id'],))}
            bound_students={r[0] for r in db.execute('SELECT student_id FROM management_device_students WHERE org_id=? AND device_id=?',(actor['org_id'],device['id']))}
            if not all_students or all_students!=bound_students:raise Problem(409,'whole_device_scope_required','当前平板切换需要覆盖全部在读规范名单')
            if (digest(json.loads(review['source_map']))!=source_map_sha256 or proof['server_sha256']!=hold['server_sha256']
                or self.enrollment_ledger_digest(db,actor['org_id'],device['id'])!=hold['server_sha256']
                or self.migration_snapshot_digest(db,actor['org_id'])!=hold['frozen_sha256']):raise Problem(409,'migration_baseline_changed','启用资料与已暂停基线不一致')
            grant=db.execute('SELECT * FROM management_device_access WHERE org_id=? AND device_id=?',(actor['org_id'],device['id'])).fetchone()
            key=uid();expiry=min(proof['expires_at'],review['expires_at'],hold['proof_expires_at']);active_until=min(device['expires_at'],grant['expires_at'],self.clock()+30*86400)
            db.execute("INSERT INTO management_activations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'offered',0,NULL)",(key,actor['org_id'],hold_id,device['id'],hold['generation'],hold['profile_id'],hold['baseline_sha256'],hold['server_sha256'],source_map_sha256,bootstrap_sha256,uid(),expiry,active_until,actor['id'],self.clock()))
            result=dict(activation_id=key,phase='offered',revision=0,offer_expires_at=expiry,active_until=active_until,device_commit_required=True,old_mirror_writable=False,duplicate=False)
            db.execute('INSERT INTO management_activation_commands VALUES(?,?,?,?,?,?)',(actor['org_id'],actor['id'],command_ref,hashed,key,encoded(result)))
            self.record_audit(db,actor,selected,'activation_offered',key,reason=reason,bootstrap_sha256=bootstrap_sha256)
            return result
        return self.command(token,run)

    def activation_plan(self,token,generation,*,profile_id):
        def run(db,actor):
            self.access(db,actor,generation)
            rows=list(db.execute('SELECT * FROM management_activations WHERE org_id=? AND device_id=? AND generation=? AND profile_id=?',(actor['org_id'],actor['id'],generation,profile_id)))
            if len(rows)!=1:raise Problem(404,'activation_missing','尚无本平板的启用授权')
            row=rows[0];self._activation_device(db,actor,generation,row)
            return dict(protocol=1,mode='canonical_activation',org_id=row['org_id'],device_id=row['device_id'],generation=row['generation'],profile_id=row['profile_id'],activation_id=row['id'],nonce=row['nonce'],baseline_sha256=row['baseline_sha256'],bootstrap_sha256=row['bootstrap_sha256'],source_map_sha256=row['source_map_sha256'],offer_expires_at=row['offer_expires_at'],active_until=row['active_until'],phase=row['phase'],old_mirror_writable=False)
        return self.command(token,run)

    def commit_activation(self,token,generation,*,activation_id,profile_id,nonce,bootstrap_sha256,source_map_sha256):
        checksum(bootstrap_sha256);checksum(source_map_sha256)
        def run(db,actor):
            self.access(db,actor,generation);row=self._activation_row(db,actor['org_id'],activation_id)
            if (row['profile_id'],row['nonce'],row['bootstrap_sha256'],row['source_map_sha256'])!=(profile_id,nonce,bootstrap_sha256,source_map_sha256):raise Problem(409,'activation_commit_differs','平板保存回执与启用授权不一致')
            self._activation_device(db,actor,generation,row);duplicate=row['phase']=='active'
            if not duplicate:
                db.execute("UPDATE management_activations SET phase='active',revision=revision+1,committed_at=? WHERE id=?",(self.clock(),row['id']))
                self.record_audit(db,actor,'device','activation_committed',row['id'],bootstrap_sha256=bootstrap_sha256,balances_changed=False)
            return dict(protocol=1,activation_id=row['id'],org_id=row['org_id'],device_id=row['device_id'],generation=row['generation'],profile_id=row['profile_id'],bootstrap_sha256=row['bootstrap_sha256'],source_map_sha256=row['source_map_sha256'],active_until=row['active_until'],phase='active',old_mirror_writable=False,balances_changed=False,duplicate=duplicate)
        return self.command(token,run)

    def change_activation(self,token,role,*,activation_id,action,expected_revision,ledger_sha256,reason,command_ref):
        checksum(ledger_sha256);reason=label(reason);command_ref=label(command_ref,80)
        if action not in ('pause','resume') or type(expected_revision)is not int or expected_revision<0:raise Problem(422,'invalid_activation_change','暂停恢复内容无效')
        hashed=digest(dict(activation_id=activation_id,action=action,expected_revision=expected_revision,ledger_sha256=ledger_sha256,reason=reason))
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            prior=self._activation_prior(db,actor,command_ref,hashed)
            if prior:return prior
            row=self._activation_row(db,actor['org_id'],activation_id)
            if row['revision']!=expected_revision or row['phase']!=('active' if action=='pause' else 'paused'):raise Problem(409,'activation_changed','启用状态已变，请刷新核对')
            if self.enrollment_ledger_digest(db,row['org_id'],row['device_id'])!=ledger_sha256:raise Problem(409,'migration_baseline_changed','核对后业务已变，保留当前状态，请重新核对')
            if action=='resume':
                device=db.execute('SELECT 1 FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',(row['org_id'],row['device_id'],self.clock())).fetchone()
                if not device or row['active_until']<=self.clock():raise Problem(403,'device_not_granted','原平板授权已失效，不自动续期或换设备')
                self.access(db,dict(kind='device',org_id=row['org_id'],id=row['device_id']),row['generation'])
            phase='paused' if action=='pause' else 'active';db.execute('UPDATE management_activations SET phase=?,revision=revision+1 WHERE id=?',(phase,row['id']))
            result=dict(activation_id=row['id'],phase=phase,revision=row['revision']+1,old_mirror_writable=False,balances_restored=False,duplicate=False)
            db.execute('INSERT INTO management_activation_commands VALUES(?,?,?,?,?,?)',(actor['org_id'],actor['id'],command_ref,hashed,row['id'],encoded(result)))
            self.record_audit(db,actor,selected,'activation_'+action,row['id'],reason=reason,balances_restored=False)
            return result
        return self.command(token,run)
