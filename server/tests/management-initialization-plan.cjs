'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{build}=require('../ops/build-management-initialization-plan.cjs'),{build:dataset}=require('../ops/build-teacher-web-data.cjs');
function fixture(){
 const c='c'.repeat(32),s='a'.repeat(32),t='d'.repeat(32),a='b'.repeat(32);
 const raw={classes:[{id:11,name:'虚构班',teacher:'虚构老师',scheduleWeekday:6}],students:[{id:22,name:'已有学生',studentNo:'001',hours:9,className:'虚构班'},{id:23,name:'新增基础身份',studentNo:'002',hours:10,className:'虚构班'},{id:24,name:'归档学生',studentNo:'003',hours:25,className:'',pausedClassName:'旧班',longTermAbsent:true}],attendance:[],transactions:[],settings:{}};
 const data=dataset(raw,{asOfDate:'2026-11-01',sourceSha256:'f'.repeat(64),sourceCapturedAt:'Fictional',mirror:true});
 const source={format:'future-school-management-review-v1',schema_version:1,complete:true,generated_at:'2026-11-01T10:00:00Z',organization:'fictional',org_id:'f'.repeat(32),source_access:'read_only',contains_private_data:true,classes:[{id:c,name:'虚构班',course_name:'',teachers:[{id:t,name:'虚构老师'}]}],students:[{id:s,name:'已有学生',external_ref:'001',class_id:c,remaining_units:1000,active:true}],archive_available:true,archived_students:[{id:a,name:'归档学生',external_ref:'003',remaining_units:2500,previous_class_name:'旧班'}],source_links_available:true,source_links:[{kind:'class',source_id:'11',entity_id:c,bundle_sha256:'f'.repeat(64)},{kind:'student',source_id:'22',entity_id:s,bundle_sha256:'f'.repeat(64)},{kind:'student',source_id:'24',entity_id:a,bundle_sha256:'f'.repeat(64)}],staff:[{id:t,login:'teacher',display_name:'虚构老师',active:true,roles:['teacher']}],mirror:{present:true,revision:1,received_at:1000,source_sha256:'e'.repeat(64),as_of_date:data.as_of_date,source_captured_at:'Fictional',source_records:0,device_active:true,device_expires_at:2000,classes:data.classes.map(c=>({...c,teacher_ids:[t]})),lessons:data.lessons,covered_weeks:data.weeks.map(w=>({start:w.start,end:w.end}))}};
 return{raw,source};
}
test('offline proposal reuses stable/archive identities, reserves only missing base ID and preserves current opening balance',()=>{
 const {raw,source}=fixture(),r=build(JSON.stringify(raw),JSON.stringify(source),{organization:'fictional',newId:()=>'9'.repeat(32)});
 assert.equal(r.summary.reused_ids,2);assert.equal(r.summary.reserved_ids,1);assert.equal(r.plan.canonical_openings[0].units,900);assert.deepEqual(r.plan.preserve_archive_ids,['b'.repeat(32)]);
 assert.equal(r.plan.students[1].server_id,'9'.repeat(32));assert.equal(r.plan.students[1].automatic_enrollment_event,false);assert.equal(r.plan.ready_for_import,false);assert.equal(r.plan.activation_authorized,false);assert.equal(r.plan.scope.active_students,2);assert.equal(r.plan.students[2].current_class_id,null);
});
test('changed identity, current mirror, teacher scope or ID collision stops the whole proposal',()=>{
 for(const change of [s=>s.students[0].name='Wrong identity',s=>s.mirror.classes[0].teacher_ids=[],s=>s.mirror.lessons[0].members[0].remaining_units=0]){
  const {raw,source}=fixture();change(source);assert.throws(()=>build(JSON.stringify(raw),JSON.stringify(source),{organization:'fictional'}));
 }
 const {raw,source}=fixture();assert.throws(()=>build(JSON.stringify(raw),JSON.stringify(source),{organization:'fictional',newId:()=>'a'.repeat(32)}),/already used/);
});
test('a paused base identity stays reserved without deleting or moving the existing row',()=>{
 const {raw,source}=fixture(),archived=source.archived_students.pop();source.students.push({...archived,class_id:source.classes[0].id,active:true});delete source.students.at(-1).previous_class_name;
 const r=build(JSON.stringify(raw),JSON.stringify(source),{organization:'fictional'});assert.deepEqual(r.plan.deactivate_without_moving_ids,['b'.repeat(32)]);assert.equal(r.plan.students[2].server_id,'b'.repeat(32));assert.equal(r.plan.side_effects.server_written,false);
});
