from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated

from fastapi import Depends, Header, Query
from fastapi.responses import Response
from pydantic import Field
from business.api import create_app as base_app
from business.models import ID, Input
from business.store import Problem, Store, uid

TZ=timezone(timedelta(hours=8))
ROOT=Path(__file__).parent


def install_data(database, dataset, *, clock=None):
    if dataset.get('format')!='future-school-teacher-web-dataset' or dataset.get('schema_version')!=1 or dataset.get('history_import_is_read_only') is not True or dataset.get('balances_charged') is not False or dataset.get('wechat_enabled') is not False:raise ValueError('Explicit read-only web dataset required')
    if not isinstance(dataset.get('lessons'),list) or len(dataset['lessons'])>30000 or not dataset.get('weeks'):raise ValueError('Bounded course and report data required')
    if len({l['key'] for l in dataset['lessons']})!=len(dataset['lessons']):raise ValueError('Duplicate lesson keys')
    store=Store(database,clock)
    with store.transaction() as db:
        orgs=db.execute('SELECT id FROM organizations').fetchall()
        if len(orgs)!=1:raise ValueError('One explicit school required')
        org=orgs[0][0];links={str(r['source_id']):r['entity_id'] for r in db.execute("SELECT source_id,entity_id FROM roster_source_links WHERE org_id=? AND kind='class'",(org,))}
        mapping={}
        for c in dataset['classes']:
            remote=links.get(c['local_id']);row=db.execute('SELECT name FROM classes WHERE org_id=? AND id=?',(org,remote)).fetchone()
            if not row or row[0]!=c['name']:raise ValueError('Class identity requires review')
            teachers=db.execute('SELECT u.display_name FROM teacher_classes t JOIN users u ON u.id=t.user_id AND u.org_id=t.org_id WHERE t.org_id=? AND t.class_id=? AND u.active=1',(org,remote)).fetchall()
            if len(teachers)!=1 or teachers[0][0]!=c['teacher']:raise ValueError('Current teacher assignment requires review')
            mapping[c['local_id']]=remote
        student_links={str(r['source_id']):r['entity_id'] for r in db.execute("SELECT source_id,entity_id FROM roster_source_links WHERE org_id=? AND kind='student'",(org,))}
        identities={r['id']:(r['name'],r['external_ref']) for table in ['students','archived_students'] for r in db.execute('SELECT id,name,external_ref FROM '+table+' WHERE org_id=?',(org,))}
        for lesson in dataset['lessons']:
            if len({r['student_local_id'] for r in lesson['members']})!=len(lesson['members']):raise ValueError('Duplicate lesson member')
            for r in lesson['members']:
                if identities.get(student_links.get(r['student_local_id']))!=(r['name'],r['external_ref']):raise ValueError('Student identity requires review')
                if r['status'] not in {'arrived','leave','missing'}:raise ValueError('Unknown attendance state')
        db.execute('CREATE TABLE IF NOT EXISTS web_dataset (id INTEGER PRIMARY KEY CHECK(id=1),data_json TEXT NOT NULL,source_sha256 TEXT NOT NULL,imported_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS web_class_links (local_id TEXT PRIMARY KEY,remote_id TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS web_makeup_requests (id TEXT PRIMARY KEY,org_id TEXT NOT NULL,lesson_key TEXT NOT NULL,student_local_id TEXT NOT NULL,requested_by TEXT NOT NULL,request_ref TEXT NOT NULL,payload_digest TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN (\'pending\',\'observed_arrived\')),source_sha256 TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(org_id,requested_by,request_ref),UNIQUE(org_id,lesson_key,student_local_id))')
        db.execute('DELETE FROM web_class_links');db.executemany('INSERT INTO web_class_links VALUES(?,?)',mapping.items())
        db.execute('INSERT INTO web_dataset VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,source_sha256=excluded.source_sha256,imported_at=excluded.imported_at',(json.dumps(dataset,ensure_ascii=True,separators=(',',':')),dataset['source_sha256'],store.clock()))
        for l in dataset['lessons']:
            for s in l['members']:
                if s['status']=='arrived':db.execute("UPDATE web_makeup_requests SET status='observed_arrived' WHERE org_id=? AND lesson_key=? AND student_local_id=?",(org,l['key'],s['student_local_id']))
    return {'classes':len(dataset['classes']),'lessons':len(dataset['lessons']),'weeks':len(dataset['weeks']),'source_records':dataset['source_records'],'balances_charged':False,'requests_confirm_attendance':False}


class Makeup(Input):
    lesson_key:str=Field(min_length=1,max_length=200)
    student_local_id:str=Field(pattern=r'^[0-9]{1,20}$')
    reason:str=Field(min_length=1,max_length=200)
    request_ref:ID
    expected_source_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:bool


def create_app(database, clock=None, *, allow_test_client=False):
    app=base_app(database,clock,allow_test_client=allow_test_client);store=app.state.store
    keep={'/v1/auth/login','/v1/auth/logout','/v1/me'}
    app.router.routes[:]=[r for r in app.router.routes if r.path in keep]

    def token(authorization:Annotated[str|None,Header()]=None):
        if not authorization or not authorization.startswith('Bearer ') or len(authorization)>128:raise Problem(401,'unauthorized','请先登录')
        return authorization[7:]
    Token=Annotated[str,Depends(token)]

    def dataset(db):
        row=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone()
        if not row:raise Problem(503,'dataset_unavailable','课表资料尚未就绪')
        return json.loads(row[0])

    def allowed(db,actor,data):
        store.require_user(actor)
        if 'admin' in actor['roles']:return {c['local_id'] for c in data['classes']}
        if 'teacher' not in actor['roles']:raise Problem(403,'teacher_required','仅教师或管理员可使用')
        return {r[0] for r in db.execute('SELECT w.local_id FROM web_class_links w JOIN teacher_classes t ON t.class_id=w.remote_id WHERE t.org_id=? AND t.user_id=?',(actor['org_id'],actor['id']))}

    def requests(db,actor):
        return [dict(r) for r in db.execute('SELECT id,lesson_key,student_local_id,requested_by,status,reason,created_at FROM web_makeup_requests WHERE org_id=? ORDER BY created_at DESC LIMIT 1000',(actor['org_id'],))]

    def metadata(data):
        return {k:data[k] for k in ['source_captured_at','source_sha256','as_of_date','source_records']}

    @app.get('/health/ready')
    def health():
        with store.transaction() as db:data=dataset(db)
        return {'status':'ready','mode':'teacher_web','version':'0.1.0','report_display_version':2,'makeup_mode':'request_pending_tablet','data_realtime':False,'student_sync_enabled':False,'wechat_sending_enabled':False,'parent_binding_enabled':False,'business_ready':False,'source_sha256':data['source_sha256']}

    @app.get('/v1/web/days')
    def days(auth:Token,date:Annotated[str|None,Query(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None):
        target=date or datetime.fromtimestamp(store.clock(),TZ).date().isoformat()
        try:datetime.strptime(target,'%Y-%m-%d')
        except ValueError:raise Problem(422,'invalid_date','日期不正确')
        def run(db,actor):
            data=dataset(db);ids=allowed(db,actor,data);pending={(r['lesson_key'],r['student_local_id']):r for r in requests(db,actor)}
            lessons=[]
            for original in data['lessons']:
                if original['date']!=target or original['class_local_id'] not in ids:continue
                l=dict(original);l['members']=[dict(r,makeup_request=pending.get((l['key'],r['student_local_id']))) for r in original['members']];lessons.append(l)
            upcoming=[{k:l[k] for k in ['date','class_name','start_time','teacher']} for l in sorted(data['lessons'],key=lambda l:(l['date'],l['start_time'],l['class_name'])) if l['date']>target and l['class_local_id'] in ids][:4]
            return {**metadata(data),'date':target,'lessons':sorted(lessons,key=lambda l:(l['start_time'],l['class_name'])),'upcoming':upcoming,'available_from':min(w['start'] for w in data['weeks']),'available_until':max(w['end'] for w in data['weeks']),'makeup_mode':'request_pending_tablet'}
        return store.command(auth,run)

    @app.get('/v1/web/weeks')
    def weeks(auth:Token,start:Annotated[str|None,Query(pattern=r'^\d{4}-\d{2}-\d{2}$')]=None):
        def run(db,actor):
            store.require_user(actor,'admin');data=dataset(db);today=datetime.fromtimestamp(store.clock(),TZ).date();default=(today-timedelta(days=today.weekday())).isoformat();target=start or default
            week=next((w for w in data['weeks'] if w['start']==target),None)
            if week is None:raise Problem(404,'week_unavailable','这周没有已导出的资料，请选择列表中的周次')
            current_names={c['name'] for c in data['classes']}
            # Historical labels belong to that week's records, not today's class list.
            # Annotate a response copy; never remove history or change its totals.
            report=dict(week,classes=[dict(row,historical_class_name=row['name'] not in current_names) for row in week['classes']])
            return {**metadata(data),'weeks':[{'start':w['start'],'end':w['end']} for w in data['weeks'] if w['start']<=default],'report':report}
        return store.command(auth,run)

    @app.get('/v1/web/requests')
    def pending(auth:Token):
        def run(db,actor):
            data=dataset(db);ids=allowed(db,actor,data);by_key={l['key']:l for l in data['lessons'] if l['class_local_id'] in ids}
            out=[]
            for r in requests(db,actor):
                l=by_key.get(r['lesson_key']);student=next((s for s in l['members'] if s['student_local_id']==r['student_local_id']),None) if l else None
                if student:out.append(dict(r,student_name=student['name'],external_ref=student['external_ref'],class_name=l['class_name'],date=l['date']))
            return {'items':out,'attendance_changed':False,'hours_deducted':False}
        return store.command(auth,run)

    @app.post('/v1/web/makeup-requests',status_code=201)
    def makeup(data:Makeup,auth:Token):
        if not data.confirmed:raise Problem(422,'confirmation_required','请先核实学生确实需要补签')
        def run(db,actor):
            snapshot=dataset(db);ids=allowed(db,actor,snapshot)
            l=next((l for l in snapshot['lessons'] if l['key']==data.lesson_key and l['class_local_id'] in ids),None)
            if not l:raise Problem(403,'class_not_assigned','该课堂不在你的任课范围内')
            digest=hashlib.sha256(json.dumps(data.model_dump(),sort_keys=True).encode()).hexdigest()
            old=db.execute('SELECT id,payload_digest,status FROM web_makeup_requests WHERE org_id=? AND requested_by=? AND request_ref=?',(actor['org_id'],actor['id'],data.request_ref)).fetchone()
            if old:
                if old['payload_digest']!=digest:raise Problem(409,'request_ref_reused','请求编号与原内容不一致')
                return {'id':old['id'],'status':old['status'],'duplicate':True,'hours_deducted':False,'attendance_changed':False}
            if data.expected_source_sha256!=snapshot['source_sha256']:raise Problem(409,'snapshot_changed','资料已更新，请刷新后重新核对')
            if l['date']>datetime.fromtimestamp(store.clock(),TZ).date().isoformat():raise Problem(409,'future_lesson','未来课堂不能提前申请补签')
            student=next((r for r in l['members'] if r['student_local_id']==data.student_local_id),None)
            if not student or not student['expected']:raise Problem(409,'not_expected','该学生不在本次应到名单内')
            if student['status']!='missing':raise Problem(409,'attendance_or_leave_exists','学生已签到或已请假，请先核对原记录')
            old=db.execute('SELECT id,status FROM web_makeup_requests WHERE org_id=? AND lesson_key=? AND student_local_id=?',(actor['org_id'],data.lesson_key,data.student_local_id)).fetchone()
            if old:return {'id':old['id'],'status':old['status'],'duplicate':True,'hours_deducted':False,'attendance_changed':False}
            request_id=uid();db.execute('INSERT INTO web_makeup_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',(request_id,actor['org_id'],data.lesson_key,data.student_local_id,actor['id'],data.request_ref,digest,data.reason,'pending',snapshot['source_sha256'],store.clock()))
            store.audit(db,actor,'web_makeup_requested',request_id,lesson_key=data.lesson_key,student_local_id=data.student_local_id,attendance_changed=False,hours_deducted=False)
            return {'id':request_id,'status':'pending','duplicate':False,'hours_deducted':False,'attendance_changed':False}
        return store.command(auth,run)

    @app.get('/',include_in_schema=False)
    def home():return Response((ROOT/'web/index.html').read_bytes(),media_type='text/html')
    @app.get('/workspace.js',include_in_schema=False)
    def script():return Response((ROOT/'web/workspace.js').read_bytes(),media_type='text/javascript')
    @app.get('/workspace.css',include_in_schema=False)
    def css():return Response((ROOT/'web/workspace.css').read_bytes(),media_type='text/css')
    return app
