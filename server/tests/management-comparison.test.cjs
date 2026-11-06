'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {compare}=require('../ops/compare-management-review.cjs'),{build}=require('../ops/build-teacher-web-data.cjs');
const c='c'.repeat(32),a='a'.repeat(32),b='b'.repeat(32),t='d'.repeat(32),organization='fictional';
function fixture(){
 const raw={classes:[{id:11,name:'虚构周六班',courseName:'信息学奥赛提高班',teacher:'虚构老师',scheduleWeekday:6,scheduleStart:'14:00'}],students:[{id:22,name:'虚构同名',studentNo:'0001',hours:9,className:'虚构周六班',face:'PRIVATE_SECRET'},{id:23,name:'虚构归档',studentNo:'0002',hours:15,className:'',pausedClassName:'已删除旧班',longTermAbsent:true}],attendance:[],transactions:[],settings:{adminPassword:'PRIVATE_SECRET'}};
 const data=build(raw,{asOfDate:'2026-11-01',sourceSha256:'f'.repeat(64),sourceCapturedAt:'Fictional',mirror:true});
 const source={format:'future-school-management-review-v1',schema_version:1,complete:true,generated_at:new Date().toISOString(),organization,org_id:'f'.repeat(32),source_access:'read_only',contains_private_data:true,
  classes:[{id:c,name:'虚构周六班',course_name:'信息学奥赛提高班',teachers:[{id:t,name:'虚构老师'}]}],students:[{id:a,name:'虚构同名',external_ref:'0001',class_id:c,remaining_units:1000,active:true}],
  archive_available:true,archived_students:[{id:b,name:'虚构归档',external_ref:'0002',remaining_units:1500,previous_class_name:''}],source_links_available:true,source_links:[{kind:'class',source_id:'11',entity_id:c,bundle_sha256:'a'.repeat(64)},{kind:'student',source_id:'22',entity_id:a,bundle_sha256:'a'.repeat(64)},{kind:'student',source_id:'23',entity_id:b,bundle_sha256:'a'.repeat(64)}],
  staff:[{id:t,login:'teacher',display_name:'虚构老师',active:true,roles:['teacher']}],mirror:{present:true,revision:1,received_at:1000,source_sha256:'e'.repeat(64),as_of_date:data.as_of_date,source_captured_at:'Fictional',source_records:0,device_active:true,device_expires_at:2000,classes:data.classes.map(x=>({...x,teacher_ids:[t]})),lessons:data.lessons,covered_weeks:data.weeks.map(w=>({start:w.start,end:w.end}))}};
 return{raw,source};
}
test('reuse archive identity and keep stale base balance separate from matching mirror; no private fields or mutation',()=>{
 const {raw,source}=fixture(),before=JSON.stringify({raw,source}),r=compare(JSON.stringify(raw),source,{organization});
 assert.equal(r.summary.existing_archives,1);assert.equal(r.summary.identity_matches,2);assert.equal(r.summary.balance_differences,1);assert.equal(r.summary.mirror_equal,true);assert.equal(r.students[1].server_id,b);assert.equal(r.summary.mirror_authorized_classes,1);assert.equal(r.ready_for_import,false);assert(!JSON.stringify(r).includes('PRIVATE_SECRET'));assert.equal(JSON.stringify({raw,source}),before);
});
test('same name with a different reference is flagged, never assigned an existing identity',()=>{
 const {raw,source}=fixture();raw.students.push({id:24,name:'虚构同名',studentNo:'0003',hours:12,className:'虚构周六班'});
 const r=compare(JSON.stringify(raw),source,{organization}),s=r.students.find(s=>s.local_id===24);assert.equal(s.server_id,null);assert.equal(s.identity,'not_in_base_or_archive');assert.deepEqual(s.same_name_other_refs,['0001']);assert.equal(r.summary.not_in_base_or_archive,1);
});
test('persisted local identity links override tempting reference/name matches; no silent reassignment',()=>{
 const {raw,source}=fixture();source.source_links.find(l=>l.kind==='student'&&l.source_id==='22').entity_id=b;
 const r=compare(JSON.stringify(raw),source,{organization});assert.equal(r.summary.identity_conflicts,1);assert.deepEqual(r.identity_conflicts[0].fields,['source_link']);assert.equal(r.students[0].identity,'conflict');
});
test('missing archives or identity tables cannot be mistaken for new students; empty coverage cannot pass',()=>{
 const {raw,source}=fixture();const incomplete=structuredClone(source);incomplete.archive_available=false;incomplete.archived_students=[];incomplete.source_links=incomplete.source_links.filter(l=>l.entity_id!==b);
 assert.throws(()=>compare(JSON.stringify(raw),incomplete,{organization}),/must both be available/);
 source.mirror.covered_weeks=[];assert.equal(compare(JSON.stringify(raw),source,{organization}).mirror.equal,false);
});
test('field ordering and member ordering do not manufacture mirror differences; real changes are detected',()=>{
 const {raw,source}=fixture();source.mirror.lessons=source.mirror.lessons.map(l=>Object.fromEntries(Object.entries({...l,members:[...l.members].reverse()}).reverse()));
 assert.equal(compare(JSON.stringify(raw),source,{organization}).mirror.equal,true);
 source.mirror.lessons[0].members[0].remaining_units-=100;
 const r=compare(JSON.stringify(raw),source,{organization});assert.equal(r.mirror.equal,false);assert.deepEqual(r.mirror.differences[0].fields,['members']);
});
test('old raw attendance outside report coverage remains explicit history to preserve, not replay or delete',()=>{
 const {raw,source}=fixture();raw.attendance.push({id:1,studentId:22,date:'2026-10-31',time:'14:02',classId:11,className:'虚构周六班'});raw.transactions.push({id:1,studentId:22,date:'2026-10-31',amount:-1,attendanceId:1});
 source.mirror.covered_weeks=source.mirror.covered_weeks.filter(w=>w.start>'2026-11-01');source.mirror.lessons=source.mirror.lessons.filter(l=>l.date>'2026-11-01');
 const r=compare(JSON.stringify(raw),source,{organization});assert.equal(r.mirror.coverage_only,true);assert.equal(r.mirror.unrepresented_attendance.length,1);assert.equal(r.mirror.unrepresented_attendance[0].action,'preserve_raw_history_no_replay');assert.equal(r.ready_for_import,false);
});
