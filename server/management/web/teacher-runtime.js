'use strict';
let teacherDay='',teacherLessons=[],currentLesson=null;
const chinaDateFormat=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
const lessonTimeFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});
function chinaDateKey(date){
 const values={};
 for(const part of chinaDateFormat.formatToParts(date))values[part.type]=part.value;
 return values.year+'-'+values.month+'-'+values.day;
}
function chinaToday(){return chinaDateKey(new Date());}
function lessonTime(seconds){return lessonTimeFormat.format(new Date(seconds*1000));}
function attendanceLabel(student){if(!student.expected)return['无需到课','badge'];return{arrived:['已到','badge arrived'],leave:['请假','badge leave'],missing:['未签到','badge late']}[student.status]||['待核对','badge'];}
function attendanceCounts(students){
 const counts={expected:0,arrived:0,leave:0,missing:0};
 for(const student of students){
  if(student.expected)counts.expected++;
  if(student.status==='arrived')counts.arrived++;
  else if(student.status==='leave')counts.leave++;
  else if(student.expected&&student.status==='missing')counts.missing++;
 }
 return counts;
}
function countCards(counts){const container=node('div',undefined,'course-counts');for(const [key,label] of [['expected','应到'],['arrived','已到'],['leave','请假'],['missing','未签到']]){const item=node('div');item.append(node('strong',String(counts[key])),node('span',label));container.append(item);}return container;}
function teacherIdentity(){$('identity').replaceChildren(node('span',identity.display_name+' · '+((identity.teacher_campus_name===undefined?identity.campus_name:identity.teacher_campus_name)||'教师')),button('退出',logout,'secondary'));}
async function teacherDashboard(day=teacherDay||chinaToday()){
 const data=await api('/v1/teacher/lessons?date='+encodeURIComponent(day));teacherDay=data.date;teacherLessons=data.items;currentLesson=null;teacherIdentity();
 const heading=node('div',undefined,'runtime-summary teacher-heading teacher-day-heading');
 const copy=node('div');
 copy.append(node('h1','我的课堂'));
 const actions=node('div',undefined,'toolbar teacher-actions');
 actions.append(button('刷新',()=>teacherDashboard(teacherDay),'secondary'),
  button('到课率',()=>teacherWeeklyReport(),'secondary'),
  button('我的课时费',()=>teachingFeesDashboard(false),'secondary'));
 if(hasManagementWorkspace())actions.append(button('设备管理',deviceDashboard,'secondary'));
 if(hasStaffRole('performance_reviewer'))actions.append(button('绩效审核',feeReviewDashboard,'secondary'));
 heading.append(copy,actions);

 const filter=node('section',undefined,'teacher-filter teacher-date-filter');
 const form=node('form',undefined,'classroom-date'),label=node('label','课堂日期'),date=node('input');
 date.type='date';date.required=true;date.value=teacherDay;date.setAttribute('aria-label','课堂日期');
 label.append(date);
 const submit=node('button','查看课堂','secondary');submit.type='submit';
 form.append(label,submit);
 const isToday=teacherDay===chinaToday();
 if(!isToday)form.append(button('回到今天',()=>teacherDashboard(chinaToday()),'secondary'));
 filter.append(form);
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{await teacherDashboard(date.value);}catch(error){showError(error);}finally{submit.disabled=false;}
 });

 const listHeading=node('div',undefined,'teacher-section-heading');
 listHeading.append(node('h2',isToday?'今日课堂':'当日课堂'),node('span',teacherLessons.length+' 节课'));
 const list=node('section',undefined,'teacher-course-list');
 list.setAttribute('aria-label','课堂列表');
 if(!teacherLessons.length)list.append(node('div',isToday?'今天没有安排课堂':'这一天没有你的课堂','empty'));
 for(const lesson of teacherLessons){
  const row=node('article',undefined,'teacher-course-row');
  const schedule=node('div',undefined,'teacher-course-time');
  schedule.append(node('strong',lessonTime(lesson.starts_at)+'–'+lessonTime(lesson.ends_at)));
  if(lesson.state!=='scheduled')schedule.append(node('span','已取消','badge lesson-cancelled'));
  else if(lesson.is_substitute)schedule.append(node('span','临时代课','badge'));
  const name=node('div',undefined,'teacher-course-name');
  name.append(node('h3',lesson.class_name));
  if(lesson.course_name)name.append(node('p',lesson.course_name));
  const counts=countCards({expected:lesson.expected_count,arrived:lesson.arrived_count,leave:lesson.leave_count,missing:lesson.missing_count});
  const open=button('查看签到',()=>teacherLesson(lesson.id));
  open.dataset.lessonId=lesson.id;
  row.append(schedule,name,counts,open);
  list.append(row);
 }
 $('main').replaceChildren(heading,filter,listHeading,list);
}
async function teacherLesson(lessonId){
 const data=await api('/v1/teacher/lessons/'+encodeURIComponent(lessonId));currentLesson=data;teacherIdentity();const lesson=data.lesson,summary=teacherLessons.find(item=>item.id===lessonId),title=summary?.class_name||'课堂签到';
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div'),meta=node('div',undefined,'lesson-meta');meta.append(node('span',lesson.state==='scheduled'?'正常课堂':'已取消','badge'+(lesson.state==='scheduled'?'':' lesson-cancelled')),node('p',lessonTime(lesson.starts_at)+'–'+lessonTime(lesson.ends_at)+(summary?.course_name?' · '+summary.course_name:'')));copy.append(node('span','课堂签到','teacher-eyebrow'),node('h1',title),meta);
 const actions=node('div',undefined,'toolbar teacher-actions');
 actions.append(button('返回课堂列表',()=>teacherDashboard(teacherDay),'secondary'),button('刷新',()=>teacherLesson(lessonId),'secondary'));
 heading.append(copy,actions);
 const table=node('table'),head=node('thead'),headRow=node('tr');table.setAttribute('aria-label','学生签到名单');for(const label of ['学生','签到状态','剩余课时','操作']){const cell=node('th',label);cell.scope='col';headRow.append(cell);}head.append(headRow);const body=node('tbody');
 for(const student of data.students){const row=node('tr'),name=node('td',undefined,'student-name');name.textContent=student.name;const status=node('td'),[label,className]=attendanceLabel(student);status.append(node('span',label,className),node('small','状态版本 '+student.attendance_revision,'revision'));const balance=node('td',teacherLessonHours(student.balance_units)),operations=node('td'),buttons=node('div',undefined,'attendance-actions');
  if(student.expected&&student.status==='missing'&&lesson.state==='scheduled'){
   if(student.student_active!==false){
    const face=button('人脸签到',()=>openTeacherFaceCheckin(student,lesson),'gold');
    face.dataset.action='face-checkin';face.dataset.studentId=student.student_id;
    face.setAttribute('aria-label','为'+student.name+'人脸签到');
    if(webFaceEnabled()){
     const starts=new Date(lesson.starts_at*1000);
     const wrongDay=chinaDateKey(starts)!==chinaToday();
     face.disabled=wrongDay||Date.now()<starts.getTime();
     if(face.disabled)face.title=wrongDay?'仅限上课当天人脸签到':'课程开始后才能人脸签到';
    }
    buttons.append(face);
   }
   const leave=button('请假',()=>openTeacherLeave(student),'secondary');
   leave.dataset.action='leave';leave.dataset.studentId=student.student_id;buttons.append(leave);
  }
  if(!buttons.childElementCount)buttons.append(node('span','无需操作','muted'));
  operations.append(buttons);row.append(name,status,balance,operations);body.append(row);}
 table.append(head,body);const scroll=node('div',undefined,'table-scroll');scroll.append(table);const panel=node('section',undefined,'panel attendance-panel teacher-roster');panel.append(scroll);$('main').replaceChildren(heading,countCards(attendanceCounts(data.students)),panel);
}
function openTeacherLeave(student){
 if(!currentLesson)throw Error('课堂资料已失效，请刷新后重试');const lesson=currentLesson.lesson,content=$('dialog-content');content.replaceChildren();const heading=node('h2','为学生请假');heading.id='dialog-title';content.append(heading);
 const fact=node('div',undefined,'correction-student');fact.append(node('strong',student.name),node('p','当前状态：未签到 · 状态版本 '+student.attendance_revision));const form=node('form'),label=node('label','请假原因'),reason=node('textarea');reason.className='correction-reason';reason.required=true;reason.maxLength=200;reason.placeholder='请填写具体原因（1–200 字）';label.append(reason);form.append(label,node('p','确认后签到状态改为“请假”，本次不扣课时。','hint'));
 const submit=node('button','确认请假');submit.type='submit';form.append(submit);content.append(form);const eventRef=runtimeRequestRef();
 form.addEventListener('submit',async event=>{event.preventDefault();const value=reason.value.trim();if(!value){reason.setCustomValidity('请填写原因');reason.reportValidity();return;}reason.setCustomValidity('');submit.disabled=true;try{await api('/v1/teacher/lessons/'+encodeURIComponent(lesson.id)+'/leave',{student_id:student.student_id,reason:value,event_ref:eventRef,expected_revision:student.attendance_revision});$('device-dialog').close();await teacherLesson(lesson.id);notify('请假成功，本次未扣课时');}catch(error){if(error.status===409){$('device-dialog').close();await teacherLesson(lesson.id);notify('签到资料已变化，已为你刷新，请重新核对后再操作');}else showError(error);}finally{submit.disabled=false;}});
 reason.addEventListener('input',()=>reason.setCustomValidity(''));if(!$('device-dialog').open)$('device-dialog').showModal();reason.focus();
}
