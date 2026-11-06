/* Confirmed current duration defaults. Never infer lesson charges from clock minutes. */
const LESSON_DURATION_POLICY_FROM='2026-10-27';
function lessonTimingDate(value){return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value+'T12:00:00Z'))&&new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;}
function validLessonDuration(value){return Number.isInteger(value)&&value>=1&&value<=720;}
function defaultLessonDuration(course){return String(course&&course.courseName||'').trim()==='信息学奥赛提高班'?120:90;}
function classLessonDuration(course){return course&&course.lessonDurationMinutes!==undefined?course.lessonDurationMinutes:defaultLessonDuration(course);}
function lessonClockEnd(start,duration){
  if(typeof start!=='string'||!/^([01]\d|2[0-3]):[0-5]\d$/.test(start)||!validLessonDuration(duration))return null;
  const minutes=Number(start.slice(0,2))*60+Number(start.slice(3))+duration;
  return{time:String(Math.floor(minutes/60)%24).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0'),dayOffset:Math.floor(minutes/1440)};
}
function classTimingProfile(course,date){
  const created=String(course.createdAt||'').slice(0,10);if(lessonTimingDate(created)&&date<created)return null;
  const entries=course.classTimingHistory||[],entry=entries.find(e=>e.startDate<=date&&(!e.endDate||e.endDate>=date));
  if(!entry&&(entries.length||date<LESSON_DURATION_POLICY_FROM))return null;
  const start=entry?entry.scheduleStart:String(course.scheduleStart||''),duration=entry?entry.lessonDurationMinutes:classLessonDuration(course),end=lessonClockEnd(start,duration);
  return end?{scheduleStart:start,lessonDurationMinutes:duration,scheduleEnd:end.time,scheduleEndDayOffset:end.dayOffset}:null;
}
function classTimingHistoryForSave(course,next,date){
  if(!lessonTimingDate(date))throw Error('保存日期无效');
  if(course)validateClassLessonTiming({classes:[course]});
  const snapshot={scheduleStart:String(next.scheduleStart||''),lessonDurationMinutes:next.lessonDurationMinutes};
  if(!course)return[{...snapshot,startDate:date,endDate:''}];
  let history=JSON.parse(JSON.stringify(course.classTimingHistory||[]));
  if(!history.length){let from=date<LESSON_DURATION_POLICY_FROM?date:LESSON_DURATION_POLICY_FROM;const created=String(course.createdAt||'').slice(0,10);if(lessonTimingDate(created)&&created>from&&created<=date)from=created;history=[{startDate:from,endDate:'',scheduleStart:String(course.scheduleStart||''),lessonDurationMinutes:classLessonDuration(course)}];}
  const last=history[history.length-1];if(last.endDate||last.startDate>date)throw Error('日期早于已保存的上课时间，请先核对系统日期');
  if(last.scheduleStart===snapshot.scheduleStart&&last.lessonDurationMinutes===snapshot.lessonDurationMinutes)return history;
  if(last.startDate===date)Object.assign(last,snapshot);else{if(history.length>=1000)throw Error('上课时间历史已达1000段，未保存本次修改');const previous=new Date(date+'T12:00:00Z');previous.setUTCDate(previous.getUTCDate()-1);last.endDate=previous.toISOString().slice(0,10);history.push({...snapshot,startDate:date,endDate:''});}
  return history;
}
function classTimingFormResult(form){
  const raw=String(form.get('lessonDurationMinutes')||'');if(!/^\d+$/.test(raw)||!validLessonDuration(Number(raw)))return{error:'每节课时长请填写1至720之间的整数分钟'};
  const duration=Number(raw),start=String(form.get('scheduleStart')||'').trim(),end=start?lessonClockEnd(start,duration):null;
  if(start&&!end)return{error:'请填写有效的开课时间'};
  return{fields:{lessonDurationMinutes:duration,scheduleEnd:end?end.time:'',scheduleEndDayOffset:end?end.dayOffset:0}};
}
function mountClassTimingEditor(course){
  const form=$('#classForm'),section=document.createElement('section');section.className='class-cycle-editor span-2';section.innerHTML=`<div class="class-cycle-heading"><b>每节课时长</b><small>提高班默认120分钟，其他课程默认90分钟；可单独调整</small></div><div class="class-reminder-time-grid"><label class="field"><span>时长（分钟） *</span><input name="lessonDurationMinutes" type="number" min="1" max="720" step="1" required value="${esc(classLessonDuration(course))}"></label><label class="field"><span>预计下课时间</span><output id="classLessonEndPreview"></output></label></div><small>只计算实际结束时间，签到仍按原规则扣课。保存后从今天生效，保留原时间段；已保存的服务器课次需单独调整。</small>`;form.querySelector('.class-reminder-editor').after(section);
  const duration=form.querySelector('[name="lessonDurationMinutes"]'),start=form.querySelector('[name="scheduleStart"]'),category=form.querySelector('[name="courseName"]');let custom=false;
  const refresh=()=>{const end=lessonClockEnd(start.value,Number(duration.value));$('#classLessonEndPreview').textContent=end?(end.dayOffset?'次日 ':'')+end.time:'填写开课时间后自动计算';};duration.addEventListener('input',()=>{custom=true;refresh()});start.addEventListener('input',refresh);start.addEventListener('change',refresh);if(category&&!course.id){if(course.lessonDurationMinutes===undefined)duration.value=defaultLessonDuration({courseName:category.value});category.addEventListener('input',()=>{if(!custom){duration.value=defaultLessonDuration({courseName:category.value});refresh()}});}refresh();
}
function preserveClassTimingOnGrouping(course,nextCourseName){
  if(course.lessonDurationMinutes!==undefined)return;
  if(nextCourseName!==undefined&&classLessonDuration(course)===defaultLessonDuration({courseName:nextCourseName}))return;
  const duration=classLessonDuration(course),end=lessonClockEnd(String(course.scheduleStart||''),duration),next={...course,lessonDurationMinutes:duration};
  course.classTimingHistory=classTimingHistoryForSave(course,next,localDate());course.lessonDurationMinutes=duration;course.scheduleEnd=end?end.time:'';course.scheduleEndDayOffset=end?end.dayOffset:0;
}
function validateClassLessonTiming(data){
  (data.classes||[]).forEach(course=>{
    if(course.lessonDurationMinutes!==undefined&&!validLessonDuration(course.lessonDurationMinutes))throw Error('备份中的班级课时长无效');
    const expected=lessonClockEnd(String(course.scheduleStart||''),classLessonDuration(course));
    if(course.scheduleEnd!==undefined&&(typeof course.scheduleEnd!=='string'||course.scheduleEnd!==(expected?expected.time:'')))throw Error('备份的下课时间与时长不一致');
    if(course.scheduleEndDayOffset!==undefined&&course.scheduleEndDayOffset!==(expected?expected.dayOffset:0))throw Error('备份的跨日时间不一致');
    if(course.classTimingHistory===undefined)return;
    if(!Array.isArray(course.classTimingHistory)||!course.classTimingHistory.length||course.classTimingHistory.length>1000)throw Error('班级上课时间历史格式无效');
    let previous=null;course.classTimingHistory.forEach(entry=>{if(!entry||!lessonTimingDate(entry.startDate)||entry.endDate&&!lessonTimingDate(entry.endDate)||entry.endDate&&entry.endDate<entry.startDate||previous&&(!previous.endDate||previous.endDate>=entry.startDate)||!validLessonDuration(entry.lessonDurationMinutes)||typeof entry.scheduleStart!=='string'||entry.scheduleStart&&!lessonClockEnd(entry.scheduleStart,entry.lessonDurationMinutes))throw Error('班级上课时间历史有日期、时长或时间问题');previous=entry});
    if(previous.endDate||previous.scheduleStart!==String(course.scheduleStart||'')||previous.lessonDurationMinutes!==classLessonDuration(course))throw Error('班级当前上课时间与历史末段不一致');
  });
}
if(typeof module!=='undefined'&&module.exports)module.exports={LESSON_DURATION_POLICY_FROM,validLessonDuration,defaultLessonDuration,classLessonDuration,lessonClockEnd,classTimingProfile,classTimingHistoryForSave,validateClassLessonTiming};
