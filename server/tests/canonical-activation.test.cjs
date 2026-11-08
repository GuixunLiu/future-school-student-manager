'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {build}=require('../ops/build-canonical-bootstrap.cjs'),{persist}=require('../management/tablet/activation-commit.js'),snapshots=require('../management/tablet/snapshot-store.js');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
function fixture(){
 const date=Date.parse('2026-11-03T14:00:00+08:00')/1000;
 const state={classes:[{id:11,name:'虚构班',teacher:'虚构老师',lessonDurationMinutes:120}],students:[{id:21,name:'虚构学生',studentNo:'001',className:'虚构班',hours:9,faceImage:'PRIVATE'}],
  attendance:[{id:100,studentId:21,classId:99,className:'旧班',date:'2026-10-24',time:'14:00'}],transactions:[{id:101,studentId:21,attendanceId:100,date:'2026-10-24',amount:-1}],settings:{studentLeaves:[]}};
 const raw=JSON.stringify(state),profile={org_id:'a'.repeat(32),device_id:'b'.repeat(32),generation:'c'.repeat(32),profile_id:'d'.repeat(32),baseline_sha256:sha(raw)};
 const source={classes:[{server_id:'class',local_id:11,name:'虚构班',teacher_id:'teacher',teacher_name:'虚构老师'}],students:[{server_id:'student',local_id:21,external_ref:'001',name:'虚构学生',class_id:'class',class_local_id:11,balance_units:900}],
  facts:[{lesson_id:'future',student_id:'student',status:'missing',expected:true,charge_units:100,starts_at:date}],history_boundary:{org_id:profile.org_id,baseline_sha256:profile.baseline_sha256,canonical_from:Date.parse('2026-11-03T00:00:00+08:00')/1000,boundary_sha256:'f'.repeat(64)}};
 const payload=build(raw,source,profile),p={configured:false,staged:true,orgId:profile.org_id,deviceId:profile.device_id,generation:profile.generation,profileId:profile.profile_id};
 const disk={raw,writes:0,getItem(){return this.raw;},setItem(k,v){this.raw=v;this.writes++;}};
 const store=()=>snapshots.create({storage:disk,key:'fixture'});
 return{state,raw,profile,source,payload,p,disk,store};
}
test('bootstrap adds canonical maps while preserving original balances, faces and old history without replay',()=>{
 const f=fixture(),next=JSON.parse(Buffer.from(f.payload.snapshot_base64,'base64'));
 for(const key of ['students','classes','attendance','transactions','settings'])assert.deepEqual(next[key],f.state[key]);
 assert.equal(next.managementSync.students.student.balanceUnits,900);assert.equal(next.managementSync.facts.future.student.chargeUnits,100);
 assert.deepEqual(next.managementSync.queue,[]);assert.deepEqual(next.managementSync.receipts,{});assert.equal(next.deviceSync.captureEnabled,false);
 assert.equal(sha(JSON.stringify(next)),f.payload.bootstrap_sha256);assert(!Object.hasOwn(f.payload,'token'));
});
test('whole scope, stable identity, existing writer, token fields and future charge are validated',()=>{
 for(const change of [f=>f.source.students.pop(),f=>f.source.students[0].balance_units=999,f=>f.source.classes[0].teacher_name='other',f=>f.source.facts[0].charge_units=150,
  f=>f.source.facts[0].starts_at=f.source.history_boundary.canonical_from-1,f=>f.profile.token='PRIVATE',f=>{const v=JSON.parse(f.raw);v.deviceSync={captureEnabled:true};f.raw=JSON.stringify(v);f.profile.baseline_sha256=sha(f.raw);f.source.history_boundary.baseline_sha256=sha(f.raw);}]){
  const f=fixture();change(f);assert.throws(()=>build(f.raw,f.source,f.profile));
 }
});
test('two same-day lessons for one tablet student refuse instead of guessing a check-in target',()=>{
 const f=fixture();f.source.facts.push({...f.source.facts[0],lesson_id:'another',starts_at:f.source.facts[0].starts_at+3600});assert.throws(()=>build(f.raw,f.source,f.profile),e=>e.code==='bootstrap_lesson_day_ambiguous');
});
test('persistent bootstrap retry after restart writes once and never overwrites later business',async()=>{
 const f=fixture();assert.equal((await persist(f.store(),f.payload,f.p)).duplicate,false);assert.equal(f.disk.writes,1);
 assert.equal((await persist(f.store(),f.payload,f.p)).duplicate,true);assert.equal(f.disk.writes,1);
 const next=JSON.parse(f.disk.raw);next.students[0].hours=8;next.transactions.push({id:102,amount:-1});f.disk.raw=JSON.stringify(next);const before=f.disk.raw;
 await assert.rejects(persist(f.store(),f.payload,f.p),e=>e.code==='local_changes');assert.equal(f.disk.raw,before);
});
test('wrong payload, wrong profile, full disk and a save during hashing leave old state intact',async()=>{
 const f=fixture();await assert.rejects(persist(f.store(),{...f.payload,bootstrap_sha256:'0'.repeat(64)},f.p));
 await assert.rejects(persist(f.store(),f.payload,{...f.p,profileId:'other'}));assert.equal(f.disk.raw,f.raw);
 f.disk.setItem=()=>{throw Error('full');};await assert.rejects(persist(f.store(),f.payload,f.p));assert.equal(f.disk.raw,f.raw);
 const g=fixture(),store=g.store(),pending=persist(store,g.payload,g.p);g.disk.raw=JSON.stringify({...g.state,note:'new saved edit'});const current=g.disk.raw;
 await assert.rejects(pending);assert.equal(g.disk.raw,current);
});
