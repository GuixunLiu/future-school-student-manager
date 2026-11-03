"""Explicit amendments of missing sealed historical attendance, never replay.

The original archive/web snapshot stays unchanged. A request admits only its
one missing fact; approval uses the current canonical balance exactly once.
"""
from contextlib import closing
from datetime import datetime,timedelta,time
import hashlib
import json
import re
import sqlite3

from business.store import Problem
from .approvals import digest,encoded
from .deadlines import TZ
from .history_boundary import load as boundary_for
from .policy import require

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_historical_facts (
 org_id TEXT NOT NULL,lesson_id TEXT NOT NULL,student_id TEXT NOT NULL,context_json TEXT NOT NULL,source_sha256 TEXT NOT NULL,
 PRIMARY KEY(org_id,lesson_id,student_id),FOREIGN KEY(org_id,lesson_id,student_id) REFERENCES management_attendance(org_id,lesson_id,student_id)
);
CREATE TABLE IF NOT EXISTS management_historical_clients (
 org_id TEXT NOT NULL,device_id TEXT NOT NULL,generation TEXT NOT NULL,protocol INTEGER NOT NULL CHECK(protocol=1),
 PRIMARY KEY(org_id,device_id,generation),FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id)
);
CREATE TABLE IF NOT EXISTS management_historical_events (
 event_id TEXT PRIMARY KEY,org_id TEXT NOT NULL,delivery_class_id TEXT NOT NULL,
 FOREIGN KEY(event_id) REFERENCES management_outbox(id),FOREIGN KEY(org_id,delivery_class_id) REFERENCES classes(org_id,id)
);
CREATE TRIGGER IF NOT EXISTS management_history_fact_no_update BEFORE UPDATE ON management_historical_facts BEGIN SELECT RAISE(ABORT,'immutable historical source'); END;
CREATE TRIGGER IF NOT EXISTS management_history_fact_no_delete BEFORE DELETE ON management_historical_facts BEGIN SELECT RAISE(ABORT,'immutable historical source'); END;
CREATE TRIGGER IF NOT EXISTS management_history_event_no_update BEFORE UPDATE ON management_historical_events BEGIN SELECT RAISE(ABORT,'immutable historical delivery'); END;
CREATE TRIGGER IF NOT EXISTS management_history_event_no_delete BEFORE DELETE ON management_historical_events BEGIN SELECT RAISE(ABORT,'immutable historical delivery'); END;
'''
TABLES={'management_historical_facts','management_historical_clients','management_historical_events'}


def enabled(db):return TABLES<={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}


def initialize(database):
    with closing(sqlite3.connect(database)) as db:db.executescript('BEGIN IMMEDIATE;\n'+SCHEMA+'\nCOMMIT;')


def refuse(message='原历史或当前同步名册需要核对，尚未修改记录'):
    raise Problem(409,'historical_review_required',message)


def source(db,org,lesson_id):
    if not enabled(db):refuse('历史申请功能尚未启用')
    boundary=boundary_for(db,org)
    if not boundary or not isinstance(lesson_id,str) or not lesson_id.startswith('legacy:'):refuse()
    saved=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone()
    if not saved or len(saved[0].encode())>16*1024*1024:refuse()
    data=json.loads(saved[0]);lessons=data.get('lessons',[])
    if len(lessons)>30000:refuse()
    matches=[v for v in lessons if v.get('key')==lesson_id[7:]]
    if len(matches)!=1:refuse('未找到唯一的原历史课堂')
    lesson=matches[0]
    if not isinstance(lesson.get('date'),str) or lesson['date']>=boundary['canonical_from_day'] or lesson.get('needs_review')is True or not re.fullmatch(r'\d{2}:\d{2}',lesson.get('start_time','')):refuse('原课堂日期、时间或应到名单需要管理员核对')
    try:starts=int(datetime.fromisoformat(lesson['date']+'T'+lesson['start_time']).replace(tzinfo=TZ).timestamp())
    except (ValueError,TypeError):refuse()
    link=db.execute("SELECT server_id,local_id FROM management_source_links WHERE org_id=? AND kind='class' AND local_id=?",(org,lesson.get('class_local_id'))).fetchone()
    if not link or not db.execute('SELECT 1 FROM classes WHERE org_id=? AND id=?',(org,link['server_id'])).fetchone():refuse('原班级尚未关联当前班级，需要管理员核对')
    return lesson,link,starts,boundary,hashlib.sha256(saved[0].encode()).hexdigest()


def current_student(db,org,student_id):
    row=db.execute('''SELECT s.id,s.name,s.external_ref,s.active,s.class_id,b.units,b.revision,
      l.local_id,d.device_id,d.class_id AS delivery_class_id,a.generation,a.enabled,a.expires_at,v.active AS device_active,v.expires_at AS device_expires
      FROM students s JOIN management_balances b ON b.org_id=s.org_id AND b.student_id=s.id
      JOIN management_source_links l ON l.org_id=s.org_id AND l.kind='student' AND l.server_id=s.id
      JOIN management_device_students d ON d.org_id=s.org_id AND d.student_id=s.id
      JOIN management_device_access a ON a.org_id=d.org_id AND a.device_id=d.device_id
      JOIN devices v ON v.org_id=a.org_id AND v.id=a.device_id WHERE s.org_id=? AND s.id=?''',(org,student_id)).fetchone()
    if not row or not row['active'] or row['class_id']!=row['delivery_class_id']:refuse('该学生不在当前同步名册，请管理员先核对归档、调班及课时')
    return row


def context_for(db,org,lesson_id,student_id):
    lesson,link,starts,boundary,checksum=source(db,org,lesson_id);student=current_student(db,org,student_id)
    members=lesson.get('members',[])
    if not isinstance(members,list) or len(members)>10000:refuse()
    selected=[m for m in members if str(m.get('student_local_id'))==str(student['local_id'])]
    if len(selected)!=1 or selected[0].get('expected')is not True or selected[0].get('status')!='missing':refuse('只能为原名单中应到、未签到且未请假的学生申请')
    # A projection alone must not contradict original raw arrivals or leave.
    archived=db.execute('SELECT archive_json FROM management_initializations WHERE org_id=?',(org,)).fetchone()
    if not archived:refuse()
    sections=json.loads(archived[0])['sections']
    for name in ('attendance','leaves'):
        if any(str(r.get('studentId'))==str(student['local_id']) and r.get('date')==lesson['date'] and (name=='attendance' or r.get('status')=='approved') for r in sections[name]):refuse('原始记录已存在签到或请假，不能再次办理')
    context=dict(protocol=1,history_boundary=boundary,local_student_id=student['local_id'],local_class_id=link['local_id'],class_name=lesson.get('class_name',''),teacher_name=lesson.get('teacher',''),date=lesson['date'])
    if not context['class_name'] or not context['teacher_name']:refuse('原班级或教师名称待核对')
    return context,checksum,link['server_id'],starts,student


def prepare_request(store,db,actor,selected,lesson_id,student_id):
    context,checksum,class_id,starts,student=context_for(db,actor['org_id'],lesson_id,student_id)
    require(actor,selected,'attendance.request',scope=store.scope(db,actor),class_id=class_id)
    org=actor['org_id'];prior=db.execute('SELECT * FROM management_historical_facts WHERE org_id=? AND lesson_id=? AND student_id=?',(org,lesson_id,student_id)).fetchone()
    if prior:
        if prior['context_json']!=encoded(context) or prior['source_sha256']!=checksum:refuse('原历史来源已变化，需管理员重新核对')
        return
    existing=db.execute('SELECT * FROM management_attendance WHERE org_id=? AND lesson_id=? AND student_id=?',(org,lesson_id,student_id)).fetchone()
    if existing:refuse('此历史课堂已有其他来源的记录，不能自动合并')
    db.execute("INSERT INTO management_attendance VALUES(?,?,?,?,?,1,100,'missing',0)",(org,lesson_id,student_id,class_id,starts))
    db.execute('INSERT INTO management_historical_facts VALUES(?,?,?,?,?)',(org,lesson_id,student_id,encoded(context),checksum))


def approve_context(db,org,lesson_id,student_id,now):
    context,checksum,class_id,starts,student=context_for(db,org,lesson_id,student_id)
    saved=db.execute('SELECT * FROM management_historical_facts WHERE org_id=? AND lesson_id=? AND student_id=?',(org,lesson_id,student_id)).fetchone()
    if not saved or saved['source_sha256']!=checksum or saved['context_json']!=encoded(context):refuse('历史申请的来源核对不一致，未生效')
    if not student['enabled'] or not student['device_active'] or min(student['expires_at'],student['device_expires'])<=now:refuse('当前平板连接已暂停或到期')
    if not db.execute('SELECT 1 FROM device_classes WHERE org_id=? AND device_id=? AND class_id=?',(org,student['device_id'],student['delivery_class_id'])).fetchone():refuse('当前平板已无此学生班级的接收授权')
    if not db.execute('SELECT 1 FROM management_historical_clients WHERE org_id=? AND device_id=? AND generation=? AND protocol=1',(org,student['device_id'],student['generation'])).fetchone():refuse('请先更新并连接平板，使其能接收历史更正')
    start=int(datetime.combine(datetime.fromtimestamp(starts,TZ).date(),time(),TZ).timestamp())
    if db.execute("SELECT 1 FROM management_attendance WHERE org_id=? AND student_id=? AND starts_at>=? AND starts_at<? AND lesson_id!=? AND status IN ('arrived','leave')",(org,student_id,start,start+86400,lesson_id)).fetchone():refuse('该学生同日已有签到或请假，请先核对，避免重复扣课')
    return context|dict(delivery_class_id=student['delivery_class_id'])


def delivery_join(db):
    if not enabled(db):return '', 'b.class_id=r.class_id'
    return ' LEFT JOIN management_historical_events hx ON hx.org_id=o.org_id AND hx.event_id=o.id ', 'b.class_id=COALESCE(hx.delivery_class_id,r.class_id)'


def annotate(db,org,lesson,member,boundary):
    """Read-only eligibility and approved overlay; never materialize on GET."""
    if not enabled(db):return {}
    lid='legacy:'+lesson['key'];local=str(member.get('student_local_id',''))
    link=db.execute("SELECT server_id FROM management_source_links WHERE org_id=? AND kind='student' AND local_id=?",(org,local)).fetchone()
    result=dict(can_request=False,request_blocked_reason='该学生不在当前同步名册，请管理员先核对')
    if not link:return result
    if not db.execute("SELECT 1 FROM management_source_links WHERE org_id=? AND kind='class' AND local_id=?",(org,lesson.get('class_local_id'))).fetchone():
        return result|dict(request_blocked_reason='原班级尚未关联当前班级，需要管理员核对')
    sid=link[0];fact=db.execute('SELECT status,revision FROM management_attendance WHERE org_id=? AND lesson_id=? AND student_id=?',(org,lid,sid)).fetchone()
    admitted=db.execute('SELECT 1 FROM management_historical_facts WHERE org_id=? AND lesson_id=? AND student_id=?',(org,lid,sid)).fetchone()
    result.update(request_student_id=sid,revision=fact['revision'] if fact and admitted else 0,has_pending_request=bool(db.execute("SELECT 1 FROM management_requests WHERE org_id=? AND lesson_id=? AND student_id=? AND status='pending'",(org,lid,sid)).fetchone()))
    try:
        s=current_student(db,org,sid);result.update(balance_units=s['units'],balance_is_current=True,charge_units=100)
        eligible=member.get('expected')is True and member.get('status')=='missing' and not lesson.get('needs_review') and bool(lesson.get('start_time'))
        result.update(can_request=eligible,request_blocked_reason='' if eligible else '原记录已签到、已请假，或课堂时间需要核对')
    except Problem as error:result['request_blocked_reason']=error.message
    if admitted and fact and fact['status']!='missing':
        correction=db.execute("SELECT decided_at,kind FROM management_requests WHERE org_id=? AND lesson_id=? AND student_id=? AND status='approved' ORDER BY decided_at DESC LIMIT 1",(org,lid,sid)).fetchone()
        result.update(status=fact['status'],can_request=False,request_blocked_reason='',corrected_at=correction['decided_at'] if correction else None)
    return result
