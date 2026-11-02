"""Read-only historical/current attendance reports across the sealed boundary."""
from datetime import date, datetime, time, timedelta
import json

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


class ReportOperations:
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
                out.append(dict(r,student_name=student['name'] if student else '待核对原学生',class_name=lesson['class_name'] if lesson else '待核对原课堂',date=lesson['date'] if lesson else '',requires_review=r['status']=='pending'))
                if len(out)>limit:break
            return dict(items=out[:limit],next_before=out[limit-1]['cursor'] if len(out)>limit else None,legacy_read_only=True,balances_changed=False)
        return self.command(token,run)

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
            saved=next((w for w in data.get('weeks',[]) if w['start']==monday.isoformat()),None)
            current_names={r[0] for r in db.execute('SELECT name FROM classes WHERE org_id=?',(actor['org_id'],))}
            sections=[]
            if monday.isoformat()<cut:
                if sunday.isoformat()<cut and selected!='teacher' and saved:
                    # Preserve exact old totals/fractions and deleted class labels.
                    report=dict(saved,classes=[dict(c,historical_class_name=c['name'] not in current_names) for c in saved['classes']])
                    sections.append(dict(kind='legacy',label='接入前历史周报',read_only=True,report=report,original_totals_preserved=True))
                else:
                    legacy=[]
                    for l in data.get('lessons',[]):
                        if not monday.isoformat()<=l['date']<=sunday.isoformat() or l['date']>=cut:continue
                        if own is not None and links.get(str(l['class_local_id'])) not in own:continue
                        legacy.append(dict(class_name=l['class_name'],teacher_name=l['teacher'],expected=l['expected'],arrived=l['arrived'],leave_count=l['leave_count']))
                    if legacy:sections.append(dict(kind='legacy',label='接入前课堂明细汇总',read_only=True,report=aggregate(legacy),original_totals_preserved=False,detail_scope_only=True))
            if sunday.isoformat()>=cut:
                where='a.org_id=? AND a.starts_at>=? AND a.starts_at<?';params=[actor['org_id'],max(starts,boundary['canonical_from']) if boundary else starts,ends]
                if own is not None:
                    where+=' AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=a.org_id AND t.class_id=a.class_id AND t.user_id=?)';params.append(actor['id'])
                rows=[]
                for r in db.execute("SELECT a.class_id,c.name AS class_name,COUNT(*) AS expected,SUM(a.status='arrived') AS arrived,SUM(a.status='leave') AS leave_count FROM management_attendance a JOIN classes c ON c.org_id=a.org_id AND c.id=a.class_id WHERE "+where+' AND a.expected=1 GROUP BY a.class_id,c.name',params):
                    teachers=list(db.execute('SELECT u.id,u.display_name FROM teacher_classes t JOIN users u ON u.org_id=t.org_id AND u.id=t.user_id WHERE t.org_id=? AND t.class_id=? ORDER BY u.id',(actor['org_id'],r['class_id'])))
                    rows.append(dict(r,teacher_id=':'.join(t['id'] for t in teachers) or 'unassigned:'+r['class_id'],teacher_name='、'.join(t['display_name'] for t in teachers) or '未分配教师'))
                sections.append(dict(kind='canonical',label='网页接入后的实际课次',read_only=True,report=aggregate(rows),original_totals_preserved=False))
            today=datetime.fromtimestamp(self.clock(),TZ).date();this_week=today-timedelta(days=today.weekday())
            weeks={w['start'] for w in data.get('weeks',[]) if w['start']<=this_week.isoformat()}
            weeks.add(this_week.isoformat())
            for r in db.execute('SELECT DISTINCT starts_at FROM management_attendance WHERE org_id=?',(actor['org_id'],)):
                d=datetime.fromtimestamp(r[0],TZ).date();w=d-timedelta(days=d.weekday())
                if w<=this_week:weeks.add(w.isoformat())
            return dict(workspace_role=selected,start=monday.isoformat(),end=sunday.isoformat(),weeks=sorted(weeks,reverse=True)[:260],sections=sections,
                        transition_week=monday.isoformat()<cut<=sunday.isoformat(),totals_merged=False,balances_changed=False,
                        source_captured_at=data.get('source_captured_at'),canonical_from_day=boundary['canonical_from_day'] if boundary else None)
        return self.command(token,run)
