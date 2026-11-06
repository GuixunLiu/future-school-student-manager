/* Explicit weekly/fortnightly schedules. Existing snapshots without a cycle stay weekly. */
function lessonCycleWeeks(profile){return profile&&profile.scheduleIntervalWeeks!==undefined?Number(profile.scheduleIntervalWeeks):1}
function lessonCycleSnapshot(profile){return{scheduleIntervalWeeks:lessonCycleWeeks(profile),scheduleAnchorDate:lessonCycleWeeks(profile)===2?String(profile.scheduleAnchorDate||''):''}}
function scheduleDayNumber(date){const parts=String(date).split('-').map(Number);return Date.UTC(parts[0],parts[1]-1,parts[2])/86400000}
function regularLessonOnDate(profile,date,weekday=Number(profile&&profile.scheduleWeekday||0)){
  if(!isValidLocalDate(date)||weekday<1||weekday>7||(new Date(`${date}T12:00:00`).getDay()||7)!==weekday)return false;
  const interval=lessonCycleWeeks(profile);if(interval===1)return true;if(interval!==2)return false;
  const anchor=String(profile&&profile.scheduleAnchorDate||'');if(!isValidLocalDate(anchor)||(new Date(`${anchor}T12:00:00`).getDay()||7)!==weekday)return false;
  const days=scheduleDayNumber(date)-scheduleDayNumber(anchor);return days>=0&&days%14===0;
}
function classScheduleProfile(course,date){
  const history=Array.isArray(course.scheduleHistory)?course.scheduleHistory:[],entry=history.filter(item=>item.startDate<=date&&(!item.endDate||item.endDate>=date)).sort((a,b)=>b.startDate.localeCompare(a.startDate))[0],schedule=entry||course;
  return{...course,classId:course.id,className:course.name,scheduleWeekday:Number(schedule.scheduleWeekday||0),...lessonCycleSnapshot(schedule)};
}
function classCycleChanged(course,next){return Number(course.scheduleWeekday||0)!==Number(next.scheduleWeekday||0)||lessonCycleWeeks(course)!==lessonCycleWeeks(next)||lessonCycleSnapshot(course).scheduleAnchorDate!==lessonCycleSnapshot(next).scheduleAnchorDate}
function recordClassCycleHistory(course,next,date=localDate()){
  if(!classCycleChanged(course,next))return;
  if(!Array.isArray(course.scheduleHistory)||!course.scheduleHistory.length)course.scheduleHistory=[{startDate:'2000-01-01',endDate:'',scheduleWeekday:Number(course.scheduleWeekday||0),...lessonCycleSnapshot(course)}];
  const open=[...course.scheduleHistory].reverse().find(item=>!item.endDate),snapshot={scheduleWeekday:Number(next.scheduleWeekday||0),...lessonCycleSnapshot(next)};
  if(open&&open.startDate===date)Object.assign(open,snapshot);else{if(open)open.endDate=dateBefore(date);course.scheduleHistory.push({...snapshot,startDate:date,endDate:''})}
}
function classCycleFormResult(form,course){
  const interval=Number(form.get('scheduleIntervalWeeks')||1),anchor=interval===2?String(form.get('scheduleAnchorDate')||''):'',weekday=Number(form.get('scheduleWeekday')||0),fields={scheduleIntervalWeeks:interval,scheduleAnchorDate:anchor};
  if(![1,2].includes(interval))return{error:'请选择每周一次或每两周一次'};
  if(interval===2&&(!isValidLocalDate(anchor)||weekday<1||weekday>7))return{error:'每两周一次需要选择上课星期及首个上课日期'};
  if(interval===2&&(new Date(`${anchor}T12:00:00`).getDay()||7)!==weekday)return{error:'首个上课日期必须与所选上课星期一致'};
  if(course&&classCycleChanged(course,{...course,...fields,scheduleWeekday:weekday})){
    const dates=classReschedules().filter(item=>String(item.classId)===String(course.id)).concat(personalClassDates(course));if(approvedStudentLeaves().some(item=>String(item.classId)===String(course.id)&&item.date>=localDate()))return{error:'今天及之后已有学生请假，请先核对并撤销相关请假，再修改上课周期或星期'};
    if(dates.some(item=>item.fromDate>=localDate()||item.toDate>=localDate()))return{error:'今天及之后已有调课安排，请先核对并撤销相关安排，再修改上课周期或星期'};
  }
  return{fields};
}
function mountClassCycleEditor(course){
  const form=$('#classForm'),section=document.createElement('section');section.className='class-cycle-editor span-2';
  section.innerHTML=`<div class="class-cycle-heading"><b>上课周期</b><small>每周与隔周班级分别排课，不凭班级名称猜测日期</small></div><div class="class-reminder-time-grid"><label class="field"><span>上课频率</span><select name="scheduleIntervalWeeks"><option value="1" ${lessonCycleWeeks(course)!==2?'selected':''}>每周一次</option><option value="2" ${lessonCycleWeeks(course)===2?'selected':''}>每两周一次（隔周）</option></select></label><label class="field" id="classCycleAnchorField"><span>首个上课日期 *</span><input name="scheduleAnchorDate" type="date" value="${esc(course.scheduleAnchorDate||'')}"></label></div><p id="classCyclePreview"></p><small>${course.id?'保存后的周期变更从今天生效，历史排课、签到和课时不自动改写。':'首个日期当天有课，之后每隔14天一次；仍需按学生实际入学日期计算应到。'}加课、调课可在“加课 / 调课”单独登记。</small>`;
  form.querySelector('.class-reminder-editor').before(section);
  const interval=form.querySelector('[name="scheduleIntervalWeeks"]'),anchor=form.querySelector('[name="scheduleAnchorDate"]'),weekday=form.querySelector('[name="scheduleWeekday"]');
  const refresh=()=>{const biweekly=Number(interval.value)===2;$('#classCycleAnchorField').classList.toggle('hidden',!biweekly);anchor.required=biweekly;$('#classCyclePreview').textContent=!biweekly?'每个排课星期均有一次课。':!isValidLocalDate(anchor.value)?'请选择真实有课的首个日期，用来确定隔周轮次。':Number(weekday.value)!==(new Date(`${anchor.value}T12:00:00`).getDay()||7)?'首个日期与上课星期不一致，请核对。':`排课示例：${[0,14,28,42].map(days=>{const date=new Date(`${anchor.value}T12:00:00`);date.setDate(date.getDate()+days);return localDate(date)}).join('、')}；中间休课周不计缺勤。`};
  interval.onchange=refresh;anchor.onchange=refresh;weekday.onchange=refresh;refresh();
}
function validateCycleData(data){
  const check=(profile,label)=>{
    const interval=lessonCycleWeeks(profile);if(![1,2].includes(interval))throw new Error(`${label}的上课周期无效`);
    if(interval===2&&(!isValidLocalDate(profile.scheduleAnchorDate)||!Number.isInteger(Number(profile.scheduleWeekday))||Number(profile.scheduleWeekday)<1||Number(profile.scheduleWeekday)>7||(new Date(`${profile.scheduleAnchorDate}T12:00:00`).getDay()||7)!==Number(profile.scheduleWeekday)))throw new Error(`${label}的隔周基准日期与上课星期无效或不一致`);
  };
  (data.classes||[]).forEach(course=>{
    check(course,'班级');if(course.scheduleHistory===undefined)return;if(!Array.isArray(course.scheduleHistory))throw new Error('班级周期历史格式无效');
    let previous=null;course.scheduleHistory.forEach(entry=>{if(!entry||!isValidLocalDate(entry.startDate)||entry.endDate&&!isValidLocalDate(entry.endDate)||entry.endDate&&entry.endDate<entry.startDate||previous&&(!previous.endDate||previous.endDate>=entry.startDate))throw new Error('班级周期历史日期冲突');check(entry,'班级周期历史');previous=entry});
    if(previous&&(previous.endDate||Number(previous.scheduleWeekday||0)!==Number(course.scheduleWeekday||0)||lessonCycleWeeks(previous)!==lessonCycleWeeks(course)||lessonCycleSnapshot(previous).scheduleAnchorDate!==lessonCycleSnapshot(course).scheduleAnchorDate))throw new Error('班级当前周期与历史末段不一致');
  });
  (data.students||[]).forEach(student=>(student.enrollmentHistory||[]).forEach(entry=>check(entry,'学生历史排课')));
  ((data.settings&&data.settings.classReschedules)||[]).forEach(change=>{['sourceSchedule','targetSchedule'].forEach(key=>{if(change[key])check(change[key],'调课周期快照')})});
}
