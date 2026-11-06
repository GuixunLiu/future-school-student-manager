'use strict';
// Planning only: accepts minimal roster previews. Never enables a device or creates lessons.
const {createHash}=require('node:crypto');
const digest=b=>createHash('sha256').update(b).digest('hex');
function date(s){if(typeof s!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(s))throw Error('Explicit school date required');const n=Date.parse(s+'T00:00:00Z');if(!Number.isFinite(n)||new Date(n).toISOString().slice(0,10)!==s)throw Error('Invalid school date');return n;}
function build(current,baseline,{classId,asOfDate,currentSha256,baselineSha256,teacherConfirmed=false}={}){
 const day=date(asOfDate);
 if(!Number.isSafeInteger(classId)||classId<1||![currentSha256,baselineSha256].every(s=>/^[a-f0-9]{64}$/.test(s||'')))throw Error('Explicit class and source hashes required');
 for(const b of [current,baseline]){
  if(b?.format!=='future-school-tablet-migration-preview'||b.schema_version!==1||b.complete_snapshot!==true||b.preview_only!==true||b.organization_target!==current.organization_target||!Array.isArray(b.students)||!Array.isArray(b.classes))throw Error('Complete minimal previews required');
  for(const [rows,key] of [[b.students,'external_ref'],[b.students,'source_local_id'],[b.classes,'class_local_id']]){if(new Set(rows.map(s=>s[key])).size!==rows.length)throw Error('Ambiguous identity');}
  if(b.students.some(s=>!['active','paused'].includes(s.status)||!Number.isSafeInteger(s.remaining_units)))throw Error('Invalid minimal student');
 }
 if(current.source.as_of_date!==asOfDate||date(baseline.source.as_of_date)>day)throw Error('Source dates differ');
 const c=current.classes.find(c=>c.class_local_id===classId),oldClass=baseline.classes.find(c=>c.class_local_id===classId);
 if(!c||!oldClass)throw Error('Selected class must have an existing imported source identity');
 if(!Number.isInteger(c.weekday)||c.weekday<1||c.weekday>7||![1,2].includes(c.interval_weeks)||!Number.isInteger(c.duration_minutes)||c.duration_minutes<1||c.duration_minutes>720||!/^([01]\d|2[0-3]):[0-5]\d$/.test(c.start_time))throw Error('Invalid explicit schedule');
 const anchor=c.interval_weeks===2?date(c.anchor_date):null;
 if(anchor!==null&&(new Date(anchor).getUTCDay()||7)!==c.weekday)throw Error('Anchor weekday differs');
 const members=current.students.filter(s=>s.status==='active'&&s.class_source_id===classId);
 if(!members.length||members.some(s=>s.current_class_name!==c.class_name))throw Error('Selected class has no unambiguous current members');
 const before=new Map(baseline.students.map(s=>[s.source_local_id,s])),now=new Map(current.students.map(s=>[s.source_local_id,s]));
 const fields=['external_ref','name','status','class_source_id','current_class_name','remaining_units'];
 const relevant=new Set([...members.map(s=>s.source_local_id),...baseline.students.filter(s=>s.status==='active'&&s.class_source_id===classId).map(s=>s.source_local_id)]);
 const changes=[...relevant].map(id=>{const a=before.get(id),b=now.get(id);return{source_local_id:id,external_ref:b?.external_ref||a?.external_ref,fields:!a?['new_identity']:!b?['missing_identity']:fields.filter(k=>a[k]!==b[k])};}).filter(x=>x.fields.length);
 const scheduleFields=['class_name','course_name','teacher_label','weekday','interval_weeks','anchor_date','start_time','duration_minutes','end_time','end_day_offset'];
 const classChanges=scheduleFields.filter(k=>c[k]!==oldClass[k]);
 const candidates=[];
 for(let n=1;n<=60&&candidates.length<4;n++){
  const t=day+n*86400000;
  if((new Date(t).getUTCDay()||7)!==c.weekday||anchor!==null&&(t<anchor||(t-anchor)%1209600000!==0))continue;
  const starts=t+Number(c.start_time.slice(0,2))*3600000+Number(c.start_time.slice(3))*60000;
  const local=ms=>new Date(ms).toISOString().slice(0,19)+'+08:00';
  candidates.push({school_date:new Date(t).toISOString().slice(0,10),starts_at:local(starts),ends_at:local(starts+c.duration_minutes*60000),confirmed:false,roster_confirmed:false});
 }
 const blockers=['FRESH_SERVER_CLASS_COMPARISON_REQUIRED','HTTPS_ENDPOINT_REQUIRED','ACTUAL_LESSON_AND_ROSTER_CONFIRMATION_REQUIRED','DEVICE_CONFIGURATION_AND_MAPPING_REQUIRED'];
 if(!teacherConfirmed)blockers.push('TEACHER_AUTHORIZATION_CONFIRMATION_REQUIRED');
 if(changes.length||classChanges.length)blockers.push('TABLET_CHANGES_SINCE_IMPORT_REVIEW_REQUIRED');
 if(current.blockers?.some(b=>b.code==='DEVICE_SYNC_STATE_REVIEW_REQUIRED'))blockers.push('EXISTING_TABLET_SYNC_STATE_REVIEW_REQUIRED');
 return{format:'future-school-single-class-pilot-preview',schema_version:1,organization:current.organization_target,as_of_date:asOfDate,
  source:{current_sha256:currentSha256,baseline_sha256:baselineSha256,tablet_backup_sha256:current.source.sha256},
  class:{...Object.fromEntries(scheduleFields.map(k=>[k,c[k]])),source_class_id:classId},
  members:members.map(s=>({source_local_id:s.source_local_id,external_ref:s.external_ref,name:s.name,remaining_units:s.remaining_units})),
  summary:{students:members.length,remaining_units:members.reduce((n,s)=>n+s.remaining_units,0),changed_students:changes.length,changed_class_fields:classChanges.length},
  changes,class_changes:classChanges,candidate_lessons:candidates,blockers,preview_only:true,ready_to_enable:false,
  side_effects:{server_written:false,tablet_modified:false,device_enabled:false,lessons_created:false,history_replayed:false,wechat_sent:false}};
}
function compareServer(plan,snapshot,{now=Date.now()}={}){
 const {validateSnapshot}=require('./tablet-review/core.js');validateSnapshot(snapshot);
 if(plan?.format!=='future-school-single-class-pilot-preview'||plan.preview_only!==true||plan.ready_to_enable!==false||snapshot.organization!==plan.organization||snapshot.source_access!=='read_only')throw Error('Explicit pilot and read-only server organization required');
 const generated=Date.parse(snapshot.generated_at);if(!Number.isFinite(generated)||Math.abs(now-generated)>300000)throw Error('Fresh server snapshot required');
 const c=snapshot.classes.find(c=>c.name===plan.class.class_name);if(!c)throw Error('Selected imported class missing on server');
 const classFields=[];if(c.course_name!==plan.class.course_name)classFields.push('course_name');
 if(c.teachers.length!==1||c.teachers[0].name!==plan.class.teacher_label)classFields.push('teacher_authorization');
 const byRef=new Map(snapshot.students.map(s=>[s.external_ref,s]));
 const comparisons=plan.members.map(s=>{const r=byRef.get(s.external_ref),fields=[];if(!r)fields.push('missing_server_student');else{if(r.class_id!==c.id)fields.push('class');if(r.name!==s.name)fields.push('name');if(r.remaining_units!==s.remaining_units)fields.push('balance');if(!r.active)fields.push('active');}return{source_local_id:s.source_local_id,external_ref:s.external_ref,server_id:r?.id||null,fields};});
 const refs=new Set(plan.members.map(s=>s.external_ref)),extra=snapshot.students.filter(s=>s.active&&s.class_id===c.id&&!refs.has(s.external_ref)).map(s=>({server_id:s.id,external_ref:s.external_ref,action:'review_no_delete'}));
 const conflicts=comparisons.filter(s=>s.fields.length).length;
 return{format:'future-school-single-class-server-check',schema_version:1,checked_at:new Date(now).toISOString(),source_generated_at:snapshot.generated_at,
  server_class_id:c.id,comparisons,extra_active_students:extra,class_conflicts:classFields,
  summary:{students:plan.members.length,matched_students:comparisons.length-conflicts,conflicting_students:conflicts,extra_students:extra.length,class_conflicts:classFields.length},
  roster_matches:!conflicts&&!extra.length&&!classFields.length,preview_only:true,ready_to_enable:false,server_written:false,device_enabled:false,
  remaining_blockers:plan.blockers.filter(b=>b!=='FRESH_SERVER_CLASS_COMPARISON_REQUIRED')};
}
module.exports={build,digest,compareServer};
