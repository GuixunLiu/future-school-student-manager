'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {pack,encoded,digest}=require('../ops/pack-enrollment-review.cjs');
const {build,buildSegmented,buildNativeSegmented}=require('../ops/build-canonical-manifest.cjs');
function fixture(){
 const students=Array.from({length:366},(_,i)=>({server_id:'student-'+i,local_id:i+1,external_ref:String(i).padStart(5,'0'),name:'虚构同名学生'+i,class_id:'class-a',class_local_id:1000,balance_units:1000}));
 return{workspace_role:'admin',device_id:'device',generation:'generation',baseline_sha256:'a'.repeat(64),source_captured_at:2000,
  source:{classes:[{server_id:'class-a',local_id:1000,name:'虚构班级',teacher_id:'teacher',teacher_name:'虚构老师'}],students,facts:[]}};
}
test('whole-school pages reassemble exactly, fixed hashes and unchanged HTTP limit',()=>{
 const body=fixture(),before=JSON.stringify(body),r=pack(body,'whole-school');assert(r.pages.length>2);
 for(const section of ['classes','students','facts']){
  const rows=r.pages.filter(p=>p.section===section).sort((a,b)=>a.page_index-b.page_index).flatMap(p=>p.rows);
  assert.deepEqual(rows,body.source[section]);assert.equal(digest(rows),r.start.manifest[section].sha256);assert.equal(rows.length,r.start.manifest[section].rows);
 }
 for(const page of r.pages){assert(Buffer.byteLength(encoded(page.rows))<=12288);assert(Buffer.byteLength(JSON.stringify(page))<=16384);assert.equal(digest(page.rows),page.sha256);}
 assert.equal(r.metadata.offline_only,true);assert.equal(JSON.stringify(body),before);
});
test('Python-compatible ASCII canonical JSON preserves Unicode, controls, non-BMP names and negative balances',()=>{
 const value={z:'汉字\u007f\n😀',a:-150,expected:true};assert.equal(encoded(value),'{"a":-150,"expected":true,"z":"\\u6c49\\u5b57\\u007f\\n\\ud83d\\ude00"}');
 assert.equal(digest(value),crypto.createHash('sha256').update(encoded(value)).digest('hex'));assert.throws(()=>encoded({bad:undefined}));assert.throws(()=>encoded({bad:1.5}));
});
test('private unexpected fields, implicit roles and oversized scope are rejected instead of truncated',()=>{
 const b=fixture();b.source.students[0].face='SECRET';assert.throws(()=>pack(b,'ref'),/Unexpected/);delete b.source.students[0].face;
 b.workspace_role='teacher';assert.throws(()=>pack(b,'ref'));b.workspace_role='admin';assert.throws(()=>pack(b,''));b.source.students=Array.from({length:2001},()=>b.source.students[0]);assert.throws(()=>pack(b,'ref'),/scope limit/);
});
test('actual backup projection retains fresh capture and full-history requirements in segmented mode',()=>{
 const body=fixture(),mapping={protocol:1,org_id:'org',device_id:body.device_id,generation:body.generation,classes:body.source.classes,students:body.source.students.map(({balance_units,...s})=>s),lessons:[]};
 const data={classes:[{id:1000,name:'虚构班级',teacher:'虚构老师',courseName:'虚构课程'}],students:body.source.students.map(s=>({id:s.local_id,name:s.name,studentNo:s.external_ref,hours:10,className:'虚构班级',faceImage:'SECRET'})),attendance:[],transactions:[],settings:{}};
 const raw=JSON.stringify(data),capture={format:'future-school-live-capture-v1',captured_at:2000,sha256:crypto.createHash('sha256').update(raw).digest('hex'),bytes:Buffer.byteLength(raw)};
 assert.throws(()=>build(raw,mapping,capture,2000),e=>e.code==='single_class_request_limit');
 const result=buildSegmented(raw,mapping,capture,'review',2000);assert.equal(result.start.manifest.students.rows,366);assert(!JSON.stringify(result).includes('SECRET'));
 const native={format:'future-school-native-capture-v1',captured_at:2000,sha256:capture.sha256,bytes:capture.bytes,payload_base64:Buffer.from(raw).toString('base64')};assert.deepEqual(buildNativeSegmented(native,mapping,'review',2000),result);
 const partial=structuredClone(mapping);partial.students.pop();assert.throws(()=>buildSegmented(raw,partial,capture,'review',2000),e=>e.code==='whole_device_scope_required');
 assert.throws(()=>buildSegmented(raw,mapping,capture,'review',2900),e=>e.code==='source_not_fresh');
 const altered=JSON.parse(raw);altered.attendance.push({id:1,studentId:1,date:'1970-01-01',classId:1000,className:'虚构班级'});const next=JSON.stringify(altered),receipt={...capture,sha256:crypto.createHash('sha256').update(next).digest('hex'),bytes:Buffer.byteLength(next)};
 assert.throws(()=>buildSegmented(next,mapping,receipt,'review',2000),e=>e.code==='unmapped_history');
});
