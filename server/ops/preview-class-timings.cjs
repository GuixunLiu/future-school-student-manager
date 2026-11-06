'use strict';
// Read-only planning; accepts a complete tablet backup, emits class fields only.
const timing=require('../../app/src/main/assets/lesson-timing.js');
function preview(data,{effectiveDate='2026-10-27',sourceSha256}={}){
  if(!data||!Array.isArray(data.classes)||data.classes.length>10000||!/^\d{4}-\d{2}-\d{2}$/.test(effectiveDate)||!Number.isFinite(Date.parse(effectiveDate+'T12:00:00Z'))||new Date(effectiveDate+'T12:00:00Z').toISOString().slice(0,10)!==effectiveDate||!sourceSha256||!/^[a-f0-9]{64}$/.test(sourceSha256))throw Error('完整班级清单、日期或源校验值无效');
  timing.validateClassLessonTiming(data);
  const ids=new Set(),names=new Set(),rows=[],issues=[];
  for(const course of data.classes){
    if(!course||!Number.isSafeInteger(course.id)||course.id<=0||ids.has(course.id)||typeof course.name!=='string'||!course.name.trim()||names.has(course.name)||course.name.length>80||typeof course.courseName!=='string'||!course.courseName.trim()||course.courseName.length>80)throw Error('班级身份或课程归属缺失/重复，不能猜测时长');ids.add(course.id);names.add(course.name);
    const current=timing.classTimingProfile(course,effectiveDate),weekday=Number(course.scheduleWeekday||0),interval=course.scheduleIntervalWeeks===undefined?1:course.scheduleIntervalWeeks,anchor=course.scheduleAnchorDate||'';
    if(!Number.isInteger(weekday)||weekday<0||weekday>7||![1,2].includes(interval))throw Error('班级课日或周期无效');
    if(interval===2){let valid=false;try{valid=/^\d{4}-\d{2}-\d{2}$/.test(anchor)&&new Date(anchor+'T12:00:00Z').toISOString().slice(0,10)===anchor&&(new Date(anchor+'T12:00:00Z').getUTCDay()||7)===weekday}catch(_){}if(!valid)throw Error('隔周首课日期或星期无效');}
    if(!current||!weekday)issues.push({class_local_id:course.id,message:'开课时间、日期口径或上课星期仍需确认'});
    rows.push({class_local_id:course.id,class_name:course.name,course_name:course.courseName,teacher_label:typeof course.teacher==='string'?course.teacher.slice(0,80):'',weekday,interval_weeks:interval,anchor_date:interval===2?anchor:'',start_time:current?current.scheduleStart:String(course.scheduleStart||''),duration_minutes:current?current.lessonDurationMinutes:null,end_time:current?current.scheduleEnd:'',end_day_offset:current?current.scheduleEndDayOffset:null,duration_source:course.lessonDurationMinutes===undefined?'confirmed_course_default':'saved_class_setting'});
  }
  return{format:'future-school-class-timing-preview',schema_version:1,source_sha256:sourceSha256,effective_from:effectiveDate,preview_only:true,ready_for_import:false,server_written:false,classes:rows,issues,notes:['本预览不展开实际课次、不创建学生、不改历史或发送消息','默认规则仅从用户确认日期起用于当前/未来排课','教师标签不等于真实人员授权，服务器课次和名单仍需单独核对','后续班级设置变化后应使用最新备份重建预览']};
}
module.exports={preview};
if(require.main===module){const fs=require('node:fs'),crypto=require('node:crypto');try{if(process.argv.length!==4)throw Error('用法：node preview-class-timings.cjs 输入备份.json 新预览.json');const bytes=fs.readFileSync(process.argv[2]),result=preview(JSON.parse(bytes.toString('utf8')),{sourceSha256:crypto.createHash('sha256').update(bytes).digest('hex')});fs.writeFileSync(process.argv[3],JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});console.log(JSON.stringify({classes:result.classes.length,issues:result.issues.length,preview_only:true,server_written:false}))}catch(e){console.error('STOP: '+e.message);process.exitCode=1}}
