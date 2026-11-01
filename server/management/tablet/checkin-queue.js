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
  const units=h=>{if(typeof h!=='number'||!Number.isFinite(h))fail('balance_changed');const n=Math.round(h*100);if(!integer(n)||Math.abs(n-h*100)>.00001)fail('balance_changed');return n;};
  function context(state){
    const s=state.managementSync;
    if(!s||s.protocol!==1||s.enabled!==true||s.mode!=='server_authoritative'||!s.orgId||!s.deviceId||!s.generation)fail('mapping_changed');
    if(s.legacyMirrorDisabled!==true||state.deviceSync?.captureEnabled)fail('legacy_writer_active');
    if(!Array.isArray(s.queue)||s.queue.length>10000)fail('invalid_queue');
    return s;
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
      if(sync.queue.length>=10000)fail('queue_full'); // no automatic eviction of receipt history
      if(!own(sync.students,studentId)||!own(sync.lessons,lessonId)||!own(sync.facts?.[lessonId],studentId))fail('mapping_changed');
      const binding=sync.students[studentId],lesson=sync.lessons[lessonId],fact=sync.facts[lessonId][studentId];
      if(!Array.isArray(state.students)||!Array.isArray(state.classes)||!Array.isArray(state.attendance)||!Array.isArray(state.transactions)||!Array.isArray(state.settings?.studentLeaves))fail('invalid_state');
      const students=state.students.filter(s=>s.id===binding.localId),classes=state.classes.filter(c=>c.id===binding.localClassId);
      if(students.length!==1||classes.length!==1)fail('mapping_changed');
      const student=students[0],course=classes[0];
      if(student.studentNo!==binding.studentNo||student.name!==binding.name||student.className!==binding.className||course.name!==binding.className||course.teacher!==binding.teacher||student.longTermAbsent===true||student.status==='archived'||lesson.classId!==binding.classId||!lesson.studentIds?.includes(studentId))fail('mapping_changed');
      if(!integer(lesson.startsAt)||lesson.date!==day(lesson.startsAt)||day(occurredAt)!==lesson.date)fail('lesson_day');
      if(fact.expected!==true||fact.status!=='missing'||!integer(fact.revision)||!integer(fact.revision+1)||fact.revision<0||!integer(fact.chargeUnits)||fact.chargeUnits<=0)fail('attendance_changed');
      if(!integer(binding.balanceRevision)||!integer(binding.balanceRevision+1)||binding.balanceRevision<0||!integer(binding.balanceUnits)||units(student.hours)!==binding.balanceUnits)fail('balance_changed');
      if(state.attendance.some(a=>a.studentId===student.id&&a.date===lesson.date)||state.settings.studentLeaves.some(l=>l.studentId===student.id&&l.date===lesson.date&&l.status==='approved'))fail('attendance_changed');
      const payload={student_id:studentId,lesson_id:lessonId,attendance_revision:fact.revision,balance_revision:binding.balanceRevision,
        previous_balance_units:binding.balanceUnits,consumed_units:fact.chargeUnits,remaining_units:binding.balanceUnits-fact.chargeUnits,occurred_at:occurredAt};
      if(!integer(payload.remaining_units))fail('balance_changed');
      let last=0;for(const rows of [state.students,state.classes,state.attendance,state.transactions,state.settings.studentLeaves])for(const row of rows){if(!integer(row.id)||row.id<=0)fail('invalid_state');last=Math.max(last,row.id);}
      const id=()=>{last++;if(!integer(last))fail('invalid_state');return last;};
      const attendanceId=id(),time=new Date((occurredAt+28800)*1000).toISOString().slice(11,16);
      const snapshot={classId:course.id,className:binding.className,teacher:binding.teacher,classroom:course.classroom||'',source:'canonical_device',deviceEventRef:eventRef};
      state.attendance.push({id:attendanceId,studentId:student.id,date:lesson.date,time,method:'平板签到',...snapshot});
      state.transactions.push({id:id(),attendanceId,studentId:student.id,amount:-fact.chargeUnits/100,reason:'平板签到扣减',date:lesson.date,time,...snapshot});
      student.hours=payload.remaining_units/100;binding.balanceUnits=payload.remaining_units;binding.balanceRevision++;fact.status='arrived';fact.revision++;
      sync.queue.push({eventRef,input:{...input},status:'pending',payload,wire:JSON.stringify({generation:sync.generation,event_ref:eventRef,...payload}),receipt:null});
      write(storage,raw,state);
      return{ok:true,duplicate:false,eventRef,attendanceId};
    });
  }
  function next(storage){return attempt(()=>{const sync=context(JSON.parse(storage.read()));const e=sync.queue.find(q=>q.status!=='accepted');if(!e||e.status==='blocked')return{ok:true,item:null,blocked:!!e};if(e.status!=='pending'||!equal(JSON.parse(e.wire),{generation:sync.generation,event_ref:e.eventRef,...e.payload}))fail('invalid_queue');return{ok:true,item:{eventRef:e.eventRef,wire:e.wire},blocked:false};});}
  // Match Python json.dumps(ensure_ascii=True, sort_keys=True, separators=(',',':')).
  // Check-in payload is flat, contains only string IDs and safe integers.
  function canonical(payload){const sorted={};Object.keys(payload).sort().forEach(k=>sorted[k]=payload[k]);return JSON.stringify(sorted).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
  async function acknowledge(storage,eventRef,receipt){
    try{
      const before=JSON.parse(storage.read()),initial=context(before),entry=initial.queue.find(e=>e.eventRef===eventRef);
      if(!entry)fail('unknown_receipt');
      const hashed=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(entry.payload)));
      const digest=Array.from(new Uint8Array(hashed),b=>b.toString(16).padStart(2,'0')).join('');
      const p=entry.payload;
      if(!receipt||receipt.protocol!==1||receipt.accepted!==true||receipt.apply_local_debit!==false||receipt.status!=='arrived'||receipt.org_id!==initial.orgId||receipt.device_id!==initial.deviceId||receipt.generation!==initial.generation||receipt.event_ref!==eventRef||receipt.student_id!==p.student_id||receipt.lesson_id!==p.lesson_id||receipt.digest!==digest||receipt.attendance_revision!==p.attendance_revision+1||receipt.balance_revision!==p.balance_revision+1||receipt.balance_units!==p.remaining_units)fail('receipt_mismatch');
      // Re-read after hashing, preserving check-ins recorded in the meantime.
      const raw=storage.read(),state=JSON.parse(raw),sync=context(state),current=sync.queue.find(e=>e.eventRef===eventRef);
      if(sync.orgId!==initial.orgId||sync.deviceId!==initial.deviceId||sync.generation!==initial.generation||!current||current.wire!==entry.wire)fail('mapping_changed');
      if(current.status==='accepted')return{ok:true,duplicate:true};
      const first=sync.queue.find(q=>q.status!=='accepted');
      if(first!==current)fail('receipt_order');
      current.status='accepted';current.receipt={digest,attendanceRevision:receipt.attendance_revision,balanceRevision:receipt.balance_revision,balanceUnits:receipt.balance_units};
      // Do not set hours from an old receipt: later offline check-ins may exist.
      write(storage,raw,state);return{ok:true,duplicate:false};
    }catch(e){return{ok:false,code:e.code||'invalid_receipt'};}
  }
  function block(storage,eventRef,code){return attempt(()=>{
    if(!['attendance_changed','balance_changed','checkin_scope','delivery_not_granted','device_clock','lesson_day','idempotency_conflict'].includes(code))fail('invalid_conflict');
    const raw=storage.read(),state=JSON.parse(raw),sync=context(state),entry=sync.queue.find(e=>e.status!=='accepted');
    if(!entry||entry.eventRef!==eventRef)fail('receipt_order');entry.status='blocked';entry.error=code;write(storage,raw,state);return{ok:true};
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
        const code=status===401||status===403?'delivery_not_granted':response?.data?.error?.code;
        const blocked=block(storage,eventRef,code);
        return blocked.ok?{ok:false,code,uploaded,blocked:true}:{...blocked,uploaded};
      }
      return{ok:true,uploaded,more:true};
    }finally{flights.delete(storage);}
  }
  const api={record,next,acknowledge,block,pump};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ManagementCheckinQueue=api;
})(globalThis);
