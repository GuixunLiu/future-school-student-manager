'use strict';
// Offline full-state bootstrap. No uploads, grants, activation or event replay.
const crypto=require('node:crypto'),{digest}=require('./pack-enrollment-review.cjs'),{review}=require('./tablet-review/core.js');
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
function build(raw,source,profile){
 if(typeof raw!=='string'||Buffer.byteLength(raw)>20*1024*1024||!profile||profile.baseline_sha256!==sha(raw))fail('bootstrap_baseline_differs');
 if(Object.keys(profile).sort().join('|')!=='baseline_sha256|device_id|generation|org_id|profile_id')fail('bootstrap_profile_invalid');
 for(const k of ['org_id','device_id','generation','profile_id'])if(typeof profile[k]!=='string'||!/^[a-f0-9]{32}$/.test(profile[k]))fail('bootstrap_profile_invalid');
 if(!source||!Array.isArray(source.students)||!Array.isArray(source.classes)||!Array.isArray(source.facts)||!source.history_boundary||source.history_boundary.baseline_sha256!==sha(raw)||source.history_boundary.org_id!==profile.org_id)fail('bootstrap_source_invalid');
 const checked=review(JSON.parse(raw));if(checked.issue_counts.error)fail('bootstrap_source_invalid');
 const state=JSON.parse(raw);if(state.managementSync?.enabled||state.deviceSync?.captureEnabled||(state.deviceSync?.queue||[]).some(r=>r.status!=='accepted'))fail('existing_writer_or_queue');
 const active=checked.students.filter(s=>s.status==='active');
 if(active.length!==source.students.length||source.students.length>2000||source.classes.length>1000||source.facts.length>10000)fail('whole_device_scope_required');
 const localStudents=new Map(state.students.map(s=>[s.id,s])),localClasses=new Map(state.classes.map(c=>[c.id,c]));
 const students={},classes=new Map(),refs=new Set(),localIds=new Set(),sync={protocol:1,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
  orgId:profile.org_id,deviceId:profile.device_id,generation:profile.generation,profileId:profile.profile_id,students,lessons:{},facts:{},queue:[],receipts:{},historyBoundarySha256:source.history_boundary.boundary_sha256};
 for(const c of source.classes){
  const local=localClasses.get(c.local_id);
  if(!local||classes.has(c.server_id)||local.name!==c.name||local.teacher!==c.teacher_name)fail('bootstrap_class_differs');
  classes.set(c.server_id,c);
 }
 for(const s of source.students){
  const local=localStudents.get(s.local_id),c=classes.get(s.class_id);
  if(!local||!c||students[s.server_id]||localIds.has(s.local_id)||refs.has(s.external_ref)||!active.some(a=>a.local_id===s.local_id)
   ||c.local_id!==s.class_local_id||local.name!==s.name||local.studentNo!==s.external_ref||local.className!==c.name||!Number.isSafeInteger(s.balance_units)
   ||Math.abs(local.hours*100-s.balance_units)>0.000001)fail('bootstrap_student_differs');
  localIds.add(s.local_id);refs.add(s.external_ref);
  students[s.server_id]={localId:s.local_id,localClassId:c.local_id,name:s.name,studentNo:s.external_ref,className:c.name,classId:c.server_id,teacher:c.teacher_name,balanceUnits:s.balance_units,balanceRevision:0};
 }
 for(const f of source.facts){
  const s=students[f.student_id];
  if(!s||!Number.isSafeInteger(f.starts_at)||f.starts_at<source.history_boundary.canonical_from||f.expected!==true||f.status!=='missing'||f.charge_units!==100)fail('bootstrap_future_fact_invalid');
  const lesson=sync.lessons[f.lesson_id]||(sync.lessons[f.lesson_id]={classId:s.classId,date:day(f.starts_at),startsAt:f.starts_at,studentIds:[]});
  if(lesson.classId!==s.classId||lesson.startsAt!==f.starts_at||lesson.studentIds.includes(f.student_id))fail('bootstrap_lesson_ambiguous');
  if(state.attendance.some(a=>a.studentId===s.localId&&a.date===lesson.date)||state.settings?.studentLeaves?.some(l=>l.studentId===s.localId&&l.date===lesson.date&&l.status==='approved'))fail('bootstrap_existing_fact');
  if(lesson.studentIds.some(id=>students[id].localId===s.localId))fail('bootstrap_lesson_ambiguous');
  for(const[otherId,other]of Object.entries(sync.lessons))if(otherId!==f.lesson_id&&other.date===lesson.date&&other.studentIds.includes(f.student_id))fail('bootstrap_lesson_day_ambiguous');
  lesson.studentIds.push(f.student_id);(sync.facts[f.lesson_id]||(sync.facts[f.lesson_id]={}))[f.student_id]={expected:true,status:'missing',revision:0,chargeUnits:100};
 }
 state.settings.studentLeaves=state.settings.studentLeaves||[];
 state.deviceSync={...state.deviceSync,captureEnabled:false};state.managementSync=sync;
 const snapshot=JSON.stringify(state);if(Buffer.byteLength(snapshot)>10*1024*1024)fail('bootstrap_size_limit');
 return{format:'future-school-canonical-bootstrap-v1',...profile,source_map_sha256:digest(source),bootstrap_sha256:sha(snapshot),snapshot_base64:Buffer.from(snapshot).toString('base64')};
}
module.exports={build};
