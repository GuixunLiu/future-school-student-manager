"""Shared transaction-local canonical write barrier, optional for older fixtures."""
from business.store import Problem

def has_active_writer(db,org,now,hold_id=None):
    return bool(db.execute("SELECT 1 FROM management_activations a JOIN management_migration_holds h ON h.id=a.hold_id AND h.org_id=a.org_id JOIN management_device_access g ON g.org_id=a.org_id AND g.device_id=a.device_id AND g.generation=a.generation JOIN devices d ON d.org_id=a.org_id AND d.id=a.device_id WHERE a.org_id=? AND a.phase='active' AND h.state='held' AND a.active_until>? AND g.enabled=1 AND g.expires_at>? AND d.active=1 AND d.expires_at>? AND (? IS NULL OR h.id=?) LIMIT 1",(org,now,now,now,hold_id,hold_id)).fetchone())


def require_not_held(db,org,now):
    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_runtime_admissions'").fetchone():
        staged=db.execute('SELECT 1 FROM management_runtime_admissions WHERE org_id=?',(org,)).fetchone()
        if staged and not has_active_writer(db,org,now):
            raise Problem(409,'migration_in_progress','服务已准备，等待平板完成双端确认，暂未应用操作')
    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_migration_holds'").fetchone():
        holds=list(db.execute("SELECT id FROM management_migration_holds WHERE org_id=? AND state='held'",(org,)))
        has_activation=db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_activations'").fetchone()
        for hold in holds:
            if has_activation and has_active_writer(db,org,now,hold['id']):
                continue
            raise Problem(409,'migration_in_progress','正在核对迁移，暂未应用操作；请保留本机记录，等待管理员完成核对')
