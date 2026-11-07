const {test}=require('node:test');const assert=require('node:assert/strict');
const {createHash,webcrypto}=require('node:crypto');globalThis.crypto??=webcrypto;
const snapshots=require('../management/tablet/snapshot-store.js'),runtime=require('../management/tablet/sync-runtime.js'),queue=require('../management/tablet/checkin-queue.js');
function fixture(){
 const starts=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 const state={students:[{id:1,name:'演示学生',studentNo:'TEST',className:'演示班',hours:10}],classes:[{id:2,name:'演示班',teacher:'演示教师'}],
 attendance:[],transactions:[],settings:{studentLeaves:[]},managementSync:{protocol:1,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
 orgId:'org',deviceId:'device',generation:'generation',profileId:'native-profile',receipts:{},queue:[],
 students:{student:{localId:1,localClassId:2,name:'演示学生',studentNo:'TEST',className:'演示班',classId:'class',teacher:'演示教师',balanceUnits:1000,balanceRevision:0}},
 lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:starts,studentIds:['student']}},facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}};
 const store=snapshots.create({storage:disk,key:'test'});
 const profile={orgId:'org',deviceId:'device',generation:'generation',profileId:'native-profile'};
 const event={protocol:1,org_id:'org',event_id:'server-event',request_id:'request',student_id:'student',lesson_id:'lesson',class_id:'class',starts_at:starts,charge_units:100,
 expected:true,status:'arrived',previous_attendance_revision:0,attendance_revision:1,previous_balance_revision:0,balance_revision:1,previous_balance_units:1000,balance_units:900,balance_delta:-100,
 reason:'演示审批',decided_by:'admin',decided_by_name:'演示管理员',effective_at:starts+3600};
 const wire=JSON.stringify(event),envelope={org_id:'org',device_id:'device',generation:'generation',item:{event_json:wire,sha256:createHash('sha256').update(wire).digest('hex'),receipt_token:'a'.repeat(32)}};
 return{store,disk,profile,envelope,starts,empty:{...envelope,item:null}};
}

test('new historical receiver advertises the exact profile before polling; old server 404 remains compatible',async()=>{
 for(const status of [200,404]){
  const f=fixture(),order=[];const request=async(kind,body)=>{order.push(kind);assert.equal(body.generation,'generation');if(kind==='history-ready')return status===404?{ok:false,status:404}:{ok:true,status:200,data:{org_id:'org',device_id:'device',generation:'generation',historical_protocol:1}};if(kind==='next')return{ok:true,data:f.empty};throw Error('unexpected');};
  assert.equal((await runtime.create({store:f.store,profile:()=>f.profile,request,historical:true}).tick()).ok,true);assert.deepEqual(order,['history-ready','next']);
 }
 const f=fixture();let polled=false;const result=await runtime.create({store:f.store,profile:()=>f.profile,historical:true,request:async kind=>{if(kind==='next')polled=true;return{ok:true,data:{org_id:'wrong'}};}}).tick();assert.equal(result.phase,'history-ready');assert.equal(polled,false);
});
test('download persists once, lost server acknowledgement retries receipt without duplicate debit',async()=>{
 const f=fixture();let lost=true,done=false,acks=0;
 const request=async(kind,body)=>{if(kind==='next')return{ok:true,data:done?f.empty:f.envelope};
   if(kind==='receipt'){acks++;if(lost){lost=false;throw Error('reply lost');}done=true;return{ok:true,data:{event_id:body.event_id,status:'applied'}};}throw Error('unexpected');};
 const first=runtime.create({store:f.store,profile:()=>f.profile,request});assert.equal((await first.tick()).ok,false);
 assert.equal(f.store.snapshot().students[0].hours,9);
 const restarted=snapshots.create({storage:f.disk,key:'test'}),second=runtime.create({store:restarted,profile:()=>f.profile,request});
 assert.equal((await second.tick()).ok,true);assert.equal(restarted.snapshot().students[0].hours,9);assert.equal(restarted.snapshot().transactions.length,1);assert.equal(acks,2);
});
test('migration pause during download keeps state intact and can retry normally',async()=>{
 const f=fixture(),raw=f.disk.raw;let paused=true;
 const sync=runtime.create({store:f.store,profile:()=>f.profile,request:async()=>paused?{ok:false,status:409,data:{error:{code:'migration_in_progress'}}}:{ok:true,data:f.empty}});
 const result=await sync.tick();assert.equal(result.paused,true);assert.equal(result.retry,true);assert.equal(f.disk.raw,raw);
 paused=false;assert.equal((await sync.tick()).ok,true);
});
test('migration pause after local apply retries acknowledgement without repeating the debit',async()=>{
 const f=fixture();let paused=true,done=false;
 const sync=runtime.create({store:f.store,profile:()=>f.profile,request:async(kind,body)=>{
   if(kind==='next')return{ok:true,data:done?f.empty:f.envelope};
   if(paused)return{ok:false,status:409,data:{error:{code:'migration_in_progress'}}};
   done=true;return{ok:true,data:{event_id:body.event_id,status:'applied'}};
 }});
 assert.equal((await sync.tick()).paused,true);assert.equal(f.store.snapshot().students[0].hours,9);
 paused=false;assert.equal((await sync.tick()).ok,true);assert.equal(f.store.snapshot().students[0].hours,9);assert.equal(f.store.snapshot().transactions.length,1);
});
test('pending local check-in uploads first; receipt never debits locally',async()=>{
 const f=fixture();queue.record(f.store,{studentId:'student',lessonId:'lesson',occurredAt:f.starts,eventRef:'local-one'},f.starts);const order=[];
 const request=async(kind,p)=>{order.push(kind);if(kind==='next')return{ok:true,data:f.empty};
 const keys=['student_id','lesson_id','attendance_revision','balance_revision','previous_balance_units','consumed_units','remaining_units','occurred_at'].sort(),payload={};keys.forEach(k=>payload[k]=p[k]);
 return{ok:true,data:{protocol:1,org_id:'org',device_id:'device',generation:'generation',event_ref:p.event_ref,student_id:p.student_id,lesson_id:p.lesson_id,
  digest:createHash('sha256').update(JSON.stringify(payload)).digest('hex'),status:'arrived',accepted:true,apply_local_debit:false,attendance_revision:1,balance_revision:1,balance_units:900}};};
 assert.equal((await runtime.create({store:f.store,profile:()=>f.profile,request}).tick()).ok,true);
 assert.deepEqual(order,['checkin','next']);assert.equal(f.store.snapshot().students[0].hours,9);assert.equal(f.store.snapshot().managementSync.queue[0].status,'accepted');
});
test('unsaved UI draft blocks synchronization and preserves its edits',async()=>{
 const f=fixture(),edit=f.store.beginEdit();edit.value.settings.displaySize=2;let calls=0;
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request:async()=>{calls++;}}).tick();
 assert.equal(result.code,'unsaved_changes');assert.equal(calls,0);assert.equal(f.store.status().editing,true);
 f.store.saveEdit(edit.token);assert.equal(f.store.snapshot().settings.displaySize,2);
});
test('native profile changes in flight prevent applying old response',async()=>{
 const f=fixture(),raw=f.disk.raw;
 const request=async()=>{f.profile.profileId='reconfigured';return{ok:true,data:f.envelope};};
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();
 assert.equal(result.code,'profile_changed');assert.equal(f.disk.raw,raw);
});
test('concurrent ticks do not create duplicate network requests',async()=>{
 const f=fixture();let release,calls=0;const sync=runtime.create({store:f.store,profile:()=>f.profile,request:()=>{calls++;return new Promise(r=>release=r);}});
 const pending=sync.tick();await new Promise(r=>setImmediate(r));assert.equal((await sync.tick()).busy,true);
 release({ok:true,data:f.empty});assert.equal((await pending).ok,true);assert.equal(calls,1);
});
test('local check-in arriving during download is retained and reported as conflict',async()=>{
 const f=fixture(),calls=[];
 const request=async(kind,body)=>{calls.push({kind,body});if(kind==='next'){
   assert.equal(queue.record(f.store,{studentId:'student',lessonId:'lesson',occurredAt:f.starts,eventRef:'racing-checkin'},f.starts).ok,true);
   return{ok:true,data:f.envelope};
 }return{ok:true,data:{event_id:body.event_id,status:'conflict'}};};
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();
 assert.equal(result.code,'local_changes');assert.equal(calls[1].kind,'receipt');assert.equal(calls[1].body.status,'conflict');
 const s=f.store.snapshot();assert.equal(s.students[0].hours,9);assert.equal(s.attendance.length,1);
 assert.equal(s.managementSync.queue[0].status,'pending');assert.equal(Object.keys(s.managementSync.receipts).length,0);
});
test('a blocked upload defers that student and receives an unrelated approval',async()=>{
 const f=fixture();
 const raw=f.store.read(),state=JSON.parse(raw),s=state.managementSync;
 s.students.other={...s.students.student,localId:3};
 s.queue.push({status:'blocked',error:'attendance_changed',payload:{student_id:'other'}});
 assert(f.store.compareAndSwap(raw,JSON.stringify(state)));
 const order=[];
 const request=async(kind,body)=>{
   order.push([kind,body.defer]);
   if(kind==='next')return{ok:true,data:order.length===1?f.envelope:f.empty};
   return{ok:true,data:{event_id:body.event_id,status:'applied'}};
 };
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();
 assert.deepEqual(order,[['next','other'],['receipt',undefined],['next','other']]);
 assert.equal(result.ok,false);assert.equal(result.blocked,true);assert.equal(result.received,1);
 assert.equal(f.store.snapshot().students[0].hours,9);
});
