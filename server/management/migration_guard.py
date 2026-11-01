"""Shared transaction-local canonical write barrier, optional for older fixtures."""
from business.store import Problem

def require_not_held(db,org):
    if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_migration_holds'").fetchone():
        if db.execute("SELECT 1 FROM management_migration_holds WHERE org_id=? AND state='held'",(org,)).fetchone():
            raise Problem(409,'migration_in_progress','正在核对迁移，暂未应用操作；请保留本机记录，等待管理员完成核对')
