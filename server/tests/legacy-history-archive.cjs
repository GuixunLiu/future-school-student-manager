'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{build,digest}=require('../ops/build-legacy-history-archive.cjs');
function fixture(){return{classes:[{id:11,name:'虚构现班',teacher:'虚构老师',scheduleWeekday:6,scheduleHistory:[{startDate:'2026-08-01',endDate:'',scheduleWeekday:6}]}],students:[{id:22,name:'虚构学生',studentNo:'0001',hours:9,className:'虚构现班',faceImage:'SECRET_FACE',contact:'SECRET_CONTACT'}],
 attendance:[{id:101,studentId:22,classId:99,className:'已删除虚构旧班',teacher:'虚构旧老师',date:'2026-10-31',time:'14:00',note:'历史原备注',recordedAt:'2026-10-31T06:00:00Z'}],transactions:[{id:201,studentId:22,classId:99,className:'已删除虚构旧班',attendanceId:101,amount:-1,date:'2026-10-31',time:'14:00',reason:'原扣课流水'}],settings:{adminPattern:'SECRET_PATTERN',studentReschedules:[{studentId:22,studentNo:'0001',classId:11,className:'虚构现班',teacher:'虚构老师',fromDate:'2026-11-07',toDate:'2026-11-08',regularWeekday:6,note:'调课原备注',verifiedAt:'2026-11-01'}]}};}
test('all old-class original facts, linked debit and arrangements survive without invented mapping or private credentials',()=>{
 const data=fixture();data.students[0].hours=9.5;const raw=JSON.stringify(data),r=build(raw);assert.equal(r.artifact.sections.students[0].hours,9.5);assert.equal(JSON.stringify(data),raw);assert.deepEqual(r.artifact.sections.attendance,data.attendance);assert.deepEqual(r.artifact.sections.transactions,data.transactions);assert.deepEqual(r.artifact.sections.student_reschedules,data.settings.studentReschedules);
 assert.equal(r.summary.attendance,1);assert.equal(r.artifact.replay_allowed,false);assert.equal(r.artifact.fresh_capture_authorization,false);assert(!JSON.stringify(r).includes('SECRET_'));assert.equal(r.archive_sha256,digest(r.artifact));
 for(const [key,rows]of Object.entries(r.artifact.sections)){assert.equal(r.artifact.manifest[key].rows,rows.length);assert.equal(r.artifact.manifest[key].sha256,digest(rows));}
});
test('unknown event fields and structural problems require review rather than silent loss',()=>{
 const d=fixture();d.attendance[0].newMeaningfulField='must-not-drop';assert.throws(()=>build(JSON.stringify(d)),/Unknown history/);delete d.attendance[0].newMeaningfulField;d.transactions[0].amount=-2;
 assert.throws(()=>build(JSON.stringify(d)),/structural/);d.transactions[0].amount=-1;
 d.attendance.push({...d.attendance[0]});assert.throws(()=>build(JSON.stringify(d)),/structural/);
});
