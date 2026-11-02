"""Explicit, OFFLINE rehearsal on a new SQLite backup. No live apply path.

The entire database is copied, including credentials and unrecognized legacy
tables. Only reviewed roster rows are aligned; historical events are sealed,
never replayed. A rehearsal cannot be served by the management API.
"""
from contextlib import closing
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

from . import approvals, assessments, delivery, uplink, enrollment, migration
from .approvals import encoded
from .history_boundary import next_day

SCHEMA = '''
CREATE TABLE management_initializations (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL UNIQUE REFERENCES organizations(id),
 phase TEXT NOT NULL CHECK(phase='rehearsal_only'),
 plan_sha256 TEXT NOT NULL, review_sha256 TEXT NOT NULL,
 tablet_sha256 TEXT NOT NULL, archive_sha256 TEXT NOT NULL,
 archive_json TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE management_legacy_rows (
 initialization_id TEXT NOT NULL REFERENCES management_initializations(id),
 section TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(initialization_id,section,ordinal)
);
CREATE TABLE management_openings (
 initialization_id TEXT NOT NULL REFERENCES management_initializations(id),
 org_id TEXT NOT NULL, student_id TEXT NOT NULL, class_id TEXT NOT NULL,
 units INTEGER NOT NULL, revision INTEGER NOT NULL CHECK(revision=0),
 PRIMARY KEY(org_id,student_id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE management_source_links (
 initialization_id TEXT NOT NULL REFERENCES management_initializations(id),
 org_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('student','class')),
 local_id INTEGER NOT NULL, server_id TEXT NOT NULL,
 PRIMARY KEY(org_id,kind,local_id), UNIQUE(org_id,kind,server_id)
);
CREATE TABLE management_initialization_seals (
 initialization_id TEXT PRIMARY KEY REFERENCES management_initializations(id),
 receipt_sha256 TEXT NOT NULL, sealed_at INTEGER NOT NULL
);
'''
for _table in ('management_initializations', 'management_legacy_rows', 'management_openings','management_source_links','management_initialization_seals'):
    for _action in ('UPDATE', 'DELETE'):
        SCHEMA += f"CREATE TRIGGER {_table}_no_{_action.lower()} BEFORE {_action} ON {_table} BEGIN SELECT RAISE(ABORT,'immutable initialization'); END;\n"
for _table in ('management_legacy_rows','management_openings','management_source_links'):
    SCHEMA += f"CREATE TRIGGER {_table}_no_append BEFORE INSERT ON {_table} WHEN EXISTS(SELECT 1 FROM management_initialization_seals WHERE initialization_id=NEW.initialization_id) BEGIN SELECT RAISE(ABORT,'sealed initialization'); END;\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    checksum=hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):checksum.update(part)
    return checksum.hexdigest()


def checked(raw, checksum, limit):
    if not isinstance(raw, bytes) or len(raw)>limit or not re.fullmatch('[a-f0-9]{64}', checksum) or sha(raw)!=checksum:
        raise ValueError('Reviewed input checksum or size differs')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Repeated JSON fields')
            result[key]=value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))


def export_helper():
    spec=importlib.util.spec_from_file_location('initialization_read_only_export',Path(__file__).parents[1]/'ops/export-management-review.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def rows(db, table):
    # Names only come from sqlite_schema, never user interpolation.
    quoted='"'+table.replace('"','""')+'"'
    return sorted((dict(r) for r in db.execute('SELECT * FROM '+quoted)),key=lambda v:json.dumps(v,sort_keys=True,default=lambda b:{'blob':b.hex()}))


def fingerprints(db):
    result={}
    for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        values=rows(db,row[0])
        # Preserve opaque unknown BLOB columns without exposing their values.
        payload=json.dumps(values,sort_keys=True,separators=(',',':'),default=lambda v:{'blob':v.hex()}).encode()
        result[row[0]]=sha(payload)
    return result


def validate(plan, history, review):
    if (plan.get('format')!='future-school-management-initialization-plan-v1' or plan.get('schema_version')!=1
        or plan.get('proposal_only') is not True or plan.get('ready_for_import') is not False
        or plan.get('activation_authorized') is not False or plan.get('fresh_capture_required') is not True
        or plan.get('org_id')!=review.get('org_id') or plan.get('organization')!=review.get('organization')):
        raise ValueError('Explicit offline proposal required')
    if (history.get('format')!='future-school-legacy-history-archive-v1' or history.get('schema_version')!=1
        or history.get('replay_allowed') is not False or history.get('fresh_capture_authorization') is not False
        or history.get('read_only_history') is not True or history.get('provenance')!='offline_original_backup'
        or history.get('source_sha256')!=plan['baseline']['tablet_sha256']
        or history.get('manifest')!=plan['preserve_history']['manifest']):
        raise ValueError('Original sealed history differs')
    if (plan['baseline']['mirror_revision']!=review['mirror']['revision']
        or plan['baseline']['mirror_source_sha256']!=review['mirror']['source_sha256']
        or plan['baseline']['server_generated_at']!=review['generated_at']):
        raise ValueError('Reviewed mirror baseline differs')
    expected={'classes','students','attendance','transactions','leaves','extra_lessons','class_reschedules','student_reschedules'}
    if set(history['sections'])!=expected or set(history['manifest'])!=expected:
        raise ValueError('Complete history sections required')
    for key, values in history['sections'].items():
        if not isinstance(values,list) or len(values)>10000 or len(values)!=history['manifest'][key]['rows']:
            raise ValueError('History section count differs')
    def unique(values,key):
        result={r[key]:r for r in values}
        if len(result)!=len(values): raise ValueError('Repeated identity')
        return result
    students=unique(plan['students'],'server_id');unique(plan['students'],'local_id');unique(plan['students'],'external_ref')
    classes=unique(plan['classes'],'server_id');unique(plan['classes'],'local_id')
    original=unique(history['sections']['students'],'id')
    source_classes=unique(history['sections']['classes'],'id')
    old={r['id']:r for r in review['students']}; archived={r['id']:r for r in review['archived_students']}
    links={(r['kind'],str(r['source_id'])):r['entity_id'] for r in review['source_links']}
    remote_classes={r['id']:r for r in review['classes']}
    mirror={r['local_id']:r for r in review['mirror']['classes']}
    staff={r['id']:r for r in review['staff']}
    if len(students)!=len(original) or len(students)>2000 or not students or len(classes)>1000 or not classes:
        raise ValueError('Whole source identity scope required')
    for c in classes.values():
        m=mirror.get(str(c['local_id']));t=staff.get(c['teacher_id'])
        if (c['server_id'] not in remote_classes or remote_classes[c['server_id']]['name']!=c['name']
            or links.get(('class',str(c['local_id'])))!=c['server_id'] or not m or m['name']!=c['name']
            or m['teacher_ids']!=[c['teacher_id']] or not t or not t['active'] or 'teacher' not in t['roles']
            or t['display_name']!=c['teacher_name'] or m['teacher']!=c['teacher_name']):
            raise ValueError('Current class or teacher identity differs')
        source_class=source_classes.get(c['local_id'])
        if not source_class or source_class['name']!=c['name'] or source_class.get('teacher')!=c['teacher_name']:
            raise ValueError('Original class identity differs')
    for s in students.values():
        r=original.get(s['local_id']); prior=(old if s['existing_table']=='students' else archived).get(s['server_id'])
        if (not r or s['name']!=r['name'] or s['external_ref']!=r.get('studentNo') or s['automatic_enrollment_event'] is not False
            or s['status'] not in ('active','paused') or type(s['opening_balance_units']) is not int
            or not -1000000<=s['opening_balance_units']<=10000000): raise ValueError('Source student differs')
        if (type(r.get('hours')) not in (int,float) or not math.isfinite(r['hours'])
            or math.floor(r['hours']*100+0.5)!=s['opening_balance_units']
            or abs(r['hours']*100-s['opening_balance_units'])>=0.000001
            or (r.get('longTermAbsent') is True)!=(s['status']=='paused')):
            raise ValueError('Opening balance or paused status differs from original source')
        if s['identity_action']=='reuse_existing_id':
            if not prior or (prior['name'],prior['external_ref'])!=(s['name'],s['external_ref']) or links.get(('student',str(s['local_id'])))!=s['server_id']:
                raise ValueError('Existing identity must be reused exactly')
        elif (s['identity_action']!='reserve_new_base_id' or s['existing_table'] is not None
              or not re.fullmatch('[a-f0-9]{32}',s['server_id']) or s['server_id'] in old or s['server_id'] in archived
              or any(x['external_ref']==s['external_ref'] for x in [*old.values(),*archived.values()])
              or ('student',str(s['local_id'])) in links or s['status']!='active'):
            raise ValueError('New reserved identity conflicts or is not active')
        if s['status']=='active' and (s['current_class_id'] not in classes or classes[s['current_class_id']]['name']!=r.get('className') or s['existing_table']=='archived_students'):
            raise ValueError('Active class or archive reactivation requires explicit review')
        if s['status']=='paused' and s['current_class_id'] is not None: raise ValueError('Paused current class differs')
    active=[s for s in students.values() if s['status']=='active']
    openings=[dict(student_id=s['server_id'],class_id=s['current_class_id'],units=s['opening_balance_units'],revision=0) for s in active]
    if sorted(openings,key=encoded)!=sorted(plan['canonical_openings'],key=encoded): raise ValueError('Exact whole-school openings required')
    if set(plan['preserve_archive_ids'])!={s['server_id'] for s in students.values() if s['existing_table']=='archived_students'}:
        raise ValueError('Archive identity preservation differs')
    if set(plan['deactivate_without_moving_ids'])!={s['server_id'] for s in students.values() if s['existing_table']=='students' and s['status']=='paused'}:
        raise ValueError('Paused identity preservation differs')
    if {r['server_id'] for r in plan['preserve_server_only']}!=(set(old)|set(archived))-set(students): raise ValueError('Server-only identity preservation differs')
    if {r['server_id'] for r in plan['preserve_server_only_classes']}!=set(remote_classes)-set(classes): raise ValueError('Server-only class preservation differs')
    if plan['scope']!=dict(active_students=len(active),archived_students=len(students)-len(active),current_classes=len(classes)):
        raise ValueError('Scope summary differs')


def populate(db, plan_raw, plan_sha256, archive_raw, review_raw, now):
    """Caller owns a NEW isolated copy; all writes are one transaction."""
    plan=checked(plan_raw,plan_sha256,8*1024*1024)
    review=checked(review_raw,plan['baseline']['server_review_sha256'],32*1024*1024)
    archive=checked(archive_raw,plan['baseline']['history_archive_sha256'],10*1024*1024)
    validate(plan,archive,review)
    before=fingerprints(db)
    if any(t.startswith(('management_','assessment_')) for t in before): raise ValueError('Only a never-initialized copy is supported')
    original_students=rows(db,'students');original_grants=rows(db,'teacher_classes')
    org=plan['org_id'];key=sha(plan_raw)
    # All schemas and business changes roll back together, including the DDL.
    db.executescript('BEGIN IMMEDIATE;\n'+approvals.SCHEMA+assessments.SCHEMA+delivery.SCHEMA+uplink.SCHEMA+enrollment.SCHEMA+migration.SCHEMA+SCHEMA)
    try:
        expected_students={s['id']:dict(s) for s in original_students}
        expected_grants=[r for r in original_grants if r['org_id']!=org or r['class_id'] not in {c['server_id'] for c in plan['classes']}]
        for c in plan['classes']:
            db.execute('DELETE FROM teacher_classes WHERE org_id=? AND class_id=?',(org,c['server_id']))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)',(org,c['teacher_id'],c['server_id']))
            expected_grants.append(dict(org_id=org,user_id=c['teacher_id'],class_id=c['server_id']))
        for s in plan['students']:
            if s['existing_table']=='archived_students': continue
            if s['identity_action']=='reserve_new_base_id':
                db.execute('INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active) VALUES(?,?,?,?,?,?,1)',
                    (s['server_id'],org,s['current_class_id'],s['name'],s['external_ref'],s['opening_balance_units']))
                # Preserve schema defaults, but never accept trigger-altered
                # identity, ownership, status or opening balance as expected.
                inserted=dict(db.execute('SELECT * FROM students WHERE id=?',(s['server_id'],)).fetchone())
                intended=dict(id=s['server_id'],org_id=org,class_id=s['current_class_id'],name=s['name'],
                              external_ref=s['external_ref'],remaining_units=s['opening_balance_units'],active=1)
                if any(inserted[k]!=v for k,v in intended.items()): raise ValueError('Reserved student insert differs')
                expected_students[s['server_id']]=inserted
            elif s['status']=='active':
                db.execute('UPDATE students SET class_id=?,remaining_units=?,active=1 WHERE org_id=? AND id=?',
                    (s['current_class_id'],s['opening_balance_units'],org,s['server_id']))
                expected_students[s['server_id']].update(class_id=s['current_class_id'],remaining_units=s['opening_balance_units'],active=1)
            else:
                db.execute('UPDATE students SET active=0 WHERE org_id=? AND id=?',(org,s['server_id']))
                expected_students[s['server_id']]['active']=0
        receipt=dict(format='future-school-initialization-rehearsal-v1',rehearsal_only=True,activation_authorized=False,
            source_written=False,events_replayed=0,accounts_preserved=True,legacy_applications_preserved=True,
            scope=plan['scope'],history_counts={k:len(v) for k,v in archive['sections'].items()},
            reserved_id_rows=sum(s['identity_action']=='reserve_new_base_id' for s in plan['students']),
            teacher_grants_aligned=sorted(original_grants,key=encoded)!=sorted(expected_grants,key=encoded),
            plan_sha256=plan_sha256,archive_sha256=sha(archive_raw),before_table_hashes=before,
            canonical_from_day=next_day(now)[0],
            openings_sha256=sha(encoded(sorted(plan['canonical_openings'],key=lambda r:r['student_id'])).encode()))
        db.execute('INSERT INTO management_initializations VALUES(?,?,?,?,?,?,?,?,?,?)',
            (key,org,'rehearsal_only',plan_sha256,sha(review_raw),plan['baseline']['tablet_sha256'],sha(archive_raw),archive_raw.decode('utf-8'),encoded(receipt),now))
        for section,values in archive['sections'].items():
            db.executemany('INSERT INTO management_legacy_rows VALUES(?,?,?,?)',((key,section,i,encoded(r)) for i,r in enumerate(values)))
        for kind,values in (('class',plan['classes']),('student',plan['students'])):
            db.executemany('INSERT INTO management_source_links VALUES(?,?,?,?,?)',((key,org,kind,r['local_id'],r['server_id']) for r in values))
        for opening in plan['canonical_openings']:
            db.execute('INSERT INTO management_balances VALUES(?,?,?,0)',(org,opening['student_id'],opening['units']))
            db.execute('INSERT INTO management_openings VALUES(?,?,?,?,?,0)',(key,org,opening['student_id'],opening['class_id'],opening['units']))
        db.execute('INSERT INTO management_initialization_seals VALUES(?,?,?)',(key,sha(encoded(receipt).encode()),now))
        # Compare sets through canonical sorting independent of SQL row order.
        if sorted(rows(db,'students'),key=encoded)!=sorted(expected_students.values(),key=encoded) or sorted(rows(db,'teacher_classes'),key=encoded)!=sorted(expected_grants,key=encoded):
            raise ValueError('Reviewed roster changes differ or a trigger changed other rows')
        after=fingerprints(db)
        if any(after[t]!=h for t,h in before.items() if t not in {'students','teacher_classes'}):
            raise ValueError('An account, legacy application or other existing table changed')
        expected_counts={'management_initializations':1,'management_legacy_rows':sum(len(v) for v in archive['sections'].values()),
                         'management_openings':len(plan['canonical_openings']),'management_balances':len(plan['canonical_openings']),
                         'management_source_links':len(plan['students'])+len(plan['classes']),'management_initialization_seals':1}
        for table in set(after)-set(before):
            if db.execute('SELECT COUNT(*) FROM "'+table.replace('"','""')+'"').fetchone()[0]!=expected_counts.get(table,0):
                raise ValueError('Unexpected event, device grant or new candidate rows')
        balances=[dict(org_id=org,student_id=o['student_id'],units=o['units'],revision=0) for o in plan['canonical_openings']]
        if sorted(rows(db,'management_balances'),key=encoded)!=sorted(balances,key=encoded): raise ValueError('Opening balances differ after initialization')
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Copy integrity differs')
        db.commit()
        return receipt
    except BaseException:
        db.rollback();raise


def rehearse(source, output_dir, *, plan_raw, plan_sha256, archive_raw, review_raw, now=None):
    """Linux private-path CLI boundary. Copies full DB; never reconstructs credentials."""
    source=Path(source);output_dir=Path(output_dir);helper=export_helper()
    helper.private_path(source.parent,True);helper.private_path(source);helper.private_path(output_dir.parent,True)
    if output_dir.exists() or output_dir.is_symlink() or not output_dir.is_absolute() or any(p.is_symlink() for p in output_dir.parents):
        raise ValueError('A new absolute private output directory is required')
    if any((p/'.git').exists() for p in (output_dir,*output_dir.parents)): raise ValueError('Private database must stay outside Git')
    if source.stat().st_size>512*1024*1024: raise ValueError('Source database exceeds rehearsal limit')
    plan=checked(plan_raw,plan_sha256,8*1024*1024)
    expected=checked(review_raw,plan['baseline']['server_review_sha256'],32*1024*1024)
    output_dir.mkdir(mode=0o700)
    target=output_dir/'rehearsal.sqlite';started=time.monotonic()
    try:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as src,closing(sqlite3.connect(target)) as dst:
            src.execute('PRAGMA query_only=ON')
            def progress(*_):
                if time.monotonic()-started>30: raise TimeoutError('Copy deadline exceeded')
            src.backup(dst,pages=256,progress=progress)
        actual=helper.snapshot(target,plan['organization'])
        if {k:v for k,v in actual.items() if k!='generated_at'}!={k:v for k,v in expected.items() if k!='generated_at'}:
            raise ValueError('Current copied database differs from reviewed source; no initialization performed')
        with closing(sqlite3.connect(target,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            receipt=populate(db,plan_raw,plan_sha256,archive_raw,review_raw,int(time.time()) if now is None else now)
        receipt['copy_sha256']=file_sha(target)
        fd=os.open(output_dir/'receipt.private.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(receipt,f,ensure_ascii=True,sort_keys=True,indent=2)
        return receipt
    except BaseException:
        # Delete only artifacts created by this invocation, never an old directory.
        for name in ('rehearsal.sqlite','rehearsal.sqlite-journal','rehearsal.sqlite-wal','rehearsal.sqlite-shm','receipt.private.json'):
            (output_dir/name).unlink(missing_ok=True)
        output_dir.rmdir();raise
