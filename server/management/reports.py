"""Read-only historical/current attendance reports across the sealed boundary."""
from datetime import date, datetime, time, timedelta
import json
import copy
import hashlib

from business.store import Problem
from .deadlines import TZ
from .history_boundary import load as history_boundary
from .policy import workspace, require


def summary(name,expected,arrived,leave):
    return dict(name=name,expected=expected,arrived=arrived,leave_count=leave,
                rate=(str(round(arrived/expected*100,1)).removesuffix('.0')+'%') if expected else '0%')


def aggregate(rows):
    groups={'classes':{},'teachers':{}}
    expected=arrived=leave=0
    for r in rows:
        expected+=r['expected'];arrived+=r['arrived'];leave+=r['leave_count']
        for kind,key,identity in (('classes','class_name','class_id'),('teachers','teacher_name','teacher_id')):
            _,counts=groups[kind].setdefault(r.get(identity,r[key]),(r[key],[0,0,0]))
            for i,k in enumerate(('expected','arrived','leave_count')):counts[i]+=r[k]
    return summary('全部',expected,arrived,leave)|{kind:[summary(name,*values) for _,(name,values) in sorted(items.items())] for kind,items in groups.items()}


def effective_history(db,org,data,source_sha,first,last,cut,links,own=None):
    """Read-only effective lessons: immutable originals plus approved amendments."""
    originals=[l for l in data.get('lessons',[]) if first<=l['date']<=last and l['date']<cut and (own is None or links.get(str(l['class_local_id'])) in own)]
    lessons=copy.deepcopy(originals);bykey={l['key']:l for l in lessons};changes=[]
    confirmations={}
    for row in db.execute("SELECT entity_id,detail FROM management_audit WHERE org_id=? AND action='historical_temporary_makeup_confirmed' ORDER BY seq LIMIT 1001",(org,)):
        if len(confirmations)>=1000:raise Problem(503,'history_review_limit','历史核对记录较多，请分段核对')
        detail=json.loads(row['detail'])
        if detail.get('format')=='future-school-temporary-makeup-review-v1' and detail.get('source_sha256')==source_sha:confirmations[row['entity_id']]=detail
    for l in lessons:
        members=l.get('members',[]);unexpected={str(m['student_local_id']) for m in members if m.get('status')=='arrived' and m.get('expected')is not True}
        l['_source_review_explained']=bool(unexpected or l['arrived']>l['expected'])
        review=confirmations.get('legacy:'+l['key'])
        if review:
            ids=review.get('expected_student_ids',[])
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<=unexpected:raise Problem(503,'history_review_differs','临时补课核对记录与原签到不一致')
            for m in members:
                if str(m['student_local_id']) in ids:m['expected']=True
            l['expected']+=len(ids);l['temporary_makeup_confirmed']=True
    from .historical import enabled as history_enabled
    if history_enabled(db):
        seen=set()
        starts=int(datetime.combine(date.fromisoformat(first),time(),TZ).timestamp())
        ends=int(datetime.combine(date.fromisoformat(last)+timedelta(days=1),time(),TZ).timestamp())
        for r in db.execute('''SELECT a.lesson_id,a.student_id,a.status,h.context_json,h.source_sha256,r.decided_at,s.name AS student_name FROM management_historical_facts h
          JOIN management_attendance a ON a.org_id=h.org_id AND a.lesson_id=h.lesson_id AND a.student_id=h.student_id
          JOIN management_requests r ON r.org_id=a.org_id AND r.lesson_id=a.lesson_id AND r.student_id=a.student_id AND r.status='approved'
          JOIN students s ON s.org_id=a.org_id AND s.id=a.student_id WHERE h.org_id=? AND a.starts_at>=? AND a.starts_at<? AND a.status IN ('arrived','leave') ORDER BY r.decided_at,r.id LIMIT 10001''',(org,starts,ends)):
            key=r['lesson_id'][7:];l=bykey.get(key)
            if not l:continue
            identity=(r['lesson_id'],r['student_id'])
            if identity in seen:continue
            seen.add(identity)
            if len(seen)>10000:raise Problem(503,'history_amendment_limit','历史更正记录较多，请分段核对')
            if r['source_sha256']!=source_sha:raise Problem(503,'history_source_changed','历史更正来源已变化，需要核对原始资料')
            c=json.loads(r['context_json']);found=[m for m in l.get('members',[]) if str(m.get('student_local_id'))==str(c['local_student_id'])]
            if len(found)!=1 or found[0].get('expected')is not True or found[0].get('status')!='missing':raise Problem(503,'history_amendment_differs','批准记录与原缺签名单不一致')
            found[0]['status']=r['status'];l['arrived' if r['status']=='arrived' else 'leave_count']+=1
            l['has_historical_corrections']=True
            changes.append(dict(class_name=c['class_name'],teacher_name=c['teacher_name'],student_name=r['student_name'],date=c['date'],kind='makeup' if r['status']=='arrived' else 'leave',approved_at=r['decided_at']))
    issues=[]
    for l in lessons:
        unexpected=[m for m in l.get('members',[]) if m.get('status')=='arrived' and m.get('expected')is not True]
        l['needs_review']=bool(unexpected or l['arrived']>l['expected'] or l.get('needs_review') and not l.pop('_source_review_explained'))
        l.pop('_source_review_explained',None)
        if l['needs_review']:issues.append(dict(lesson_id='legacy:'+l['key'],date=l['date'],class_name=l['class_name'],teacher_name=l['teacher'],reason='有签到学生未列入该次应到名单，请核对临时补课或调课' if unexpected else '原课次应到与实到或排课资料需要核对'))
    return originals,lessons,changes,issues


def corrected_saved(saved,originals,lessons):
    report=copy.deepcopy(saved);changed=False
    def apply(group,before,after):
        nonlocal changed
        delta={k:sum(l[k] for l in after)-sum(l[k] for l in before) for k in ('expected','arrived','leave_count')}
        if not any(delta.values()):return
        changed=True
        covered=all(abs(sum(l[k] for l in before)-group[k])<1e-7 for k in delta)
        for k,value in delta.items():group[k]+=value
        group['needs_review']=bool(group.get('needs_review') and not covered or any(l.get('needs_review') for l in after) or group['arrived']>group['expected'])
        group['rate']='待核对' if group['needs_review'] else summary(group['name'],group['expected'],group['arrived'],group['leave_count'])['rate']
    apply(report,originals,lessons)
    for key,field in [('classes','class_name'),('teachers','teacher')]:
        for group in report[key]:apply(group,[l for l in originals if l[field]==group['name']],[l for l in lessons if l[field]==group['name']])
    return report,changed


def aggregate_history(lessons):
    report=aggregate([dict(l,teacher_name=l['teacher']) for l in lessons])
    for group,scope in [(report,lessons)]+[(g,[l for l in lessons if l[field]==g['name']]) for key,field in [('classes','class_name'),('teachers','teacher')] for g in report[key]]:
        group['needs_review']=any(l.get('needs_review') for l in scope)
        if group['needs_review']:group['rate']='待核对'
    return report


class ReportOperations:
    def attendance_day(self,token,role,*,on,limit=100,before=None,lesson_id=None,status='all',q=''):
        try:
            day=date.fromisoformat(on)
            if day.isoformat()!=on:raise ValueError()
            starts=int(datetime.combine(day,time(),TZ).timestamp());ends=int(datetime.combine(day+timedelta(days=1),time(),TZ).timestamp())
        except (TypeError,ValueError,OverflowError):raise Problem(422,'invalid_date','上课日期不正确')
        if (type(limit)is not int or not 1<=limit<=100 or before is not None and (type(before)is not int or before<1)
            or status not in {'all','arrived','missing','leave'} or not isinstance(q,str) or len(q)>80
            or lesson_id is not None and (not isinstance(lesson_id,str) or not 1<=len(lesson_id)<=200)):
            raise Problem(422,'invalid_filter','课堂筛选条件不正确')
        q=q.strip()
        def run(db,actor):
            selected=workspace(actor,role)
            if selected not in {'admin','teacher','principal'}:raise Problem(403,'permission_denied','此身份请在审批列表查看管辖范围申请')
            boundary=history_boundary(db,actor['org_id'])
            balance_adjustments=bool(db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='management_balance_adjustments'").fetchone())
            # Without a sealed boundary, candidate fixtures continue to use the
            # canonical facts. A live admitted copy always has its boundary.
            legacy=bool(boundary and on<boundary['canonical_from_day'])
            result=dict(on=on,source='legacy' if legacy else 'canonical',read_only=legacy,
                        server_time=self.clock(),balances_changed=False,lessons=[],items=[],next_before=None)
            if not legacy:
                clauses=['a.org_id=?','a.starts_at>=?','a.starts_at<?'];params=[actor['org_id'],starts,ends]
                if selected=='teacher':
                    clauses.append('EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=a.org_id AND t.class_id=a.class_id AND t.user_id=?)');params.append(actor['id'])
                where=' AND '.join(clauses)
                groups=db.execute('''SELECT a.lesson_id,a.class_id,c.name AS class_name,c.course_name,a.starts_at,
                    SUM(a.expected) AS expected,SUM(a.status='arrived') AS arrived,SUM(a.status='leave') AS leave_count,
                    SUM(a.expected=1 AND a.status='missing') AS missing
                    FROM management_attendance a JOIN classes c ON c.org_id=a.org_id AND c.id=a.class_id WHERE '''+where+
                    ' GROUP BY a.lesson_id,a.class_id,c.name,c.course_name,a.starts_at ORDER BY a.starts_at,c.name,a.lesson_id',params).fetchall()
                if len(groups)>1000:raise Problem(503,'attendance_scope_exceeded','当日课次较多，请分段核对')
                result['lessons']=[dict(r,read_only=False,source='canonical') for r in groups]
                if lesson_id is not None:clauses.append('a.lesson_id=?');params.append(lesson_id)
                if status!='all':clauses.append('a.status=?');params.append(status)
                if q:clauses.append('(instr(s.name,?)>0 OR instr(s.external_ref,?)>0)');params.extend([q,q])
                if before is not None:clauses.append('a.rowid<?');params.append(before)
                rows=db.execute('''SELECT a.*,a.rowid AS cursor,s.name AS student_name,s.external_ref AS student_no,c.name AS class_name,
                    b.units AS balance_units,b.revision AS balance_revision,EXISTS(SELECT 1 FROM management_requests r WHERE r.org_id=a.org_id AND r.lesson_id=a.lesson_id
                    AND r.student_id=a.student_id AND r.status='pending') AS has_pending_request
                    FROM management_attendance a JOIN students s ON s.org_id=a.org_id AND s.id=a.student_id
                    JOIN classes c ON c.org_id=a.org_id AND c.id=a.class_id JOIN management_balances b ON b.org_id=a.org_id AND b.student_id=a.student_id
                    WHERE '''+' AND '.join(clauses)+' ORDER BY a.rowid DESC LIMIT ?',(*params,limit+1)).fetchall()
                result['items']=[dict(r,source='canonical',read_only=False,
                    can_adjust_balance=balance_adjustments and selected in {'admin','teacher'}) for r in rows[:limit]]
                result['next_before']=result['items'][-1]['cursor'] if len(rows)>limit else None
                return result
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if 'web_dataset' not in tables:return result
            saved=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone()
            if not saved:return result
            if len(saved[0].encode())>16*1024*1024:raise Problem(503,'attendance_scope_exceeded','原签到资料较多，需要分段核对')
            data=json.loads(saved[0]);lessons=data.get('lessons',[])
            if not isinstance(lessons,list) or len(lessons)>30000:raise Problem(503,'attendance_scope_exceeded','原课次范围需要核对')
            links={str(r['local_id']):r['server_id'] for r in db.execute("SELECT local_id,server_id FROM management_source_links WHERE org_id=? AND kind='class'",(actor['org_id'],))} if 'management_source_links' in tables else {}
            if not links and 'web_class_links' in tables:links={str(r[0]):r[1] for r in db.execute('SELECT local_id,remote_id FROM web_class_links')}
            own={r[0] for r in db.execute('SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?',(actor['org_id'],actor['id']))} if selected=='teacher' else None
            _,lessons,_,_=effective_history(db,actor['org_id'],data,hashlib.sha256(saved[0].encode()).hexdigest(),on,on,boundary['canonical_from_day'],links,own)
            items=[];seen=set();sources={}
            for lesson in lessons:
                if lesson.get('date')!=on:continue
                class_id=links.get(str(lesson.get('class_local_id','')))
                # Unknown/deleted class names are never used to guess permission.
                if own is not None and class_id not in own:continue
                key=lesson.get('key');members=lesson.get('members',[])
                if not isinstance(key,str) or len(key)>190 or key in seen or not isinstance(members,list) or len(members)>10000:
                    raise Problem(503,'attendance_scope_exceeded','原课堂明细需要核对')
                seen.add(key);lid='legacy:'+key
                starts_at=None
                try:starts_at=int(datetime.fromisoformat(on+'T'+lesson['start_time']).replace(tzinfo=TZ).timestamp())
                except (KeyError,TypeError,ValueError):pass
                result['lessons'].append(dict(lesson_id=lid,class_id=class_id,class_name=lesson.get('class_name','原班级'),
                    starts_at=starts_at,start_time=lesson.get('start_time',''),end_time=lesson.get('end_time',''),
                    teacher_name=lesson.get('teacher',''),expected=lesson.get('expected'),arrived=lesson.get('arrived'),
                    leave_count=lesson.get('leave_count'),missing=sum(m.get('expected') is True and m.get('status')=='missing' for m in members),
                    source='legacy',read_only=True,
                    **({'has_historical_corrections':True} if lesson.get('has_historical_corrections') else {})))
                for member in members:
                    cursor=len(items)+1
                    item=dict(cursor=cursor,lesson_id=lid,student_id=str(member.get('student_local_id','')),
                        student_name=member.get('name','原学生'),student_no=member.get('external_ref',''),class_id=class_id,
                        class_name=lesson.get('class_name','原班级'),starts_at=starts_at,
                        status=member.get('status') if member.get('status') in {'missing','leave','arrived'} else 'unknown',
                        expected=member.get('expected'),balance_units=member.get('remaining_units'),charge_units=None,
                        attendance_time=member.get('attendance_time',''),attendance_method=member.get('attendance_method',''),
                        makeup=member.get('makeup') is True,has_pending_request=False,source='legacy',read_only=True)
                    items.append(item)
                    sources[cursor]=lesson,member
                    if len(items)>10000:raise Problem(503,'attendance_scope_exceeded','当日原名单较多，需要分段核对')
            result['lessons'].sort(key=lambda r:(r['starts_at'] or 0,r['class_name'],r['lesson_id']))
            filtered=[r for r in reversed(items) if (before is None or r['cursor']<before) and
                (lesson_id is None or r['lesson_id']==lesson_id) and (status=='all' or r['status']==status) and
                (not q or q in r['student_name'] or q in r['student_no'])]
            result['items']=filtered[:limit];result['next_before']=filtered[limit-1]['cursor'] if len(filtered)>limit else None
            from .historical import annotate
            for item in result['items']:
                lesson,member=sources[item['cursor']]
                item.update(annotate(db,actor['org_id'],lesson,member,boundary))
            result['source_captured_at']=data.get('source_captured_at')
            return result
        return self.command(token,run,read_only=True)

    def legacy_requests(self,token,role,*,before=None,limit=50):
        if type(limit)is not int or not 1<=limit<=100 or before is not None and (type(before)is not int or before<1):raise Problem(422,'invalid_cursor','分页参数无效')
        def run(db,actor):
            selected=workspace(actor,role)
            if selected not in {'admin','teacher','assessor'}:raise Problem(403,'permission_denied','此身份不能查看原补签申请')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            if 'web_makeup_requests' not in tables:return dict(items=[],next_before=None,legacy_read_only=True,balances_changed=False)
            data={};saved=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone() if 'web_dataset' in tables else None
            if saved:data=json.loads(saved[0])
            lessons={r['key']:r for r in data.get('lessons',[])}
            links={str(r['local_id']):r['server_id'] for r in db.execute("SELECT local_id,server_id FROM management_source_links WHERE org_id=? AND kind='class'",(actor['org_id'],))} if 'management_source_links' in tables else {}
            if not links and 'web_class_links' in tables:links={r[0]:r[1] for r in db.execute('SELECT local_id,remote_id FROM web_class_links')}
            scope=self.scope(db,actor);allowed=scope.teacher_classes if selected=='teacher' else scope.approval_classes
            sql='SELECT rowid AS cursor,id,lesson_key,student_local_id,reason,status,created_at FROM web_makeup_requests WHERE org_id=?';params=[actor['org_id']]
            if before:sql+=' AND rowid<?';params.append(before)
            rows=db.execute(sql+' ORDER BY rowid DESC LIMIT 10001',params).fetchall()
            if len(rows)>10000:raise Problem(503,'legacy_scope_exceeded','原申请较多，需要分段核对')
            out=[]
            for r in rows:
                lesson=lessons.get(r['lesson_key']);class_id=links.get(str(lesson['class_local_id'])) if lesson else None
                if selected!='admin' and class_id not in allowed:continue
                student=next((m for m in lesson['members'] if str(m['student_local_id'])==str(r['student_local_id'])),None) if lesson else None
                item=dict(r,student_name=student['name'] if student else '待核对原学生',class_name=lesson['class_name'] if lesson else '待核对原课堂',date=lesson['date'] if lesson else '',requires_review=r['status']=='pending')
                from .legacy_approvals import describe
                item.update(describe(self,db,actor,selected,r));out.append(item)
                if len(out)>limit:break
            from .legacy_approvals import enabled as legacy_enabled
            return dict(items=out[:limit],next_before=out[limit-1]['cursor'] if len(out)>limit else None,legacy_read_only=not legacy_enabled(db),balances_changed=False)
        return self.command(token,run,read_only=True)

    def weekly_report(self,token,role,*,start=None):
        try:
            day=date.fromisoformat(start) if start else datetime.fromtimestamp(self.clock(),TZ).date()
            monday=day-timedelta(days=day.weekday())
            if start and start!=monday.isoformat():raise ValueError()
        except (ValueError,TypeError):raise Problem(422,'invalid_week','请选择周一开始的周次')
        sunday=monday+timedelta(days=6);starts=int(datetime.combine(monday,time(),TZ).timestamp());ends=starts+7*86400
        def run(db,actor):
            selected=workspace(actor,role)
            if selected!='teacher':require(actor,selected,'statistics.read')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            data={};row=db.execute('SELECT data_json FROM web_dataset WHERE id=1').fetchone() if 'web_dataset' in tables else None
            if row:
                data=json.loads(row[0])
                if len(data.get('weeks',[]))>260 or len(data.get('lessons',[]))>30000:raise Problem(503,'report_scope_exceeded','历史周报范围需要分段核对')
            boundary=history_boundary(db,actor['org_id'])
            # If no sealed transition exists, legacy data is read-only throughout.
            cut=boundary['canonical_from_day'] if boundary else ('9999-12-31' if data else '0001-01-01')
            own={r[0] for r in db.execute('SELECT class_id FROM teacher_classes WHERE org_id=? AND user_id=?',(actor['org_id'],actor['id']))} if selected=='teacher' else None
            links={str(r['local_id']):r['server_id'] for r in db.execute("SELECT local_id,server_id FROM management_source_links WHERE org_id=? AND kind='class'",(actor['org_id'],))} if 'management_source_links' in tables else {}
            if not links and 'web_class_links' in tables:links={r[0]:r[1] for r in db.execute('SELECT local_id,remote_id FROM web_class_links')}
            source_sha=hashlib.sha256(row[0].encode()).hexdigest() if row else ''
            original_lessons,effective_lessons,changes,review_issues=effective_history(db,actor['org_id'],data,source_sha,monday.isoformat(),sunday.isoformat(),cut,links,own)
            saved=next((w for w in data.get('weeks',[]) if w['start']==monday.isoformat()),None)
            current_names={r[0] for r in db.execute('SELECT name FROM classes WHERE org_id=?',(actor['org_id'],))}
            sections=[]
            if monday.isoformat()<cut:
                if sunday.isoformat()<cut and selected!='teacher' and saved:
                    # Preserve exact old totals/fractions and deleted class labels.
                    report,adjusted=corrected_saved(saved,original_lessons,effective_lessons)
                    report['classes']=[dict(c,historical_class_name=c['name'] not in current_names) for c in report['classes']]
                    sections.append(dict(kind='legacy',label='历史周报 · 含已批准更正' if adjusted else '接入前历史周报',read_only=True,report=report,original_totals_preserved=not adjusted,original_snapshot_preserved=True,includes_approved_corrections=bool(changes),original_report=saved if adjusted else None,review_issues=review_issues))
                else:
                    legacy=[]
                    for l in effective_lessons:
                        if not monday.isoformat()<=l['date']<=sunday.isoformat() or l['date']>=cut:continue
                        if own is not None and links.get(str(l['class_local_id'])) not in own:continue
                        legacy.append(l)
                    if legacy:sections.append(dict(kind='legacy',label='历史课堂明细 · 含已批准更正' if changes else '接入前课堂明细汇总',read_only=True,report=aggregate_history(legacy),original_totals_preserved=False,detail_scope_only=True,includes_approved_corrections=bool(changes),review_issues=review_issues))
            if sunday.isoformat()>=cut:
                where='a.org_id=? AND a.starts_at>=? AND a.starts_at<?';params=[actor['org_id'],max(starts,boundary['canonical_from']) if boundary else starts,ends]
                if own is not None:
                    where+=' AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=a.org_id AND t.class_id=a.class_id AND t.user_id=?)';params.append(actor['id'])
                rows=[]
                for r in db.execute("SELECT a.lesson_id,a.class_id,c.name AS class_name,COUNT(*) AS expected,SUM(a.status='arrived') AS arrived,SUM(a.status='leave') AS leave_count FROM management_attendance a JOIN classes c ON c.org_id=a.org_id AND c.id=a.class_id WHERE "+where+' AND a.expected=1 GROUP BY a.lesson_id,a.class_id,c.name',params):
                    sealed=db.execute('SELECT teachers_json FROM management_lesson_teacher_snapshots WHERE org_id=? AND lesson_id=?',(actor['org_id'],r['lesson_id'])).fetchone() if 'management_lesson_teacher_snapshots' in tables else None
                    teachers=json.loads(sealed['teachers_json']) if sealed else list(db.execute('SELECT u.id,u.display_name FROM teacher_classes t JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id WHERE t.org_id=? AND t.class_id=? ORDER BY u.id',(actor['org_id'],r['class_id'])))
                    rows.append(dict(r,teacher_id=':'.join(t['id'] for t in teachers) or 'unassigned:'+r['class_id'],teacher_name='、'.join(t['display_name'] for t in teachers) or '未分配教师'))
                sections.append(dict(kind='canonical',label='网页接入后的实际课次',read_only=True,report=aggregate(rows),original_totals_preserved=False))
            if changes:
                sections.append(dict(kind='historical_corrections',label='已计入周报的更正明细',correction_only=True,included_in_report=True,makeup_count=sum(r['kind']=='makeup' for r in changes),leave_count=sum(r['kind']=='leave' for r in changes),items=changes,read_only=True))
            today=datetime.fromtimestamp(self.clock(),TZ).date();this_week=today-timedelta(days=today.weekday())
            weeks={w['start'] for w in data.get('weeks',[]) if w['start']<=this_week.isoformat()}
            weeks.add(this_week.isoformat())
            for r in db.execute('SELECT DISTINCT starts_at FROM management_attendance WHERE org_id=?',(actor['org_id'],)):
                d=datetime.fromtimestamp(r[0],TZ).date();w=d-timedelta(days=d.weekday())
                if w<=this_week:weeks.add(w.isoformat())
            return dict(workspace_role=selected,start=monday.isoformat(),end=sunday.isoformat(),weeks=sorted(weeks,reverse=True)[:260],sections=sections,
                        transition_week=monday.isoformat()<cut<=sunday.isoformat(),totals_merged=False,balances_changed=False,
                        source_captured_at=data.get('source_captured_at'),canonical_from_day=boundary['canonical_from_day'] if boundary else None)
        return self.command(token,run,read_only=True)
