"""Fixed code-only update of the already active canonical classroom service.

No migration, account creation, device provisioning, database restore, arbitrary
path or command input. The original gateway and tablet identity stay running.
"""
import base64
from contextlib import closing
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time

WORK=Path('/opt/wxnotify/app/.management-cutover-3fqe7xfx')
PYTHON=Path('/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python')
UNIT='wxnotify-canonical-management.service'
FILES=frozenset({'management/api.py','management/reports.py','management/web/workspace.js','management/web/workspace.css'})
sha=lambda b:hashlib.sha256(b).hexdigest()


def private(p,directory=False):
    if os.name!='posix' or not p.is_absolute() or any(v.is_symlink() for v in (p,*p.parents)):raise ValueError('Private Linux path required')
    s=p.stat()
    if s.st_uid!=os.getuid() or s.st_mode&0o077 or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):raise ValueError('Private owned path required')


def service(action):
    p=subprocess.run(['systemctl','--user',action,UNIT],capture_output=True,text=True,timeout=45)
    if p.returncode:raise ValueError('Canonical service action failed')
    return p.stdout.strip()


def health():
    c=HTTPConnection('127.0.0.1',18098,timeout=3)
    try:
        c.request('GET','/health/ready',headers={'Host':'127.0.0.1:18098'});r=c.getresponse();raw=r.read(32769)
        if r.status!=200 or len(raw)>32768:raise ValueError('Health not ready')
        data=json.loads(raw)
        if data.get('mode')!='management_active' or data.get('tablet_delivery_enabled')is not True or data.get('wechat_sending_enabled')is not False:raise ValueError('Active canonical service required')
        return data
    finally:c.close()


def wait_ready():
    for attempt in range(20):
        try:return health()
        except (OSError,ValueError):
            if attempt==19:raise
            time.sleep(.5)


def atomic(p,raw):
    fd,name=tempfile.mkstemp(prefix='.classroom-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.chmod(name,0o600);os.replace(name,p)
    finally:
        if Path(name).exists():Path(name).unlink()


def database_digest(db):
    # Server-memory only. Never return rows, passwords or student names.
    h=hashlib.sha256()
    for line in db.iterdump():h.update(line.encode());h.update(b'\n')
    return h.hexdigest()


def install(payload):
    if set(payload)!={'format','files','previous_runtime_sha256'} or payload['format']!='future-school-classroom-update-v1' or set(payload['files'])!=FILES:raise ValueError('Exact classroom code bundle required')
    private(WORK,True);release=WORK/'release';private(release,True)
    config=WORK/'runtime.private.json';database=WORK/'business.sqlite';manifest_file=release/'MANIFEST.json'
    for p in (config,database):private(p)
    config_raw=config.read_bytes();cfg=json.loads(config_raw)
    if sha(json.dumps(cfg.get('files'),sort_keys=True,separators=(',',':')).encode())!=payload['previous_runtime_sha256']:raise ValueError('Original complete runtime manifest differs')
    # The first production extractor kept the complete manifest in runtime
    # configuration but did not create a separate MANIFEST.json. Verify that
    # exact original manifest instead of guessing or weakening file checks.
    manifest_raw=None
    if manifest_file.exists():
        private(manifest_file);manifest_raw=manifest_file.read_bytes();manifest=json.loads(manifest_raw)
    else:manifest=dict(format='future-school-management-release-v1',contains_data=False,files=cfg['files'])
    if set(cfg)!={'database','files'} or cfg['database']!=str(database) or cfg['files']!=manifest['files'] or manifest.get('contains_data')is not False:raise ValueError('Current admitted runtime differs')
    incoming={};saved={}
    for name in FILES:
        item=payload['files'][name]
        if set(item)!={'previous_sha256','sha256','base64'}:raise ValueError('Pinned file required')
        target=release/name;private(target);old=target.read_bytes();new=base64.b64decode(item['base64'],validate=True)
        if len(new)>200000 or sha(new)!=item['sha256'] or sha(old)!=item['previous_sha256'] or cfg['files'].get(name)!=sha(old):raise ValueError('Reviewed source differs; do not overwrite another release')
        if name.endswith('.py'):compile(new,name,'exec')
        incoming[name]=new;saved[name]=old
    if sum(map(len,incoming.values()))>500000:raise ValueError('Code bundle too large')
    for name,checksum in cfg['files'].items():
        if name.startswith('/') or '..' in name.split('/') or '\\' in name:raise ValueError('Runtime file changed')
        private(release/name)
        if sha((release/name).read_bytes())!=checksum:raise ValueError('Runtime file changed')
    if service('is-active')!='active':raise ValueError('Expected active service')
    health();backup=Path(tempfile.mkdtemp(prefix='.classroom-update-',dir=WORK));os.chmod(backup,0o700)
    for name,raw in saved.items():
        p=backup/name;p.parent.mkdir(parents=True,mode=0o700,exist_ok=True);atomic(p,raw)
    atomic(backup/'runtime.private.json',config_raw)
    if manifest_raw is not None:atomic(backup/'MANIFEST.json',manifest_raw)
    stopped=False
    try:
        service('stop');stopped=True
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as source:
            source.execute('PRAGMA query_only=ON')
            if source.execute('PRAGMA quick_check').fetchone()[0]!='ok' or source.execute("SELECT COUNT(*) FROM management_activations WHERE phase='active'").fetchone()[0]!=1:raise ValueError('Active database check failed')
            before=database_digest(source)
            with closing(sqlite3.connect(backup/'business.sqlite')) as dest:source.backup(dest)
        os.chmod(backup/'business.sqlite',0o600)
        for name,raw in incoming.items():atomic(release/name,raw);cfg['files'][name]=sha(raw)
        manifest['files']=cfg['files'];atomic(manifest_file,json.dumps(manifest,sort_keys=True,separators=(',',':')).encode());atomic(config,json.dumps(cfg,sort_keys=True,separators=(',',':')).encode())
        # Load the exact new isolated release against the backup before starting
        # it on the live database. This performs no schema initialization.
        env=os.environ.copy();env['PYTHONPATH']=str(release)
        check=subprocess.run([str(PYTHON),'-c','import sys;from management.api import create_app;create_app(sys.argv[1]);print("classroom startup checked")',str(backup/'business.sqlite')],cwd=release,env=env,capture_output=True,timeout=30)
        if check.returncode:raise ValueError('Isolated updated release check failed')
        service('start');ready=wait_ready()
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:after=database_digest(db)
        return dict(updated=True,mode=ready['mode'],tablet_delivery_enabled=True,backup_directory=str(backup),
            database_restored=False,business_unchanged_during_check=before==after,accounts_reset=False,
            gateway_changed=False,files={name:sha(raw) for name,raw in incoming.items()})
    except BaseException:
        if stopped:
            # Restore ONLY code/config. Live business and queues are never
            # replaced by the backup, even if writes resumed before a failure.
            service('stop')
            for name,raw in saved.items():atomic(release/name,raw)
            atomic(config,config_raw)
            if manifest_raw is not None:atomic(manifest_file,manifest_raw)
            elif manifest_file.exists():manifest_file.unlink()
            service('start');wait_ready()
        raise


if __name__=='__main__':
    import sys
    try:
        raw=sys.stdin.readline(800001)
        if len(raw)>800000:raise ValueError('Bundle limit')
        result=install(json.loads(raw));print(json.dumps(dict(ok=True,result=result),separators=(',',':')),flush=True)
    except Exception as error:
        # No exception body can contain database rows or credential fields.
        detail=str(error)[:160] if isinstance(error,ValueError) else type(error).__name__
        print(json.dumps(dict(ok=False,error='classroom_update_stopped',kind=type(error).__name__,detail=detail)),flush=True);raise SystemExit(1)
