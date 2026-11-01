'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {build}=require('../ops/build-canonical-manifest.cjs');
const now=Math.floor(Date.parse('2026-11-01T10:00:00+08:00')/1000),starts=Math.floor(Date.parse('2026-10-31T14:00:00+08:00')/1000);
const secret='PRIVATE_FACE_CONTACT_PASSWORD_MARKER';
function fixture(){
  const data={classes:[{id:11,name:'虚构班甲',teacher:'虚构老师甲',courseName:'虚构课程'},{id:12,name:'虚构班乙',teacher:'虚构老师乙',courseName:'虚构课程'}],
    students:[{id:1,studentNo:'001',name:'虚构同名',className:'虚构班甲',hours:9,faceImage:secret,contact:secret},{id:2,studentNo:'002',name:'虚构同名',className:'虚构班乙',hours:-1.5},{id:3,studentNo:'003',name:'虚构请假',className:'虚构班甲',hours:10}],
    attendance:[{id:101,studentId:1,date:'2026-10-31',time:'14:01',classId:11,className:'虚构班甲'}],
    transactions:[{id:201,attendanceId:101,studentId:1,date:'2026-10-31',amount:-1,private:secret}],
    settings:{adminPattern:secret,studentLeaves:[{id:301,studentId:3,date:'2026-10-31',status:'approved',classId:11,className:'虚构班甲',reason:secret}]}};
  const mapping={protocol:1,org_id:'org',device_id:'device',generation:'generation',classes:[{server_id:'class-a',local_id:11,name:'虚构班甲',teacher_id:'teacher-a',teacher_name:'虚构老师甲'},{server_id:'class-b',local_id:12,name:'虚构班乙',teacher_id:'teacher-b',teacher_name:'虚构老师乙'}],
    students:[{server_id:'student-a',local_id:1,external_ref:'001',name:'虚构同名',class_id:'class-a',class_local_id:11},{server_id:'student-b',local_id:2,external_ref:'002',name:'虚构同名',class_id:'class-b',class_local_id:12},{server_id:'student-c',local_id:3,external_ref:'003',name:'虚构请假',class_id:'class-a',class_local_id:11}],
    lessons:[{lesson_id:'lesson-a',class_id:'class-a',starts_at:starts,charge_units:100,student_ids:['student-a','student-c']},{lesson_id:'lesson-b',class_id:'class-b',starts_at:starts,charge_units:100,student_ids:['student-b']}]};
  return {data,mapping};
}
function input(f=fixture()){
  const raw=JSON.stringify(f.data),capture={format:'future-school-live-capture-v1',sha256:crypto.createHash('sha256').update(raw).digest('hex'),bytes:Buffer.byteLength(raw),captured_at:now};
  return {...f,raw,capture};
}
const run=f=>{const i=input(f);return build(i.raw,i.mapping,i.capture,now);};
const reject=(f,code)=>assert.throws(()=>run(f),e=>e.code===code);
test('minimal projection preserves distinct same names, leading zeros and balances without replay',()=>{
  const f=fixture(),before=JSON.stringify(f),out=run(f);
  assert.equal(JSON.stringify(f),before);assert(!JSON.stringify(out).includes(secret));
  assert.equal(out.body.source.students[0].external_ref,'001');assert.equal(out.body.source.students[1].balance_units,-150);
  assert.deepEqual(out.body.source.facts.map(f=>f.status),['arrived','leave','missing']);
  assert.equal(out.metadata.server_written,false);assert.equal(out.metadata.tablet_modified,false);
});
test('capture bytes, digest and actual acquisition time are required with no fresh-time default',()=>{
  const i=input();
  for(const c of [{...i.capture,captured_at:now-900},{...i.capture,captured_at:now+121},{...i.capture,captured_at:true},{...i.capture,sha256:'a'.repeat(64)},{...i.capture,bytes:1}])assert.throws(()=>build(i.raw,i.mapping,c,now));
  assert.throws(()=>build(i.raw+' ',i.mapping,i.capture,now),e=>e.code==='capture_digest_mismatch');
});
test('explicit identities cannot silently switch homonyms, teachers or archive status',()=>{
  let f=fixture();f.mapping.students[0].local_id=2;reject(f,'student_identity_differs');
  f=fixture();f.mapping.students[0].external_ref='1';reject(f,'student_identity_differs');
  f=fixture();f.mapping.classes[0].teacher_name='虚构老师乙';reject(f,'class_identity_differs');
  f=fixture();f.data.students[0].longTermAbsent=true;reject(f,'student_identity_differs');
});
test('every selected local attendance and approved leave must map to exactly one explicit lesson',()=>{
  let f=fixture();f.mapping.lessons[0].student_ids=['student-c'];reject(f,'unmapped_history');
  f=fixture();f.mapping.lessons[0].student_ids=['student-a'];reject(f,'unmapped_history');
  f=fixture();f.mapping.lessons.push({...f.mapping.lessons[0],lesson_id:'another'});reject(f,'ambiguous_lesson_day');
  f=fixture();f.data.attendance[0].classId=12;reject(f,'historical_class_review_required');
  f=fixture();f.data.settings.studentLeaves.push({...f.data.settings.studentLeaves[0],id:302,studentId:1});reject(f,'structural_issues');
});
test('explicit charge must agree with original linked transaction, never derive charge from duration',()=>{
  let f=fixture();f.mapping.lessons[0].charge_units=200;reject(f,'charge_evidence_differs');
  f=fixture();f.data.transactions[0].date='2026-10-30';reject(f,'structural_issues');
  f=fixture();f.data.transactions.push({...f.data.transactions[0],id:202});reject(f,'structural_issues');
  f=fixture();f.mapping.lessons[0].duration_minutes=120;reject(f,'invalid_mapping');
});
test('duplicates and unsupported mapping fields stop rather than infer or truncate',()=>{
  let f=fixture();f.mapping.classes.push({...f.mapping.classes[0]});reject(f,'ambiguous_mapping');
  f=fixture();f.mapping.students.push({...f.mapping.students[0]});reject(f,'ambiguous_mapping');
  f=fixture();f.mapping.lessons[0].student_ids.push('student-a');reject(f,'invalid_lesson_mapping');
  f=fixture();f.mapping.students[0].contact=secret;reject(f,'invalid_mapping');
});
test('subset projection leaves other classes private, but cannot omit selected students history',()=>{
  const f=fixture();f.mapping.students=f.mapping.students.filter(s=>s.class_id==='class-b');f.mapping.classes=f.mapping.classes.filter(c=>c.server_id==='class-b');f.mapping.lessons=f.mapping.lessons.filter(l=>l.class_id==='class-b');
  const out=run(f);assert.equal(out.body.source.students.length,1);assert.equal(out.body.source.facts[0].status,'missing');assert.deepEqual(out.body.source.students.map(s=>s.external_ref),['002']);
});
test('no-history sources need no invented lessons; oversized requests require separate handling',()=>{
  let f=fixture();f.data.attendance=[];f.data.transactions=[];f.data.settings.studentLeaves=[];f.mapping.lessons=[];
  assert.deepEqual(run(f).body.source.facts,[]);
  f=fixture();
  for(let n=4;n<=100;n++){
    const local={id:n,name:'虚构学生'+n,studentNo:'TEST'+n,className:'虚构班甲',hours:10};
    f.data.students.push(local);f.mapping.students.push({server_id:'student-'+n,local_id:n,external_ref:local.studentNo,name:local.name,class_id:'class-a',class_local_id:11});f.mapping.lessons[0].student_ids.push('student-'+n);
  }
  reject(f,'single_class_request_limit');
});
test('CLI keeps private output outside Git, refuses overwrites and never exposes input secrets',()=>{
  const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawnSync}=require('node:child_process');
  const base=path.resolve(os.tmpdir()),dir=fs.mkdtempSync(path.join(base,'canonical-manifest-'));
  try{
    const i=input(),capture={...i.capture,captured_at:Math.floor(Date.now()/1000)};
    for(const [name,value]of [['backup',i.data],['mapping',i.mapping],['capture',capture]])fs.writeFileSync(path.join(dir,name+'.json'),JSON.stringify(value));
    const target=path.join(dir,'result.private.json'),script=path.resolve(__dirname,'../ops/build-canonical-manifest.cjs');
    const args=['--backup',path.join(dir,'backup.json'),'--mapping',path.join(dir,'mapping.json'),'--capture-receipt',path.join(dir,'capture.json'),'--output',target];
    const invoke=a=>spawnSync(process.execPath,[script,...a],{encoding:'utf8',windowsHide:true});
    const first=invoke(args);assert.equal(first.status,0,first.stderr);assert(!first.stdout.includes(secret));assert(!first.stdout.includes('虚构同名'));
    const original=fs.readFileSync(target);assert(!original.toString().includes(secret));
    const again=invoke(args);assert.equal(again.status,1);assert(again.stderr.includes('EEXIST'));assert.deepEqual(fs.readFileSync(target),original);
    const bad=[...args];bad[bad.length-1]=path.resolve(__dirname,'must-not-exist.private.json');
    const rejected=invoke(bad);assert.equal(rejected.status,1);assert(rejected.stderr.includes('private_output_must_be_outside_repository'));assert(!fs.existsSync(bad.at(-1)));
    assert.equal(invoke([]).status,0);
  }finally{if(!path.resolve(dir).startsWith(base+path.sep+'canonical-manifest-'))throw Error('unsafe cleanup');fs.rmSync(dir,{recursive:true,force:true});}
});
