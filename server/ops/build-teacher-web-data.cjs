'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const assets=path.resolve(__dirname,'../../app/src/main/assets');
const common=['id','studentId','studentNo','name','classId','className','teacher','classroom','school','hours','createdAt','longTermAbsent','longTermAbsentSince','pausedClassId','pausedClassName','pausedTeacher','startDate','endDate','scheduleWeekday','scheduleIntervalWeeks','scheduleAnchorDate','scheduleStart','scheduleEnd','lessonDurationMinutes','scheduleEndDayOffset','durationMinutes','endDayOffset','courseName','classLabel','fromDate','toDate','regularWeekday','sourceExtra','date','time','status','method','makeup','recordedAt'];
function pick(row){if(!row||typeof row!=='object')throw Error('Invalid snapshot row');const out={};for(const k of common)if(Object.hasOwn(row,k)&&['string','number','boolean'].includes(typeof row[k]))out[k]=row[k];for(const k of ['enrollmentHistory','longTermAbsenceHistory','scheduleHistory','classTimingHistory'])if(Array.isArray(row[k]))out[k]=row[k].map(pick);for(const k of ['sourceSchedule','targetSchedule'])if(row[k])out[k]=pick(row[k]);return out;}
function build(raw,{asOfDate,sourceSha256,sourceCapturedAt}={}){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(asOfDate||'')||!/^[a-f0-9]{64}$/.test(sourceSha256||'')||!sourceCapturedAt)throw Error('Explicit source metadata required');
 if(!Array.isArray(raw.students)||!Array.isArray(raw.classes)||!Array.isArray(raw.attendance)||raw.students.length>10000||raw.attendance.length>100000)throw Error('Complete bounded snapshot required');
 const settings={weeklyTarget:raw.settings?.weeklyTarget||2,cameraPositionConfigured:true};for(const key of ['studentLeaves','extraClassLessons','classReschedules','studentReschedules'])settings[key]=(raw.settings?.[key]||[]).map(pick);
 const data={students:raw.students.map(pick),classes:raw.classes.map(pick),attendance:raw.attendance.map(pick),transactions:[],settings};
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8'),marker="\n$('#todayLabel').textContent =";if(app.indexOf(marker)<0)throw Error('Report engine boundary changed');
 const oldTZ=process.env.TZ;process.env.TZ='Asia/Shanghai';
 try{
  const now=Date.parse(asOfDate+'T08:00:00+08:00'),RealDate=Date;
  class SchoolDate extends RealDate{constructor(...a){super(...(a.length?a:[now]))}static now(){return now}}
  const context=vm.createContext({__snapshot:data,Date:SchoolDate,Intl,Map,Set,JSON,Math,Number,String,Array,Object,Boolean,RegExp,console:{log(){}},window:{},setTimeout(){throw Error('Unexpected timer')},localStorage:{getItem(){return null},setItem(){throw Error('Read-only compilation')}}});
  for(const name of ['class-space.js','lesson-review.js','lesson-cycle.js','lesson-timing.js','student-leave.js'])vm.runInContext(fs.readFileSync(path.join(assets,name),'utf8'),context,{timeout:1000,filename:name});
  vm.runInContext(app.slice(0,app.indexOf(marker)).replace('let state = loadState();','let state = globalThis.__snapshot;'),context,{timeout:2000,filename:'read-only-app-report-engine'});
  const code=`(()=>{const earliest=state.attendance.reduce((a,r)=>r.date&&r.date<a?r.date:a,localDate()),weeks=[],lessons=[];const start=calendarWeekRange(0).start,first=dateWeekStart(earliest);let count=Math.max(12,Math.round((new Date(start+'T12:00:00')-new Date(first+'T12:00:00'))/604800000)+1);if(count>260)throw Error('Historical range exceeds five years; split export explicitly');
   const summary=g=>({name:g.name,expected:g.expected,arrived:g.records.length,leave_count:g.leaves.size,rate:reportRate(g),needs_review:reportNeedsScheduleReview(g)});
   for(let offset=1;offset>=1-count;offset--){const stats=buildWeeklyStats(offset),week={...stats.range,...summary(stats.all),fallback:stats.fallback,classes:[...stats.groups.class.values()].map(summary),teachers:[...stats.groups.teacher.values()].map(summary)};weeks.push(week);
    for(const session of stats.all.sessions.values()){if(!session.date||session.kind==='cancelled')continue;const c=classById(session.classId),timing=c?classTimingProfile(c,session.date):null,records=new Map(session.records.map(r=>[r.studentId,r])),members=new Map(session.members);for(const r of session.records){if(!members.has(r.studentId)){const s=studentById(r.studentId);if(s)members.set(r.studentId,s)}}
     lessons.push({key:String(session.classId||'deleted:'+session.className)+':'+session.date,class_local_id:String(session.classId||''),class_name:session.className,teacher:session.teacher,date:session.date,kind:session.kind,start_time:timing?.scheduleStart||'',end_time:timing?.scheduleEnd||'',expected:session.expected,arrived:session.records.length,leave_count:session.leaves.size,needs_review:reportNeedsScheduleReview(session),members:[...members.values()].map(s=>{const r=records.get(s.id),isExpected=session.members.has(s.id);return{student_local_id:String(s.id),name:s.name,external_ref:s.studentNo||'',remaining_units:Math.round(Number(studentById(s.id)?.hours||0)*100),expected:isExpected,status:r?'arrived':session.leaves.has(s.id)?'leave':'missing',attendance_time:r?.time||'',attendance_method:r?.method||'',makeup:Boolean(r?.makeup)}})});
    }
   }
   return {classes:state.classes.map(c=>({local_id:String(c.id),name:c.name,course_name:c.courseName||'',teacher:c.teacher||''})),weeks,lessons};})()`;
  const result=JSON.parse(JSON.stringify(vm.runInContext(code,context,{timeout:20000})));
  const engineFiles=['app.js','class-space.js','lesson-review.js','lesson-cycle.js','lesson-timing.js','student-leave.js'];
  return {format:'future-school-teacher-web-dataset',schema_version:1,as_of_date:asOfDate,source_sha256:sourceSha256,source_captured_at:sourceCapturedAt,generated_at:new Date().toISOString(),source_records:raw.attendance.length,engine_sha256:Object.fromEntries(engineFiles.map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(path.join(assets,n))).digest('hex')])),...result,history_import_is_read_only:true,balances_charged:false,wechat_enabled:false};
 }finally{if(oldTZ===undefined)delete process.env.TZ;else process.env.TZ=oldTZ;}
}
module.exports={build,pick};
