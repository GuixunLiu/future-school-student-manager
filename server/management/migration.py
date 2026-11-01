"""Explicit reversible preparation barrier; this module cannot activate a device.

The legacy mirror dataset is database-wide. Its pause therefore covers the
whole database, even when the canonical pilot binds only one class.
"""
import hashlib
from business.store import Problem,uid
from .approvals import digest,encoded,label
from .policy import workspace,require

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_migration_holds (
 id TEXT PRIMARY KEY,org_id TEXT NOT NULL,device_id TEXT NOT NULL,generation TEXT NOT NULL,
 review_id TEXT NOT NULL,profile_id TEXT NOT NULL,baseline_sha256 TEXT NOT NULL,server_sha256 TEXT NOT NULL,
 frozen_sha256 TEXT NOT NULL,command_ref TEXT NOT NULL,command_digest TEXT NOT NULL,reason TEXT NOT NULL,
 prepared_by TEXT NOT NULL,created_at INTEGER NOT NULL,proof_expires_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('held','cancelled')),cancelled_by TEXT,cancelled_at INTEGER,
 cancel_ref TEXT,cancel_digest TEXT,cancel_reason TEXT,
 UNIQUE(org_id,command_ref),UNIQUE(org_id,cancel_ref),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(review_id) REFERENCES management_enrollment_reviews(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS management_one_hold_per_org ON management_migration_holds(org_id) WHERE state='held';
'''


class MigrationOperations:
    def migration_snapshot_digest(self,db,org):
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        hashed=hashlib.sha256()
        def feed(value):
            part=encoded(value).encode('utf-8');hashed.update(str(len(part)).encode('ascii')+b':'+part)
        feed(['migration-snapshot-v1',org])
        def scan(table,where='',args=()):
            if table not in tables:return
            feed(['table',table])
            count=len(list(db.execute('PRAGMA table_info('+table+')')))
            order=','.join(str(n) for n in range(1,count+1))
            for r in db.execute('SELECT * FROM '+table+where+' ORDER BY '+order,args):feed(dict(r))
        for table in ('users','students','classes','teacher_classes','roles','management_roles','management_approval_scopes',
                      'devices','device_classes','management_device_access','management_balances',
                      'management_attendance','management_outbox','management_uploads','management_deliveries','management_device_students'):
            scan(table,' WHERE org_id=?',(org,))
        for table in ('web_dataset','web_mirror_assignments','web_mirror_status','web_mirror_devices','web_mirror_receipts','web_makeup_requests'):
            scan(table)
        return hashed.hexdigest()

    @staticmethod
    def hold_receipt(row,duplicate=False,db=None):
        mirror_paused=bool(db.execute("SELECT 1 FROM management_migration_holds WHERE state='held' LIMIT 1").fetchone()) if db is not None else row['state']=='held'
        return dict(hold_id=row['id'],state=row['state'],created_at=row['created_at'],proof_expires_at=row['proof_expires_at'],
                    duplicate=duplicate,activated=False,writes_paused=row['state']=='held',automatic_release=False,
                    mirror_writes_paused=mirror_paused,mirror_pause_scope='database',canonical_pause_scope='organization')

    def prepare_migration(self,token,role,*,review_id,profile_id,reason,command_ref,accept_database_mirror_pause):
        reason,command_ref=label(reason),label(command_ref,80)
        if accept_database_mirror_pause is not True:raise Problem(422,'mirror_pause_confirmation_required','需要确认暂停本数据库全部旧镜像写入')
        hashed=digest(dict(review_id=review_id,profile_id=profile_id,reason=reason,accept_database_mirror_pause=True))
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            old=db.execute('SELECT * FROM management_migration_holds WHERE org_id=? AND command_ref=?',(actor['org_id'],command_ref)).fetchone()
            if old:
                if old['command_digest']!=hashed:raise Problem(409,'idempotency_conflict','同一准备编号不能改变内容')
                return self.hold_receipt(old,True,db)
            if db.execute("SELECT 1 FROM management_migration_holds WHERE org_id=? AND state='held'",(actor['org_id'],)).fetchone():
                raise Problem(409,'migration_already_held','已有迁移核对暂停，请先核对原记录')
            review=db.execute('SELECT * FROM management_enrollment_reviews WHERE org_id=? AND id=?',(actor['org_id'],review_id)).fetchone()
            if not review or review['expires_at']<=self.clock():raise Problem(409,'review_expired','资料核对已失效')
            p=db.execute('''SELECT * FROM management_enrollment_proofs WHERE org_id=? AND device_id=? AND generation=?
              AND profile_id=?''',(actor['org_id'],review['device_id'],review['generation'],profile_id)).fetchone()
            if not p or p['expires_at']<=self.clock() or (p['baseline_sha256'],p['server_sha256'])!=(review['baseline_sha256'],review['server_sha256']):
                raise Problem(409,'migration_proof_required','没有与核对记录对应的有效暂存授权')
            device=db.execute('SELECT 1 FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',(actor['org_id'],p['device_id'],self.clock())).fetchone()
            if not device:raise Problem(403,'device_not_granted','设备授权已失效')
            self.access(db,dict(kind='device',org_id=actor['org_id'],id=p['device_id']),p['generation'])
            if self.enrollment_ledger_digest(db,actor['org_id'],p['device_id'])!=p['server_sha256']:
                raise Problem(409,'migration_baseline_changed','核对后资料已变化，本次未暂停')
            key=uid();frozen=self.migration_snapshot_digest(db,actor['org_id'])
            db.execute('''INSERT INTO management_migration_holds
              (id,org_id,device_id,generation,review_id,profile_id,baseline_sha256,server_sha256,frozen_sha256,
               command_ref,command_digest,reason,prepared_by,created_at,proof_expires_at,state)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'held')''',
              (key,actor['org_id'],p['device_id'],p['generation'],review_id,profile_id,p['baseline_sha256'],p['server_sha256'],
               frozen,command_ref,hashed,reason,actor['id'],self.clock(),min(review['expires_at'],p['expires_at'])))
            self.record_audit(db,actor,selected,'migration_held',key,reason=reason,mirror_pause_scope='database',activated=False)
            return self.hold_receipt(db.execute('SELECT * FROM management_migration_holds WHERE id=?',(key,)).fetchone(),db=db)
        return self.command(token,run)

    def cancel_migration(self,token,role,*,hold_id,reason,command_ref):
        reason,command_ref=label(reason),label(command_ref,80);hashed=digest(dict(hold_id=hold_id,reason=reason))
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            row=db.execute('SELECT * FROM management_migration_holds WHERE org_id=? AND id=?',(actor['org_id'],hold_id)).fetchone()
            if not row:raise Problem(404,'migration_missing','未找到本机构迁移准备记录')
            old=db.execute('SELECT * FROM management_migration_holds WHERE org_id=? AND cancel_ref=?',(actor['org_id'],command_ref)).fetchone()
            if old:
                if old['cancel_digest']!=hashed:raise Problem(409,'idempotency_conflict','同一取消编号不能改变内容')
                return self.hold_receipt(old,True,db)
            if row['state']!='held':raise Problem(409,'migration_not_held','迁移准备已结束')
            if self.migration_snapshot_digest(db,actor['org_id'])!=row['frozen_sha256']:
                raise Problem(409,'migration_frozen_data_changed','暂停后资料发生变化，保留暂停并人工核对，不能恢复旧写入')
            db.execute("UPDATE management_migration_holds SET state='cancelled',cancelled_by=?,cancelled_at=?,cancel_ref=?,cancel_digest=?,cancel_reason=? WHERE id=?",
                       (actor['id'],self.clock(),command_ref,hashed,reason,hold_id))
            self.record_audit(db,actor,selected,'migration_cancelled',hold_id,reason=reason,balances_restored=False)
            return self.hold_receipt(db.execute('SELECT * FROM management_migration_holds WHERE id=?',(hold_id,)).fetchone(),db=db)
        return self.command(token,run)
