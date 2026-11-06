"""One fixed lesson, read-only SQLite export; no credentials, names, reasons, or whole DB."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def private(path, directory=False):
    if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):raise ValueError("Ordinary scoped path required")


def stage_root(value):
    if not isinstance(value, str) or not re.fullmatch(r"/opt/wxnotify/app/single-class-offline-\d{14}-[a-f0-9]{8}", value):raise ValueError("Fixed stage required")
    path=Path(value);private(path,True);return path


def export(config):
    work=stage_root(config['stage_directory'])/'teacher-pilot';private(work,True)
    for name in ['business.sqlite','scoped.private.json','scoped-pilot.py']:private(work/name)
    runtime=(work/'scoped-pilot.py').read_bytes()
    if hashlib.sha256(runtime).hexdigest()!=config['runtime_sha256']:raise ValueError("Scoped runtime differs")
    verifier={'__name__':'verified_read_only_scoped_runtime','__file__':str(work/'scoped-pilot.py')}
    exec(compile(runtime,'<verified-scoped-runtime>','exec'),verifier)
    cfg=json.loads((work/'scoped.private.json').read_bytes());scope=cfg['scope']
    if cfg['format']!='future-school-scoped-teacher-config' or cfg['files']['scoped-pilot.py']!=config['runtime_sha256']:raise ValueError("Scoped configuration differs")
    for key in ['class_id','lesson_id','student_ids']:
        if scope[key]!=config[key]:raise ValueError("Confirmed scope differs")
    org=scope['organization_id'];lesson_id=scope['lesson_id'];class_id=scope['class_id']
    with closing(sqlite3.connect((work/'business.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check(1)').fetchone()[0]!='ok':raise ValueError("Database integrity requires review")
        if verifier['seal'](db)!=scope['master_sha256']:raise ValueError("Protected baseline changed")
        if db.execute('SELECT count(*) FROM devices WHERE active=1').fetchone()[0] or db.execute('SELECT count(*) FROM device_classes').fetchone()[0] or db.execute("SELECT count(*) FROM outbox WHERE status='pending_simulation'").fetchone()[0]:raise ValueError("Device or notice state changed")
        lesson=db.execute('SELECT org_id,class_id,teacher_id,starts_at,ends_at FROM lessons WHERE id=?',(lesson_id,)).fetchone()
        if lesson is None or tuple(lesson)!=(org,class_id,scope['teacher_id'],scope['starts_at'],scope['ends_at']):raise ValueError("Lesson changed")
        members=[r[0] for r in db.execute('SELECT student_id FROM lesson_members WHERE org_id=? AND lesson_id=? ORDER BY student_id',(org,lesson_id))]
        if members!=scope['student_ids']:raise ValueError("Confirmed membership changed")
        arrived=[r[0] for r in db.execute('SELECT student_id FROM attendance WHERE org_id=? AND lesson_id=? AND revoked=0 ORDER BY student_id',(org,lesson_id))]
        leaves=[]
        for r in db.execute('SELECT id,student_id,status,recorded_by FROM leaves WHERE org_id=? AND lesson_id=? ORDER BY id LIMIT 10001',(org,lesson_id)):
            if len(leaves)>=10000 or r['student_id'] not in members:raise ValueError("Leave records outside scope or limit")
            device=db.execute("SELECT 1 FROM devices d JOIN audit a ON a.org_id=d.org_id AND a.actor_id=d.id WHERE d.org_id=? AND d.id=? AND a.entity_id=? AND a.action='device_leave_mirrored' LIMIT 1",(org,r['recorded_by'],r['id'])).fetchone()
            teacher=db.execute("SELECT 1 FROM users u JOIN audit a ON a.org_id=u.org_id AND a.actor_id=u.id WHERE u.org_id=? AND u.id=? AND a.entity_id=? AND a.action='leave_approved' LIMIT 1",(org,r['recorded_by'],r['id'])).fetchone()
            if bool(device)==bool(teacher):raise ValueError("Leave origin requires manual audit review")
            leaves.append(dict(r)|{'class_id':class_id,'lesson_id':lesson_id,'origin':'device' if device else 'teacher'})
        payload={'class_id':class_id,'lesson_id':lesson_id,'identity_mapping_verified':True,'arrived_student_ids':arrived,'leaves':leaves}
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        db.rollback()
    return {'format':'future-school-pilot-server-leave-snapshot','schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'source_access':'read_only','contains_private_data':True,'student_ids':members,'snapshot_sha256':fingerprint,**payload,
            'server_written':False,'device_enabled':False,'wechat_enabled':False}
