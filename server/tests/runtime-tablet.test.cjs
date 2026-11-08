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
function faceData(items=[]){
 return{protocol:2,org_id:'org',device_id:'device',generation:'generation',snapshot_at:1100,model_version:'sface-int8bq-v1',items};
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
 for(const wrongDevice of [true,false]){
  const f=fixture(),p=structuredClone(f.data);
  if(wrongDevice)p.device.id='other';
  else {
   const state=f.store.snapshot();
   state.students.push({...state.students[0],id:3,runtimeStudentId:'student'});
   f.disk.raw=JSON.stringify(state);f.store.refresh();
  }
  const raw=f.disk.raw;
  assert.equal(bootstrap.apply(f.store,p,f.profile).ok,false);assert.equal(f.disk.raw,raw);
 }
});

test('roster refresh matches renamed records by server ID and adds a student across classes once',()=>{
 const f=fixture();assert.equal(bootstrap.apply(f.store,f.data,f.profile).ok,true);
 const data=structuredClone(f.data),student=data.lessons[0].students[0];
 student.name='改名学生';data.classes[0].name='改名班级';
 data.classes.push({id:'second-class',name:'第二班',course_name:'演示课程'});
 data.lessons.push({...data.lessons[0],id:'second-lesson',class_id:'second-class',
  students:[{...student,status:'missing',attendance_revision:0},{...student,student_id:'new',external_ref:'NEW'}]});
 for(let i=0;i<2;i++)assert.equal(bootstrap.apply(f.store,data,f.profile).ok,true);
 const state=f.store.snapshot();
 assert.equal(state.students.length,2);assert.equal(state.students[0].id,1);assert.equal(state.students[0].name,'改名学生');
 assert.equal(state.students[0].face,'KEEP');assert.equal(state.classes[0].id,2);assert.equal(state.classes[0].name,'改名班级');
 assert.equal(queue.record(f.store,{...f.input,lessonId:'second-lesson'},f.starts).ok,true);
 assert.equal(f.store.snapshot().attendance[0].className,'第二班');
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
 assert.equal(bootstrap.rebind(f.store,null,profile).awaitingSync,true);
 const invalid=structuredClone(data);invalid.device.id='other';
 assert.equal(bootstrap.apply(f.store,invalid,profile).code,'invalid_bootstrap');
 let state=f.store.snapshot();assert.equal(state.managementSync.generation,'generation-2');assert.equal(state.managementSync.rebindPending,true);assert.equal(state.students[0].face,'KEEP');
 assert.equal(bootstrap.apply(f.store,data,profile).ok,true);
 state=f.store.snapshot();assert.equal('rebindPending' in state.managementSync,false);assert.equal(state.transactions[0].reason,'保留本机历史');
});

test('rebind keeps pending and blocked queues and defers the roster refresh',()=>{
 for(const status of ['pending','blocked']){
  const f=fixture();queue.record(f.store,f.input,f.starts);if(status==='blocked')queue.block(f.store,'local-one','attendance_changed');
  const before=f.store.snapshot(),profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
  assert.equal(bootstrap.rebind(f.store,data,profile).awaitingSync,true);
  const after=f.store.snapshot();
  assert.equal(after.managementSync.generation,'generation-2');assert.equal(after.managementSync.rebindPending,true);
  assert.deepEqual(after.managementSync.queue,before.managementSync.queue);
  assert.deepEqual(after.transactions,before.transactions);assert.equal(after.students[0].hours,9);
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
 const request=async(kind,body)=>{order.push(kind);if(kind==='checkin')return{ok:true,status:201,data:receipt(f)};if(kind==='bootstrap')return{ok:true,status:200,data:f.data};if(kind==='faces')return{ok:true,status:200,data:faceData()};throw Error('unexpected route');};
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();
 assert.equal(result.ok,true);assert.deepEqual(order,['checkin','bootstrap','faces']);assert.equal(f.store.snapshot().students[0].hours,9);
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

test('a completely empty tablet receives its first roster without inventing face biometrics',()=>{
 const f=fixture(),state=f.store.snapshot();
 state.students=[];state.classes=[];state.attendance=[];state.transactions=[];state.settings.studentLeaves=[];
 state.managementSync={enabled:false};state.deviceSync={captureEnabled:false};f.disk.raw=JSON.stringify(state);f.store.refresh();
 const result=bootstrap.initialize(f.store,f.data,f.profile);assert.equal(result.ok,true);
 const next=f.store.snapshot();assert.equal(next.students.length,1);assert.equal(next.classes.length,1);
 assert.equal(next.students[0].name,'演示学生');assert.equal(next.students[0].studentNo,'TEST');assert.equal(next.students[0].className,'演示班');
 assert.equal(next.students[0].hours,9);assert.equal(next.managementSync.students.student.balanceUnits,900);assert.equal(next.managementSync.students.student.balanceRevision,1);
 assert.equal(next.students[0].faceRegistered,false);assert.deepEqual(next.students[0].sfaceDescriptors,[]);assert.deepEqual(next.students[0].sfaceAdaptiveDescriptors,[]);assert.equal('face' in next.students[0],false);
});

test('two different lessons on one day each check in once, while the same lesson stays idempotent',()=>{
 const f=fixture(),state=f.store.snapshot();state.transactions=[];
 state.managementSync.lessons['lesson-2']={...state.managementSync.lessons.lesson};
 state.managementSync.facts['lesson-2']={student:{expected:true,status:'missing',revision:0,chargeUnits:100}};
 f.disk.raw=JSON.stringify(state);f.store.refresh();
 assert.equal(queue.record(f.store,f.input,f.starts).ok,true);
 assert.equal(queue.record(f.store,{...f.input,lessonId:'lesson-2',eventRef:'local-two',occurredAt:f.starts+3600},f.starts+3600).ok,true);
 const duplicate=queue.record(f.store,{...f.input,eventRef:'local-three'},f.starts);
 assert.equal(duplicate.ok,false);assert.equal(duplicate.code,'attendance_changed');
 const next=f.store.snapshot();assert.equal(next.attendance.length,2);assert.equal(next.transactions.length,2);assert.equal(next.students[0].hours,8);
 assert.deepEqual(next.attendance.map(row=>row.deviceLessonId),['lesson','lesson-2']);assert.equal(next.managementSync.queue.length,2);
});

test('a lost acknowledgement followed by restart retries the exact wire and debits only once',async()=>{
 const f=fixture();assert.equal(queue.record(f.store,f.input,f.starts).ok,true);const wires=[];let serverAccepted=false;
 const first=await queue.pump(f.store,async body=>{wires.push(JSON.stringify(body));serverAccepted=true;return{ok:false,status:0};});
 assert.equal(first.ok,false);assert.equal(first.retry,true);assert.equal(serverAccepted,true);assert.equal(f.store.snapshot().managementSync.queue[0].status,'pending');
 const restarted=snapshots.create({storage:f.disk,key:'test'}),withRestart={...f,store:restarted};
 const second=await queue.pump(restarted,async body=>{wires.push(JSON.stringify(body));return{ok:true,status:200,data:receipt(withRestart)};});
 assert.equal(second.ok,true);assert.equal(second.uploaded,1);assert.equal(wires[1],wires[0]);
 const next=restarted.snapshot();assert.equal(next.attendance.length,1);assert.equal(next.transactions.length,2);assert.equal(next.students[0].hours,9);assert.equal(next.managementSync.queue[0].status,'accepted');
});

test('cancelled lessons and web balance edits preserve evidence and prevent snapshot overwrite',async()=>{
 for(const code of ['lesson_unavailable','balance_changed']){
  const f=fixture();queue.record(f.store,f.input,f.starts);const before=f.disk.raw;let downloaded=false;
  const result=await runtime.create({store:f.store,profile:()=>f.profile,request:async kind=>{if(kind==='bootstrap'){downloaded=true;return{ok:true,data:f.data};}return{ok:false,status:409,data:{error:{code}}};}}).tick();
  const next=f.store.snapshot();assert.equal(result.blocked,true);assert.equal(downloaded,false);assert.equal(next.students[0].hours,9);
  assert.equal(next.attendance.length,1);assert.equal(next.transactions.length,2);assert.equal(next.managementSync.queue[0].status,'blocked');assert.equal(next.managementSync.queue[0].error,code);
  const blockedRaw=f.disk.raw;assert.equal(bootstrap.apply(f.store,f.data,f.profile).code,'offline_queue');assert.equal(f.disk.raw,blockedRaw);
 }
});

function resolution(f,status='arrived'){
 const entry=f.store.snapshot().managementSync.queue[0],p=entry.payload;
 return{protocol:2,org_id:'org',device_id:'device',generation:'generation',accepted:true,resolved:true,duplicate:false,
  event_id:'web-event-1',event_ref:entry.eventRef,student_id:p.student_id,lesson_id:p.lesson_id,status,apply_local_debit:false,
  attendance_revision:p.attendance_revision+1,balance_revision:p.balance_revision+1,balance_units:status==='leave'?p.previous_balance_units:p.remaining_units};
}

test('web arrived and leave resolutions stop upload, preserve evidence and resume only bootstrap',async()=>{
 for(const status of ['arrived','leave']){
  const f=fixture();assert.equal(queue.record(f.store,f.input,f.starts).ok,true);assert.equal(queue.block(f.store,'local-one','attendance_conflict').ok,true);
  const result=queue.resolveConflict(f.store,'local-one',resolution(f,status));assert.equal(result.ok,true);assert.equal(result.duplicate,false);assert.equal(result.status,status);
  let state=f.store.snapshot();assert.equal(state.managementSync.queue[0].status,'resolved');assert.equal(state.managementSync.queue[0].receipt.eventId,'web-event-1');
  assert.equal(state.attendance[0].syncResolution,'web_resolved');assert.equal(state.attendance[0].serverStatus,status);assert.equal(state.attendance[0].serverEventId,'web-event-1');
  assert.equal(state.students[0].hours,9);assert.equal(state.attendance.length,1);assert.equal(state.transactions.length,2);
  assert.equal(queue.resolveConflict(f.store,'local-one',resolution(f,status)).duplicate,true);assert.equal(queue.next(f.store).item,null);assert.equal(queue.next(f.store).blocked,false);
  f.data.lessons[0].students[0].status=status;f.data.lessons[0].students[0].balance_units=status==='leave'?1000:900;
  const order=[];const tick=await runtime.create({store:f.store,profile:()=>f.profile,request:async kind=>{order.push(kind);return{ok:true,status:200,data:kind==='bootstrap'?f.data:faceData()};}}).tick();
  assert.equal(tick.ok,true);assert.deepEqual(order,['bootstrap','faces']);state=f.store.snapshot();assert.equal(state.students[0].hours,status==='leave'?10:9);
  assert.equal(state.managementSync.facts.lesson.student.status,status);assert.equal(state.attendance.length,1);assert.equal(state.transactions.length,2);
  assert.equal(state.attendance[0].syncResolution,'web_resolved');assert.equal(state.managementSync.queue[0].status,'resolved');
 }
});

test('resolution rejects altered or incomplete receipts atomically, including duplicates',()=>{
 const deltas=[{accepted:false},{resolved:false},{apply_local_debit:true},{org_id:'other'},{device_id:'other'},{generation:'other'},
  {event_ref:'other'},{student_id:'other'},{lesson_id:'other'},{status:'missing'},{event_id:''},{attendance_revision:0},{balance_revision:-1},{balance_units:'900'}];
 for(const delta of deltas){
  const f=fixture();queue.record(f.store,f.input,f.starts);queue.block(f.store,'local-one','attendance_changed');const raw=f.disk.raw;
  assert.equal(queue.resolveConflict(f.store,'local-one',{...resolution(f),...delta}).code,'receipt_mismatch');assert.equal(f.disk.raw,raw);
 }
 const f=fixture();queue.record(f.store,f.input,f.starts);queue.block(f.store,'local-one','attendance_changed');
 assert.equal(queue.resolveConflict(f.store,'local-one',resolution(f)).ok,true);const raw=f.disk.raw;
 assert.equal(queue.resolveConflict(f.store,'local-one',{...resolution(f),accepted:false}).code,'receipt_mismatch');assert.equal(f.disk.raw,raw);
});

test('resolution never accepts pending entries, unknown references or legacy protocol',()=>{
 const f=fixture();queue.record(f.store,f.input,f.starts);const raw=f.disk.raw;
 assert.equal(queue.resolveConflict(f.store,'local-one',resolution(f)).code,'invalid_conflict');assert.equal(f.disk.raw,raw);
 assert.equal(queue.resolveConflict(f.store,'unknown',resolution(f)).code,'invalid_conflict');assert.equal(f.disk.raw,raw);
 queue.block(f.store,'local-one','attendance_changed');const state=f.store.snapshot();state.managementSync.protocol=1;f.disk.raw=JSON.stringify(state);f.store.refresh();
 const legacyRaw=f.disk.raw;assert.equal(queue.resolveConflict(f.store,'local-one',resolution(f)).code,'invalid_conflict');assert.equal(f.disk.raw,legacyRaw);
});

test('safe rebind preserves a blocked conflict until it is resolved with the new credential',()=>{
 const f=fixture();queue.record(f.store,f.input,f.starts);queue.block(f.store,'local-one','attendance_changed');
 const profile={...f.profile,generation:'generation-2',profileId:'profile-2'},data=structuredClone(f.data);data.device.generation=profile.generation;
 assert.equal(bootstrap.rebind(f.store,data,profile).awaitingSync,true);
 assert.equal(queue.resolveConflict(f.store,'local-one',resolution(f)).code,'receipt_mismatch');
 assert.equal(queue.resolveConflict(f.store,'local-one',{...resolution(f),generation:profile.generation}).ok,true);
 const result=bootstrap.apply(f.store,data,profile);assert.equal(result.ok,true);const state=f.store.snapshot();
 assert.equal(state.managementSync.generation,'generation-2');assert.equal(state.managementSync.queue[0].status,'resolved');
 assert.equal(state.attendance[0].serverEventId,'web-event-1');assert.equal(state.transactions.length,2);
});
