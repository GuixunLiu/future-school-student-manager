'use strict';
// Offline reconciliation. A proposed identity is never an import or enrollment.
const crypto=require('node:crypto'),{isDeepStrictEqual:equal}=require('node:util');
const {validateSource}=require('./read-management-review.cjs');
const {review}=require('./tablet-review/core.js');
const {build}=require('./build-teacher-web-data.cjs');
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
function compare(raw,source,{organization}={}){
 if(typeof raw!=='string'||Buffer.byteLength(raw)>50000000)throw Error('Complete bounded tablet backup required');
 validateSource(source,organization);
 if(!source.archive_available||!source.source_links_available)throw Error('Archive and source identity tables must both be available before reconciliation');
 const tablet=JSON.parse(raw),checked=review(tablet);
 if(checked.issue_counts.error)throw Error('Resolve tablet structural errors first');
 const remoteRows=[...source.students.map(s=>({...s,table:'students'})),...source.archived_students.map(s=>({...s,table:'archived_students'}))];
 const remoteByRef=new Map(remoteRows.map(s=>[s.external_ref,s]));
 const links=new Map(source.source_links.map(s=>[s.kind+':'+s.source_id,s.entity_id]));
 const remoteClasses=new Map(source.classes.map(c=>[c.name,c]));
 const used=new Set(),conflicts=[];
 const students=checked.students.map(s=>{
  const r=remoteByRef.get(s.student_no),linked=links.get('student:'+s.local_id),fields=[];
  if(r&&r.name!==s.name)fields.push('name');
  if(linked&&linked!==r?.id)fields.push('source_link');
  if(r&&used.has(r.id))fields.push('identity_reused');
  if(r)used.add(r.id);
  if(fields.length)conflicts.push({kind:'student',local_id:s.local_id,fields});
  return{local_id:s.local_id,external_ref:s.student_no,name:s.name,status:s.status,
   server_id:r?.id||null,existing_table:r?.table||null,identity:fields.length?'conflict':r?'match':'not_in_base_or_archive',
   source_link:linked?'linked':r?'unlinked_requires_review':'new_requires_review',
   balance_equal:r?r.remaining_units===s.remaining_units:null,remaining_units:s.remaining_units,
   base_status_change:r?.table==='students'&&r.active!==(s.status==='active'),
   class_name:s.class_name,previous_class_name:s.paused_class_name,
   // Same-name people are flagged, never merged by name.
   same_name_other_refs:remoteRows.filter(x=>x.name===s.name&&x.external_ref!==s.student_no).map(x=>x.external_ref)};
 });
 const staff=new Map(source.staff.map(s=>[s.id,s]));
 const classes=checked.classes.map(c=>{
  const r=remoteClasses.get(c.name),linked=links.get('class:'+c.local_id),m=source.mirror.classes.find(x=>x.local_id===String(c.local_id)),fields=[];
  if(r&&r.course_name!==c.course_name)fields.push('course');
  if(linked&&linked!==r?.id)fields.push('source_link');
  if(fields.length)conflicts.push({kind:'class',local_id:c.local_id,fields});
  return{local_id:c.local_id,name:c.name,teacher_label:c.teacher_label,server_id:r?.id||null,
   source_link:linked?'linked':'unlinked_requires_review',identity:fields.length?'conflict':r?'match':'not_in_base',
   mirror_matches:!!m&&m.name===c.name&&m.course_name===c.course_name&&m.teacher===c.teacher_label,
   mirror_teacher_ids:m?.teacher_ids||[],
   mirror_authorized:!!m&&m.teacher_ids.length===1&&staff.get(m.teacher_ids[0])?.display_name===c.teacher_label};
 });
 let mirror={present:false,coverage_only:true,equal:false,compared_lessons:0,missing:[],extra:[],differences:[],unrepresented_attendance:[]};
 if(source.mirror.present&&source.mirror.covered_weeks.length){
  const local=build(tablet,{asOfDate:source.mirror.as_of_date,sourceSha256:sha(raw),sourceCapturedAt:'Offline comparison only',mirror:true});
  const coverage=source.mirror.covered_weeks,inside=date=>coverage.some(w=>w.start<=date&&date<=w.end);
  const current=local.lessons.filter(l=>inside(l.date));
  const byKey=new Map(current.map(l=>[l.key,l])),remote=new Map(source.mirror.lessons.map(l=>[l.key,l]));
  const normalize=l=>({...l,members:[...l.members].sort((a,b)=>a.student_local_id.localeCompare(b.student_local_id))});
  const differences=[];
  for(const l of source.mirror.lessons){const e=byKey.get(l.key);if(e&&!equal(normalize(l),normalize(e)))differences.push({key:l.key,fields:Object.keys(l).filter(k=>!equal(normalize(l)[k],normalize(e)[k]))});}
  const arrived=new Set(source.mirror.lessons.flatMap(l=>l.members.filter(s=>s.status==='arrived').map(s=>s.student_local_id+':'+l.date)));
  const missing=current.filter(l=>!remote.has(l.key)).map(l=>l.key),extra=source.mirror.lessons.filter(l=>!byKey.has(l.key)).map(l=>l.key);
  mirror={present:true,coverage_only:true,revision:source.mirror.revision,received_at:source.mirror.received_at,source_captured_at:source.mirror.source_captured_at,
   equal:!missing.length&&!extra.length&&!differences.length,compared_lessons:current.length,missing,extra,differences,
   unrepresented_attendance:tablet.attendance.filter(a=>!arrived.has(String(a.studentId)+':'+a.date)).map(a=>({local_id:a.id,student_local_id:a.studentId,date:a.date,class_name:a.className||'',action:'preserve_raw_history_no_replay'}))};
 }
 const summary={students:students.length,identity_matches:students.filter(s=>s.identity==='match').length,
  existing_archives:students.filter(s=>s.existing_table==='archived_students').length,
  not_in_base_or_archive:students.filter(s=>s.identity==='not_in_base_or_archive').length,
  paused_in_base:students.filter(s=>s.status==='paused'&&s.existing_table==='students').length,
  balance_differences:students.filter(s=>s.balance_equal===false).length,identity_conflicts:conflicts.length,
  current_classes:classes.length,classes_matched:classes.filter(c=>c.identity==='match').length,
  mirror_authorized_classes:classes.filter(c=>c.mirror_authorized).length,
  server_only_students:remoteRows.filter(s=>!used.has(s.id)).length,
  mirror_equal:mirror.equal,mirror_compared_lessons:mirror.compared_lessons,unrepresented_attendance:mirror.unrepresented_attendance.length};
 return{format:'future-school-combined-management-comparison-v1',organization,org_id:source.org_id,review_only:true,ready_for_import:false,
  tablet_sha256:sha(raw),server_generated_at:source.generated_at,summary,students,classes,identity_conflicts:conflicts,mirror,
  server_only_students:remoteRows.filter(s=>!used.has(s.id)).map(s=>({server_id:s.id,external_ref:s.external_ref,action:'preserve_no_delete'})),
  notes:['Existing archive identities must be reused, never recreated','Missing base identities do not imply new enrollment or renewal','Mirror covers only its listed weeks and includes no complete current student status roster','Raw attendance absent from the report view must be preserved separately, never dropped or replayed','No freshness or activation authorization is issued by this offline comparison']};
}
module.exports={compare};
