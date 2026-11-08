"""Bounded candidate-only staging for a complete enrollment review.

Pages are immutable evidence, never a partial business import. Finalization
uses the original exact reconciliation in the SAME transaction as its receipt.
"""
import json,re
from business.store import Problem,uid
from .approvals import digest,encoded,label
from .policy import workspace,require

LIMITS={'classes':1000,'students':2000,'facts':10000}
FIELDS={'classes':'server_id local_id name teacher_id teacher_name',
        'students':'server_id local_id external_ref name class_id class_local_id balance_units',
        'facts':'lesson_id student_id status expected charge_units starts_at'}
PAGE_BYTES=12288
SCHEMA='''
CREATE TABLE IF NOT EXISTS management_enrollment_batches (
 id TEXT PRIMARY KEY,org_id TEXT NOT NULL,actor_id TEXT NOT NULL,
 device_id TEXT NOT NULL,generation TEXT NOT NULL,baseline_sha256 TEXT NOT NULL,
 source_captured_at INTEGER NOT NULL,server_sha256 TEXT NOT NULL,manifest TEXT NOT NULL,
 command_ref TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('open','finalized')),
 review_id TEXT,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
 UNIQUE(org_id,actor_id,command_ref),
 FOREIGN KEY(org_id,actor_id) REFERENCES users(org_id,id),
 FOREIGN KEY(org_id,device_id) REFERENCES management_device_access(org_id,device_id),
 FOREIGN KEY(review_id) REFERENCES management_enrollment_reviews(id)
);
CREATE TABLE IF NOT EXISTS management_enrollment_pages (
 batch_id TEXT NOT NULL,section TEXT NOT NULL,page_index INTEGER NOT NULL,
 sha256 TEXT NOT NULL,rows_json TEXT NOT NULL,created_at INTEGER NOT NULL,
 PRIMARY KEY(batch_id,section,page_index),
 FOREIGN KEY(batch_id) REFERENCES management_enrollment_batches(id)
);
CREATE TRIGGER IF NOT EXISTS management_enrollment_page_no_update
 BEFORE UPDATE ON management_enrollment_pages BEGIN SELECT RAISE(ABORT,'immutable enrollment page'); END;
CREATE TRIGGER IF NOT EXISTS management_enrollment_page_no_delete
 BEFORE DELETE ON management_enrollment_pages BEGIN SELECT RAISE(ABORT,'immutable enrollment page'); END;
'''

def refuse(code='invalid_review_batch',status=422):
    raise Problem(status,code,'分段核对资料不完整或已经变化，请保留原资料重新核对')

def fingerprint(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):refuse()

def page_rows(section,rows):
    if section not in FIELDS or not isinstance(rows,list) or not 1<=len(rows)<=100:refuse()
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(FIELDS[section].split()):refuse()
        for k,v in row.items():
            if k in {'local_id','class_local_id'}:
                if type(v)is not int or not 0<v<=9007199254740991:refuse()
            elif k=='balance_units':
                if type(v)is not int or not -1000000<=v<=10000000:refuse()
            elif k in {'charge_units','starts_at'}:
                if type(v)is not int or not (0<v<=10000000 if k=='charge_units' else 0<=v<=253402185599):refuse()
            elif k=='expected':
                if type(v)is not bool:refuse()
            elif k=='status':
                if not isinstance(v,str) or v not in {'arrived','missing','leave'}:refuse()
            elif not isinstance(v,str) or not v or len(v)>(200 if k=='lesson_id' else 100) or any(ord(c)<32 for c in v):refuse()
    if len(encoded(rows).encode())>PAGE_BYTES:refuse('review_page_limit',413)

class SegmentedEnrollmentOperations:
    def _batch_access(self,db,actor,role,batch_id):
        selected=workspace(actor,role);require(actor,selected,'settings.manage')
        row=db.execute('SELECT * FROM management_enrollment_batches WHERE id=? AND org_id=? AND actor_id=?',
                       (batch_id,actor['org_id'],actor['id'])).fetchone()
        if not row:refuse('review_batch_missing',404)
        if row['expires_at']<=self.clock():refuse('review_batch_expired',409)
        device=db.execute('SELECT * FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',
                          (actor['org_id'],row['device_id'],self.clock())).fetchone()
        if not device:refuse('device_not_granted',403)
        self.access(db,dict(kind='device',org_id=actor['org_id'],id=row['device_id']),row['generation'])
        if row['server_sha256']!=self.enrollment_ledger_digest(db,actor['org_id'],row['device_id']):refuse('migration_baseline_changed',409)
        return row,selected

    def begin_enrollment_batch(self,token,role,*,device_id,generation,baseline_sha256,source_captured_at,manifest,command_ref):
        fingerprint(baseline_sha256);command_ref=label(command_ref,80)
        if not isinstance(manifest,dict) or set(manifest)!=set(LIMITS):refuse()
        for key,limit in LIMITS.items():
            part=manifest[key]
            if not isinstance(part,dict) or set(part)!={'rows','pages','sha256'}:refuse()
            if type(part['rows'])is not int or not (0<=part['rows']<=limit) or key!='facts' and not part['rows']:refuse()
            if type(part['pages'])is not int or not (0<=part['pages']<=500) or not (part['rows']==part['pages']==0 or 1<=part['pages']<=part['rows']<=100*part['pages']):refuse()
            fingerprint(part['sha256'])
            if not part['rows'] and part['sha256']!=digest([]):refuse()
        if sum(x['pages'] for x in manifest.values())>500:refuse('review_batch_limit',413)
        if not isinstance(generation,str) or not generation or len(generation)>80:refuse()
        if type(source_captured_at)is not int or source_captured_at<0 or source_captured_at>self.clock()+120 or self.clock()-source_captured_at>=900:refuse('source_not_fresh',409)
        content=dict(device_id=device_id,generation=generation,baseline_sha256=baseline_sha256,source_captured_at=source_captured_at,manifest=manifest)
        sha=digest(content)
        def run(db,actor):
            selected=workspace(actor,role);require(actor,selected,'settings.manage')
            old=db.execute('SELECT * FROM management_enrollment_batches WHERE org_id=? AND actor_id=? AND command_ref=?',
                           (actor['org_id'],actor['id'],command_ref)).fetchone()
            if old:
                if old['digest']!=sha:refuse('review_reference_reused',409)
                self._batch_access(db,actor,role,old['id'])
                return dict(batch_id=old['id'],expires_at=old['expires_at'],duplicate=True)
            device=db.execute('SELECT * FROM devices WHERE org_id=? AND id=? AND active=1 AND expires_at>?',
                              (actor['org_id'],device_id,self.clock())).fetchone()
            if not device:refuse('device_not_granted',403)
            self.access(db,dict(kind='device',org_id=actor['org_id'],id=device_id),generation)
            if db.execute("SELECT COUNT(*) FROM management_enrollment_batches WHERE org_id=? AND device_id=? AND state='open' AND expires_at>?",
                          (actor['org_id'],device_id,self.clock())).fetchone()[0]>=3:refuse('review_batch_limit',409)
            checksum=self.enrollment_ledger_digest(db,actor['org_id'],device_id)
            access=db.execute('SELECT expires_at FROM management_device_access WHERE org_id=? AND device_id=?',(actor['org_id'],device_id)).fetchone()
            key=uid();expires=min(self.clock()+900,source_captured_at+900,device['expires_at'],access['expires_at'])
            db.execute("INSERT INTO management_enrollment_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,'open',NULL,?,?)",
                       (key,actor['org_id'],actor['id'],device_id,generation,baseline_sha256,source_captured_at,checksum,encoded(manifest),command_ref,sha,self.clock(),expires))
            self.record_audit(db,actor,selected,'enrollment_batch_started',key,device_id=device_id,rows={k:v['rows'] for k,v in manifest.items()})
            return dict(batch_id=key,expires_at=expires,duplicate=False)
        return self.command(token,run)

    def put_enrollment_page(self,token,role,*,batch_id,section,page_index,rows,sha256):
        page_rows(section,rows);fingerprint(sha256)
        if type(page_index)is not int or page_index<0 or sha256!=digest(rows):refuse('review_page_digest',409)
        raw=encoded(rows)
        def run(db,actor):
            row,selected=self._batch_access(db,actor,role,batch_id);part=json.loads(row['manifest'])[section]
            if page_index>=part['pages'] or len(rows)>part['rows']:refuse()
            old=db.execute('SELECT * FROM management_enrollment_pages WHERE batch_id=? AND section=? AND page_index=?',(batch_id,section,page_index)).fetchone()
            if old:
                if old['sha256']!=sha256 or old['rows_json']!=raw:refuse('review_page_reused',409)
                return dict(batch_id=batch_id,section=section,page_index=page_index,duplicate=True)
            if row['state']!='open':refuse('review_batch_finalized',409)
            used=db.execute('SELECT rows_json FROM management_enrollment_pages WHERE batch_id=? AND section=?',(batch_id,section)).fetchall()
            if sum(len(json.loads(r[0])) for r in used)+len(rows)>part['rows']:refuse('review_row_limit',413)
            db.execute('INSERT INTO management_enrollment_pages VALUES(?,?,?,?,?,?)',(batch_id,section,page_index,sha256,raw,self.clock()))
            self.record_audit(db,actor,selected,'enrollment_page_received',batch_id,section=section,page_index=page_index,sha256=sha256,rows=len(rows))
            return dict(batch_id=batch_id,section=section,page_index=page_index,duplicate=False)
        return self.command(token,run)

    def finalize_enrollment_batch(self,token,role,*,batch_id):
        def run(db,actor):
            batch,selected=self._batch_access(db,actor,role,batch_id)
            if batch['state']=='finalized':
                old=db.execute('SELECT * FROM management_enrollment_reviews WHERE id=? AND org_id=?',(batch['review_id'],actor['org_id'])).fetchone()
                if not old or old['expires_at']<=self.clock():refuse('review_expired',409)
                source=json.loads(old['source_map'])
                return dict(review_id=old['id'],baseline_sha256=old['baseline_sha256'],server_sha256=old['server_sha256'],expires_at=old['expires_at'],
                            ready_for_enrollment=True,students=len(source['students']),classes=len(source['classes']),facts=len(source['facts']),duplicate=True)
            source={}
            for section,part in json.loads(batch['manifest']).items():
                pages=list(db.execute('SELECT * FROM management_enrollment_pages WHERE batch_id=? AND section=? ORDER BY page_index',(batch_id,section)))
                if len(pages)!=part['pages'] or [r['page_index'] for r in pages]!=list(range(part['pages'])):refuse('review_pages_missing',409)
                values=[]
                for page in pages:
                    rows=json.loads(page['rows_json'])
                    if digest(rows)!=page['sha256']:refuse('review_page_digest',409)
                    values.extend(rows)
                if len(values)!=part['rows'] or digest(values)!=part['sha256']:refuse('review_section_digest',409)
                source[section]=values
            result=self._record_enrollment_review(db,actor,role=role,device_id=batch['device_id'],generation=batch['generation'],
                baseline_sha256=batch['baseline_sha256'],source_captured_at=batch['source_captured_at'],source=source)
            db.execute("UPDATE management_enrollment_batches SET state='finalized',review_id=? WHERE id=?",(result['review_id'],batch_id))
            self.record_audit(db,actor,selected,'enrollment_batch_finalized',batch_id,review_id=result['review_id'])
            return result|dict(duplicate=False)
        return self.command(token,run)
