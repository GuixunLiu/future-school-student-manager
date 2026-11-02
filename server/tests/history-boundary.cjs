'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const history=require('../ops/build-legacy-history-archive.cjs'),{digest}=require('../ops/pack-enrollment-review.cjs');
const {buildBoundarySegmented,buildSegmented}=require('../ops/build-canonical-manifest.cjs');
function fixture(){
 const now=1793505600,cutoff=1793548800;
 const data={classes:[{id:11,name:'虚构新班',courseName:'虚构课程',teacher:'虚构老师'}],students:[{id:21,name:'虚构学生',studentNo:'0001',className:'虚构新班',hours:9,contact:'PRIVATE'}],
  attendance:[{id:100,studentId:21,classId:99,className:'已经删除的旧班',date:'2026-10-24',time:'14:00'}],
  transactions:[{id:101,studentId:21,classId:99,className:'已经删除的旧班',date:'2026-10-24',attendanceId:100,amount:-1}],settings:{}};
 const mapping={protocol:1,org_id:'org',device_id:'device',generation:'g',classes:[{server_id:'class',local_id:11,name:'虚构新班',teacher_id:'teacher',teacher_name:'虚构老师'}],
  students:[{server_id:'student',local_id:21,name:'虚构学生',external_ref:'0001',class_id:'class',class_local_id:11}],
  lessons:[{lesson_id:'future',class_id:'class',starts_at:cutoff+14*3600,charge_units:100,student_ids:['student']}]};
 const raw=JSON.stringify(data),capture={format:'future-school-live-capture-v1',sha256:crypto.createHash('sha256').update(raw).digest('hex'),bytes:Buffer.byteLength(raw),captured_at:now};
 let boundary={format:'future-school-history-boundary-v1',org_id:'org',initialization_id:'a'.repeat(64),baseline_sha256:capture.sha256,archive_sha256:history.build(raw).archive_sha256,
  canonical_from_day:'2026-11-02',canonical_from:cutoff,openings_sha256:digest([{student_id:'student',class_id:'class',units:900,revision:0}]),rehearsal_only:true,activation_authorized:false};
 boundary={...boundary,boundary_sha256:digest(boundary)};
 return{data,raw,mapping,capture,boundary,now};
}
function run(f){return buildBoundarySegmented(f.raw,f.mapping,f.capture,f.boundary,'review',f.now);}
function reseal(b){delete b.boundary_sha256;b.boundary_sha256=digest(b);}

test('sealed deleted-class history stays original while only future facts enter the unchanged paged protocol',()=>{
 const f=fixture(),before=JSON.stringify(f),result=run(f);
 assert.equal(JSON.stringify(f),before);assert(!JSON.stringify(result).includes('PRIVATE'));
 assert.deepEqual(result.pages.filter(p=>p.section==='facts').flatMap(p=>p.rows).map(r=>[r.lesson_id,r.status,r.charge_units]),[['future','missing',100]]);
 assert.equal(result.metadata.legacy_read_only,true);assert.equal(result.metadata.legacy_replayed,false);
 assert.throws(()=>buildSegmented(f.raw,f.mapping,f.capture,'old',f.now),e=>e.code==='unmapped_history');
});
test('changed original bytes, archive, opening balance and forged boundary digest refuse',()=>{
 for(const change of [f=>{f.raw+=' ';f.capture.bytes++;f.capture.sha256=crypto.createHash('sha256').update(f.raw).digest('hex');},
  f=>f.boundary.archive_sha256='b'.repeat(64),f=>{f.boundary.openings_sha256='b'.repeat(64);reseal(f.boundary);},
  f=>{f.boundary.canonical_from+=1;reseal(f.boundary);},f=>{f.boundary.activation_authorized=true;reseal(f.boundary);},
  f=>{f.mapping.students=[];}]){const f=fixture();change(f);assert.throws(()=>run(f));}
});
test('old lessons and an expired or relabelled capture cannot bypass original history checks',()=>{
 const old=fixture();old.mapping.lessons[0].starts_at=old.boundary.canonical_from-1;assert.throws(()=>run(old),e=>e.code==='legacy_lesson_read_only');
 const stale=fixture();stale.now+=900;assert.throws(()=>run(stale),e=>e.code==='source_not_fresh');
 const cross=fixture();cross.now=cross.boundary.canonical_from;cross.capture.captured_at=cross.now;assert.throws(()=>run(cross),e=>e.code==='history_boundary_differs');
});
test('future approved legacy leave requires explicit admission rather than being silently omitted',()=>{
 const f=fixture();f.data.settings.studentLeaves=[{id:102,studentId:21,studentNo:'0001',classId:11,className:'虚构新班',date:'2026-11-03',status:'approved',createdAt:'2026-11-01 13:00'}];
 f.raw=JSON.stringify(f.data);f.capture.sha256=crypto.createHash('sha256').update(f.raw).digest('hex');f.capture.bytes=Buffer.byteLength(f.raw);
 f.boundary.baseline_sha256=f.capture.sha256;f.boundary.archive_sha256=history.build(f.raw).archive_sha256;reseal(f.boundary);
 assert.throws(()=>run(f),e=>e.code==='future_legacy_admission_required');
});
