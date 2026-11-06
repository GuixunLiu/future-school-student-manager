"""Sealed original history vs future canonical facts. No replay or activation."""
from datetime import datetime, timedelta, time
import hashlib
import json

from business.store import Problem
from .approvals import digest, encoded
from .deadlines import TZ


def next_day(now):
    date=datetime.fromtimestamp(now,TZ).date()+timedelta(days=1)
    return date.isoformat(),int(datetime.combine(date,time(),TZ).timestamp())


def refuse():
    raise Problem(409,'history_boundary_differs','旧历史、期初或切换边界不一致，请保留资料重新核对')


def load(db,org):
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_initializations'").fetchone():return None
    markers=list(db.execute('SELECT * FROM management_initializations WHERE org_id=?',(org,)))
    if len(markers)!=1:refuse()
    marker=markers[0];receipt=json.loads(marker['receipt_json']);archive=json.loads(marker['archive_json'])
    seal=db.execute('SELECT * FROM management_initialization_seals WHERE initialization_id=?',(marker['id'],)).fetchone()
    if (not seal or seal['receipt_sha256']!=hashlib.sha256(marker['receipt_json'].encode()).hexdigest()
        or hashlib.sha256(marker['archive_json'].encode()).hexdigest()!=marker['archive_sha256']
        or archive['source_sha256']!=marker['tablet_sha256'] or receipt.get('events_replayed')!=0
        or marker['phase']!='rehearsal_only' or receipt.get('canonical_from_day')!=next_day(marker['created_at'])[0]):refuse()
    day,starts_at=next_day(marker['created_at'])
    # A whole local day is one boundary; no lesson is split around a check-in.
    # Future leave/arrangement carry-over needs a separate explicit admission.
    for section in ('attendance','transactions','leaves'):
        for row in archive['sections'][section]:
            date=row.get('date')
            if not isinstance(date,str) or len(date)!=10 or date>=day:refuse()
            try:datetime.strptime(date,'%Y-%m-%d')
            except ValueError:refuse()
    for section,values in archive['sections'].items():
        stored=list(db.execute('SELECT ordinal,payload FROM management_legacy_rows WHERE initialization_id=? AND section=? ORDER BY ordinal',(marker['id'],section)))
        if len(stored)!=len(values) or any(row['ordinal']!=i or json.loads(row['payload'])!=values[i] for i,row in enumerate(stored)):refuse()
    if db.execute('SELECT COUNT(*) FROM management_legacy_rows WHERE initialization_id=?',(marker['id'],)).fetchone()[0]!=sum(len(v) for v in archive['sections'].values()):refuse()
    openings=[dict(r) for r in db.execute('SELECT student_id,class_id,units,revision FROM management_openings WHERE org_id=? ORDER BY student_id',(org,))]
    if not openings or digest(openings)!=receipt.get('openings_sha256'):refuse()
    result=dict(format='future-school-history-boundary-v1',org_id=org,initialization_id=marker['id'],
                baseline_sha256=marker['tablet_sha256'],archive_sha256=marker['archive_sha256'],
                canonical_from_day=day,canonical_from=starts_at,openings_sha256=digest(openings),
                rehearsal_only=True,activation_authorized=False)
    return result|dict(boundary_sha256=digest(result))


def validate_review(db,org,baseline,captured_at,now,source):
    boundary=load(db,org)
    if boundary is None:return None
    if baseline!=boundary['baseline_sha256'] or captured_at>=boundary['canonical_from'] or now>=boundary['canonical_from']:refuse()
    openings={r['student_id']:r for r in db.execute('SELECT * FROM management_openings WHERE org_id=?',(org,))}
    source_students=source['students'];source_classes=source['classes']
    if {r['server_id'] for r in source_students}!=set(openings):refuse()
    links={(r['kind'],r['local_id']):r['server_id'] for r in db.execute('SELECT * FROM management_source_links WHERE org_id=?',(org,))}
    for kind,values in (('student',source_students),('class',source_classes)):
        if any(links.get((kind,r['local_id']))!=r['server_id'] for r in values):refuse()
    if any(s['class_id']!=openings[s['server_id']]['class_id'] or s['balance_units']!=openings[s['server_id']]['units'] for s in source_students):refuse()
    # Existing facts must still be compared in full by enrollment.py.
    if any(type(f['starts_at'])is not int or f['starts_at']<boundary['canonical_from'] for f in source['facts']):refuse()
    if db.execute('SELECT 1 FROM management_attendance WHERE org_id=? AND starts_at<? LIMIT 1',(org,boundary['canonical_from'])).fetchone():refuse()
    if db.execute('SELECT 1 FROM management_balances WHERE org_id=? AND revision!=0 LIMIT 1',(org,)).fetchone():refuse()
    return boundary
