"""Authenticated tablet snapshots for web viewing, without replaying attendance or charging."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time

TZ=timezone(timedelta(hours=8))
SCHEMA='''
CREATE TABLE IF NOT EXISTS web_mirror_devices(id TEXT PRIMARY KEY,org_id TEXT NOT NULL,token_digest TEXT UNIQUE NOT NULL,active INTEGER NOT NULL,expires_at INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 0,last_received INTEGER,source_sha256 TEXT);
CREATE TABLE IF NOT EXISTS web_mirror_receipts(device_id TEXT NOT NULL,revision INTEGER NOT NULL,request_ref TEXT NOT NULL,digest TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(device_id,revision),UNIQUE(device_id,request_ref));
CREATE TABLE IF NOT EXISTS web_mirror_assignments(class_local_id TEXT NOT NULL,user_id TEXT NOT NULL,PRIMARY KEY(class_local_id,user_id));
CREATE TABLE IF NOT EXISTS web_mirror_status(id INTEGER PRIMARY KEY CHECK(id=1),device_id TEXT NOT NULL,revision INTEGER NOT NULL,received_at INTEGER NOT NULL,source_sha256 TEXT NOT NULL,warnings TEXT NOT NULL);
'''
class MirrorError(ValueError):
    def __init__(self,status,code):self.status=status;self.code=code;super().__init__(code)

def connect(database):
    db=sqlite3.connect(database,timeout=15);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
def initialize(database):
    with closing(connect(database)) as db,db:db.executescript(SCHEMA)
def exact(row,keys):
    if not isinstance(row,dict) or set(row)!=set(keys.split()):raise MirrorError(422,'unexpected_snapshot_fields')
def text(value,limit=120):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 for c in value):raise MirrorError(422,'invalid_snapshot_text')
def number(value,maximum=1000000,integer=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or value>maximum or integer and int(value)!=value:raise MirrorError(422,'invalid_snapshot_count')
def local_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,20}',value):raise MirrorError(422,'invalid_source_identity')
def boolean(value):
    if type(value) is not bool:raise MirrorError(422,'invalid_snapshot_boolean')
def summary(row):
    for k in ['name','rate']:text(row[k])
    number(row['expected']);number(row['arrived'],integer=True);number(row['leave_count'],integer=True);boolean(row['needs_review'])

def validate(data,clock):
    exact(data,'format schema_version as_of_date source_captured_at source_records classes students weeks lessons history_import_is_read_only balances_charged wechat_enabled')
    if data['format']!='future-school-teacher-web-dataset' or data['schema_version']!=1 or data['history_import_is_read_only'] is not True or data['balances_charged'] is not False or data['wechat_enabled'] is not False:raise MirrorError(422,'read_only_view_required')
    try:day=datetime.strptime(data['as_of_date'],'%Y-%m-%d').date()
    except (ValueError,TypeError):raise MirrorError(422,'invalid_snapshot_date')
    if abs((day-datetime.fromtimestamp(clock,TZ).date()).days)>1:raise MirrorError(409,'tablet_clock_requires_review')
    text(data['source_captured_at'],60);number(data['source_records'],1000000,True)
    for name,maximum in [('classes',1000),('students',10000),('weeks',262),('lessons',30000)]:
        if not isinstance(data[name],list) or len(data[name])>maximum or name=='weeks' and not data[name]:raise MirrorError(422,'snapshot_limit')
    classes=set();students={};numbers=set()
    for c in data['classes']:
        exact(c,'local_id name course_name teacher');local_id(c['local_id'])
        if c['local_id'] in classes:raise MirrorError(422,'duplicate_class_identity')
        classes.add(c['local_id'])
        for key in ['name','course_name','teacher']:text(c[key])
    for s in data['students']:
        exact(s,'local_id name external_ref remaining_units');local_id(s['local_id']);text(s['name']);text(s['external_ref'],64)
        if s['local_id'] in students or s['external_ref'] and s['external_ref'] in numbers:raise MirrorError(422,'duplicate_student_identity')
        if type(s['remaining_units']) is not int or not -1000000<=s['remaining_units']<=10000000:raise MirrorError(422,'invalid_balance_snapshot')
        students[s['local_id']]=s;numbers.add(s['external_ref'])
    week_keys=set()
    for w in data['weeks']:
        exact(w,'offset start end name expected arrived leave_count rate needs_review fallback classes teachers');summary(w);boolean(w['fallback'])
        try:
            start=datetime.strptime(w['start'],'%Y-%m-%d').date();end=datetime.strptime(w['end'],'%Y-%m-%d').date()
            if start.weekday()!=0 or end-start!=timedelta(days=6) or w['start'] in week_keys:raise ValueError()
        except (ValueError,TypeError):raise MirrorError(422,'invalid_report_week')
        week_keys.add(w['start'])
        for name in ['classes','teachers']:
            if not isinstance(w[name],list) or len(w[name])>3000:raise MirrorError(422,'snapshot_limit')
            for g in w[name]:exact(g,'name expected arrived leave_count rate needs_review');summary(g)
    lesson_keys=set()
    for l in data['lessons']:
        exact(l,'key class_local_id class_name teacher date kind start_time end_time expected arrived leave_count needs_review members')
        text(l['key'],200)
        if l['key'] in lesson_keys:raise MirrorError(422,'duplicate_lesson_identity')
        lesson_keys.add(l['key'])
        for k in ['class_local_id','class_name','teacher','date','kind','start_time','end_time']:text(l[k])
        number(l['expected'],10000,True);number(l['arrived'],10000,True);number(l['leave_count'],10000,True);boolean(l['needs_review'])
        if not isinstance(l['members'],list) or len(l['members'])>10000:raise MirrorError(422,'snapshot_limit')
        members=set()
        for s in l['members']:
            exact(s,'student_local_id name external_ref remaining_units expected status attendance_time attendance_method makeup');local_id(s['student_local_id'])
            source=students.get(s['student_local_id'])
            if not source or s['student_local_id'] in members or any(s[k]!=source[k] for k in ['name','external_ref','remaining_units']):raise MirrorError(422,'lesson_student_identity_differs')
            members.add(s['student_local_id']);boolean(s['expected']);boolean(s['makeup'])
            if s['status'] not in {'arrived','leave','missing'}:raise MirrorError(422,'unknown_attendance_state')
            text(s['attendance_time'],40);text(s['attendance_method'],40)
        if sum(s['status']=='arrived' for s in l['members'])!=l['arrived']:raise MirrorError(422,'attendance_count_differs')

def authenticate(db,token,clock):
    if not re.fullmatch(r'wxd_[A-Za-z0-9_-]{43}',token or ''):raise MirrorError(401,'device_required')
    digest=hashlib.sha256(token.encode()).hexdigest();row=db.execute('SELECT * FROM web_mirror_devices WHERE token_digest=?',(digest,)).fetchone()
    if not row or not row['active'] or row['expires_at']<=clock:raise MirrorError(403,'device_revoked_or_expired')
    return row

def receive(database,token,raw,clock=None):
    clock=int(time.time()) if clock is None else clock
    if len(raw)>4*1024*1024:raise MirrorError(413,'snapshot_limit')
    def pairs(items):
        out={}
        for key,value in items:
            if key in out:raise MirrorError(422,'duplicate_json_key')
            out[key]=value
        return out
    try:envelope=json.loads(raw,object_pairs_hook=pairs)
    except (ValueError,UnicodeError,RecursionError) as e:
        if isinstance(e,MirrorError):raise
        raise MirrorError(422,'invalid_json')
    exact(envelope,'protocol device_id revision request_ref data')
    if type(envelope['protocol']) is not int or envelope['protocol']!=1 or not isinstance(envelope['device_id'],str) or not re.fullmatch('[a-f0-9]{32}',envelope['device_id']) or type(envelope['revision']) is not int or not 1<=envelope['revision']<=9007199254740991 or not isinstance(envelope['request_ref'],str) or not re.fullmatch('[a-f0-9]{32}',envelope['request_ref']):raise MirrorError(422,'invalid_envelope')
    digest=hashlib.sha256(raw).hexdigest()
    with closing(connect(database)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            device=authenticate(db,token,clock)
            if device['id']!=envelope['device_id']:raise MirrorError(403,'different_device')
            # The old dataset/assignments are database-wide. Even an exact old
            # retry must not claim successful mirror synchronization while a
            # canonical migration is held. No import from candidate modules.
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if 'management_balances' in tables and 'management_migration_holds' not in tables:
                raise MirrorError(409,'migration_guard_missing')
            if 'management_migration_holds' in tables and db.execute("SELECT 1 FROM management_migration_holds WHERE state='held' LIMIT 1").fetchone():
                raise MirrorError(409,'migration_in_progress')
            old=db.execute('SELECT * FROM web_mirror_receipts WHERE device_id=? AND (revision=? OR request_ref=?)',(device['id'],envelope['revision'],envelope['request_ref'])).fetchone()
            if old:
                if old['digest']!=digest:raise MirrorError(409,'snapshot_reference_reused')
                db.rollback();return json.loads(old['receipt'])|{'duplicate':True}
            if envelope['revision']<=device['revision']:raise MirrorError(409,'old_snapshot_revision')
            validate(envelope['data'],clock);data=dict(envelope['data']);data.pop('students')
            data.update(source_sha256=digest,source_captured_at=datetime.fromtimestamp(clock,TZ).strftime('%Y-%m-%d %H:%M:%S')+'（平板自动同步）',generated_at=envelope['data']['source_captured_at'])
            teachers={}
            for r in db.execute("SELECT u.id,u.display_name FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.active=1 AND r.role='teacher'",(device['org_id'],)):teachers.setdefault(r['display_name'],[]).append(r['id'])
            warnings=[];assignments=[]
            for c in data['classes']:
                users=teachers.get(c['teacher'],[])
                if len(users)==1:assignments.append((c['local_id'],users[0]))
                else:warnings.append({'class_name':c['name'],'teacher':c['teacher'],'reason':'teacher_account_unmatched'})
            db.execute('DELETE FROM web_mirror_assignments');db.executemany('INSERT INTO web_mirror_assignments VALUES(?,?)',assignments)
            db.execute('UPDATE web_dataset SET data_json=?,source_sha256=?,imported_at=? WHERE id=1',(json.dumps(data,ensure_ascii=True,separators=(',',':')),digest,clock))
            for l in data['lessons']:
                for s in l['members']:
                    if s['status']=='arrived':db.execute("UPDATE web_makeup_requests SET status='observed_arrived' WHERE org_id=? AND lesson_key=? AND student_local_id=?",(device['org_id'],l['key'],s['student_local_id']))
            db.execute('INSERT INTO web_mirror_status VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET device_id=excluded.device_id,revision=excluded.revision,received_at=excluded.received_at,source_sha256=excluded.source_sha256,warnings=excluded.warnings',(device['id'],envelope['revision'],clock,digest,json.dumps(warnings,ensure_ascii=True)))
            db.execute('UPDATE web_mirror_devices SET revision=?,last_received=?,source_sha256=? WHERE id=?',(envelope['revision'],clock,digest,device['id']))
            result={'protocol':1,'device_id':device['id'],'revision':envelope['revision'],'request_ref':envelope['request_ref'],'source_sha256':digest,'received_at':clock,'classes':len(data['classes']),'students':len(envelope['data']['students']),'teacher_warnings':len(warnings),'duplicate':False,'hours_deducted':False}
            db.execute('INSERT INTO web_mirror_receipts VALUES(?,?,?,?,?)',(device['id'],envelope['revision'],envelope['request_ref'],digest,json.dumps(result,separators=(',',':'))));db.commit();return result
        except BaseException:db.rollback();raise
