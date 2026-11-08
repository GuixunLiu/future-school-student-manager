'use strict';
// A sealed OFFLINE proposal. No SQL, device enrollment or executable events.
const crypto=require('node:crypto'),{compare}=require('./compare-management-review.cjs'),history=require('./build-legacy-history-archive.cjs');
const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
function build(raw,serverRaw,{organization,newId=()=>crypto.randomUUID().replaceAll('-','')}={}){
 if(typeof serverRaw!=='string'||Buffer.byteLength(serverRaw)>32*1024*1024)throw Error('Complete bounded server review required');
 const source=JSON.parse(serverRaw),checked=compare(raw,source,{organization});
 if(checked.summary.identity_conflicts||!checked.mirror.equal||checked.classes.some(c=>c.identity!=='match'||c.source_link!=='linked'||!c.mirror_matches||!c.mirror_authorized))throw Error('Identity, class, mirror or current teacher review differs');
 const archives=history.build(raw),ids=new Set([...source.students,...source.archived_students].map(s=>s.id));
 const classes=checked.classes.map(c=>({server_id:c.server_id,local_id:c.local_id,name:c.name,teacher_id:c.mirror_teacher_ids[0],teacher_name:c.teacher_label}));
 const classIds=new Map(classes.map(c=>[c.name,c]));
 const students=checked.students.map(s=>{
  if(s.server_id&&s.source_link!=='linked')throw Error('Existing identities require verified persisted source links');
  let key=s.server_id;
  if(!key){
   // A matching current mirror identity is supporting evidence, NOT a new
   // enrollment/renewal event. Homonyms remain visibly marked for review.
   const seen=source.mirror.lessons.flatMap(l=>l.members.filter(m=>m.student_local_id===String(s.local_id)));
   if(!seen.length||seen.some(m=>m.name!==s.name||m.external_ref!==s.external_ref||m.remaining_units!==s.remaining_units))throw Error('New base identity has no consistent current mirror evidence');
   key=newId(s.local_id);if(typeof key!=='string'||!/^[a-f0-9]{32}$/.test(key)||ids.has(key))throw Error('Reserved identity is invalid or already used');ids.add(key);
  }
  const c=classIds.get(s.class_name);
  if(s.status==='active'&&!c)throw Error('Current active class missing');
  return{server_id:key,local_id:s.local_id,external_ref:s.external_ref,name:s.name,status:s.status,
   current_class_id:s.status==='active'?c.server_id:null,previous_class_name:s.previous_class_name,
   opening_balance_units:s.remaining_units,existing_table:s.existing_table,
   identity_action:s.server_id?'reuse_existing_id':'reserve_new_base_id',
   same_name_other_refs:s.same_name_other_refs,automatic_enrollment_event:false};
 });
 const plan={format:'future-school-management-initialization-plan-v1',schema_version:1,organization,org_id:source.org_id,
  proposal_only:true,ready_for_import:false,fresh_capture_required:true,activation_authorized:false,
  baseline:{tablet_sha256:checked.tablet_sha256,server_review_sha256:sha(serverRaw),server_generated_at:source.generated_at,
   mirror_revision:source.mirror.revision,mirror_source_sha256:source.mirror.source_sha256,history_archive_sha256:archives.archive_sha256},
  classes,students,
  preserve_server_only:checked.server_only_students,
  preserve_server_only_classes:source.classes.filter(c=>!classes.some(x=>x.server_id===c.id)).map(c=>({server_id:c.id,action:'preserve_no_delete'})),
  preserve_archive_ids:students.filter(s=>s.existing_table==='archived_students').map(s=>s.server_id),
  deactivate_without_moving_ids:students.filter(s=>s.existing_table==='students'&&s.status==='paused').map(s=>s.server_id),
  canonical_openings:students.filter(s=>s.status==='active').map(s=>({student_id:s.server_id,class_id:s.current_class_id,units:s.opening_balance_units,revision:0})),
  preserve_history:{manifest:archives.artifact.manifest,unrepresented_attendance_ids:checked.mirror.unrepresented_attendance.map(a=>a.local_id),replay_allowed:false},
  scope:{active_students:students.filter(s=>s.status==='active').length,archived_students:students.filter(s=>s.status==='paused').length,current_classes:classes.length},
  unresolved:['Explicit isolated-copy initializer and immutable history storage','Current/future lesson boundary without inventing old lessons','Fresh actual capture and server baseline review before any import','Two-endpoint activation and recovery after new business'],
  side_effects:{server_written:false,tablet_modified:false,accounts_changed:false,permissions_changed:false,balances_charged:false}};
 return{plan,plan_sha256:history.digest(plan),summary:{...plan.scope,reused_ids:students.filter(s=>s.identity_action==='reuse_existing_id').length,reserved_ids:students.filter(s=>s.identity_action==='reserve_new_base_id').length,
  existing_archive_ids:plan.preserve_archive_ids.length,deactivate_without_moving:plan.deactivate_without_moving_ids.length,server_only_preserved:plan.preserve_server_only.length,server_only_classes_preserved:plan.preserve_server_only_classes.length}};
}
module.exports={build};
