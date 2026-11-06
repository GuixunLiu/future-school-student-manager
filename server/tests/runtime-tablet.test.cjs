const {test}=require('node:test');
const assert=require('node:assert/strict');
const queue=require('../management/tablet/checkin-queue.js');
const bootstrap=require('../management/tablet/runtime-bootstrap.js');
const runtime=require('../management/tablet/sync-runtime.js');
const snapshots=require('../management/tablet/snapshot-store.js');
function fixture(){
 const starts=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 const state={students:[{id:1,name:'演示学生',studentNo:'TEST',className:'演示班',hours:10,face:'KEEP'}],
  classes:[{id:2,name:'演示班',teacher:'演示教师'}],attendance:[],transactions:[{id:7,reason:'保留本机历史'}],settings:{studentLeaves:[]},
  managementSync:{protocol:2,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile',queue:[],receipts:{},
   students:{student:{localId:1,localClassId:2,name:'演示学生',studentNo:'TEST',className:'演示班',classId:'class',teacher:'演示教师',balanceUnits:1000,balanceRevision:0}},
   lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:starts,endsAt:starts+7200,state:'scheduled',studentIds:['student']}},
   facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}};
 const store=snapshots.create({storage:disk,key:'test'});
 const profile={orgId:'org',deviceId:'device',generation:'generation',profileId:'profile'};
 const input={studentId:'student',lessonId:'lesson',occurredAt:starts,eventRef:'local-one'};
 const data={device:{id:'device',name:'签到平板',generation:'generation'},snapshot_at:starts+100,
  classes:[{id:'class',name:'演示班',course_name:'演示课程'}],lessons:[{id:'lesson',class_id:'class',starts_at:starts,ends_at:starts+7200,state:'scheduled',students:[{student_id:'student',name:'演示学生',external_ref:'TEST',expected:true,charge_units:100,status:'arrived',attendance_revision:1,balance_units:900,balance_revision:1}]}]};
 return{store,disk,profile,input,starts,data};
}
function receipt(f,index=0){
 const entry=f.store.snapshot().managementSync.queue[index],p=entry.payload;
 return{protocol:2,org_id:'org',device_id:'device',generation:'generation',accepted:true,event_ref:entry.eventRef,student_id:p.student_id,lesson_id:p.lesson_id,status:'arrived',apply_local_debit:false,attendance_revision:p.attendance_revision+1,balance_revision:p.balance_revision+1,balance_units:p.remaining_units};
}

test('protocol 2 wire omits legacy generation and client-computed remaining balance',()=>{
 const f=fixture();assert.equal(queue.record(f.store,f.input,f.starts).ok,true);
 const wire=JSON.parse(queue.next(f.store).item.wire);
 assert.equal(wire.event_ref,'local-one');assert.equal(wire.previous_balance_units,1000);assert.equal(wire.consumed_units,100);
 assert.equal('generation' in wire,false);assert.equal('remaining_units' in wire,false);
});

test('protocol 2 acknowledgement and duplicate receipt never debit twice',async()=>{
 const f=fixture();queue.record(f.store,f.input,f.starts);const r=receipt(f);
 assert.equal((await queue.acknowledge(f.store,'local-one',r)).ok,true);
 assert.equal((await queue.acknowledge(f.store,'local-one',r)).duplicate,true);
 assert.equal(f.store.snapshot().students[0].hours,9);assert.equal(f.store.snapshot().transactions.length,2);
});

test('wrong device, generation, balance or legacy digest-only receipt is rejected',async()=>{
 for(const delta of [{device_id:'other'},{generation:'other'},{balance_units:800},{protocol:1,digest:'0'.repeat(64)}]){
  const f=fixture();queue.record(f.store,f.input,f.starts);const raw=f.disk.raw;
  assert.equal((await queue.acknowledge(f.store,'local-one',{...receipt(f),...delta})).code,'receipt_mismatch');
  assert.equal(f.disk.raw,raw);
 }
});

test('bootstrap refuses to overwrite pending or blocked offline check-ins',()=>{
 for(const status of ['pending','blocked']){
  const f=fixture();queue.record(f.store,f.input,f.starts);const raw=f.disk.raw;
  if(status==='blocked')queue.block(f.store,'local-one','attendance_changed');
  const before=f.disk.raw;assert.equal(bootstrap.apply(f.store,f.data,f.profile).code,'offline_queue');assert.equal(f.disk.raw,before);assert.equal(f.store.snapshot().students[0].hours,9);assert.ok(raw);
 }
});

test('bootstrap atomically refreshes versions and balances while retaining local private data',()=>{
 const f=fixture();f.data.lessons[0].students[0].balance_units=850;f.data.lessons[0].students[0].balance_revision=2;
 const result=bootstrap.apply(f.store,f.data,f.profile);assert.equal(result.ok,true);
 const state=f.store.snapshot();assert.equal(state.students[0].hours,8.5);assert.equal(state.students[0].face,'KEEP');
 assert.equal(state.transactions[0].reason,'保留本机历史');assert.equal(state.managementSync.students.student.balanceRevision,2);
 assert.equal(state.managementSync.facts.lesson.student.revision,1);assert.equal(state.managementSync.lessons.lesson.state,'scheduled');
});

test('wrong device snapshot and ambiguous student mapping cannot change local state',()=>{
 for(const mutate of [p=>p.device.id='other',p=>p.lessons[0].students[0].external_ref='UNKNOWN']){
  const f=fixture(),raw=f.disk.raw,p=structuredClone(f.data);mutate(p);
  assert.equal(bootstrap.apply(f.store,p,f.profile).ok,false);assert.equal(f.disk.raw,raw);
 }
});

test('local check-in racing bootstrap CAS is preserved for the next upload',()=>{
 const f=fixture(),cas=f.store.compareAndSwap.bind(f.store);let raced=false;
 f.store.compareAndSwap=(before,after)=>{if(!raced){raced=true;assert.equal(queue.record(f.store,f.input,f.starts).ok,true);}return cas(before,after);};
 assert.equal(bootstrap.apply(f.store,f.data,f.profile).code,'local_changes');
 assert.equal(f.store.snapshot().students[0].hours,9);assert.equal(f.store.snapshot().managementSync.queue[0].status,'pending');
});

test('rebind updates only the native profile mapping and preserves local history and face data',()=>{
 const f=fixture(),profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);
 data.device.generation=profile.generation;
 const result=bootstrap.rebind(f.store,data,profile);assert.equal(result.ok,true);
 const state=f.store.snapshot();assert.equal(state.managementSync.generation,'generation-2');assert.equal(state.managementSync.profileId,'profile-2');
 assert.equal(state.students[0].face,'KEEP');assert.equal(state.transactions[0].reason,'保留本机历史');assert.equal(state.students[0].hours,9);
});

test('an interrupted rebind is retryable with the already saved native profile',()=>{
 const f=fixture(),profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
 data.lessons[0].students[0].external_ref='UNKNOWN';assert.equal(bootstrap.rebind(f.store,data,profile).code,'mapping_changed');
 let state=f.store.snapshot();assert.equal(state.managementSync.generation,'generation-2');assert.equal(state.managementSync.rebindPending,true);assert.equal(state.students[0].face,'KEEP');
 data.lessons[0].students[0].external_ref='TEST';assert.equal(bootstrap.rebind(f.store,data,profile).ok,true);
 state=f.store.snapshot();assert.equal('rebindPending' in state.managementSync,false);assert.equal(state.transactions[0].reason,'保留本机历史');
});

test('rebind refuses pending or blocked queues without changing the old profile',()=>{
 for(const status of ['pending','blocked']){
  const f=fixture();queue.record(f.store,f.input,f.starts);if(status==='blocked')queue.block(f.store,'local-one','attendance_changed');
  const raw=f.disk.raw,profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
  assert.equal(bootstrap.rebind(f.store,data,profile).code,'offline_queue');assert.equal(f.disk.raw,raw);
  assert.equal(f.store.snapshot().managementSync.generation,'generation');assert.equal(f.store.snapshot().students[0].hours,9);
 }
});

test('rebind refuses another organization or device without replacing local state',()=>{
 for(const delta of [{orgId:'other'},{deviceId:'other'}]){
  const f=fixture(),raw=f.disk.raw,profile={...f.profile,...delta,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
  assert.equal(bootstrap.rebind(f.store,data,profile).code,'profile_changed');assert.equal(f.disk.raw,raw);
 }
});

test('a local check-in racing rebind CAS remains pending and is never erased',()=>{
 const f=fixture(),profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
 const cas=f.store.compareAndSwap.bind(f.store);let raced=false;
 f.store.compareAndSwap=(before,after)=>{if(!raced){raced=true;assert.equal(queue.record(f.store,f.input,f.starts).ok,true);}return cas(before,after);};
 assert.equal(bootstrap.rebind(f.store,data,profile).code,'local_changes');
 const state=f.store.snapshot();assert.equal(state.managementSync.queue[0].status,'pending');assert.equal(state.managementSync.generation,'generation');assert.equal(state.students[0].hours,9);
});

test('runtime uploads protocol 2 check-in before bootstrap and preserves one debit',async()=>{
 const f=fixture();queue.record(f.store,f.input,f.starts);const order=[];
 const request=async(kind,body)=>{order.push(kind);if(kind==='checkin')return{ok:true,status:201,data:receipt(f)};if(kind==='bootstrap')return{ok:true,status:200,data:f.data};throw Error('unexpected route');};
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();
 assert.equal(result.ok,true);assert.deepEqual(order,['checkin','bootstrap']);assert.equal(f.store.snapshot().students[0].hours,9);
 assert.equal(f.store.snapshot().managementSync.queue[0].status,'accepted');
});

test('first activation initializes protocol 2 after the legacy writer is stopped',()=>{
 const f=fixture(),state=f.store.snapshot();state.managementSync={enabled:false};state.deviceSync={captureEnabled:false};
 f.disk.raw=JSON.stringify(state);f.store.refresh();
 const result=bootstrap.initialize(f.store,f.data,f.profile);assert.equal(result.ok,true);
 const next=f.store.snapshot();assert.equal(next.managementSync.protocol,2);assert.equal(next.managementSync.enabled,true);
 assert.equal(next.deviceSync.captureEnabled,false);assert.equal(next.students[0].hours,9);assert.equal(next.students[0].face,'KEEP');
});

test('blocked conflict stops bootstrap so server cannot erase its local evidence',async()=>{
 const f=fixture();queue.record(f.store,f.input,f.starts);let downloaded=false;
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request:async kind=>{if(kind==='bootstrap'){downloaded=true;return{ok:true,data:f.data};}return{ok:false,status:409,data:{error:{code:'lesson_unavailable'}}};}}).tick();
 assert.equal(result.blocked,true);assert.equal(downloaded,false);assert.equal(f.store.snapshot().students[0].hours,9);
});
