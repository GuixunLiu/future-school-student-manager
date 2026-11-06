"""Explicit adoption and decision of preserved pre-cutover makeup requests.

Original requests stay immutable. An atomic decision links to the canonical
approval, reusing an existing matching request instead of charging twice.
"""
from contextlib import closing
import json
import sqlite3
from business.store import Problem,uid
from .approvals import digest,encoded,label
from .historical import context_for,prepare_request
from .policy import require,workspace

SCHEMA='''
CREATE TABLE IF NOT EXISTS management_legacy_decisions (
 org_id TEXT NOT NULL,legacy_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,source_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('approved','rejected')),decided_by TEXT NOT NULL,decided_at INTEGER NOT NULL,
 reason TEXT NOT NULL,decision_ref TEXT NOT NULL,decision_digest TEXT NOT NULL,request_id TEXT,
 PRIMARY KEY(org_id,legacy_id),UNIQUE(org_id,decided_by,decision_ref),
 FOREIGN KEY(org_id,decided_by) REFERENCES users(org_id,id),FOREIGN KEY(request_id) REFERENCES management_requests(id)
);
CREATE TRIGGER IF NOT EXISTS management_legacy_decision_no_update BEFORE UPDATE ON management_legacy_decisions BEGIN SELECT RAISE(ABORT,'immutable legacy decision'); END;
CREATE TRIGGER IF NOT EXISTS management_legacy_decision_no_delete BEFORE DELETE ON management_legacy_decisions BEGIN SELECT RAISE(ABORT,'immutable legacy decision'); END;
'''

def enabled(db):return db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_legacy_decisions'").fetchone() is not None

def initialize(database):
    with closing(sqlite3.connect(database)) as db:db.executescript('BEGIN IMMEDIATE;\n'+SCHEMA+'\nCOMMIT;')

def original(db,org,key):
    row=db.execute('SELECT id,org_id,lesson_key,student_local_id,reason,status,created_at FROM web_makeup_requests WHERE org_id=? AND id=?',(org,key)).fetchone()
    if not row:raise Problem(404,'legacy_request_missing','未找到这条接入前申请')
    return dict(row)

def class_for(db,org,lesson_key):
    saved=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone()
    if not saved or len(saved[0].encode())>16*1024*1024:return None
    lessons=[l for l in json.loads(saved[0]).get('lessons',[]) if l.get('key')==lesson_key]
    if len(lessons)!=1:return None
    row=db.execute("SELECT server_id FROM management_source_links WHERE org_id=? AND kind='class' AND local_id=?",(org,lessons[0].get('class_local_id'))).fetchone()
    return row[0] if row else None

def target(db,org,row):
    link=db.execute("SELECT server_id FROM management_source_links WHERE org_id=? AND kind='student' AND local_id=?",(org,row['student_local_id'])).fetchone()
    if not link:raise Problem(409,'legacy_student_unmapped','原学生尚未关联当前同步名册，请先核对，不能直接扣课')
    sid=link[0];lid='legacy:'+row['lesson_key']
    context_for(db,org,lid,sid)
    # Reuse a matching pending/approved canonical makeup. A leave request is a
    # different decision and must never be silently replaced by this operation.
    current=db.execute("SELECT * FROM management_requests WHERE org_id=? AND lesson_id=? AND student_id=? AND status IN ('pending','approved') ORDER BY created_at DESC,id DESC LIMIT 1",(org,lid,sid)).fetchone()
    if current and current['kind']!='makeup':raise Problem(409,'legacy_conflicting_request','该学生已有请假申请或批准记录，请先核对，不能再补签扣课')
    return lid,sid,current

def result(store,db,org,saved,*,duplicate=False):
    request_id=saved['request_id']
    delivery=store.delivery_status(db,org,request_id,'approved') if request_id and saved['status']=='approved' else 'not_created'
    return dict(id=saved['legacy_id'],status=saved['status'],duplicate=duplicate,effective=saved['status']=='approved',request_id=request_id,tablet_delivery=delivery,decided_at=saved['decided_at'],decision_reason=saved['reason'])

def describe(store,db,actor,selected,row):
    if not enabled(db):return {}
    old=original(db,actor['org_id'],row['id']);saved=db.execute('SELECT * FROM management_legacy_decisions WHERE org_id=? AND legacy_id=?',(actor['org_id'],row['id'])).fetchone()
    if saved:
        return result(store,db,actor['org_id'],saved)|dict(legacy_status=old['status'],requires_review=False,can_decide=False,can_approve=False,can_reject=False)
    scope=store.scope(db,actor);class_id=class_for(db,actor['org_id'],old['lesson_key'])
    allowed=selected=='admin' or selected=='assessor' and class_id in scope.approval_classes
    answer=dict(can_decide=allowed and old['status']=='pending',can_reject=allowed and old['status']=='pending',can_approve=False)
    if old['status']!='pending':return answer|dict(approval_blocked_reason='原申请已处理，不重复执行')
    try:
        lid,sid,current=target(db,actor['org_id'],old)
        answer.update(can_approve=allowed,linked_request_id=current['id'] if current else None,already_effective=bool(current and current['status']=='approved'))
    except Problem as e:answer['approval_blocked_reason']=e.message
    return answer

def decide(store,token,role,*,legacy_id,approve,reason,decision_ref):
    if type(approve)is not bool:raise Problem(422,'invalid_decision','请选择审批结果')
    reason=label(reason);decision_ref=label(decision_ref,80)
    hashed=digest(dict(legacy_id=legacy_id,approve=approve,reason=reason))
    def run(db,actor):
        selected=workspace(actor,role)
        if not enabled(db):raise Problem(409,'legacy_approval_disabled','旧申请审批尚未启用')
        old=original(db,actor['org_id'],legacy_id);org=actor['org_id'];class_id=class_for(db,org,old['lesson_key'])
        require(actor,selected,'approvals.decide',scope=store.scope(db,actor),class_id=class_id)
        prior=db.execute('SELECT * FROM management_legacy_decisions WHERE org_id=? AND decided_by=? AND decision_ref=?',(org,actor['id'],decision_ref)).fetchone()
        if prior:
            if prior['decision_digest']!=hashed or prior['source_sha256']!=digest(old):raise Problem(409,'idempotency_conflict','原申请或审批内容已变化，请刷新核对')
            return result(store,db,org,prior,duplicate=True)
        if db.execute('SELECT 1 FROM management_legacy_decisions WHERE org_id=? AND legacy_id=?',(org,legacy_id)).fetchone() or old['status']!='pending':raise Problem(409,'already_decided','这条原申请已经处理，请刷新查看结果')
        request_id=None;reused=False
        if approve:
            from .migration_guard import require_not_held
            require_not_held(db,org,store.clock())
            lid,sid,current=target(db,org,old)
            prepare_request(store,db,actor,selected,lid,sid,permission='approvals.decide')
            if current:request_id=current['id'];reused=True
            else:
                fact=store.fact(db,org,lid,sid)
                if fact['status']!='missing' or fact['revision']!=0 or not fact['expected']:raise Problem(409,'attendance_changed','原课堂已生效或已变化，不能再扣课')
                request_id=uid();request_ref='legacy-adopt:'+digest(dict(org=org,id=legacy_id))[:48]
                request_reason=label(old['reason'])
                payload=digest(dict(lesson=lid,student=sid,kind='makeup',reason=request_reason,revision=fact['revision']))
                db.execute("INSERT INTO management_requests(id,org_id,lesson_id,student_id,class_id,kind,requested_by,request_ref,digest,reason,expected_revision,status,created_at) VALUES(?,?,?,?,?,'makeup',?,?,?,?,?,'pending',?)",(request_id,org,lid,sid,fact['class_id'],actor['id'],request_ref,payload,request_reason,fact['revision'],old['created_at']))
                store.record_audit(db,actor,selected,'legacy_makeup_adopted',request_id,legacy_id=legacy_id,source_sha256=digest(old))
            if not current or current['status']=='pending':
                ref='legacy-decision:'+digest(dict(org=org,id=legacy_id,ref=decision_ref))[:48]
                store.decide_in_transaction(db,actor,selected,request_id=request_id,approve=True,reason=reason,decision_ref=ref)
        db.execute('INSERT INTO management_legacy_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(org,legacy_id,digest(old),encoded(old),'approved' if approve else 'rejected',actor['id'],store.clock(),reason,decision_ref,hashed,request_id))
        store.record_audit(db,actor,selected,'legacy_makeup_approved' if approve else 'legacy_makeup_rejected',legacy_id,canonical_request_id=request_id,reused_existing=reused,reason=reason)
        saved=db.execute('SELECT * FROM management_legacy_decisions WHERE org_id=? AND legacy_id=?',(org,legacy_id)).fetchone()
        return result(store,db,org,saved)
    return store.command(token,run)
