'use strict';
let teacherDay='',teacherLessons=[],currentLesson=null;
function chinaToday(){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()),values={};for(const part of parts)values[part.type]=part.value;return values.year+'-'+values.month+'-'+values.day;}
function lessonTime(seconds){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(seconds*1000));}
function attendanceLabel(student){if(!student.expected)return['无需到课','badge'];return{arrived:['已到','badge arrived'],leave:['请假','badge leave'],missing:['未签到','badge late']}[student.status]||['待核对','badge'];}
function attendanceCounts(students){return{expected:students.filter(item=>item.expected).length,arrived:students.filter(item=>item.status==='arrived').length,leave:students.filter(item=>item.status==='leave').length,missing:students.filter(item=>item.expected&&item.status==='missing').length};}
function countCards(counts){const container=node('div',undefined,'course-counts');for(const [key,label] of [['expected','应到'],['arrived','已到'],['leave','请假'],['missing','未签到']]){const item=node('div');item.append(node('strong',String(counts[key])),node('span',label));container.append(item);}return container;}
function teacherIdentity(){$('identity').replaceChildren(node('span',identity.display_name+' · '+(identity.campus_name||'教师')),button('退出',logout,'secondary'));}
async function teacherDashboard(day=teacherDay||chinaToday()){
 const data=await api('/v1/teacher/lessons?date='+encodeURIComponent(day));teacherDay=data.date;teacherLessons=data.items;currentLesson=null;teacherIdentity();
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div');copy.append(node('span','教师工作台','teacher-eyebrow'),node('h1','我的课堂'),node('p','查看任课班级，处理人脸签到和请假。'));
 const actions=node('div',undefined,'toolbar teacher-actions');
 actions.append(button('刷新',()=>teacherDashboard(teacherDay),'secondary'),
  button('到课率',()=>teacherWeeklyReport(),'secondary'),
  button('我的课时费',()=>teachingFeesDashboard(false),'secondary'),
  button('课后反馈',()=>notify('课后反馈暂未开放'),'secondary'));
 if(identity.roles.includes('admin'))actions.append(button('设备管理',dashboard,'secondary'));
 heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter teacher-date-filter'),form=node('form',undefined,'classroom-date'),label=node('label','课堂日期'),date=node('input');date.type='date';date.required=true;date.value=teacherDay;date.setAttribute('aria-label','课堂日期');label.append(date);const submit=node('button','查看课堂');submit.type='submit';form.append(label,submit);filter.append(form);
 form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{await teacherDashboard(date.value);}catch(error){showError(error);}finally{submit.disabled=false;}});
 const list=node('section',undefined,'grid course-grid teacher-course-grid');if(!teacherLessons.length)list.append(node('div','这一天没有你的课堂','empty'));
 for(const lesson of teacherLessons){const card=node('article',undefined,'panel course-card'),meta=node('div',undefined,'lesson-meta');meta.append(node('span',lesson.state==='scheduled'?(lesson.is_substitute?'临时代课':'正常课堂'):'已取消','badge'+(lesson.state==='scheduled'?'':' lesson-cancelled')),node('p',lessonTime(lesson.starts_at)+'–'+lessonTime(lesson.ends_at)));card.append(meta,node('h2',lesson.class_name),node('p',lesson.course_name||'未设置课程名称'),countCards({expected:lesson.expected_count,arrived:lesson.arrived_count,leave:lesson.leave_count,missing:lesson.missing_count}));const open=button('查看签到',()=>teacherLesson(lesson.id));open.dataset.lessonId=lesson.id;card.append(open);list.append(card);}
 const listHeading=node('div',undefined,'teacher-section-heading');listHeading.append(node('h2','当日课堂'),node('span',teacherLessons.length+' 节课'));
 $('main').replaceChildren(heading,filter,listHeading,list);
}
async function teacherLesson(lessonId){
 const data=await api('/v1/teacher/lessons/'+encodeURIComponent(lessonId));currentLesson=data;teacherIdentity();const lesson=data.lesson,summary=teacherLessons.find(item=>item.id===lessonId),title=summary?.class_name||'课堂签到';
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div'),meta=node('div',undefined,'lesson-meta');meta.append(node('span',lesson.state==='scheduled'?'正常课堂':'已取消','badge'+(lesson.state==='scheduled'?'':' lesson-cancelled')),node('p',lessonTime(lesson.starts_at)+'–'+lessonTime(lesson.ends_at)+(summary?.course_name?' · '+summary.course_name:'')));copy.append(node('span','课堂签到','teacher-eyebrow'),node('h1',title),meta);
 const actions=node('div',undefined,'toolbar teacher-actions');actions.append(button('返回课堂列表',()=>teacherDashboard(teacherDay),'secondary'),button('刷新',()=>teacherLesson(lessonId),'secondary'));if(lesson.state==='scheduled'){const face=button('人脸签到',openTeacherFaceCheckin,'gold');face.disabled=Date.now()<lesson.starts_at*1000;if(face.disabled)face.title='课程开始后才能人脸签到';face.dataset.action='face-checkin';actions.append(face);}heading.append(copy,actions);
 const table=node('table'),head=node('thead'),headRow=node('tr');table.setAttribute('aria-label','学生签到名单');for(const label of ['学生','签到状态','剩余课时','操作']){const cell=node('th',label);cell.scope='col';headRow.append(cell);}head.append(headRow);const body=node('tbody');
 for(const student of data.students){const row=node('tr'),name=node('td',undefined,'student-name');name.textContent=student.name;const status=node('td'),[label,className]=attendanceLabel(student);status.append(node('span',label,className),node('small','状态版本 '+student.attendance_revision,'revision'));const balance=node('td',String(student.balance_units)+' 课时'),operations=node('td'),buttons=node('div',undefined,'attendance-actions');
  if(student.expected&&student.status==='missing'&&lesson.state==='scheduled'){const leave=button('请假',()=>openTeacherLeave(student),'secondary');leave.dataset.action='leave';leave.dataset.studentId=student.student_id;buttons.append(leave);}else buttons.append(node('span','无需操作','muted'));
  operations.append(buttons);row.append(name,status,balance,operations);body.append(row);}
 table.append(head,body);const scroll=node('div',undefined,'table-scroll');scroll.append(table);const panel=node('section',undefined,'panel attendance-panel teacher-roster');panel.append(scroll);$('main').replaceChildren(heading,countCards(attendanceCounts(data.students)),panel);
}
function openTeacherLeave(student){
 if(!currentLesson)throw Error('课堂资料已失效，请刷新后重试');const lesson=currentLesson.lesson,content=$('dialog-content');content.replaceChildren();const heading=node('h2','为学生请假');heading.id='dialog-title';content.append(heading);
 const fact=node('div',undefined,'correction-student');fact.append(node('strong',student.name),node('p','当前状态：未签到 · 状态版本 '+student.attendance_revision));const form=node('form'),label=node('label','请假原因'),reason=node('textarea');reason.className='correction-reason';reason.required=true;reason.maxLength=200;reason.placeholder='请填写具体原因（1–200 字）';label.append(reason);form.append(label,node('p','确认后签到状态改为“请假”，本次不扣课时。','hint'));
 const submit=node('button','确认请假');submit.type='submit';form.append(submit);content.append(form);const eventRef=crypto.randomUUID().replaceAll('-','');
 form.addEventListener('submit',async event=>{event.preventDefault();const value=reason.value.trim();if(!value){reason.setCustomValidity('请填写原因');reason.reportValidity();return;}reason.setCustomValidity('');submit.disabled=true;try{await api('/v1/teacher/lessons/'+encodeURIComponent(lesson.id)+'/leave',{student_id:student.student_id,reason:value,event_ref:eventRef,expected_revision:student.attendance_revision});$('device-dialog').close();await teacherLesson(lesson.id);notify('请假成功，本次未扣课时');}catch(error){if(error.status===409){$('device-dialog').close();await teacherLesson(lesson.id);notify('签到资料已变化，已为你刷新，请重新核对后再操作');}else showError(error);}finally{submit.disabled=false;}});
 reason.addEventListener('input',()=>reason.setCustomValidity(''));if(!$('device-dialog').open)$('device-dialog').showModal();reason.focus();
}
