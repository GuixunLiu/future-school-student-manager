"""Single-purpose, explicit class teacher upgrade of the current canonical service.

Seven exact code files, five additive tables, no account provisioning, no
database restore, no device/HTTPS identity change, no arbitrary remote actions.
"""
import ast
import base64
from contextlib import closing
import hashlib
from http.client import HTTPConnection,HTTPSConnection
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import tempfile
import time

WORK=Path('/opt/wxnotify/app/.management-cutover-3fqe7xfx')
PYTHON=Path('/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python')
BACKEND='wxnotify-canonical-management.service';GATEWAY='wxnotify-canonical-gateway.service'
FILES=frozenset({'management/api.py','management/gateway.py','management/reports.py','management/web/workspace.js',
    'management/class_teachers.py','management/class_teachers_api.py','management/web/class-teachers.js'})
NEW=frozenset({'management/class_teachers.py','management/class_teachers_api.py','management/web/class-teachers.js'})
OBJECTS=frozenset({'management_class_versions','management_class_commands','management_lesson_teacher_snapshots','management_metadata_clients','management_class_teacher_events',
    'management_class_command_no_update','management_class_command_no_delete','management_teacher_snapshot_no_update','management_teacher_snapshot_no_delete','management_class_event_no_update','management_class_event_no_delete'})
CERTIFICATE_SHA256='320750ba1113dafa0410bcdbeffd7b04a68f42f64b74744adf01731bad92e8fe'
sha=lambda b:hashlib.sha256(b).hexdigest()
encoded=lambda v:json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()


def private(p,directory=False):
    import stat
    if os.name!='posix' or not p.is_absolute() or any(v.is_symlink() for v in (p,*p.parents)):raise ValueError('Private Linux path required')
    s=p.stat()
    if s.st_uid!=os.getuid() or s.st_mode&0o077 or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):raise ValueError('Private owned path required')


def atomic(p,raw):
    fd,name=tempfile.mkstemp(prefix='.class-teacher-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.chmod(name,0o600);os.replace(name,p)
    finally:
        if Path(name).exists():Path(name).unlink()


def service(action,name):
    if name not in {BACKEND,GATEWAY} or action not in {'is-active','stop','start'}:raise ValueError('Fixed service action required')
    r=subprocess.run(['systemctl','--user',action,name],capture_output=True,text=True,timeout=45)
    if r.returncode:raise ValueError('Canonical service action failed')
    return r.stdout.strip()


def health(port,certificate=None):
    c=HTTPSConnection('127.0.0.1',port,context=ssl.create_default_context(cafile=str(certificate)),timeout=4) if certificate else HTTPConnection('127.0.0.1',port,timeout=4)
    try:
        c.request('GET','/health/ready',headers={'Host':'47.97.226.219:18083' if certificate else '127.0.0.1:18098'});r=c.getresponse();raw=r.read(32769)
        if r.status!=200 or len(raw)>32768:raise ValueError('Canonical health not ready')
        v=json.loads(raw)
        if v.get('mode')!='management_active' or v.get('tablet_delivery_enabled')is not True or v.get('wechat_sending_enabled')is not False:raise ValueError('Active canonical runtime required')
        return v
    finally:c.close()


def wait_ready(port,certificate=None,class_teachers=None):
    for attempt in range(30):
        try:
            h=health(port,certificate)
            if class_teachers is not None and h.get('class_teachers_enabled',False)is not class_teachers:raise ValueError('Class teacher feature readiness differs')
            return h
        except (OSError,ValueError):
            if attempt==29:raise
            time.sleep(.5)


def objects(db):return {r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")}


def contents(db,names):
    h=hashlib.sha256()
    for name in sorted(names):
        h.update(encoded(name));quoted='"'+name.replace('"','""')+'"'
        values=[json.dumps(list(row),ensure_ascii=True,separators=(',',':'),default=lambda b:dict(binary=base64.b64encode(b).decode())) for row in db.execute('SELECT * FROM '+quoted)]
        for row in sorted(values):h.update(row.encode());h.update(b'\n')
    return h.hexdigest()


def schema_from_source(raw):
    tree=ast.parse(raw)
    schema=next(v.value.value for v in tree.body if isinstance(v,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in v.targets) and isinstance(v.value,ast.Constant))
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript(schema);wanted=objects(db)
    if set(wanted)!=OBJECTS:raise ValueError('Only the reviewed class teacher schema may be added')
    return schema,wanted


def install(payload):
    if set(payload)!={'format','files','previous_runtime_sha256'} or payload['format']!='future-school-class-teacher-upgrade-v1' or set(payload['files'])!=FILES:raise ValueError('Exact seven-file class teacher upgrade required')
    private(WORK,True);release=WORK/'release';private(release,True)
    config=WORK/'runtime.private.json';gateway_config=WORK/'gateway.private.json';manifest_file=release/'MANIFEST.json';database=WORK/'business.sqlite'
    for p in (config,gateway_config,manifest_file,database):private(p)
    original={p:p.read_bytes() for p in (config,gateway_config,manifest_file)}
    cfg=json.loads(original[config]);gw=json.loads(original[gateway_config]);manifest=json.loads(original[manifest_file])
    if set(cfg)!={'database','files'} or cfg['database']!=str(database) or manifest.get('contains_data')is not False or manifest['files']!=cfg['files'] or sha(encoded(cfg['files']))!=payload['previous_runtime_sha256']:raise ValueError('Current complete runtime fingerprint differs')
    if gw.get('origin')!='http://47.97.226.219:18083' or gw.get('expires_at')!=1794391683 or gw.get('gateway_sha256')!=cfg['files']['management/gateway.py']:raise ValueError('Original gateway identity or expiry differs')
    cert=Path(gw['certificate']);key=Path(gw['private_key'])
    for p in (cert,key):
        if not p.is_relative_to(WORK/'tls'):raise ValueError('Original TLS identity required')
        private(p)
    if sha(cert.read_bytes())!=CERTIFICATE_SHA256:raise ValueError('Original certificate changed')
    incoming={};saved={}
    for name,item in payload['files'].items():
        if set(item)!={'base64','sha256','previous_sha256'}:raise ValueError('Pinned class teacher source required')
        raw=base64.b64decode(item['base64'],validate=True)
        if len(raw)>200000 or sha(raw)!=item['sha256']:raise ValueError('New code digest differs')
        if name.endswith('.py'):compile(raw,name,'exec')
        target=release/name
        if name in NEW:
            if item['previous_sha256']is not None or name in cfg['files'] or target.exists():raise ValueError('Class teacher module already exists; reconcile before retry')
            saved[target]=None
        else:
            private(target);old=target.read_bytes()
            if sha(old)!=item['previous_sha256'] or cfg['files'].get(name)!=sha(old):raise ValueError('Existing source changed')
            saved[target]=old
        incoming[name]=raw
    for name,checksum in cfg['files'].items():
        if name.startswith('/') or '..' in name.split('/') or '\\' in name:raise ValueError('Runtime path differs')
        private(release/name)
        if sha((release/name).read_bytes())!=checksum:raise ValueError('A runtime file changed')
    schema,wanted=schema_from_source(incoming['management/class_teachers.py'])
    for name in (BACKEND,GATEWAY):
        if service('is-active',name)!='active':raise ValueError('Both canonical services must be active')
    if health(18098).get('class_teachers_enabled'):raise ValueError('Class teacher is already enabled')
    backup=Path(tempfile.mkdtemp(prefix='.class-teacher-upgrade-',dir=WORK));os.chmod(backup,0o700)
    for p,raw in original.items():atomic(backup/p.name,raw)
    for p,raw in saved.items():
        if raw is not None:
            b=backup/'code'/p.relative_to(release);b.parent.mkdir(parents=True,mode=0o700,exist_ok=True);atomic(b,raw)
    stopped=False
    try:
        stopped=True;service('stop',GATEWAY);service('stop',BACKEND)
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.execute('PRAGMA query_only=ON')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
            before_objects=objects(db);tables=[r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")];before=contents(db,tables)
            with closing(sqlite3.connect(backup/'business.sqlite')) as dest:db.backup(dest)
        os.chmod(backup/'business.sqlite',0o600)
        if OBJECTS & set(before_objects):raise ValueError('Class teacher schema already exists; reconcile explicitly')
        for name,raw in incoming.items():atomic(release/name,raw);cfg['files'][name]=sha(raw)
        manifest['files']=cfg['files'];gw['gateway_sha256']=cfg['files']['management/gateway.py']
        atomic(config,encoded(cfg));atomic(manifest_file,encoded(manifest));atomic(gateway_config,encoded(gw))
        env=os.environ.copy();env['PYTHONPATH']=str(release)
        preflight="import sys;from management.class_teachers import ClassTeacherStore;s=ClassTeacherStore(sys.argv[1]);s.initialize_class_teachers();from management.api import create_app;a=create_app(sys.argv[1]);assert a.state.class_teacher_store;print('class teacher ready')"
        r=subprocess.run([str(PYTHON),'-c',preflight,str(backup/'business.sqlite')],cwd=release,env=env,capture_output=True,timeout=35)
        if r.returncode:raise ValueError('Isolated class teacher startup failed')
        with closing(sqlite3.connect(database)) as db:
            db.executescript('BEGIN IMMEDIATE;\n'+schema+'\nCOMMIT;')
            after_objects=objects(db)
            if any(after_objects.get(n)!=sql for n,sql in before_objects.items()) or {n:after_objects.get(n) for n in OBJECTS}!=wanted or contents(db,tables)!=before or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Additive schema did not preserve existing business')
        service('start',BACKEND);wait_ready(18098,class_teachers=True)
        # The existing public gateway is restarted with the same certificate,
        # port, expiry and device authentication, only the new route allowlist.
        service('start',GATEWAY);wait_ready(18083,cert,class_teachers=True)
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:unchanged=contents(db,tables)==before
        return dict(updated=True,class_teachers_enabled=True,mode='management_active',tablet_delivery_enabled=True,
            database_restored=False,accounts_created=0,accounts_reset=False,old_business_unchanged_during_check=unchanged,
            added_tables=['management_class_versions','management_class_commands','management_lesson_teacher_snapshots','management_metadata_clients','management_class_teacher_events'],tls_identity_changed=False,expires_at=gw['expires_at'],
            backup_directory=str(backup),files={name:sha(raw) for name,raw in incoming.items()})
    except BaseException:
        if stopped:
            service('stop',GATEWAY);service('stop',BACKEND)
            for p,raw in saved.items():
                if raw is None:
                    if p.exists():p.unlink()
                else:atomic(p,raw)
            for p,raw in original.items():atomic(p,raw)
            # Retain any new schema/business. Never restore the snapshot DB.
            service('start',BACKEND);wait_ready(18098);service('start',GATEWAY);wait_ready(18083,cert)
        raise


if __name__=='__main__':
    import sys
    try:
        raw=sys.stdin.readline(1000001)
        if len(raw)>1000000:raise ValueError('Upgrade bundle limit')
        print(json.dumps(dict(ok=True,result=install(json.loads(raw))),separators=(',',':')),flush=True)
    except Exception as e:
        print(json.dumps(dict(ok=False,error='class_teacher_upgrade_stopped',kind=type(e).__name__,detail=str(e)[:160] if isinstance(e,ValueError) else type(e).__name__)),flush=True);raise SystemExit(1)
