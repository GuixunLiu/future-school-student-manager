"""Owner-confirmed single teacher addition to the current web database only.

Checks the live mirror's exact class labels before any write. Never imports
student records, changes old passwords or starts/restarts a service.
"""
from contextlib import closing
from pathlib import Path
import hashlib,json,os,re,sqlite3,time

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def rows(db,table):return [tuple(r) for r in db.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
def checksum(value):return sha(repr(sorted(value,key=repr)).encode())
def validate(plan):
    if set(plan)!={'format','confirmed_by_owner','plan_id','organization','owner_login','person','classes'} or plan['format']!='future-school-current-teacher-v1' or plan['confirmed_by_owner'] is not True:raise ValueError('Owner confirmation required')
    if not re.fullmatch('[a-f0-9]{32}',plan['plan_id']) or not re.fullmatch('[a-z0-9][a-z0-9_.-]{2,63}',plan['organization']):raise ValueError('Invalid target')
    p=plan['person']
    if set(p)!={'id','login','display_name','password_hash'} or not re.fullmatch('[a-f0-9]{32}',p['id']) or not re.fullmatch('[a-z][a-z0-9_-]{2,63}',p['login']) or not isinstance(p['display_name'],str) or not 1<=len(p['display_name'])<=80 or any(ord(c)<32 for c in p['display_name']) or not re.fullmatch(r'pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}',p['password_hash']):raise ValueError('Explicit teacher identity and supported salted hash required')
    if not isinstance(plan['classes'],list) or not 1<=len(plan['classes'])<=100:raise ValueError('Explicit class scope required')
    ids=set();local=set()
    for c in plan['classes']:
        if set(c)!={'server_id','local_id','name','course_name'} or not re.fullmatch('[a-f0-9]{32}',c['server_id']) or not isinstance(c['local_id'],str) or not re.fullmatch('[0-9]{1,20}',c['local_id']) or c['server_id'] in ids or c['local_id'] in local or any(not isinstance(c[k],str) or not c[k] or len(c[k])>80 for k in ('name','course_name')):raise ValueError('Invalid or repeated class mapping')
        ids.add(c['server_id']);local.add(c['local_id'])

def inspect(db,plan,replay=False):
    if db.execute('SELECT COUNT(*) FROM organizations').fetchone()[0]!=1:raise ValueError('Database-wide mirror requires one organization')
    org=db.execute('SELECT id FROM organizations WHERE slug=?',(plan['organization'],)).fetchone()
    if not org:raise ValueError('Organization missing')
    org=org[0];owner=db.execute("SELECT u.id FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin' WHERE u.org_id=? AND u.login=? AND u.active=1",(org,plan['owner_login'])).fetchone()
    if not owner:raise ValueError('Current owner administrator required')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if 'management_migration_holds' in tables and db.execute("SELECT 1 FROM management_migration_holds WHERE state='held'").fetchone():raise ValueError('Migration is paused; no assignment changes')
    status=db.execute('SELECT source_sha256 FROM web_mirror_status WHERE id=1').fetchone();dataset=db.execute('SELECT data_json,source_sha256 FROM web_dataset WHERE id=1').fetchone()
    if not status or not dataset or status[0]!=dataset[1]:raise ValueError('Current mirror source required')
    data=json.loads(dataset[0])
    for c in plan['classes']:
        actual=db.execute('SELECT name,course_name FROM classes WHERE org_id=? AND id=?',(org,c['server_id'])).fetchone()
        mirrored=[r for r in data['classes'] if r['local_id']==c['local_id']]
        if not actual or tuple(actual)!=(c['name'],c['course_name']) or len(mirrored)!=1 or (mirrored[0]['name'],mirrored[0]['course_name'],mirrored[0]['teacher'])!=(c['name'],c['course_name'],plan['person']['display_name']):raise ValueError('Class or current mirror teacher changed; no grants written')
    p=plan['person'];matches=db.execute('SELECT * FROM users WHERE org_id=? AND (login=? OR display_name=?)',(org,p['login'],p['display_name'])).fetchall()
    if matches:
        if len(matches)!=1:raise ValueError('Ambiguous existing teacher identity')
        u=tuple(matches[0])
        if (u[2],u[3],u[5])!=(p['login'],p['display_name'],1) or [r[0] for r in db.execute('SELECT role FROM roles WHERE org_id=? AND user_id=? ORDER BY role',(org,u[0]))]!=['teacher']:raise ValueError('Existing account needs separate review; not reset')
        if replay:
            for c in plan['classes']:
                if [r[0] for r in db.execute('SELECT user_id FROM teacher_classes WHERE org_id=? AND class_id=?',(org,c['server_id']))]!=[u[0]] or [r[0] for r in db.execute('SELECT user_id FROM web_mirror_assignments WHERE class_local_id=?',(c['local_id'],))]!=[u[0]]:raise ValueError('Assignments changed after apply; no overwrite')
        return org,owner[0],u
    if replay or db.execute('SELECT 1 FROM users WHERE id=?',(p['id'],)).fetchone():raise ValueError('Teacher identity missing or ID collision')
    return org,owner[0],None

def apply(database,plan,backup_directory):
    validate(plan)
    with closing(sqlite3.connect(database,timeout=15)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        org=db.execute('SELECT id FROM organizations WHERE slug=?',(plan['organization'],)).fetchone()
        old=None if not org else db.execute("SELECT metadata FROM audit WHERE org_id=? AND action='current_teacher_added' AND entity_id=?",(org[0],plan['plan_id'])).fetchone()
        if old:
            inspect(db,plan,True);result=json.loads(old[0])
            if result['plan_sha256']!=sha(canonical(plan).encode()):raise ValueError('Applied plan differs')
            return result|{'created_account':False,'password_preserved':True,'replayed':True}
        inspect(db,plan)
        if db.execute('PRAGMA application_id').fetchone()[0]!=1465405012 or db.execute('PRAGMA user_version').fetchone()[0]!=1 or db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database identity or integrity failed')
        backup_directory.mkdir(mode=0o700);backup=backup_directory/'before.sqlite'
        with closing(sqlite3.connect(backup)) as target:db.backup(target)
        os.chmod(backup,0o600)
        with closing(sqlite3.connect(backup)) as target:
            if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity failed')
        backup_sha=sha(backup.read_bytes());db.execute('BEGIN IMMEDIATE')
        try:
            org,owner,existing=inspect(db,plan);p=plan['person'];who=existing[0] if existing else p['id']
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            allowed={'users','roles','teacher_classes','web_mirror_assignments','audit'}
            protected={t:checksum(rows(db,t)) for t in tables if t not in allowed}
            users=rows(db,'users');roles=rows(db,'roles');grants=rows(db,'teacher_classes');mirror=rows(db,'web_mirror_assignments');audit=rows(db,'audit');now=int(time.time())
            if existing is None:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,1,?)',(who,org,p['login'],p['display_name'],p['password_hash'],now));db.execute("INSERT INTO roles VALUES(?,?,'teacher')",(org,who))
            for c in plan['classes']:
                db.execute('DELETE FROM teacher_classes WHERE org_id=? AND class_id=?',(org,c['server_id']));db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(org,who,c['server_id']))
                db.execute('DELETE FROM web_mirror_assignments WHERE class_local_id=?',(c['local_id'],));db.execute('INSERT INTO web_mirror_assignments VALUES(?,?)',(c['local_id'],who))
            expected_grants=[r for r in grants if not(r[0]==org and r[2] in {c['server_id'] for c in plan['classes']})]+[(org,who,c['server_id']) for c in plan['classes']]
            expected_mirror=[r for r in mirror if r[0] not in {c['local_id'] for c in plan['classes']}]+[(c['local_id'],who) for c in plan['classes']]
            if any(checksum(rows(db,t))!=v for t,v in protected.items()) or not set(users).issubset(set(rows(db,'users'))) or not set(roles).issubset(set(rows(db,'roles'))) or len(rows(db,'users'))!=len(users)+(existing is None) or len(rows(db,'roles'))!=len(roles)+(existing is None) or checksum(rows(db,'teacher_classes'))!=checksum(expected_grants) or checksum(rows(db,'web_mirror_assignments'))!=checksum(expected_mirror) or rows(db,'audit')!=audit or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Unexpected existing account or business change; rolled back')
            inspect(db,plan,True)
            result=dict(format='future-school-current-teacher-added',plan_id=plan['plan_id'],plan_sha256=sha(canonical(plan).encode()),login=p['login'],display_name=p['display_name'],user_id=who,created_account=existing is None,password_preserved=existing is not None,authorized_classes=len(plan['classes']),other_accounts_unchanged=True,student_data_unchanged=True,pending_requests_unchanged=True,mirror_dataset_unchanged=True,backup_sha256=backup_sha,backup_integrity='ok',replayed=False)
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',(os.urandom(16).hex(),org,owner,'current_teacher_added',plan['plan_id'],json.dumps(result,separators=(',',':')),now));db.commit();return result
        except BaseException:db.rollback();raise

def install(config):
    import subprocess
    from http.client import HTTPConnection
    if os.name!='posix' or os.geteuid()==0:raise ValueError('Ordinary Linux deployment account required')
    work=Path(config['work_directory']);root=Path('/opt/wxnotify/app')
    if work.is_symlink() or work.parent!=root or not re.fullmatch('teacher-web-[0-9]{14}-[a-f0-9]{8}',work.name):raise ValueError('Explicit current web directory required')
    for p in (work,*work.parents):
        if p.is_symlink():raise ValueError('No symlink paths')
    database=work/'business.sqlite'
    if database.is_symlink() or database.stat().st_uid!=os.getuid() or database.stat().st_mode&0o077:raise ValueError('Private owned current database required')
    unit=subprocess.run(['systemctl','--user','show','wxnotify-teacher-web.service','-p','WorkingDirectory','--value'],capture_output=True,text=True,timeout=15)
    if unit.returncode or unit.stdout.strip()!=str(work):raise ValueError('Current service directory differs')
    if (work/'web.private.json').is_symlink():raise ValueError('Configuration link refused')
    cfg=json.loads((work/'web.private.json').read_bytes())
    for name,expected in cfg['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:raise ValueError('Unexpected source path')
        source=work/name
        if source.is_symlink() or sha(source.read_bytes())!=expected:raise ValueError('Current source fingerprint differs')
    connection=HTTPConnection('127.0.0.1',18086,timeout=5)
    try:
        connection.request('GET','/health/ready');response=connection.getresponse();raw=response.read(32769)
        if response.status!=200 or len(raw)>32768:raise ValueError('Current service not ready')
        health=json.loads(raw)
        # Live mirror updates change the data digest; cfg's source digest is
        # the original import, not today's dataset. Runtime code pins above
        # and current mirror consistency inside the write transaction protect
        # the operation without comparing a live digest to an obsolete one.
        if health.get('mode')!='teacher_web' or health.get('workspace_ui_version')!=3 or health.get('workspace_role_selection') is not True or health.get('wechat_sending_enabled') is not False or not re.fullmatch('[a-f0-9]{64}',health.get('source_sha256','')):raise ValueError('Expected existing teacher web required')
    finally:connection.close()
    result=apply(database,config['plan'],work/('teacher-addition-'+config['plan']['plan_id']))
    receipt=work/('teacher-addition-'+config['plan']['plan_id'])/'receipt.json';receipt.write_text(json.dumps(result,indent=2),encoding='utf8');os.chmod(receipt,0o600)
    return result
