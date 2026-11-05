const {test}=require('node:test');
const assert=require('node:assert/strict');
const {webcrypto,createHash}=require('node:crypto');
globalThis.crypto ??= webcrypto;
const queue=require('../management/tablet/checkin-queue.js');
function fixture(){
 const now=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 const state={students:[{id:1,name:'虚构学生',studentNo:'TEST',className:'演示班',hours:10,face:'KEEP'}],
  classes:[{id:2,name:'演示班',teacher:'演示教师',durationMinutes:120}],attendance:[],transactions:[],settings:{studentLeaves:[]},
  managementSync:{protocol:1,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,orgId:'org',deviceId:'device',generation:'generation',queue:[],receipts:{},
   students:{student:{localId:1,localClassId:2,name:'虚构学生',studentNo:'TEST',className:'演示班',classId:'class',teacher:'演示教师',balanceUnits:1000,balanceRevision:0}},
   lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:now,studentIds:['student']}},
   facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const storage={raw:JSON.stringify(state),writes:0,read(){return this.raw;},compareAndSwap(old,next){if(old!==this.raw)return false;this.raw=next;this.writes++;return true;}};
 const input={studentId:'student',lessonId:'lesson',occurredAt:now,eventRef:'local-1'};
 return{storage,input,now};
}
function edit(f,fn){const s=JSON.parse(f.storage.raw);fn(s);f.storage.raw=JSON.stringify(s);}
function receipt(f,index=0){const e=JSON.parse(f.storage.raw).managementSync.queue[index],p=e.payload;
 const sorted={};Object.keys(p).sort().forEach(k=>sorted[k]=p[k]);const wire=JSON.stringify(sorted).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
 return{protocol:1,org_id:'org',device_id:'device',generation:'generation',event_ref:e.eventRef,student_id:p.student_id,lesson_id:p.lesson_id,
 digest:createHash('sha256').update(wire).digest('hex'),accepted:true,status:'arrived',apply_local_debit:false,
 attendance_revision:p.attendance_revision+1,balance_revision:p.balance_revision+1,balance_units:p.remaining_units};}

test('migration hold preserves pending wire and resumes without a second local debit',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);const raw=f.storage.raw,wire=queue.next(f.storage).item.wire;
 const paused=await queue.pump(f.storage,async()=>({ok:false,status:409,data:{error:{code:'migration_in_progress'}}}));
 assert.equal(paused.paused,true);assert.equal(paused.retry,true);assert.equal(f.storage.raw,raw);assert.equal(queue.next(f.storage).item.wire,wire);
 const resumed=await queue.pump(f.storage,async payload=>{assert.equal(JSON.stringify(payload),wire);return{ok:true,data:receipt(f)};});
 assert.equal(resumed.ok,true);assert.equal(resumed.uploaded,1);assert.equal(JSON.parse(f.storage.raw).students[0].hours,9);
});
test('check-in, debit and queue persist together; same ref retry is free',()=>{
 const f=fixture();assert.equal(queue.record(f.storage,f.input,f.now).ok,true);
 assert.equal(queue.record(f.storage,{eventRef:'local-1',occurredAt:f.now,lessonId:'lesson',studentId:'student'},f.now).duplicate,true);
 const s=JSON.parse(f.storage.raw);assert.equal(s.students[0].hours,9);assert.equal(s.students[0].face,'KEEP');
 assert.equal(s.attendance.length,1);assert.equal(s.transactions[0].amount,-1);assert.equal(s.managementSync.queue.length,1);assert.equal(f.storage.writes,1);
 const wire=queue.next(f.storage).item.wire;assert.equal(JSON.parse(wire).consumed_units,100); // independent of 120 minutes
 assert.equal(queue.next(f.storage).item.wire,wire);
});
test('save failure or CAS collision leaves the complete snapshot unchanged',()=>{
 for(const throws of [false,true]){const f=fixture(),raw=f.storage.raw;f.storage.compareAndSwap=()=>{if(throws)throw Error('disk full');return false;};
 assert.deepEqual(queue.record(f.storage,f.input,f.now),{ok:false,code:'storage_failed'});assert.equal(f.storage.raw,raw);}
});
test('lost upload receipt, restart and duplicate acknowledgement never debit again',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);const r=receipt(f),wire=queue.next(f.storage).item.wire;
 assert.equal(queue.next(f.storage).item.wire,wire);assert.equal((await queue.acknowledge(f.storage,'local-1',r)).ok,true);
 assert.equal((await queue.acknowledge(f.storage,'local-1',r)).duplicate,true);assert.equal(queue.next(f.storage).item,null);
 assert.equal(JSON.parse(f.storage.raw).students[0].hours,9);
});
test('older receipt preserves newer offline check-in; queue uploads strictly in order',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);
 edit(f,s=>{s.managementSync.lessons.next={...s.managementSync.lessons.lesson,date:'2026-11-08',startsAt:f.now+604800};s.managementSync.facts.next={student:{expected:true,status:'missing',revision:0,chargeUnits:100}};});
 queue.record(f.storage,{...f.input,lessonId:'next',eventRef:'local-2',occurredAt:f.now+604800},f.now+604800);
 const later=receipt(f,1);assert.equal((await queue.acknowledge(f.storage,'local-2',later)).code,'receipt_order');
 assert.equal((await queue.acknowledge(f.storage,'local-1',receipt(f))).ok,true);
 assert.equal(JSON.parse(f.storage.raw).students[0].hours,8);assert.equal(queue.next(f.storage).item.eventRef,'local-2');
 assert.equal((await queue.acknowledge(f.storage,'local-2',later)).ok,true);assert.equal(JSON.parse(f.storage.raw).transactions.length,2);
});
test('mismatched or missing receipt is not success; full disk retains pending event',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);const r=receipt(f),raw=f.storage.raw;
 for(const delta of [{balance_units:1000},{generation:'other'},{apply_local_debit:true},{digest:'0'.repeat(64)},{student_id:'other'},{accepted:false}]){
 assert.equal((await queue.acknowledge(f.storage,'local-1',{...r,...delta})).code,'receipt_mismatch');assert.equal(f.storage.raw,raw);}
 f.storage.compareAndSwap=()=>false;assert.equal((await queue.acknowledge(f.storage,'local-1',r)).code,'storage_failed');assert.equal(f.storage.raw,raw);
});
test('server conflict preserves local attendance and debit for review',()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);assert.equal(queue.block(f.storage,'local-1','attendance_changed').ok,true);
 assert.deepEqual(queue.next(f.storage),{ok:true,item:null,blocked:true});const s=JSON.parse(f.storage.raw);
 assert.equal(s.students[0].hours,9);assert.equal(s.attendance.length,1);assert.equal(s.managementSync.queue[0].status,'blocked');
});
test('acknowledged history cannot exhaust the live queue',()=>{
 const f=fixture();
 edit(f,s=>{s.managementSync.queue=Array.from({length:10000},(_,i)=>({status:'accepted',eventRef:'old-'+i}));
   s.attendance.push({id:3,studentId:999,date:'2026-01-01',deviceEventRef:'old-0'});});
 assert.equal(queue.record(f.storage,f.input,f.now).ok,true);
 assert.equal(JSON.parse(f.storage.raw).managementSync.queue.length,129);
 assert.equal(queue.record(f.storage,{...f.input,eventRef:'old-0'},f.now).code,'idempotency_conflict');
});
test('a real conflict isolates the student while authorization can recover on retry',async()=>{
 const f=fixture();
 queue.record(f.storage,f.input,f.now);
 edit(f,s=>{
   const sync=s.managementSync;
   sync.queue.push({status:'pending',eventRef:'later',payload:{student_id:'student'}});
   const payload={...sync.queue[0].payload,student_id:'other'};
   sync.queue.push({status:'pending',eventRef:'other',payload,wire:JSON.stringify({generation:'generation',event_ref:'other',...payload})});
 });
 assert.equal((await queue.pump(f.storage,async()=>({ok:false,status:403,data:{error:{code:'delivery_not_granted'}}}))).retry,true);
 assert.equal(queue.next(f.storage).item.eventRef,'local-1');
 const sent=[];
 const result=await queue.pump(f.storage,async body=>{
   sent.push(body.event_ref);
   return body.event_ref==='local-1'?{ok:false,status:409,data:{error:{code:'attendance_changed'}}}:{ok:true,data:receipt(f,2)};
 });
 assert.deepEqual(sent,['local-1','other']);
 assert.equal(result.blocked,true);
 assert.equal(result.uploaded,1);
 assert.deepEqual(JSON.parse(f.storage.raw).managementSync.queue.map(e=>e.status),['blocked','pending','accepted']);
 const before=JSON.parse(f.storage.raw);
 assert.equal(queue.retryConflict(f.storage,'local-1').ok,true);
 assert.equal(queue.next(f.storage).item.wire,before.managementSync.queue[0].wire);
 assert.equal(JSON.parse(f.storage.raw).students[0].hours,before.students[0].hours);
});
const guards=[['wrong balance',s=>s.students[0].hours=11,'balance_changed'],['renamed student',s=>s.students[0].name='其他人','mapping_changed'],
 ['changed teacher',s=>s.classes[0].teacher='另一教师','mapping_changed'],['legacy writer',s=>s.deviceSync={captureEnabled:true},'legacy_writer_active'],
 ['leave',s=>s.settings.studentLeaves.push({id:3,studentId:1,date:'2026-11-01',status:'approved'}),'attendance_changed'],
 ['not expected',s=>s.managementSync.facts.lesson.student.expected=false,'attendance_changed']];
for(const [name,fn,code] of guards)test(name+' refuses capture without altering data',()=>{const f=fixture();edit(f,fn);const raw=f.storage.raw;assert.deepEqual(queue.record(f.storage,f.input,f.now),{ok:false,code});assert.equal(f.storage.raw,raw);});
test('changed retry reference, future clock, wrong day and corrupt wire refuse silently overwriting',()=>{
 const f=fixture();assert.equal(queue.record(f.storage,{...f.input,occurredAt:f.now+121},f.now).code,'invalid_checkin');
 assert.equal(queue.record(f.storage,{...f.input,occurredAt:f.now-86400},f.now).code,'lesson_day');
 queue.record(f.storage,f.input,f.now);const raw=f.storage.raw;
 assert.equal(queue.record(f.storage,{...f.input,occurredAt:f.now+1},f.now).code,'idempotency_conflict');assert.equal(f.storage.raw,raw);
 edit(f,s=>s.managementSync.queue[0].wire='{}');assert.equal(queue.next(f.storage).code,'invalid_queue');
});

test('upload pump reuses fixed payload after network failure and lost receipt',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);const sent=[];
 const first=await queue.pump(f.storage,async body=>{sent.push(JSON.stringify(body));throw Error('offline');});
 assert.equal(first.retry,true);assert.equal(JSON.parse(f.storage.raw).managementSync.queue[0].status,'pending');
 const r=receipt(f);
 const second=await queue.pump(f.storage,async body=>{sent.push(JSON.stringify(body));return{ok:true,data:r};});
 assert.equal(second.uploaded,1);assert.equal(sent[0],sent[1]);assert.equal(JSON.parse(f.storage.raw).students[0].hours,9);
});
test('upload pump guards concurrent calls and pauses on server conflict',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);let release;
 const running=queue.pump(f.storage,()=>new Promise(r=>release=r));
 assert.equal((await queue.pump(f.storage,()=>{throw Error('must not run');})).busy,true);
 release({ok:false,status:409,data:{error:{code:'attendance_changed'}}});
 const result=await running;assert.equal(result.blocked,true);assert.equal(queue.next(f.storage).blocked,true);
 assert.equal(JSON.parse(f.storage.raw).students[0].hours,9);
});
test('retryable server failure does not block or delete event; bad success cannot advance',async()=>{
 const f=fixture();queue.record(f.storage,f.input,f.now);
 for(const status of [429,500,503])assert.equal((await queue.pump(f.storage,async()=>({ok:false,status}))).retry,true);
 assert.ok(queue.next(f.storage).item);
 const result=await queue.pump(f.storage,async()=>({ok:true,data:{accepted:true}}));
 assert.equal(result.code,'receipt_mismatch');assert.ok(queue.next(f.storage).item);
});
