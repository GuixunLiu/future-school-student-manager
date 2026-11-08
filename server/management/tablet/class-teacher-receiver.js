/* Authenticated non-financial class metadata. Whole snapshot + revision in one CAS. */
(function(root){'use strict';
 const fail=code=>{const e=Error(code);e.code=code;throw e;},text=v=>typeof v==='string'&&v.length>0&&v.length<=200;
 async function receive(store,envelope){try{
  if(!envelope||![envelope.org_id,envelope.device_id,envelope.generation].every(text))fail('invalid_event');
  const item=envelope.item;if(!item||typeof item.event_json!=='string'||item.event_json.length>8000||!/^[a-f0-9]{64}$/.test(item.sha256)||!/^[a-f0-9]{32}$/.test(item.receipt_token))fail('invalid_event');
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(item.event_json));if(Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('')!==item.sha256)fail('invalid_event');
  const e=JSON.parse(item.event_json);if(e.protocol!==1||e.kind!=='class_teacher'||e.org_id!==envelope.org_id||![e.event_id,e.class_id,e.class_name,e.previous_teacher,e.teacher].every(text)||!Number.isSafeInteger(e.local_class_id)||e.local_class_id<1||!Number.isSafeInteger(e.previous_revision)||e.previous_revision<0||e.revision!==e.previous_revision+1)fail('invalid_event');
  const raw=store.read(),state=JSON.parse(raw),s=state.managementSync;
  if(!s||s.enabled!==true||s.mode!=='server_authoritative'||s.legacyMirrorDisabled!==true||s.orgId!==envelope.org_id||s.deviceId!==envelope.device_id||s.generation!==envelope.generation)fail('mapping_changed');
  if(state.deviceSync?.captureEnabled||!Array.isArray(s.queue))fail('local_changes');
  const revisions=s.classTeacherRevisions||{};if(Array.isArray(revisions)||typeof revisions!=='object')fail('mapping_changed');
  const old=Object.prototype.hasOwnProperty.call(revisions,e.class_id)?revisions[e.class_id]:{revision:0};
  const receipt={generation:s.generation,class_id:e.class_id,revision:e.revision,sha256:item.sha256,receipt_token:item.receipt_token,status:'applied'};
  if(old.revision===e.revision){if(old.sha256!==item.sha256)fail('invalid_event');return{ok:true,duplicate:true,receipt};}
  if(s.queue.some(q=>q.status!=='accepted'&&
    (!s.students?.[q.payload?.student_id]||s.students[q.payload.student_id].classId===e.class_id)))fail('local_changes');
  if(old.revision!==e.previous_revision)fail('mapping_changed');
  if(!Array.isArray(state.classes)||!s.students||Array.isArray(s.students))fail('mapping_changed');
  const classes=state.classes.filter(c=>c.id===e.local_class_id);if(classes.length!==1||classes[0].name!==e.class_name||classes[0].teacher!==e.previous_teacher)fail('mapping_changed');
  const bindings=Object.values(s.students).filter(b=>b.classId===e.class_id);
  if(bindings.some(b=>b.localClassId!==e.local_class_id||b.className!==e.class_name||b.teacher!==e.previous_teacher))fail('mapping_changed');
  classes[0].teacher=e.teacher;for(const b of bindings)b.teacher=e.teacher;
  s.classTeacherRevisions={...revisions,[e.class_id]:{revision:e.revision,sha256:item.sha256}};
  try{if(!store.compareAndSwap(raw,JSON.stringify(state)))fail('storage_failed');}catch(_){fail('storage_failed');}
  return{ok:true,duplicate:false,receipt};
 }catch(e){return{ok:false,code:e.code||'invalid_event'};}}
 const api={receive};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementClassTeacherReceiver=api;
})(globalThis);
