/* Persist the reviewed full bootstrap once before native/device commit.
 * Retries only accept the exact saved bootstrap; never overwrite later business.
 */
(function(root){
 'use strict';
 const fail=code=>{const e=new Error(code);e.code=code;throw e;};
 async function sha(raw){const value=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));return Array.from(new Uint8Array(value),b=>b.toString(16).padStart(2,'0')).join('');}
 async function persist(store,payload,profile){
  if(!payload||payload.format!=='future-school-canonical-bootstrap-v1'||!profile||profile.configured===true||!profile.staged)fail('activation_profile_differs');
  for(const[a,b]of [['org_id','orgId'],['device_id','deviceId'],['generation','generation'],['profile_id','profileId']])if(payload[a]!==profile[b])fail('activation_profile_differs');
  if(!/^[a-f0-9]{64}$/.test(payload.bootstrap_sha256)||!/^[a-f0-9]{64}$/.test(payload.baseline_sha256)||!/^[a-f0-9]{64}$/.test(payload.source_map_sha256)||typeof payload.snapshot_base64!=='string'||payload.snapshot_base64.length>28*1024*1024)fail('invalid_bootstrap');
  const bytes=Uint8Array.from(atob(payload.snapshot_base64),c=>c.charCodeAt(0)),next=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  if(bytes.length>20*1024*1024||await sha(next)!==payload.bootstrap_sha256)fail('invalid_bootstrap');
  const state=JSON.parse(next),sync=state.managementSync;
  if(sync?.protocol!==1||sync.enabled!==true||sync.mode!=='server_authoritative'||sync.legacyMirrorDisabled!==true||state.deviceSync?.captureEnabled!==false||sync.queue?.length!==0||Object.keys(sync.receipts||{}).length!==0)fail('invalid_bootstrap');
  for(const[a,b]of [['org_id','orgId'],['device_id','deviceId'],['generation','generation'],['profile_id','profileId']])if(payload[a]!==sync[b])fail('invalid_bootstrap');
  const before=store.read(),hash=await sha(before);
  if(store.read()!==before)fail('local_changes');
  if(hash===payload.bootstrap_sha256)return{duplicate:true,bootstrapSha256:hash,sourceMapSha256:payload.source_map_sha256};
  if(hash!==payload.baseline_sha256||JSON.parse(before).managementSync?.enabled===true)fail('local_changes');
  if(!store.compareAndSwap(before,next))fail('local_changes');
  return{duplicate:false,bootstrapSha256:payload.bootstrap_sha256,sourceMapSha256:payload.source_map_sha256};
 }
 const api={persist};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementActivation=api;
})(globalThis);
