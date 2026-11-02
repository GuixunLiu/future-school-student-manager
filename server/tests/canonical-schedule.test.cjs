'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {build}=require('../ops/build-canonical-schedule.cjs'),{digest}=require('../ops/pack-enrollment-review.cjs');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
function fixture(){
 const classes=[{id:11,name:'隔周提高班',teacher:'甲老师',courseName:'提高课程',scheduleWeekday:6,scheduleIntervalWeeks:2,scheduleAnchorDate:'2026-11-07',scheduleStart:'14:00',scheduleEnd:'16:00',lessonDurationMinutes:120},
  {id:12,name:'每周班',teacher:'乙老师',courseName:'基础课程',scheduleWeekday:7,scheduleStart:'09:00',scheduleEnd:'10:30',lessonDurationMinutes:90}];
 const students=classes.map((c,i)=>({id:21+i,name:'虚构学员'+i,studentNo:'T00'+i,className:c.name,hours:10,createdAt:'2026-01-01',enrollmentHistory:[{classId:c.id,className:c.name,teacher:c.teacher,startDate:'2026-01-01',scheduleWeekday:c.scheduleWeekday,scheduleIntervalWeeks:c.scheduleIntervalWeeks||1,scheduleAnchorDate:c.scheduleAnchorDate}]}));
 const raw=JSON.stringify({classes,students,attendance:[],transactions:[],settings:{weeklyTarget:1}}),now=Date.parse('2026-11-02T14:00:00+08:00')/1000;
 const plan={format:'future-school-management-initialization-plan-v1',org_id:'a'.repeat(32),baseline:{tablet_sha256:sha(raw)},
  classes:classes.map(c=>({server_id:'class-'+c.id,local_id:c.id,name:c.name,teacher_id:'teacher-'+c.id,teacher_name:c.teacher})),
  students:students.map((s,i)=>({server_id:'student-'+s.id,local_id:s.id,name:s.name,external_ref:s.studentNo,status:'active',current_class_id:'class-'+classes[i].id,opening_balance_units:1000}))};
 const header={format:'future-school-history-boundary-v1',org_id:plan.org_id,initialization_id:'init',baseline_sha256:sha(raw),archive_sha256:'c'.repeat(64),canonical_from_day:'2026-11-03',canonical_from:Date.parse('2026-11-03T00:00:00+08:00')/1000,openings_sha256:'d'.repeat(64),rehearsal_only:true,activation_authorized:false};
 const boundary={...header,boundary_sha256:digest(header)};return{raw,plan,boundary,now};
}
test('actual App calendar excludes the off-week; 90 and 120 minute lessons both cost one lesson',()=>{
 const f=fixture(),a=build(f.raw,f.plan,f.boundary,{now:f.now}),b=build(f.raw,f.plan,f.boundary,{now:f.now});
 assert.deepEqual(a,b);assert.deepEqual(a.lessons.map(l=>[l.date,l.start_time,l.end_time]),[['2026-11-07','14:00','16:00'],['2026-11-08','09:00','10:30'],['2026-11-15','09:00','10:30']]);
 assert.equal(a.source.facts.length,3);assert(a.source.facts.every(v=>v.charge_units===100&&v.status==='missing'));assert.equal(a.balances_changed,false);assert.equal(a.history_replayed,false);
});
test('stale boundary, balance differences and oversized future windows stop without producing a plan',()=>{
 const f=fixture();assert.throws(()=>build(f.raw,f.plan,f.boundary,{now:f.now+86400}));
 assert.throws(()=>build(f.raw,f.plan,f.boundary,{now:f.now,days:15}));
 const changed=structuredClone(f.plan);changed.students[0].opening_balance_units=900;assert.throws(()=>build(f.raw,changed,f.boundary,{now:f.now}));
 const corrupt={...f.boundary,boundary_sha256:'0'.repeat(64)};assert.throws(()=>build(f.raw,f.plan,corrupt,{now:f.now}));
});
test('a future leave or already recorded arrival cannot be silently converted to missing',()=>{
 const f=fixture(),state=JSON.parse(f.raw);state.attendance.push({id:1,studentId:21,classId:11,className:state.classes[0].name,date:'2026-11-07',time:'14:00',method:'manual'});
 const raw=JSON.stringify(state),plan=structuredClone(f.plan);plan.baseline.tablet_sha256=sha(raw);
 const {boundary_sha256,...header}=f.boundary;header.baseline_sha256=sha(raw);const boundary={...header,boundary_sha256:digest(header)};
 assert.throws(()=>build(raw,plan,boundary,{now:f.now}),e=>e.code==='schedule_student_requires_review');assert.equal(state.students[0].hours,10);
});
