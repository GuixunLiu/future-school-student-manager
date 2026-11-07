/* Candidate server-authoritative check-in writer. Not loaded by daily App.
 * One CAS write includes attendance, debit, optimistic revisions and queue.
 * The Android adapter must call this INSTEAD OF the old local check-in writer.
 * It must authenticate the pinned transport before passing a receipt here.
 */
(function(root) {
  'use strict';
  const fail=code=>{const e=new Error(code);e.code=code;throw e;};
  const integer=Number.isSafeInteger;
  const own=(o,k)=>Object.prototype.hasOwnProperty.call(o||{},k);
  const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
  const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const receiptHistory=128;
  const conflictCodes=new Set(['attendance_changed','balance_changed','checkin_scope','lesson_day','idempotency_conflict','attendance_conflict','lesson_unavailable','device_clock']);
  const isConflict=entry=>entry.status==='blocked'&&conflictCodes.has(entry.error);
  function nextEntry(queue){
    const blockedStudents=new Set();
    for(const entry of queue){
      if(entry.status==='accepted'||entry.status==='resolved')continue;
      if(isConflict(entry)){blockedStudents.add(entry.payload.student_id);continue;}
      if(!blockedStudents.has(entry.payload.student_id))return entry;
    }
    return null;
  }
  function compact(queue){
    let remove=queue.filter(e=>e.status==='accepted'||e.status==='resolved').length-receiptHistory;
    if(remove<=0)return queue;
    return queue.filter(entry=>{
      if(!['accepted','resolved'].includes(entry.status)||remove===0)return true;
      remove--;
      return false;
    });
  }
  const units=h=>{if(typeof h!=='number'||!Number.isFinite(h))fail('balance_changed');const n=Math.round(h*100);if(!integer(n)||Math.abs(n-h*100)>.00001)fail('balance_changed');return n;};
  function context(state){
    const s=state.managementSync;
    if(!s||![1,2].includes(s.protocol)||s.enabled!==true||s.mode!=='server_authoritative'||!s.orgId||!s.deviceId||!s.generation)fail('mapping_changed');
    if(s.legacyMirrorDisabled!==true||state.deviceSync?.captureEnabled)fail('legacy_writer_active');
    if(!Array.isArray(s.queue)||s.queue.length>10000)fail('invalid_queue');
    return s;
  }
  function wireFor(sync,eventRef,payload){
    if(sync.protocol===2){const {remaining_units,...upload}=payload;return{event_ref:eventRef,...upload};}
    return{generation:sync.generation,event_ref:eventRef,...payload};
  }
  function write(storage,before,after){try{if(storage.compareAndSwap(before,JSON.stringify(after))!==true)fail('storage_failed');}catch(_){fail('storage_failed');}}
  function attempt(fn){try{return fn();}catch(e){return{ok:false,code:e.code||'invalid_state'};}}
  function record(storage,input,now=Math.floor(Date.now()/1000)){
    return attempt(()=>{
      const {studentId,lessonId,occurredAt,eventRef}=input;
      if(!equal(Object.keys(input).sort(),['eventRef','lessonId','occurredAt','studentId'])||typeof studentId!=='string'||typeof lessonId!=='string'||!/^[A-Za-z0-9_.:-]{1,80}$/.test(eventRef||'')||!integer(occurredAt)||occurredAt<0||occurredAt>253402185599||!integer(now)||occurredAt>now+120)fail('invalid_checkin');
      const raw=storage.read(),state=JSON.parse(raw),sync=context(state);
      const old=sync.queue.find(e=>e.eventRef===eventRef);
      if(old){if(['studentId','lessonId','occurredAt','eventRef'].some(k=>old.input[k]!==input[k]))fail('idempotency_conflict');return{ok:true,duplicate:true,eventRef};}
      sync.queue=compact(sync.queue);
      if(sync.queue.length>=10000)fail('queue_full');
      if(!own(sync.students,studentId)||!own(sync.lessons,lessonId)||!own(sync.facts?.[lessonId],studentId))fail('mapping_changed');
      const binding=sync.students[studentId],lesson=sync.lessons[lessonId],fact=sync.facts[lessonId][studentId];
      if(!Array.isArray(state.students)||!Array.isArray(state.classes)||!Array.isArray(state.attendance)||!Array.isArray(state.transactions)||!Array.isArray(state.settings?.studentLeaves))fail('invalid_state');
      if(state.attendance.some(a=>a.deviceEventRef===eventRef)||state.transactions.some(t=>t.deviceEventRef===eventRef))fail('idempotency_conflict');
      const students=state.students.filter(s=>s.id===binding.localId),classes=state.classes.filter(c=>c.id===binding.localClassId);
      if(students.length!==1||classes.length!==1)fail('mapping_changed');
      const student=students[0],course=classes[0];
      if(student.studentNo!==binding.studentNo||student.name!==binding.name||student.className!==binding.className||course.name!==binding.className||course.teacher!==binding.teacher||student.longTermAbsent===true||student.status==='archived'||lesson.classId!==binding.classId||!lesson.studentIds?.includes(studentId)||(sync.protocol===2&&lesson.state!=='scheduled'))fail('mapping_changed');
      if(!integer(lesson.startsAt)||lesson.date!==day(lesson.startsAt)||day(occurredAt)!==lesson.date)fail('lesson_day');
      if(fact.expected!==true||fact.status!=='missing'||!integer(fact.revision)||!integer(fact.revision+1)||fact.revision<0||!integer(fact.chargeUnits)||fact.chargeUnits<=0)fail('attendance_changed');
      if(!integer(binding.balanceRevision)||!integer(binding.balanceRevision+1)||binding.balanceRevision<0||!integer(binding.balanceUnits)||units(student.hours)!==binding.balanceUnits)fail('balance_changed');
      // Server attendance is lesson-scoped. Two different lessons on one day are valid;
      // old queue entries remain recognizable through their stable device event ref.
      const sameLesson=state.attendance.some(a=>a.studentId===student.id&&(a.deviceLessonId===lessonId||sync.queue.some(e=>e.eventRef===a.deviceEventRef&&e.payload?.lesson_id===lessonId)));
      if(sameLesson||state.settings.studentLeaves.some(l=>l.studentId===student.id&&l.date===lesson.date&&l.status==='approved'))fail('attendance_changed');
      const payload={student_id:studentId,lesson_id:lessonId,attendance_revision:fact.revision,balance_revision:binding.balanceRevision,
        previous_balance_units:binding.balanceUnits,consumed_units:fact.chargeUnits,remaining_units:binding.balanceUnits-fact.chargeUnits,occurred_at:occurredAt};
      if(!integer(payload.remaining_units))fail('balance_changed');
      let last=0;for(const rows of [state.students,state.classes,state.attendance,state.transactions,state.settings.studentLeaves])for(const row of rows){if(!integer(row.id)||row.id<=0)fail('invalid_state');last=Math.max(last,row.id);}
      const id=()=>{last++;if(!integer(last))fail('invalid_state');return last;};
      const attendanceId=id(),time=new Date((occurredAt+28800)*1000).toISOString().slice(11,16);
      const snapshot={classId:course.id,className:binding.className,teacher:binding.teacher,classroom:course.classroom||'',source:'canonical_device',deviceEventRef:eventRef,deviceLessonId:lessonId};
      state.attendance.push({id:attendanceId,studentId:student.id,date:lesson.date,time,method:'平板签到',...snapshot});
      state.transactions.push({id:id(),attendanceId,studentId:student.id,amount:-fact.chargeUnits/100,reason:'平板签到扣减',date:lesson.date,time,...snapshot});
      student.hours=payload.remaining_units/100;binding.balanceUnits=payload.remaining_units;binding.balanceRevision++;fact.status='arrived';fact.revision++;
      sync.queue.push({eventRef,input:{...input},status:'pending',payload,wire:JSON.stringify(wireFor(sync,eventRef,payload)),receipt:null});
      write(storage,raw,state);
      return{ok:true,duplicate:false,eventRef,attendanceId};
    });
  }
  function next(storage){return attempt(()=>{
    const sync=context(JSON.parse(storage.read())),e=nextEntry(sync.queue);
    if(!e)return{ok:true,item:null,blocked:sync.queue.some(isConflict)};
    if(!['pending','blocked'].includes(e.status)||!equal(JSON.parse(e.wire),wireFor(sync,e.eventRef,e.payload)))fail('invalid_queue');
    return{ok:true,item:{eventRef:e.eventRef,wire:e.wire},blocked:false};
  });}
  // Match Python json.dumps(ensure_ascii=True, sort_keys=True, separators=(',',':')).
  // Check-in payload is flat, contains only string IDs and safe integers.
  function canonical(payload){const sorted={};Object.keys(payload).sort().forEach(k=>sorted[k]=payload[k]);return JSON.stringify(sorted).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
  async function acknowledge(storage,eventRef,receipt){
    try{
      const before=JSON.parse(storage.read()),initial=context(before),entry=initial.queue.find(e=>e.eventRef===eventRef);
      if(!entry)fail('unknown_receipt');
      const p=entry.payload;let digest=null;
      if(initial.protocol===1){
        const hashed=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(entry.payload)));
        digest=Array.from(new Uint8Array(hashed),b=>b.toString(16).padStart(2,'0')).join('');
      }
      if(!receipt||receipt.protocol!==initial.protocol||receipt.accepted!==true||receipt.apply_local_debit!==false||receipt.status!=='arrived'||receipt.org_id!==initial.orgId||receipt.device_id!==initial.deviceId||receipt.generation!==initial.generation||receipt.event_ref!==eventRef||receipt.student_id!==p.student_id||receipt.lesson_id!==p.lesson_id||receipt.attendance_revision!==p.attendance_revision+1||receipt.balance_revision!==p.balance_revision+1||receipt.balance_units!==p.remaining_units||(initial.protocol===1&&receipt.digest!==digest))fail('receipt_mismatch');
      // Re-read after hashing, preserving check-ins recorded in the meantime.
      const raw=storage.read(),state=JSON.parse(raw),sync=context(state),current=sync.queue.find(e=>e.eventRef===eventRef);
      if(sync.orgId!==initial.orgId||sync.deviceId!==initial.deviceId||sync.generation!==initial.generation||!current||current.wire!==entry.wire)fail('mapping_changed');
      if(current.status==='accepted')return{ok:true,duplicate:true};
      const first=nextEntry(sync.queue);
      if(first!==current)fail('receipt_order');
      current.status='accepted';current.receipt={digest,attendanceRevision:receipt.attendance_revision,balanceRevision:receipt.balance_revision,balanceUnits:receipt.balance_units};
      sync.queue=compact(sync.queue);
      // Do not set hours from an old receipt: later offline check-ins may exist.
      write(storage,raw,state);return{ok:true,duplicate:false};
    }catch(e){return{ok:false,code:e.code||'invalid_receipt'};}
  }
  function block(storage,eventRef,code){return attempt(()=>{
    if(!conflictCodes.has(code))fail('invalid_conflict');
    const raw=storage.read(),state=JSON.parse(raw),sync=context(state),entry=nextEntry(sync.queue);
    if(!entry||entry.eventRef!==eventRef)fail('receipt_order');entry.status='blocked';entry.error=code;write(storage,raw,state);return{ok:true};
  });}
  function retryConflict(storage,eventRef){return attempt(()=>{
    const raw=storage.read(),state=JSON.parse(raw),sync=context(state);
    const entry=sync.queue.find(e=>e.eventRef===eventRef);
    if(!entry||!isConflict(entry))fail('invalid_conflict');
    entry.status='pending';
    delete entry.error;
    write(storage,raw,state);
    return{ok:true};
  });}
  function resolveConflict(storage,eventRef,receipt){return attempt(()=>{
    const raw=storage.read(),state=JSON.parse(raw),sync=context(state);
    if(sync.protocol!==2)fail('invalid_conflict');
    const entry=sync.queue.find(e=>e.eventRef===eventRef),p=entry?.payload;
    if(!entry||!p)fail('invalid_conflict');
    const saved={eventId:receipt?.event_id,status:receipt?.status,attendanceRevision:receipt?.attendance_revision,
      balanceRevision:receipt?.balance_revision,balanceUnits:receipt?.balance_units};
    if(!isConflict(entry)&&entry.status!=='resolved')fail('invalid_conflict');
    if(!receipt||receipt.protocol!==2||receipt.accepted!==true||receipt.resolved!==true||receipt.apply_local_debit!==false||receipt.org_id!==sync.orgId||receipt.device_id!==sync.deviceId||receipt.generation!==sync.generation||receipt.event_ref!==eventRef||receipt.student_id!==p.student_id||receipt.lesson_id!==p.lesson_id||!['arrived','leave'].includes(receipt.status)||typeof receipt.event_id!=='string'||!/^[A-Za-z0-9_.:-]{1,80}$/.test(receipt.event_id)||!integer(receipt.attendance_revision)||receipt.attendance_revision<=p.attendance_revision||!integer(receipt.balance_revision)||receipt.balance_revision<0||!integer(receipt.balance_units))fail('receipt_mismatch');
    if(entry.status==='resolved'){if(!equal(entry.receipt,saved))fail('receipt_mismatch');return{ok:true,duplicate:true,status:entry.receipt.status};}
    const attendance=state.attendance.filter(row=>row.deviceEventRef===eventRef);
    if(attendance.length!==1)fail('mapping_changed');
    attendance[0].syncResolution='web_resolved';attendance[0].serverStatus=receipt.status;attendance[0].serverEventId=receipt.event_id;
    entry.status='resolved';entry.receipt=saved;delete entry.error;
    // Keep the original local debit as evidence. Bootstrap alone applies the
    // authoritative server balance after all pending and blocked entries finish.
    write(storage,raw,state);return{ok:true,duplicate:false,status:receipt.status};
  });}
  const flights=new WeakSet();
  async function pump(storage,transport){
    if(flights.has(storage))return{ok:true,busy:true,uploaded:0};
    flights.add(storage);let uploaded=0;
    try{
      for(let i=0;i<20;i++){
        const pending=next(storage);
        if(!pending.ok)return{...pending,uploaded};
        if(!pending.item)return{ok:true,uploaded,blocked:pending.blocked};
        const {eventRef,wire}=pending.item;
        let response;
        try{response=await transport(JSON.parse(wire));}catch(_){return{ok:false,code:'offline',uploaded,retry:true};}
        if(response?.ok===true){
          const saved=await acknowledge(storage,eventRef,response.data);
          if(!saved.ok)return{...saved,uploaded,retry:true};
          uploaded++;continue;
        }
        const status=response?.status;
        if(!status||status===429||status>=500)return{ok:false,code:'offline',uploaded,retry:true};
        const code=response?.data?.error?.code;
        if(status===409&&code==='migration_in_progress')return{ok:false,code,uploaded,retry:true,paused:true};
        if(!conflictCodes.has(code))return{ok:false,code:code||'delivery_not_granted',uploaded,retry:true};
        const blocked=block(storage,eventRef,code);
        if(!blocked.ok)return{...blocked,uploaded};
      }
      return{ok:true,uploaded,more:true};
    }finally{flights.delete(storage);}
  }
  const api={record,next,acknowledge,block,retryConflict,resolveConflict,pump};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementCheckinQueue=api;
})(globalThis);
