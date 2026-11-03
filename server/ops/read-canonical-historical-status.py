"""Fixed read-only post-upgrade audit. Returns counts and digests, no records."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

WORK=Path('/opt/wxnotify/app/.management-cutover-3fqe7xfx')
BACKUP=WORK/'.historical-request-upgrade-6qncgh40/business.sqlite'
TABLES=('users','roles','management_roles','teacher_classes','classes','students','management_balances',
        'management_attendance','management_requests','management_outbox','management_initializations',
        'management_legacy_rows','management_initialization_seals','management_openings','web_dataset')

def fingerprint(db,name):
    values=sorted(json.dumps(list(r),ensure_ascii=True,separators=(',',':')) for r in db.execute('SELECT * FROM '+name))
    return hashlib.sha256(('\n'.join(values)).encode()).hexdigest()

def run():
    assert WORK.is_absolute() and not WORK.is_symlink() and not BACKUP.is_symlink()
    with closing(sqlite3.connect((WORK/'business.sqlite').as_uri()+'?mode=ro',uri=True)) as db,closing(sqlite3.connect(BACKUP.as_uri()+'?mode=ro',uri=True)) as old:
        db.execute('PRAGMA query_only=ON');old.execute('PRAGMA query_only=ON')
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        preserved={name:fingerprint(db,name)==fingerprint(old,name) for name in TABLES}
        # Cast current time explicitly because SQLite storage-class ordering must
        # not turn a numeric expiry vs text comparison into a false negative.
        clients=db.execute('''SELECT COUNT(*) FROM management_historical_clients h JOIN management_device_access a ON a.org_id=h.org_id AND a.device_id=h.device_id AND a.generation=h.generation JOIN devices d ON d.org_id=h.org_id AND d.id=h.device_id WHERE h.protocol=1 AND a.enabled=1 AND d.active=1 AND min(a.expires_at,d.expires_at)>CAST(strftime('%s','now') AS INTEGER)''').fetchone()[0]
        result=dict(read_only=True,original_tables_unchanged=preserved,historical_ready_devices=clients,
                    historical_admitted_facts=db.execute('SELECT COUNT(*) FROM management_historical_facts').fetchone()[0],
                    historical_approval_events=db.execute('SELECT COUNT(*) FROM management_historical_events').fetchone()[0])
    result['services']={name:subprocess.run(['systemctl','--user','is-active',name],capture_output=True,text=True,timeout=10).stdout.strip() for name in ('wxnotify-canonical-management.service','wxnotify-canonical-gateway.service')}
    return result

if __name__=='__main__':print(json.dumps(run(),separators=(',',':')))
