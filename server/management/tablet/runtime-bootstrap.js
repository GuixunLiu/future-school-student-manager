/* Protocol 2 server snapshot receiver. Credentials remain in native code.
 * Never replace the local snapshot while an offline check-in awaits upload.
 * One CAS write updates only runtime mappings and server-owned balances.
 */
(function(root){
  'use strict';
  const integer=Number.isSafeInteger;
  const fail=code=>{const error=new Error(code);error.code=code;throw error;};
  const id=value=>typeof value==='string'&&/^[A-Za-z0-9_.:-]{1,80}$/.test(value);
  const text=(value,max=200)=>typeof value==='string'&&value.length>0&&value.length<=max;
  const day=value=>new Date((value+28800)*1000).toISOString().slice(0,10);
  function unique(rows,label,limit){
    if(!Array.isArray(rows)||rows.length>limit)fail('invalid_bootstrap');
    const ids=new Set();for(const row of rows){if(!row||!id(row.id)||ids.has(row.id))fail('invalid_bootstrap');ids.add(row.id);}return ids;
  }
  function project(state,payload,profile,allowNew){
    const sync=state.managementSync,device=payload?.device;
    if(!device||device.id!==profile.deviceId||device.generation!==profile.generation||!text(device.name)||!integer(payload.snapshot_at)||payload.snapshot_at<0)fail('invalid_bootstrap');
    if(!Array.isArray(state.students)||!Array.isArray(state.classes))fail('invalid_state');
    unique(payload.classes,'classes',200);unique(payload.lessons,'lessons',5000);
    let lastId=0;
    for(const row of [...state.students,...state.classes]){
      if(!integer(row.id)||row.id<=0)fail('invalid_state');
      lastId=Math.max(lastId,row.id);
    }
    function findLocal(rows,field,remoteId,priorId,matches,create){
      let candidates=rows.filter(row=>row[field]===remoteId||row.id===priorId);
      if(candidates.length===0)candidates=rows.filter(row=>!row[field]&&matches(row));
      if(candidates.length>1)fail('mapping_changed');
      const local=candidates[0];
      if(local){
        if(local[field]&&local[field]!==remoteId)fail('mapping_changed');
        return local;
      }
      if(!allowNew)fail('mapping_changed');
      if(!integer(++lastId))fail('invalid_state');
      const added={id:lastId,...create};
      rows.push(added);
      return added;
    }
    const classes=new Map(),students=new Map();
    const previousClassIds=new Map(Object.entries(sync.classes||{}).map(([key,value])=>[key,value.localId]));
    for(const student of Object.values(sync.students||{})){
      if(!previousClassIds.has(student.classId))previousClassIds.set(student.classId,student.localClassId);
    }
    for(const item of payload.classes){
      if(!text(item.name)||!text(item.course_name))fail('invalid_bootstrap');
      const local=findLocal(state.classes,'runtimeClassId',item.id,previousClassIds.get(item.id),
        row=>row.name===item.name,{teacher:'',classroom:'',scheduleWeekday:0,scheduleStart:'',reminderEnabled:false});
      Object.assign(local,{runtimeClassId:item.id,name:item.name,courseName:item.course_name});
      classes.set(item.id,local);
    }
    const lessons=Object.create(null),facts=Object.create(null);
    let total=0;
    for(const lesson of payload.lessons){
      const course=classes.get(lesson.class_id);
      if(!course||!integer(lesson.starts_at)||!integer(lesson.ends_at)||lesson.starts_at<0||lesson.ends_at<lesson.starts_at||!['scheduled','cancelled','completed'].includes(lesson.state)||!Array.isArray(lesson.students)||lesson.students.length>500)fail('invalid_bootstrap');
      total+=lesson.students.length;if(total>50000)fail('invalid_bootstrap');
      const studentIds=[],byStudent=Object.create(null);
      for(const item of lesson.students){
        if(!id(item.student_id)||!text(item.name)||!text(item.external_ref,80)||typeof item.expected!=='boolean'||!['missing','arrived','leave'].includes(item.status)||!integer(item.attendance_revision)||item.attendance_revision<0||!integer(item.balance_revision)||item.balance_revision<0||!integer(item.balance_units)||!integer(item.charge_units)||item.charge_units<=0)fail('invalid_bootstrap');
        if(byStudent[item.student_id])fail('invalid_bootstrap');
        let binding=students.get(item.student_id);
        if(!binding){
          const prior=Object.prototype.hasOwnProperty.call(sync.students||{},item.student_id)?sync.students[item.student_id]:null;
          const local=findLocal(state.students,'runtimeStudentId',item.student_id,prior?.localId,
            row=>row.studentNo===item.external_ref&&row.name===item.name&&row.className===course.name,
            {faceRegistered:false,sfaceDescriptors:[],sfaceAdaptiveDescriptors:[]});
          Object.assign(local,{runtimeStudentId:item.student_id,name:item.name,studentNo:item.external_ref,
            className:course.name,teacher:course.teacher||'',hours:item.balance_units/100});
          binding={localId:local.id,localClassId:course.id,name:item.name,studentNo:item.external_ref,
            className:course.name,classId:lesson.class_id,teacher:course.teacher||'',
            balanceUnits:item.balance_units,balanceRevision:item.balance_revision};
          students.set(item.student_id,binding);
        }
        if(binding.name!==item.name||binding.studentNo!==item.external_ref||binding.balanceUnits!==item.balance_units||binding.balanceRevision!==item.balance_revision)fail('invalid_bootstrap');
        studentIds.push(item.student_id);byStudent[item.student_id]={expected:item.expected,status:item.status,revision:item.attendance_revision,chargeUnits:item.charge_units};
      }
      lessons[lesson.id]={classId:lesson.class_id,date:day(lesson.starts_at),startsAt:lesson.starts_at,endsAt:lesson.ends_at,state:lesson.state,studentIds};facts[lesson.id]=byStudent;
    }
    sync.classes=Object.fromEntries([...classes].map(([key,local])=>[key,{localId:local.id}]));
    sync.students=Object.fromEntries(students);sync.lessons=lessons;sync.facts=facts;sync.snapshotAt=payload.snapshot_at;delete sync.rebindPending;
    return {ok:true,classes:classes.size,lessons:payload.lessons.length,students:students.size,snapshotAt:payload.snapshot_at};
  }
  function apply(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),sync=state.managementSync;
      if(!sync||sync.protocol!==2||!profile||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId||profile.generation!==sync.generation||profile.profileId!==sync.profileId)fail('profile_changed');
      if(sync.queue.some(entry=>!['accepted','resolved'].includes(entry.status)))fail('offline_queue');
      const result=project(state,payload,profile,integer(sync.snapshotAt));
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
      return result;
    }catch(error){return{ok:false,code:error.code||'invalid_bootstrap'};}
  }
  function initialize(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),current=state.managementSync;
      if(current?.enabled===true||state.deviceSync?.captureEnabled)fail('legacy_writer_active');
      if(!profile||!id(profile.orgId)||!id(profile.deviceId)||!text(profile.generation)||!text(profile.profileId))fail('profile_changed');
      const fresh=['students','classes','attendance','transactions'].every(key=>Array.isArray(state[key])&&state[key].length===0)&&!(state.settings?.studentLeaves||[]).length;
      state.managementSync={protocol:2,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
        orgId:profile.orgId,deviceId:profile.deviceId,generation:profile.generation,profileId:profile.profileId,
        queue:[],receipts:{},students:{},lessons:{},facts:{}};
      const result=project(state,payload,profile,fresh);
      state.deviceSync={...(state.deviceSync||{}),captureEnabled:false};
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
      return result;
    }catch(error){return{ok:false,code:error.code||'invalid_state'};}
  }
  function rebind(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),sync=state.managementSync;
      if(!sync||sync.protocol!==2||sync.enabled!==true||!profile||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId||!text(profile.generation)||!text(profile.profileId))fail('profile_changed');
      const unchanged=profile.generation===sync.generation&&profile.profileId===sync.profileId;
      if(unchanged&&!sync.rebindPending)fail('profile_unchanged');
      sync.generation=profile.generation;sync.profileId=profile.profileId;sync.rebindPending=true;
      // Protocol 2 pending uploads carry stable event refs and no generation.
      // Adopt the credential first, then let the normal upload-first loop drain them.
      const result=payload&&!sync.queue.some(entry=>!['accepted','resolved'].includes(entry.status))
        ?project(state,payload,profile,integer(sync.snapshotAt)):{ok:true,awaitingSync:true};
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
      return result;
    }catch(error){return{ok:false,code:error.code||'invalid_state'};}
  }
  const api={apply,initialize,rebind};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementRuntimeBootstrap=api;
})(globalThis);
