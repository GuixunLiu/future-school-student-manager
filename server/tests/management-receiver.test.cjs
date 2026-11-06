// Entirely fictional snapshots; no Android bridge, database, or network.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createHash, webcrypto} = require('node:crypto');
globalThis.crypto ??= webcrypto;
const {receive} = require('../management/tablet/approval-receiver.js');

function fixture(kind='arrived') {
  const starts = Date.parse('2026-10-31T14:00:00+08:00')/1000;
  const e = {protocol:1,org_id:'fictional',event_id:'event-1',request_id:'request-1',
    lesson_id:'lesson',student_id:'student',class_id:'class',starts_at:starts,charge_units:100,
    status:kind,expected:true,previous_attendance_revision:0,attendance_revision:1,
    previous_balance_revision:0,balance_revision:kind==='arrived'?1:0,
    previous_balance_units:1000,balance_units:kind==='arrived'?900:1000,balance_delta:kind==='arrived'?-100:0,
    decided_by:'fictional-admin',decided_by_name:'虚构审批人',reason:'虚构测试',effective_at:starts+7200};
  const state = {
    students:[{id:1,studentNo:'TEST',name:'虚构学生',className:'虚构班',hours:10,faceProfile:'KEEP PRIVATE DATA'}],
    classes:[{id:2,name:'虚构班',teacher:'虚构教师',classroom:'虚构教室',durationMinutes:120}],
    attendance:[],transactions:[],settings:{studentLeaves:[],otherSetting:'keep'},
    managementSync:{protocol:1,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
      orgId:'fictional',deviceId:'device',generation:'generation',receipts:{},queue:[],
      students:{student:{localId:1,localClassId:2,studentNo:'TEST',name:'虚构学生',classId:'class',className:'虚构班',teacher:'虚构教师',balanceUnits:1000,balanceRevision:0}},
      lessons:{lesson:{classId:'class',startsAt:starts,date:'2026-10-31',studentIds:['student']}},
      facts:{lesson:{student:{status:'missing',expected:true,revision:0,chargeUnits:100}}}}
  };
  const storage = {raw:JSON.stringify(state), writes:0, read(){return this.raw;},
    compareAndSwap(before,after){if(this.raw!==before)return false;this.raw=after;this.writes++;return true;}};
  const envelope = event => {
    const wire = JSON.stringify(event);
    return {org_id:'fictional',device_id:'device',generation:'generation',item:{event_json:wire,
      sha256:createHash('sha256').update(wire).digest('hex'),receipt_token:'a'.repeat(32),status:'pending'}};
  };
  return {e,state,storage,envelope};
}
function change(f, fn) {const s=JSON.parse(f.storage.raw);fn(s);f.storage.raw=JSON.stringify(s);}

function historicalFixture(kind='arrived') {
  const f=fixture(kind),header={format:'future-school-history-boundary-v1',org_id:'fictional',canonical_from_day:'2026-11-03',canonical_from:Date.parse('2026-11-03T00:00:00+08:00')/1000,baseline_sha256:'a'.repeat(64)};
  const hash=createHash('sha256').update(JSON.stringify(Object.fromEntries(Object.keys(header).sort().map(k=>[k,header[k]])))).digest('hex');
  f.e.lesson_id='legacy:old-class:2026-10-31';f.e.class_id='old-class';
  f.e.historical={protocol:1,local_student_id:1,local_class_id:99,class_name:'原历史班',teacher_name:'原历史教师',delivery_class_id:'class',date:'2026-10-31',history_boundary:{...header,boundary_sha256:hash}};
  change(f,s=>{s.managementSync.lessons={};s.managementSync.facts={};s.managementSync.historyBoundarySha256=hash;s.attendance.push({id:80,studentId:777,date:'2026-10-24',className:'保留原记录'});});
  return f;
}

test('historical approval atomically admits only one old fact; transferred class and original history stay unchanged',async()=>{
  const f=historicalFixture(),envelope=f.envelope(f.e);
  assert.equal((await receive(f.storage,envelope)).ok,true);
  const s=JSON.parse(f.storage.raw);
  assert.equal(s.students[0].className,'虚构班');assert.equal(s.students[0].hours,9);
  assert.equal(s.attendance[0].className,'保留原记录');assert.equal(s.attendance[1].className,'原历史班');assert.equal(s.attendance[1].teacher,'原历史教师');assert.equal(s.attendance[1].classId,99);
  assert.equal(s.transactions.length,1);assert.equal(s.transactions[0].amount,-1);
  assert.equal(s.managementSync.students.student.classId,'class');assert.equal(s.managementSync.lessons[f.e.lesson_id].classId,'old-class');
  assert.equal((await receive(f.storage,envelope)).duplicate,true);assert.equal(f.storage.writes,1);
});

test('historical leave admits one old fact without deducting or inventing arrival',async()=>{
  const f=historicalFixture('leave');assert.equal((await receive(f.storage,f.envelope(f.e))).ok,true);
  const s=JSON.parse(f.storage.raw);assert.equal(s.students[0].hours,10);assert.equal(s.attendance.length,1);assert.equal(s.transactions.length,0);assert.equal(s.settings.studentLeaves[0].className,'原历史班');
});

test('historical proof, current class, duplicate day and disk conflicts leave no partly admitted fact',async()=>{
  for(const mutate of [f=>f.e.historical.history_boundary.baseline_sha256='b'.repeat(64),f=>change(f,s=>s.managementSync.historyBoundarySha256='0'.repeat(64)),f=>f.e.historical.delivery_class_id='wrong',f=>change(f,s=>s.attendance.push({id:81,studentId:1,date:'2026-10-31'})),f=>f.storage.compareAndSwap=()=>false]){
    const f=historicalFixture();mutate(f);const before=f.storage.raw;assert.equal((await receive(f.storage,f.envelope(f.e))).ok,false);assert.equal(f.storage.raw,before);assert.equal(JSON.parse(before).managementSync.facts[f.e.lesson_id],undefined);
  }
});

test('makeup, lost receipt, process restart and retry persist exactly one deduction',async()=>{
  const f=fixture(), envelope=f.envelope(f.e);
  const first=await receive(f.storage,envelope);
  assert.equal(first.ok,true);
  const s=JSON.parse(f.storage.raw);
  assert.equal(s.students[0].hours,9);
  assert.equal(s.attendance.length,1);
  assert.equal(s.transactions.length,1);
  assert.equal(s.transactions[0].amount,-1); // 120 minutes DOES NOT mean 2 charged units
  assert.equal(s.students[0].faceProfile,'KEEP PRIVATE DATA');
  assert.equal(s.settings.otherSetting,'keep');
  assert.equal(s.attendance[0].date,'2026-10-31');
  assert.equal(s.attendance[0].makeup,true);
  const second=await receive(f.storage,envelope);
  assert.equal(second.ok,true);
  assert.equal(second.duplicate,true);
  assert.deepEqual(second.receipt,first.receipt);
  assert.equal(f.storage.writes,1);
});

test('leave retains expected denominator and never deducts or records check-in',async()=>{
  const f=fixture('leave');
  assert.equal((await receive(f.storage,f.envelope(f.e))).ok,true);
  const s=JSON.parse(f.storage.raw);
  assert.equal(s.students[0].hours,10);
  assert.equal(s.attendance.length,0);
  assert.equal(s.transactions.length,0);
  assert.equal(s.settings.studentLeaves[0].status,'approved');
  assert.equal(s.managementSync.facts.lesson.student.expected,true);
  assert.equal(s.managementSync.students.student.balanceRevision,0);
});

test('disk failure or CAS collision cannot report success or partly persist',async()=>{
  for(const mode of ['throw','false']) {
    const f=fixture(),original=f.storage.raw;
    f.storage.compareAndSwap=()=>{if(mode==='throw')throw Error('disk full');return false;};
    const result=await receive(f.storage,f.envelope(f.e));
    assert.deepEqual(result,{ok:false,code:'storage_failed'});
    assert.equal(f.storage.raw,original);
    assert.equal(result.receipt,undefined);
  }
});

const conflicts = [
  ['offline queue',s=>s.managementSync.queue.push({status:'pending'}),'local_changes'],
  ['legacy writer',s=>s.deviceSync={captureEnabled:true},'local_changes'],
  ['old mirror',s=>s.managementSync.legacyMirrorDisabled=false,'local_changes'],
  ['balance edit',s=>s.students[0].hours=11,'balance_changed'],
  ['balance revision',s=>s.managementSync.students.student.balanceRevision=1,'balance_changed'],
  ['offline check-in',s=>s.attendance.push({id:99,studentId:1,date:'2026-10-31'}),'attendance_changed'],
  ['offline leave',s=>s.settings.studentLeaves.push({id:98,studentId:1,date:'2026-10-31',status:'approved'}),'attendance_changed'],
  ['revision mismatch',s=>s.managementSync.facts.lesson.student.revision=2,'attendance_changed'],
  ['not expected',s=>s.managementSync.facts.lesson.student.expected=false,'attendance_changed'],
  ['student renamed',s=>s.students[0].name='不同的人','mapping_changed'],
  ['class changed',s=>s.students[0].className='别的班','mapping_changed'],
  ['teacher changed',s=>s.classes[0].teacher='别的教师','mapping_changed'],
  ['duplicate local ID',s=>s.students.push({...s.students[0]}),'mapping_changed'],
  ['generation changed',s=>s.managementSync.generation='new','mapping_changed'],
  ['wrong tenant',s=>s.managementSync.orgId='other','mapping_changed'],
  ['missing roster',s=>s.managementSync.lessons.lesson.studentIds=[],'mapping_changed'],
];
for(const [name,fn,code] of conflicts) test(name+' blocks without changing any local data',async()=>{
  const f=fixture();change(f,fn);const original=f.storage.raw;
  assert.deepEqual(await receive(f.storage,f.envelope(f.e)),{ok:false,code});
  assert.equal(f.storage.raw,original);
  assert.equal(f.storage.writes,0);
});

test('digest corruption and malformed ledger arithmetic rejected',async()=>{
  const variants=[{protocol:2},{org_id:'other'},{balance_delta:-200},{balance_units:1},
    {attendance_revision:0},{charge_units:0},{effective_at:0},{expected:false},{previous_balance_revision:true}];
  for(const variant of variants){
    const f=fixture(), original=f.storage.raw;
    assert.deepEqual(await receive(f.storage,f.envelope({...f.e,...variant})),{ok:false,code:'invalid_event'});
    assert.equal(f.storage.raw,original);
  }
  const f=fixture(),e=f.envelope(f.e);e.item.sha256='0'.repeat(64);
  assert.deepEqual(await receive(f.storage,e),{ok:false,code:'invalid_event'});
});

test('simultaneous redelivery persists once; verifies fresh state after async digest',async()=>{
  const f=fixture(),e=f.envelope(f.e);
  const results=await Promise.all([receive(f.storage,e),receive(f.storage,e)]);
  assert.ok(results.every(r=>r.ok));
  assert.equal(results.filter(r=>!r.duplicate).length,1);
  assert.equal(f.storage.writes,1);
});

test('old exact event may be acknowledged after later balance update, never reapplied',async()=>{
  const f=fixture(),e=f.envelope(f.e);
  const first=await receive(f.storage,e);
  change(f,s=>{s.students[0].hours=8;s.managementSync.students.student.balanceUnits=800;s.managementSync.students.student.balanceRevision=2;});
  const second=await receive(f.storage,e);
  assert.equal(second.duplicate,true);
  assert.deepEqual(second.receipt,first.receipt);
  assert.equal(JSON.parse(f.storage.raw).students[0].hours,8);
  assert.deepEqual(await receive(f.storage,f.envelope({...f.e,reason:'changed'})),{ok:false,code:'invalid_event'});
});
