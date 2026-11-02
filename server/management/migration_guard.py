"""Shared transaction-local canonical write barrier, optional for older fixtures."""
from business.store import Problem

def require_not_held(db,org,now):
    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_runtime_admissions'").fetchone():
        staged=db.execute('SELECT 1 FROM management_runtime_admissions WHERE org_id=?',(org,)).fetchone()
        if staged and not db.execute("SELECT 1 FROM management_activations WHERE org_id=? AND phase='active' AND active_until>?",(org,now)).fetchone():
            raise Problem(409,'migration_in_progress','服务已准备，等待平板完成双端确认，暂未应用操作')
    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_migration_holds'").fetchone():
        holds=list(db.execute("SELECT id FROM management_migration_holds WHERE org_id=? AND state='held'",(org,)))
        has_activation=db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_activations'").fetchone()
        for hold in holds:
            if has_activation and db.execute("SELECT 1 FROM management_activations WHERE hold_id=? AND org_id=? AND phase='active' AND active_until>?",(hold['id'],org,now)).fetchone():
                continue
            raise Problem(409,'migration_in_progress','正在核对迁移，暂未应用操作；请保留本机记录，等待管理员完成核对')
