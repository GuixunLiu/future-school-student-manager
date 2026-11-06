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
  function apply(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),sync=state.managementSync;
      if(!sync||sync.protocol!==2||!profile||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId||profile.generation!==sync.generation||profile.profileId!==sync.profileId)fail('profile_changed');
      if(sync.queue.some(entry=>entry.status!=='accepted'))fail('offline_queue');
      const device=payload?.device;
      if(!device||device.id!==sync.deviceId||device.generation!==sync.generation||!text(device.name)||!integer(payload.snapshot_at)||payload.snapshot_at<0)fail('invalid_bootstrap');
      if(!Array.isArray(state.students)||!Array.isArray(state.classes))fail('invalid_state');
      unique(payload.classes,'classes',200);unique(payload.lessons,'lessons',5000);
      const classes=new Map(),localClasses=new Map();
      for(const item of payload.classes){
        if(!text(item.name)||!text(item.course_name))fail('invalid_bootstrap');
        const matches=state.classes.filter(row=>row.name===item.name);
        if(matches.length!==1)fail('mapping_changed');
        const local=matches[0];classes.set(item.id,{local,name:item.name});
        localClasses.set(local.name,item.id);
      }
      const students=new Map(),lessons={},facts={};let total=0;
      for(const lesson of payload.lessons){
        const course=classes.get(lesson.class_id);
        if(!course||!integer(lesson.starts_at)||!integer(lesson.ends_at)||lesson.starts_at<0||lesson.ends_at<lesson.starts_at||!['scheduled','cancelled','completed'].includes(lesson.state)||!Array.isArray(lesson.students)||lesson.students.length>500)fail('invalid_bootstrap');
        total+=lesson.students.length;if(total>50000)fail('invalid_bootstrap');
        const studentIds=[],byStudent={};
        for(const item of lesson.students){
          if(!id(item.student_id)||!text(item.name)||!text(item.external_ref,80)||typeof item.expected!=='boolean'||!['missing','arrived','leave'].includes(item.status)||!integer(item.attendance_revision)||item.attendance_revision<0||!integer(item.balance_revision)||item.balance_revision<0||!integer(item.balance_units)||!integer(item.charge_units)||item.charge_units<=0)fail('invalid_bootstrap');
          const matches=state.students.filter(row=>row.studentNo===item.external_ref&&row.name===item.name&&localClasses.get(row.className)===lesson.class_id);
          if(matches.length!==1)fail('mapping_changed');
          const local=matches[0],previous=students.get(item.student_id);
          if(previous&&(previous.localId!==local.id||previous.classId!==lesson.class_id))fail('mapping_changed');
          if(!previous)students.set(item.student_id,{localId:local.id,localClassId:course.local.id,name:item.name,studentNo:item.external_ref,className:course.name,classId:lesson.class_id,teacher:course.local.teacher||'',balanceUnits:item.balance_units,balanceRevision:item.balance_revision});
          const binding=students.get(item.student_id);
          if(binding.balanceUnits!==item.balance_units||binding.balanceRevision!==item.balance_revision)fail('invalid_bootstrap');
          studentIds.push(item.student_id);byStudent[item.student_id]={expected:item.expected,status:item.status,revision:item.attendance_revision,chargeUnits:item.charge_units};
        }
        if(new Set(studentIds).size!==studentIds.length)fail('invalid_bootstrap');
        lessons[lesson.id]={classId:lesson.class_id,date:day(lesson.starts_at),startsAt:lesson.starts_at,endsAt:lesson.ends_at,state:lesson.state,studentIds};facts[lesson.id]=byStudent;
      }
      sync.students=Object.fromEntries(students);sync.lessons=lessons;sync.facts=facts;sync.snapshotAt=payload.snapshot_at;delete sync.rebindPending;
      for(const binding of students.values())state.students.find(row=>row.id===binding.localId).hours=binding.balanceUnits/100;
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
      return{ok:true,classes:classes.size,lessons:payload.lessons.length,students:students.size,snapshotAt:payload.snapshot_at};
    }catch(error){return{ok:false,code:error.code||'invalid_bootstrap'};}
  }
  function initialize(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),current=state.managementSync;
      if(current?.enabled===true||state.deviceSync?.captureEnabled)fail('legacy_writer_active');
      state.managementSync={protocol:2,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
        orgId:profile.orgId,deviceId:profile.deviceId,generation:profile.generation,profileId:profile.profileId,
        queue:[],receipts:{},students:{},lessons:{},facts:{}};
      state.deviceSync={...(state.deviceSync||{}),captureEnabled:false};
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
    }catch(error){return{ok:false,code:error.code||'invalid_state'};}
    return apply(store,payload,profile);
  }
  function rebind(store,payload,profile){
    try{
      const raw=store.read(),state=JSON.parse(raw),sync=state.managementSync;
      if(!sync||sync.protocol!==2||sync.enabled!==true||!profile||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId)fail('profile_changed');
      if(sync.queue.some(entry=>entry.status!=='accepted'))fail('offline_queue');
      const unchanged=profile.generation===sync.generation&&profile.profileId===sync.profileId;
      if(unchanged&&!sync.rebindPending)fail('profile_unchanged');
      sync.generation=profile.generation;sync.profileId=profile.profileId;sync.rebindPending=true;
      if(store.compareAndSwap(raw,JSON.stringify(state))!==true)fail('local_changes');
    }catch(error){return{ok:false,code:error.code||'invalid_state'};}
    return apply(store,payload,profile);
  }
  const api={apply,initialize,rebind};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementRuntimeBootstrap=api;
})(globalThis);
