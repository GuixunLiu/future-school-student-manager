"""Add the owner's three requested administrators to the current web database only."""
from contextlib import closing
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

ROOT=Path('/opt/wxnotify/app')
UNIT='wxnotify-teacher-web.service'
WANTED=('jichenxiao','zhoujunye','tianmeiqi')
DISPLAY_NAMES=('纪晨晓','周骏烨','田梅琪')
TEACHER_NAME='牟建权'
TEACHER_TARGET='mujianquan'
ORGANIZATION='weilai'
OWNER='liuguixun'
sha=lambda b:hashlib.sha256(b).hexdigest()


def validate(plan):
    if plan.get('organization')!=ORGANIZATION or plan.get('owner_login')!=OWNER or plan.get('confirmed_add_admins') is not True or not re.fullmatch('[a-f0-9]{32}',plan.get('plan_id','')):raise ValueError('Explicit owner account plan required')
    people=plan.get('people')
    if not isinstance(people,list) or tuple(p.get('login') for p in people)!=WANTED:raise ValueError('Requested three logins required')
    ids=set()
    for p,name in zip(people,DISPLAY_NAMES):
        if p.get('display_name')!=name or p.get('roles')!=['admin'] or not re.fullmatch('[a-f0-9]{32}',p.get('id','')) or p['id'] in ids or not re.fullmatch(r'pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}',p.get('password_hash','')):raise ValueError('Explicit admin-only identity and supported salted hash required')
        ids.add(p['id'])
    teacher=plan.get('teacher_correction',{})
    if teacher.get('display_name')!=TEACHER_NAME or teacher.get('new_login')!=TEACHER_TARGET or not re.fullmatch('[a-z][a-z0-9_-]{2,63}',teacher.get('source_login','')) or teacher.get('roles')!=['teacher'] or not re.fullmatch(r'pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}',teacher.get('expected_password_hash','')):raise ValueError('Explicit retained teacher identity/password required')


def rows(db,table):return [tuple(r) for r in db.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
def digest(values):return sha(repr(sorted(values,key=repr)).encode())


def inspect(db,plan,replay=False):
    org=db.execute('SELECT id FROM organizations WHERE slug=?',(plan['organization'],)).fetchone()
    if not org:raise ValueError('School not found')
    owner=db.execute('SELECT id FROM users WHERE org_id=? AND login=? AND active=1',(org[0],plan['owner_login'])).fetchone()
    if not owner or not db.execute("SELECT 1 FROM roles WHERE org_id=? AND user_id=? AND role='admin'",(org[0],owner[0])).fetchone():raise ValueError('Existing owner administrator required')
    for p in plan['people']:
        user=db.execute('SELECT id,display_name,password_hash,active FROM users WHERE org_id=? AND login=?',(org[0],p['login'])).fetchone()
        if not replay:
            if user or db.execute('SELECT 1 FROM users WHERE id=?',(p['id'],)).fetchone():raise ValueError('Login or identity already exists; nothing overwritten: '+p['login'])
        elif not user or tuple(user)!=(p['id'],p['display_name'],p['password_hash'],1) or [r[0] for r in db.execute('SELECT role FROM roles WHERE org_id=? AND user_id=? ORDER BY role',(org[0],p['id']))]!=['admin'] or db.execute('SELECT 1 FROM teacher_classes WHERE org_id=? AND user_id=?',(org[0],p['id'])).fetchone():raise ValueError('Added account changed; no password reset or role overwrite')
    correction=plan['teacher_correction'];teachers=db.execute('SELECT * FROM users WHERE org_id=? AND display_name=?',(org[0],correction['display_name'])).fetchall()
    if len(teachers)!=1:raise ValueError('Teacher name does not identify one account')
    teacher=tuple(teachers[0])
    if teacher[2] not in ({correction['new_login']} if replay else {correction['source_login'],correction['new_login']}) or teacher[4]!=correction['expected_password_hash'] or teacher[5]!=1 or [r[0] for r in db.execute('SELECT role FROM roles WHERE org_id=? AND user_id=? ORDER BY role',(org[0],teacher[0]))]!=correction['roles']:raise ValueError('Teacher account or password changed; nothing overwritten')
    collision=db.execute('SELECT id FROM users WHERE org_id=? AND login=?',(org[0],correction['new_login'])).fetchone()
    if collision and collision[0]!=teacher[0]:raise ValueError('Corrected teacher login already belongs to another account')
    return org[0],owner[0],teacher


def add(database,plan,backup_directory):
    validate(plan)
    with closing(sqlite3.connect(database,timeout=15)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        org=db.execute('SELECT id FROM organizations WHERE slug=?',(plan['organization'],)).fetchone()
        previous=None if not org else db.execute("SELECT metadata FROM audit WHERE org_id=? AND action='web_admin_accounts_added' AND entity_id=?",(org[0],plan['plan_id'])).fetchone()
        if previous:
            inspect(db,plan,True);return json.loads(previous[0])|{'created_accounts':0,'replayed':True}
        inspect(db,plan)
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Existing database integrity failed')
        backup_directory.mkdir(mode=0o700)
        backup=backup_directory/'before.sqlite'
        with closing(sqlite3.connect(backup)) as dst:db.backup(dst)
        os.chmod(backup,0o600)
        with closing(sqlite3.connect(backup)) as dst:
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity failed')
        backup_sha=sha(backup.read_bytes())
        db.execute('BEGIN IMMEDIATE')
        try:
            org,owner,teacher=inspect(db,plan)
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            protected={t:digest(rows(db,t)) for t in tables if t not in {'users','roles','audit'}}
            original_users=rows(db,'users');original_roles=rows(db,'roles');original_audit=rows(db,'audit')
            now=int(time.time())
            for p in plan['people']:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(p['id'],org,p['login'],p['display_name'],p['password_hash'],1,now))
                db.execute('INSERT INTO roles VALUES(?,?,?)',(org,p['id'],'admin'))
            db.execute('UPDATE users SET login=? WHERE org_id=? AND id=?',(plan['teacher_correction']['new_login'],org,teacher[0]))
            inspect(db,plan,True)
            expected_users=[u[:2]+(plan['teacher_correction']['new_login'],)+u[3:] if u[0]==teacher[0] else u for u in original_users]
            if any(digest(rows(db,t))!=before for t,before in protected.items()) or not set(expected_users).issubset(set(rows(db,'users'))) or not set(original_roles).issubset(set(rows(db,'roles'))) or rows(db,'audit')!=original_audit or len(rows(db,'users'))!=len(original_users)+3 or len(rows(db,'roles'))!=len(original_roles)+3 or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Unexpected existing-account or business change; rolled back')
            result={'format':'future-school-web-admins-added','organization':plan['organization'],'plan_id':plan['plan_id'],'logins':list(WANTED),'display_names':list(DISPLAY_NAMES),'created_accounts':3,'roles':['admin'],'teacher_login':plan['teacher_correction']['new_login'],'teacher_password_preserved':True,'other_accounts_unchanged':True,'backup_directory':str(backup_directory),'backup_sha256':backup_sha,'backup_integrity':'ok','teacher_assignments_unchanged':True,'student_data_unchanged':True,'pending_requests_unchanged':True,'replayed':False}
            result.update(total_accounts=db.execute('SELECT count(*) FROM users WHERE org_id=?',(org,)).fetchone()[0],active_admin_accounts=db.execute("SELECT count(*) FROM roles r JOIN users u ON u.org_id=r.org_id AND u.id=r.user_id WHERE r.org_id=? AND r.role='admin' AND u.active=1",(org,)).fetchone()[0])
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',(os.urandom(16).hex(),org,owner,'web_admin_accounts_added',plan['plan_id'],json.dumps(result,separators=(',',':')),now))
            db.commit();return result
        except BaseException:db.rollback();raise


def install(config,payload):
    if os.geteuid()==0:raise ValueError('Ordinary deployment account required')
    work=Path(config['work_directory'])
    if work.is_symlink() or work.resolve().parent!=ROOT.resolve() or not work.name.startswith('teacher-web-'):raise ValueError('Current teacher web directory required')
    unit=subprocess.run(['systemctl','--user','show',UNIT,'-p','WorkingDirectory','--value'],capture_output=True,text=True,timeout=15)
    if unit.returncode or unit.stdout.strip()!=str(work):raise ValueError('Running teacher web directory differs')
    if (work/'business.sqlite').is_symlink() or (work/'web.private.json').is_symlink():raise ValueError('Unexpected database or configuration link')
    cfg=json.loads((work/'web.private.json').read_bytes())
    if cfg!={'files':config['runtime_files'],'source_sha256':config['source_sha256']}:raise ValueError('Current web runtime pins differ')
    for name,expected in cfg['files'].items():
        if name.startswith('/') or '..' in Path(name).parts or '\\' in name:raise ValueError('Unexpected runtime path')
        p=work/name
        if p.is_symlink() or sha(p.read_bytes())!=expected:raise ValueError('Current web source changed')
    c=HTTPConnection('127.0.0.1',18086,timeout=5)
    try:
        c.request('GET','/health/ready');r=c.getresponse();raw=r.read(32769)
        if r.status!=200 or len(raw)>32768:raise ValueError('Teacher web not ready')
        h=json.loads(raw)
        if h.get('workspace_ui_version')!=3 or h.get('workspace_role_selection') is not True or h.get('source_sha256')!=config['source_sha256'] or h.get('student_sync_enabled') is not False or h.get('wechat_sending_enabled') is not False:raise ValueError('Expected current role-selection web required')
    finally:c.close()
    plan=config['plan'];result=add(work/'business.sqlite',plan,work/('admin-addition-'+plan['plan_id']))
    receipt=work/('admin-addition-'+plan['plan_id'])/'receipt.json';receipt.write_text(json.dumps(result,indent=2),encoding='utf-8');os.chmod(receipt,0o600)
    return result
