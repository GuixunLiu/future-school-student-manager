'use strict';
// A bounded FUTURE-only projection using the same calendar as the daily App.
// No database, native preferences, grants or balance changes.
const crypto=require('node:crypto'),{build:project}=require('./build-teacher-web-data.cjs');
const {digest}=require('./pack-enrollment-review.cjs');
const sha=v=>crypto.createHash('sha256').update(v).digest('hex');
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
function build(raw,plan,boundary,{now,days=14}={}){
 if(typeof raw!=='string'||Buffer.byteLength(raw)>10*1024*1024||!Number.isSafeInteger(now)||!Number.isInteger(days)||days<1||days>14)fail('schedule_input_invalid');
 const hash=sha(raw),state=JSON.parse(raw);
 if(plan?.format!=='future-school-management-initialization-plan-v1'||plan.baseline?.tablet_sha256!==hash
  ||boundary?.format!=='future-school-history-boundary-v1'||boundary.org_id!==plan.org_id||boundary.baseline_sha256!==hash
  ||boundary.canonical_from!==Date.parse(day(now+86400)+'T00:00:00+08:00')/1000)fail('schedule_boundary_differs');
 const {boundary_sha256,...header}=boundary;if(digest(header)!==boundary_sha256)fail('schedule_boundary_differs');
 const classes=plan.classes.map(c=>({...c})),byClass=new Map(classes.map(c=>[String(c.local_id),c]));
 if(byClass.size!==classes.length)fail('schedule_identity_differs');
 const students=plan.students.filter(s=>s.status==='active').map(s=>{
  const c=classes.find(c=>c.server_id===s.current_class_id),local=state.students.find(v=>v.id===s.local_id);
  if(!c||!local||local.name!==s.name||local.studentNo!==s.external_ref||local.className!==c.name||local.longTermAbsent===true
   ||!Number.isSafeInteger(s.opening_balance_units)||Math.abs(local.hours*100-s.opening_balance_units)>0.000001)fail('schedule_identity_differs');
  return{server_id:s.server_id,local_id:s.local_id,external_ref:s.external_ref,name:s.name,class_id:c.server_id,class_local_id:c.local_id,balance_units:s.opening_balance_units};
 });
 const active=new Map(students.map(s=>[String(s.local_id),s]));
 if(active.size!==students.length||students.length!==state.students.filter(s=>s.longTermAbsent!==true).length)fail('schedule_identity_differs');
 for(const c of classes){const local=state.classes.find(v=>v.id===c.local_id);if(!local||local.name!==c.name||local.teacher!==c.teacher_name)fail('schedule_identity_differs');}
 // The report engine deliberately treats future sessions as forecasts. Check
 // original facts first so an entered future arrival/leave cannot disappear.
 if(state.attendance.some(r=>r.date>=boundary.canonical_from_day)
  ||(state.settings?.studentLeaves||[]).some(r=>r.status==='approved'&&r.date>=boundary.canonical_from_day))fail('schedule_student_requires_review');
 const data=project(state,{asOfDate:day(now),sourceSha256:hash,sourceCapturedAt:new Date(now*1000).toISOString(),mirror:true});
 const until=boundary.canonical_from+days*86400,facts=[],lessons=[],seen=new Set(),studentDays=new Set();
 for(const l of data.lessons){
  if(l.date<boundary.canonical_from_day||l.date>=day(until))continue;
  const c=byClass.get(l.class_local_id);
  if(!c||c.name!==l.class_name||c.teacher_name!==l.teacher||l.needs_review||!/^\d{2}:\d{2}$/.test(l.start_time)||!Number.isInteger(l.expected))fail('schedule_requires_review');
  const starts=Date.parse(l.date+'T'+l.start_time+':00+08:00')/1000;
  if(!Number.isSafeInteger(starts)||starts<boundary.canonical_from||starts>=until)fail('schedule_requires_review');
  const id=sha(plan.org_id+':'+c.server_id+':'+l.date).slice(0,32),members=[];
  if(seen.has(id))fail('schedule_lesson_ambiguous');seen.add(id);
  for(const m of l.members){
   if(!m.expected)continue;
   const s=active.get(m.student_local_id),key=s?.server_id+':'+l.date;
   if(!s||s.class_id!==c.server_id||s.name!==m.name||s.external_ref!==m.external_ref||m.status!=='missing'||studentDays.has(key))fail('schedule_student_requires_review');
   studentDays.add(key);members.push(s.server_id);
   facts.push({lesson_id:id,student_id:s.server_id,status:'missing',expected:true,charge_units:100,starts_at:starts});
  }
  if(members.length!==l.expected)fail('schedule_membership_differs');
  lessons.push({lesson_id:id,class_id:c.server_id,date:l.date,starts_at:starts,start_time:l.start_time,end_time:l.end_time,student_ids:members.sort()});
 }
 if(facts.length>10000)fail('schedule_scope_limit');
 facts.sort((a,b)=>a.lesson_id.localeCompare(b.lesson_id)||a.student_id.localeCompare(b.student_id));lessons.sort((a,b)=>a.starts_at-b.starts_at||a.lesson_id.localeCompare(b.lesson_id));
 // Empty classes stay in the original state, sealed history and lesson
 // metadata. Device enrollment covers only classes with canonical students.
 const occupied=new Set(students.map(s=>s.class_id));
 const source={classes:classes.filter(c=>occupied.has(c.server_id)),students,facts};
 return{format:'future-school-canonical-schedule-v1',org_id:plan.org_id,baseline_sha256:hash,initialization_id:boundary.initialization_id,boundary_sha256,
  canonical_from:boundary.canonical_from,until,source,source_sha256:digest(source),lessons,
  summary:{students:students.length,classes:classes.length,lessons:lessons.length,expected:facts.length,days,charge_units_per_lesson:100},
  offline_only:true,balances_changed:false,history_replayed:false};
}
module.exports={build};
