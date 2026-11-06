"""One user-confirmed 2026-11-01 Python afternoon makeup; no charge or raw edit."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

WORK=Path('/opt/wxnotify/app/.management-cutover-3fqe7xfx')
sha=lambda raw:hashlib.sha256(raw).hexdigest()
encoded=lambda v:json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':'))

def private(path):
    if path.is_symlink() or os.name!='posix' or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:raise ValueError('Private owned runtime required')

def confirm(payload):
    if set(payload)!={'format','expected_lesson_sha256'} or payload['format']!='future-school-confirm-known-temporary-makeup-v1' or len(payload['expected_lesson_sha256'])!=64:raise ValueError('Exact confirmed makeup review required')
    database=WORK/'business.sqlite'
    private(WORK);private(database)
    backup=Path(tempfile.mkdtemp(prefix='.confirmed-makeup-review-',dir=WORK));os.chmod(backup,0o700)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(backup/'business.sqlite')) as dest:source.backup(dest)
    os.chmod(backup/'business.sqlite',0o600)
    with closing(sqlite3.connect(database,timeout=10)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        try:
            orgs=list(db.execute("SELECT id FROM organizations WHERE slug='weilai'"))
            if len(orgs)!=1:raise ValueError('Original organization differs')
            org=orgs[0][0];actors=list(db.execute("SELECT u.id FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin' WHERE u.org_id=? AND u.login='liuguixun' AND u.active=1",(org,)))
            if len(actors)!=1:raise ValueError('Confirmed operator account differs')
            saved=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone();data=json.loads(saved[0])
            lessons=[l for l in data['lessons'] if l.get('date')=='2026-11-01' and l.get('class_name')=='Python周六下午1' and l.get('teacher')=='尹海勇']
            if len(lessons)!=1 or sha(encoded(lessons[0]).encode())!=payload['expected_lesson_sha256']:raise ValueError('Confirmed original lesson differs')
            lesson=lessons[0];members=lesson.get('members',[]);unexpected=[m for m in members if m.get('status')=='arrived' and m.get('expected')is not True]
            if lesson['expected']!=0 or lesson['arrived']!=1 or lesson['leave_count']!=0 or lesson.get('needs_review')is not True or len(unexpected)!=1 or sum(m.get('status')=='arrived' for m in members)!=1:raise ValueError('Only the confirmed individual makeup may be reviewed')
            marker=db.execute('SELECT archive_json FROM management_initializations WHERE org_id=?',(org,)).fetchone();records=json.loads(marker[0])['sections']['attendance']
            originals=[r for r in records if str(r.get('studentId'))==str(unexpected[0]['student_local_id']) and r.get('date')=='2026-11-01']
            if len(originals)!=1:raise ValueError('Original arrival is not unique')
            detail=dict(format='future-school-temporary-makeup-review-v1',source_sha256=sha(saved[0].encode()),expected_student_ids=[str(unexpected[0]['student_local_id'])],authorization='User explicitly confirmed actual November 1 temporary makeup in conversation; no attendance or charge is being created')
            lid='legacy:'+lesson['key'];existing=list(db.execute("SELECT detail FROM management_audit WHERE org_id=? AND action='historical_temporary_makeup_confirmed' AND entity_id=?",(org,lid)))
            if existing:
                if len(existing)!=1 or json.loads(existing[0][0])!=detail:raise ValueError('An existing review differs; reconcile explicitly')
                db.rollback();return dict(confirmed=True,duplicate=True,expected_added=1,attendance_added=0,charges_added=0,backup_directory=str(backup))
            db.execute('INSERT INTO management_audit(org_id,actor_id,workspace_role,action,entity_id,detail,created_at) VALUES(?,?,?,?,?,?,?)',(org,actors[0][0],'admin','historical_temporary_makeup_confirmed',lid,encoded(detail),int(time.time())))
            db.commit();return dict(confirmed=True,duplicate=False,expected_added=1,attendance_added=0,charges_added=0,raw_history_changed=False,backup_directory=str(backup))
        except BaseException:db.rollback();raise

if __name__=='__main__':
    import sys
    try:print(json.dumps(dict(ok=True,result=confirm(json.loads(sys.stdin.readline(2000)))),separators=(',',':')),flush=True)
    except Exception as e:print(json.dumps(dict(ok=False,error=type(e).__name__,detail=str(e)[:160] if isinstance(e,ValueError) else type(e).__name__)),flush=True);raise SystemExit(1)
