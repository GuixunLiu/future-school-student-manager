const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const faces=require('../management/tablet/face-profiles.js');
const bootstrap=require('../management/tablet/runtime-bootstrap.js');
const runtime=require('../management/tablet/sync-runtime.js');
const queue=require('../management/tablet/checkin-queue.js');
const snapshots=require('../management/tablet/snapshot-store.js');

const MODEL='sface-int8bq-v1';
function frames(position=0){const frame=Array(128).fill(0);frame[position]=1;return[frame.slice(),frame.slice(),frame.slice()];}
function item(profileId='face-profile-1',studentId='student',position=0){return{profile_id:profileId,student_id:studentId,model_version:MODEL,descriptors:frames(position)};}
function faceData(items=[item()]){return{protocol:2,org_id:'org',device_id:'device',generation:'generation',snapshot_at:1100,model_version:MODEL,items};}
function fixture(){
 const starts=Date.parse('2026-11-01T14:00:00+08:00')/1000;
 const state={students:[{id:1,name:'演示学生',studentNo:'TEST',className:'演示班',hours:10,face:'KEEP',sfaceDescriptors:[],sfaceAdaptiveDescriptors:[]}],
  classes:[{id:2,name:'演示班',teacher:'演示教师'}],attendance:[],transactions:[{id:7,reason:'保留本机历史'}],settings:{studentLeaves:[]},
  managementSync:{protocol:2,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,orgId:'org',deviceId:'device',generation:'generation',profileId:'profile',queue:[],receipts:{},
   students:{student:{localId:1,localClassId:2,name:'演示学生',studentNo:'TEST',className:'演示班',classId:'class',teacher:'演示教师',balanceUnits:1000,balanceRevision:0}},
   lessons:{lesson:{classId:'class',date:'2026-11-01',startsAt:starts,endsAt:starts+7200,state:'scheduled',studentIds:['student']}},
   facts:{lesson:{student:{expected:true,status:'missing',revision:0,chargeUnits:100}}}}};
 const disk={raw:JSON.stringify(state),getItem(){return this.raw;},setItem(k,v){this.raw=v;}};
 const store=snapshots.create({storage:disk,key:'test'});
 const profile={orgId:'org',deviceId:'device',generation:'generation',profileId:'profile'};
 const data={device:{id:'device',name:'签到平板',generation:'generation'},snapshot_at:starts+100,
  classes:[{id:'class',name:'演示班',course_name:'演示课程'}],lessons:[{id:'lesson',class_id:'class',starts_at:starts,ends_at:starts+7200,state:'scheduled',students:[{student_id:'student',name:'演示学生',external_ref:'TEST',expected:true,charge_units:100,status:'arrived',attendance_revision:1,balance_units:900,balance_revision:1}]}]};
 return{store,disk,profile,starts,data,input:{studentId:'student',lessonId:'lesson',occurredAt:starts,eventRef:'local-one'}};
}
function receipt(f){const entry=f.store.snapshot().managementSync.queue[0],p=entry.payload;return{protocol:2,org_id:'org',device_id:'device',generation:'generation',accepted:true,event_ref:entry.eventRef,student_id:p.student_id,lesson_id:p.lesson_id,status:'arrived',apply_local_debit:false,attendance_revision:p.attendance_revision+1,balance_revision:p.balance_revision+1,balance_units:p.remaining_units};}

test('a blank tablet receives usable SFace anchors through real App decoding',()=>{
 const f=fixture(),state=f.store.snapshot();state.students=[];state.classes=[];state.attendance=[];state.transactions=[];state.settings.studentLeaves=[];state.managementSync={enabled:false};state.deviceSync={captureEnabled:false};
 f.data.lessons[0].students[0]={...f.data.lessons[0].students[0],status:'missing',attendance_revision:0,balance_units:1000,balance_revision:0};
 f.disk.raw=JSON.stringify(state);f.store.refresh();assert.equal(bootstrap.initialize(f.store,f.data,f.profile).ok,true);
 const result=faces.apply(f.store,faceData(),f.profile);assert.equal(result.ok,true);assert.equal(result.updated,1);
 const student=f.store.snapshot().students[0];assert.equal(student.faceRegistered,true);assert.equal(student.faceProfileSource,'server');assert.equal(student.faceProfileId,'face-profile-1');assert.equal(student.sfaceDescriptors.length,3);assert.ok(student.sfaceDescriptors.every(sample=>sample.startsWith('q16:')));
 const app=fs.readFileSync(path.resolve(__dirname,'../../app/src/main/assets/app.js'),'utf8'),context=vm.createContext({atob,btoa});
 const begin=app.indexOf('function normalizedVector(values)'),end=app.indexOf('function averageFaceDescriptor',begin);assert.ok(begin>=0&&end>begin);
 vm.runInContext(app.slice(begin,end)+"const SFACE_MODEL_VERSION=4;const hasSFaceProfile = s => Number(s.sfaceModelVersion) >= SFACE_MODEL_VERSION && Array.isArray(s.sfaceDescriptors) && s.sfaceDescriptors.some(isStoredFaceDescriptor);const studentFaceSamples = student => [...(Array.isArray(student.sfaceDescriptors)?student.sfaceDescriptors:[]),...(Array.isArray(student.sfaceAdaptiveDescriptors)?student.sfaceAdaptiveDescriptors:[])].map(unpackFaceDescriptor).filter(sample=>sample.length===128);",context);
 context.student=student;assert.equal(vm.runInContext('hasSFaceProfile(student)',context),true);const samples=vm.runInContext('studentFaceSamples(student)',context);assert.equal(samples.length,3);assert.ok(samples.every(sample=>sample.length===128&&Math.abs(sample[0]-1)<.001));
});

test('same profile retains adaptive learning, replacement and damaged anchors reset safely',()=>{
 const f=fixture();assert.equal(faces.apply(f.store,faceData(),f.profile).ok,true);
 let state=f.store.snapshot(),student=state.students[0];const anchors=student.sfaceDescriptors.slice();student.sfaceAdaptiveDescriptors=['q16:adaptive'];student.faceLearningCount=2;student.lastFaceLearnedAt='learned';f.disk.raw=JSON.stringify(state);f.store.refresh();
 let result=faces.apply(f.store,faceData(),f.profile);assert.equal(result.updated,0);student=f.store.snapshot().students[0];assert.deepEqual(student.sfaceDescriptors,anchors);assert.deepEqual(student.sfaceAdaptiveDescriptors,['q16:adaptive']);assert.equal(student.faceLearningCount,2);
 state=f.store.snapshot();state.students[0].sfaceDescriptors=[];f.disk.raw=JSON.stringify(state);f.store.refresh();result=faces.apply(f.store,faceData(),f.profile);assert.equal(result.updated,1);student=f.store.snapshot().students[0];assert.equal(student.sfaceDescriptors.length,3);assert.deepEqual(student.sfaceAdaptiveDescriptors,[]);
 state=f.store.snapshot();state.students[0].sfaceAdaptiveDescriptors=['q16:adaptive-new'];f.disk.raw=JSON.stringify(state);f.store.refresh();result=faces.apply(f.store,faceData([item('face-profile-2','student',1)]),f.profile);assert.equal(result.updated,1);student=f.store.snapshot().students[0];assert.equal(student.faceProfileId,'face-profile-2');assert.deepEqual(student.sfaceAdaptiveDescriptors,[]);assert.equal(student.faceLearningCount,0);assert.notDeepEqual(student.sfaceDescriptors,anchors);
});

test('revocation clears only server-managed profiles and preserves local evidence and balances',()=>{
 const f=fixture();assert.equal(faces.apply(f.store,faceData(),f.profile).ok,true);
 const state=f.store.snapshot();state.students[0].hours=8.5;state.attendance.push({id:'attendance-keep'});state.students.push({id:3,name:'本地学生',studentNo:'LOCAL',className:'演示班',hours:7,face:'LOCAL-KEEP',faceImage:'local-photo',faceDescriptors:['legacy'],sfaceDescriptors:['q16:local'],sfaceAdaptiveDescriptors:['q16:local-adaptive'],sfaceModelVersion:4,faceRegistered:true,faceProfileSource:'photo'});
 state.managementSync.students.local={localId:3,localClassId:2,name:'本地学生',studentNo:'LOCAL',className:'演示班',classId:'class'};f.disk.raw=JSON.stringify(state);f.store.refresh();
 const result=faces.apply(f.store,faceData([]),f.profile);assert.equal(result.ok,true);assert.equal(result.removed,1);const next=f.store.snapshot(),server=next.students[0],local=next.students[1];
 assert.equal(server.faceRegistered,false);assert.deepEqual(server.sfaceDescriptors,[]);assert.equal(server.faceProfileSource,'');assert.equal(server.hours,8.5);assert.equal(server.face,'KEEP');
 assert.equal(local.faceImage,'local-photo');assert.deepEqual(local.faceDescriptors,['legacy']);assert.deepEqual(local.sfaceDescriptors,['q16:local']);assert.equal(local.faceRegistered,true);assert.equal(local.face,'LOCAL-KEEP');
 assert.equal(next.attendance.length,1);assert.equal(next.transactions.length,1);assert.equal(next.managementSync.queue.length,0);
});

test('wrong identity, mapping, model, dimensions, values and inconsistent frames are atomic',()=>{
 const mutations=[p=>p.org_id='other',p=>p.device_id='other',p=>p.generation='other',p=>p.items[0].student_id='unknown',p=>p.items.push(structuredClone(p.items[0])),p=>p.model_version='other',p=>p.items[0].model_version='other',p=>p.items[0].descriptors=[[0], [0], [0]],p=>p.items[0].descriptors[0][0]=NaN,p=>p.items[0].descriptors=[frames(0)[0],frames(1)[0],frames(2)[0]],p=>p.items[0].extra='unexpected'];
 for(const mutate of mutations){const f=fixture(),payload=faceData(),raw=f.disk.raw;mutate(payload);assert.equal(faces.apply(f.store,payload,f.profile).ok,false);assert.equal(f.disk.raw,raw);}
 const f=fixture(),raw=f.disk.raw;assert.equal(faces.apply(f.store,faceData(),{...f.profile,generation:'other'}).code,'profile_changed');assert.equal(f.disk.raw,raw);
});

test('CAS conflict leaves every previous face and mapping unchanged',()=>{
 const f=fixture(),raw=f.disk.raw;f.store.compareAndSwap=()=>false;const result=faces.apply(f.store,faceData(),f.profile);assert.equal(result.code,'local_changes');assert.equal(f.disk.raw,raw);assert.equal(f.store.snapshot().students[0].faceRegistered,undefined);
});

test('runtime order is checkin, bootstrap, faces without another debit',async()=>{
 const f=fixture(),order=[];assert.equal(queue.record(f.store,f.input,f.starts).ok,true);
 const request=async kind=>{order.push(kind);if(kind==='checkin')return{ok:true,status:201,data:receipt(f)};if(kind==='bootstrap')return{ok:true,status:200,data:f.data};if(kind==='faces')return{ok:true,status:200,data:faceData()};throw Error('unexpected');};
 const result=await runtime.create({store:f.store,profile:()=>f.profile,request}).tick();assert.equal(result.ok,true);assert.deepEqual(order,['checkin','bootstrap','faces']);const state=f.store.snapshot();assert.equal(state.students[0].hours,9);assert.equal(state.students[0].faceProfileId,'face-profile-1');assert.equal(state.attendance.length,1);assert.equal(state.transactions.length,2);
});

test('face failure preserves updated roster and one debit, then retries without reupload',async()=>{
 const f=fixture(),orders=[];assert.equal(queue.record(f.store,f.input,f.starts).ok,true);let available=false;
 const request=async kind=>{orders.push(kind);if(kind==='checkin')return{ok:true,status:201,data:receipt(f)};if(kind==='bootstrap')return{ok:true,status:200,data:f.data};if(kind==='faces')return available?{ok:true,status:200,data:faceData()}:{ok:false,status:503,data:{error:{code:'face_not_configured'}}};throw Error('unexpected');};
 const sync=runtime.create({store:f.store,profile:()=>f.profile,request});let result=await sync.tick();assert.equal(result.phase,'faces');assert.equal(result.retry,true);assert.equal(f.store.snapshot().students[0].hours,9);assert.equal(f.store.snapshot().students[0].faceRegistered,undefined);
 available=true;result=await sync.tick();assert.equal(result.ok,true);assert.deepEqual(orders,['checkin','bootstrap','faces','bootstrap','faces']);const state=f.store.snapshot();assert.equal(state.attendance.length,1);assert.equal(state.transactions.length,2);assert.equal(state.students[0].hours,9);assert.equal(state.students[0].faceRegistered,true);
});

test('an old server without the face route remains compatible',async()=>{
 const f=fixture(),order=[];const result=await runtime.create({store:f.store,profile:()=>f.profile,request:async kind=>{order.push(kind);if(kind==='bootstrap')return{ok:true,status:200,data:f.data};if(kind==='faces')return{ok:false,status:404,data:{}};throw Error('unexpected');}}).tick();
 assert.equal(result.ok,true);assert.equal(result.faceSync,'unavailable');assert.deepEqual(order,['bootstrap','faces']);assert.equal(f.store.snapshot().students[0].face,'KEEP');
});
