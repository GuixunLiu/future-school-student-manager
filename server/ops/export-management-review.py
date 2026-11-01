"""One read-only transaction: base roster, archives, staff and live mirror.

No passwords, tokens, faces, contact details, configuration files or writes.
The base roster and mirror are separate sources and are never merged here.
"""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import json,os,re,sqlite3,stat

def private_path(p,directory=False):
    if os.name!='posix' or not p.is_absolute() or any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('Explicit private Linux paths required')
    s=p.lstat()
    if s.st_uid!=os.getuid() or s.st_mode&0o077 or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):raise ValueError('Private owned path required')

def snapshot(database,organization):
    if not re.fullmatch('[a-z0-9][a-z0-9_.-]{2,63}',organization):raise ValueError('Invalid organization')
    private_path(database.parent,True);private_path(database)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        if db.execute('PRAGMA application_id').fetchone()[0]!=1465405012 or db.execute('PRAGMA user_version').fetchone()[0]!=1 or db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Database identity or relationships differ')
        org=db.execute('SELECT id,slug FROM organizations WHERE slug=?',(organization,)).fetchone()
        if not org or db.execute('SELECT COUNT(*) FROM organizations').fetchone()[0]!=1:raise ValueError('Database-wide mirror requires the exact single organization')
        org=org['id'];tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        def read(sql,args=(),limit=10000):
            rows=db.execute(sql,args).fetchmany(limit+1)
            if len(rows)>limit:raise ValueError('Export limit exceeded; no partial source')
            return [dict(r) for r in rows]
        classes=read('SELECT id,name,course_name FROM classes WHERE org_id=? ORDER BY id',(org,),1000)
        students=read('SELECT id,name,external_ref,class_id,remaining_units,active FROM students WHERE org_id=? ORDER BY id',(org,))
        for s in students:
            if s['active'] not in (0,1):raise ValueError('Invalid student status')
            s['active']=bool(s['active'])
        role_rows=read('SELECT user_id,role FROM roles WHERE org_id=? ORDER BY user_id,role',(org,))
        if 'management_roles' in tables:role_rows+=read('SELECT user_id,role FROM management_roles WHERE org_id=? ORDER BY user_id,role',(org,))
        roles={}
        for r in role_rows:
            if r['role'] in {'admin','teacher','principal','assessor'}:roles.setdefault(r['user_id'],set()).add(r['role'])
        staff=read('SELECT id,login,display_name,active FROM users WHERE org_id=? ORDER BY id',(org,),1000)
        if any(s['active'] not in (0,1) for s in staff):raise ValueError('Invalid account status')
        staff=[s|dict(active=bool(s['active']),roles=sorted(roles[s['id']])) for s in staff if s['id'] in roles]
        active_teachers={s['id']:s for s in staff if s['active'] and 'teacher' in s['roles']}
        grants=read('SELECT class_id,user_id FROM teacher_classes WHERE org_id=? ORDER BY class_id,user_id',(org,),50000)
        for c in classes:c['teachers']=[dict(id=r['user_id'],name=active_teachers[r['user_id']]['display_name']) for r in grants if r['class_id']==c['id'] and r['user_id'] in active_teachers]
        archives=[];links=[]
        if 'archived_students' in tables:archives=read('SELECT id,name,external_ref,remaining_units,previous_class_name FROM archived_students WHERE org_id=? ORDER BY id',(org,))
        if 'roster_source_links' in tables:
            links=read('SELECT kind,source_id,entity_id,bundle_sha256 FROM roster_source_links WHERE org_id=? ORDER BY kind,source_id',(org,),20000)
            for row in links:row['source_id']=str(row['source_id'])
        mirror=dict(present=False,revision=None,received_at=None,source_sha256=None,as_of_date=None,source_captured_at=None,source_records=None,device_active=None,device_expires_at=None,classes=[],lessons=[],covered_weeks=[])
        if 'web_mirror_status' in tables:
            status=db.execute('SELECT * FROM web_mirror_status WHERE id=1').fetchone()
            if status:
                dataset=db.execute('SELECT data_json,source_sha256 FROM web_dataset WHERE id=1').fetchone()
                device=db.execute('SELECT org_id,active,expires_at FROM web_mirror_devices WHERE id=?',(status['device_id'],)).fetchone()
                if not dataset or len(dataset['data_json'].encode())>16*1024*1024 or not device or device['org_id']!=org or dataset['source_sha256']!=status['source_sha256']:raise ValueError('Mirror ownership or source differs')
                data=json.loads(dataset['data_json'])
                if data.get('source_sha256')!=status['source_sha256']:raise ValueError('Mirror metadata differs')
                if device['active'] not in (0,1) or not isinstance(data.get('classes'),list) or len(data['classes'])>1000 or not isinstance(data.get('lessons'),list) or len(data['lessons'])>30000 or not isinstance(data.get('weeks'),list) or len(data['weeks'])>262:raise ValueError('Mirror scope exceeds limit')
                assignments=read('SELECT class_local_id,user_id FROM web_mirror_assignments ORDER BY class_local_id,user_id',limit=50000)
                projected_classes=[];projected_lessons=[]
                for c in data['classes']:
                    projected_classes.append({k:c[k] for k in ('local_id','name','course_name','teacher')}|dict(teacher_ids=[r['user_id'] for r in assignments if r['class_local_id']==c['local_id'] and r['user_id'] in active_teachers]))
                for lesson in data['lessons']:
                    if not isinstance(lesson.get('members'),list) or len(lesson['members'])>10000:raise ValueError('Mirror member limit exceeded')
                    row={k:lesson[k] for k in ('key','class_local_id','class_name','teacher','date','kind','start_time','end_time','expected','arrived','leave_count','needs_review')}
                    row['members']=[{k:s[k] for k in ('student_local_id','name','external_ref','remaining_units','expected','status','attendance_time','attendance_method','makeup')} for s in lesson['members']]
                    projected_lessons.append(row)
                mirror=dict(present=True,revision=status['revision'],received_at=status['received_at'],source_sha256=status['source_sha256'],as_of_date=data['as_of_date'],source_captured_at=data['source_captured_at'],source_records=data['source_records'],device_active=bool(device['active']),device_expires_at=device['expires_at'],classes=projected_classes,lessons=projected_lessons,covered_weeks=[dict(start=w['start'],end=w['end']) for w in data['weeks']])
        return dict(format='future-school-management-review-v1',schema_version=1,complete=True,generated_at=datetime.now(timezone.utc).isoformat(),organization=organization,org_id=org,source_access='read_only',contains_private_data=True,classes=classes,students=students,archive_available='archived_students' in tables,archived_students=archives,source_links_available='roster_source_links' in tables,source_links=links,staff=staff,mirror=mirror)
