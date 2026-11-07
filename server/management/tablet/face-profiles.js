/* Protocol 2 active SFace projection. Credentials and photos never enter this module.
 * Validate the complete native response before one CAS write. Local-only profiles
 * are preserved; only profiles previously marked as server-managed can be removed.
 */
(function(root){
  'use strict';
  const MODEL='sface-int8bq-v1',MODEL_VERSION=4,FRAME_COUNT=3,SIZE=128;
  const own=(object,key)=>Object.prototype.hasOwnProperty.call(object||{},key);
  const fail=code=>{const error=new Error(code);error.code=code;throw error;};
  const integer=Number.isSafeInteger;
  const id=value=>typeof value==='string'&&/^[A-Za-z0-9_.:-]{1,80}$/.test(value);
  const fields=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)&&JSON.stringify(Object.keys(value).sort())===JSON.stringify(names.slice().sort());
  function frames(input){
    if(!Array.isArray(input)||input.length!==FRAME_COUNT)fail('invalid_face_profile');
    const normalized=input.map(frame=>{
      if(!Array.isArray(frame)||frame.length!==SIZE||frame.some(value=>typeof value!=='number'||!Number.isFinite(value)))fail('invalid_face_profile');
      const norm=Math.sqrt(frame.reduce((sum,value)=>sum+value*value,0));
      if(norm<.95||norm>1.05)fail('invalid_face_profile');return frame.map(value=>value/norm);
    });
    for(let i=1;i<FRAME_COUNT;i++){
      const similarity=normalized[0].reduce((sum,value,index)=>sum+value*normalized[i][index],0);
      if(similarity<.25)fail('invalid_face_profile');
    }
    return normalized;
  }
  function pack(frame){
    let binary='';for(const value of frame){let quantized=Math.max(-32767,Math.min(32767,Math.round(value*32767)));if(quantized<0)quantized+=65536;binary+=String.fromCharCode((quantized>>8)&255,quantized&255);}
    if(typeof btoa!=='function')fail('face_storage_unavailable');return'q16:'+btoa(binary);
  }
  function clear(student){
    if(!student)return;
    student.faceImage='';student.faceDescriptors=[];student.faceAdaptiveDescriptors=[];
    student.sfaceDescriptors=[];student.sfaceAdaptiveDescriptors=[];student.sfaceModelVersion=0;student.faceSecurityVersion=0;
    student.faceRegistered=false;student.faceProfileSource='';student.faceProfileId='';student.faceLearningCount=0;student.lastFaceLearnedAt='';
  }
  function apply(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),sync=state.managementSync;
      if(!sync||sync.protocol!==2||sync.enabled!==true||sync.mode!=='server_authoritative'||!profile||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId||profile.generation!==sync.generation||profile.profileId!==sync.profileId)fail('profile_changed');
      if(!fields(payload,['protocol','org_id','device_id','generation','snapshot_at','model_version','items'])||payload.protocol!==2||payload.org_id!==sync.orgId||payload.device_id!==sync.deviceId||payload.generation!==sync.generation||!integer(payload.snapshot_at)||payload.snapshot_at<0||payload.model_version!==MODEL||!Array.isArray(payload.items)||payload.items.length>2000||!Array.isArray(state.students)||!sync.students||typeof sync.students!=='object')fail('invalid_face_response');
      const previous=sync.faceProfiles&&typeof sync.faceProfiles==='object'?sync.faceProfiles:{},next={},seen=new Set();let updated=0;
      for(const item of payload.items){
        if(!fields(item,['profile_id','student_id','model_version','descriptors'])||!id(item.profile_id)||!id(item.student_id)||item.model_version!==MODEL||seen.has(item.student_id))fail('invalid_face_profile');
        seen.add(item.student_id);const binding=sync.students[item.student_id];
        if(!binding||!integer(binding.localId))fail('face_mapping_changed');
        const matches=state.students.filter(student=>student.id===binding.localId&&student.name===binding.name&&student.studentNo===binding.studentNo&&student.className===binding.className);
        if(matches.length!==1)fail('face_mapping_changed');
        const student=matches[0],packed=frames(item.descriptors).map(pack);
        const anchorsReady=Array.isArray(student.sfaceDescriptors)&&student.sfaceDescriptors.length===FRAME_COUNT&&student.sfaceDescriptors.every(sample=>typeof sample==='string'&&sample.startsWith('q16:'));
        if(student.faceProfileSource!=='server'||student.faceProfileId!==item.profile_id||!anchorsReady){student.sfaceDescriptors=packed;student.sfaceAdaptiveDescriptors=[];student.faceLearningCount=0;student.lastFaceLearnedAt='';updated++;}
        student.faceImage='';student.faceDescriptors=[];student.faceAdaptiveDescriptors=[];student.sfaceModelVersion=MODEL_VERSION;student.faceSecurityVersion=MODEL_VERSION;
        student.faceRegistered=true;student.faceProfileSource='server';student.faceProfileId=item.profile_id;
        next[item.student_id]={localId:binding.localId,profileId:item.profile_id};
      }
      let removed=0;const revoked=new Set();
      const remove=(studentId,localId)=>{
        if(revoked.has(studentId)||(own(next,studentId)&&next[studentId].localId===localId))return;
        const matches=state.students.filter(student=>student.id===localId);if(matches.length!==1)return;
        if(matches[0].faceProfileSource==='server'||own(previous,studentId)){clear(matches[0]);revoked.add(studentId);removed++;}
      };
      for(const [studentId,old] of Object.entries(previous))if(old&&integer(old.localId))remove(studentId,old.localId);
      for(const [studentId,binding] of Object.entries(sync.students))if(!own(next,studentId)&&integer(binding?.localId))remove(studentId,binding.localId);
      sync.faceProfiles=next;sync.faceSnapshotAt=payload.snapshot_at;
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
      return{ok:true,received:payload.items.length,updated,removed,snapshotAt:payload.snapshot_at};
    }catch(error){return{ok:false,code:error.code||'invalid_face_response'};}
  }
  const api={apply};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementFaceProfiles=api;
})(globalThis);
