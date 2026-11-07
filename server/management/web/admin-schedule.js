'use strict';
let adminScheduleDate='';
function scheduleDateKey(seconds){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(seconds*1000)),values={};for(const part of parts)values[part.type]=part.value;return values.year+'-'+values.month+'-'+values.day;}
function shiftScheduleDay(day,offset){const [year,month,date]=day.split('-').map(Number);return new Date(Date.UTC(year,month-1,date+offset)).toISOString().slice(0,10);}
function scheduleOperations(item,serverTime,desktop=false){
 const operations=node(desktop?'td':'div',undefined,'directory-actions'),editable=item.state==='scheduled'&&item.starts_at>serverTime&&item.processed_count===0;
 if(editable){const reschedule=button('调整时间',()=>openScheduleReschedule(item),'secondary'),cancel=button('取消课次',()=>openScheduleCancel(item),'secondary danger');if(desktop){reschedule.dataset.action='reschedule-lesson';cancel.dataset.action='cancel-lesson';reschedule.dataset.lessonId=item.id;cancel.dataset.lessonId=item.id;}operations.append(reschedule,cancel);}
 else if(item.state==='cancelled'){const review=button('处理签到',()=>openCancelledLesson(item),'secondary');review.dataset.action='review-cancelled';review.dataset.lessonId=item.id;operations.append(review);}
 else operations.append(node('span',item.processed_count?'已有 '+item.processed_count+' 人处理，保留原课次':'课次已开始，仅可查看','hint'));
 return operations;
}
function scheduleLessonTable(items,serverTime){
 const section=node('section',undefined,'panel'),table=directoryTable(['开始时间','班级与课程','授课教师','扣课规则','应到 / 已到 / 请假 / 未签到','状态','课次操作']),cards=node('div',undefined,'schedule-mobile-list');section.append(node('h2','当日课次'));
 if(!items.length){section.append(node('div','这一天没有课次','empty'));return section;}
 for(const item of items){const row=node('tr'),course=item.class_name+(item.course_name?' · '+item.course_name:''),charge=item.charge_units===null?'—':item.charge_units+' 课时 · 规则 v'+item.charge_rule_revision,counts=item.expected_count+' / '+item.arrived_count+' / '+item.leave_count+' / '+item.missing_count,state=(item.state==='scheduled'?'正常':'已取消')+' · 资料 v'+item.version;
  row.append(node('td',monthlyTime(item.starts_at)),node('td',course),node('td',item.teacher_names||'未安排'),node('td',charge),node('td',counts),node('td',state),scheduleOperations(item,serverTime,true));table.body.append(row);
  const card=node('article',undefined,'item schedule-mobile-card');card.append(node('h3',monthlyTime(item.starts_at)+' · '+course),node('p','授课教师：'+(item.teacher_names||'未安排')+'　｜　'+charge),node('p','应到 / 已到 / 请假 / 未签到：'+counts),node('p',state),scheduleOperations(item,serverTime));cards.append(card);
 }
 table.scroll.classList.add('schedule-desktop');section.append(table.scroll,cards);return section;
}
function scheduleReceipt(receipt){
 if(!receipt)return null;const result=receipt.result,panel=node('section',undefined,'panel enrollment-receipt');
 if(receipt.kind==='cancel'){panel.append(node('h2',result.duplicate?'已恢复取消回执':'课次已取消'),node('p',monthlyTime(result.starts_at)+' · '+result.class_name+' · 资料版本 '+result.version),node('p','应到名单与签到记录已保留；统计排除该课次；未自动退课时。'));}
 else if(receipt.kind==='reschedule'){panel.append(node('h2',result.duplicate?'已恢复改期回执':'课次时间已调整'),node('p',result.class_name+' · '+monthlyTime(result.starts_at)+' · 资料版本 '+result.version),node('p','原名单、签到、课时余额和课时流水均未改变。'));}
 else panel.append(node('h2',result.duplicate?'该课次已创建':'课次创建完成'),node('p',monthlyTime(result.starts_at)+' · '+result.class_name+' · '+result.teacher_name),node('p','统一扣课 '+result.charge_units+' 课时，已纳入 '+result.roster_count+' 名在读学生；余额与历史未改变。'));
 return panel;
}
async function adminScheduleDashboard(day=adminScheduleDate||chinaToday(),receipt=null){
 const [lessonData,classData,teacherData,studentData]=await Promise.all([api('/v1/admin/lessons?date='+encodeURIComponent(day)),api('/v1/admin/classes'),api('/v1/admin/teachers'),api('/v1/admin/students')]);adminScheduleDate=lessonData.date;adminIdentity();
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','课表管理'),node('p','创建未来课次并安排已授权教师。创建时将该班当前在读学生全部列入应到名单，使用统一扣课单位，不立即扣课时。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('招新登记',()=>adminEnrollmentDashboard(),'secondary'),button('刷新',()=>adminScheduleDashboard(adminScheduleDate),'secondary'));heading.append(copy,actions);const sections=[heading];
 const done=scheduleReceipt(receipt);if(done)sections.push(done);
 const filter=node('section',undefined,'panel teacher-filter'),filterForm=node('form',undefined,'classroom-date'),dateLabel=node('label','课表日期'),date=node('input');date.type='date';date.required=true;date.value=adminScheduleDate;date.setAttribute('aria-label','课表日期');dateLabel.append(date);const view=node('button','查看课表');view.type='submit';filterForm.append(dateLabel,view);
 const navigation=node('nav',undefined,'weekly-navigation');navigation.setAttribute('aria-label','课表日期切换');navigation.append(button('前一天',()=>adminScheduleDashboard(shiftScheduleDay(adminScheduleDate,-1)),'secondary'),button('回到今天',()=>adminScheduleDashboard(chinaToday()),'secondary'),button('后一天',()=>adminScheduleDashboard(shiftScheduleDay(adminScheduleDate,1)),'secondary'));filter.append(filterForm,navigation);sections.push(filter);
 const create=node('section',undefined,'panel schedule-create'),form=node('form',undefined,'enrollment-form schedule-form');create.append(node('h2','创建未来课次'),node('p','只能创建服务器当前时间之后的课次，时长不超过 12 小时；同班同一时间不能重复开课。'));
 const classLabel=node('label','开课班级'),classSelect=node('select');classSelect.required=true;classSelect.setAttribute('aria-label','开课班级');for(const item of classData.items){const option=node('option',item.name+(item.course_name?' · '+item.course_name:''));option.value=item.id;classSelect.append(option);}classLabel.append(classSelect);form.append(classLabel);
 const teacherLabel=node('label','授课教师'),teacherSelect=node('select');teacherSelect.required=true;teacherSelect.setAttribute('aria-label','授课教师');teacherLabel.append(teacherSelect);form.append(teacherLabel);
 const start=field(form,'开始时间（北京时间）','datetime-local'),end=field(form,'结束时间（北京时间）','datetime-local');start.value=chinaDateTimeLocal(new Date(Date.now()+3600000));end.value=chinaDateTimeLocal(new Date(Date.now()+2*3600000));
 const charge=field(form,'统一扣课课时','number');charge.min='1';charge.max='10000000';charge.step='1';charge.value='1';charge.inputMode='numeric';
 const confirmation=node('label',undefined,'check renewal-confirmation enrollment-wide'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对班级、教师、时间和扣课单位'));form.append(confirmation);
 const hint=node('p','','hint enrollment-wide'),submit=node('button','确认创建课次','enrollment-wide');submit.type='submit';form.append(hint,submit);create.append(form);sections.push(create,scheduleLessonTable(lessonData.items,lessonData.server_time));
 function refreshTeachers(){const selected=teacherSelect.value,eligible=teacherData.items.filter(item=>(item.class_ids||[]).includes(classSelect.value));teacherSelect.replaceChildren();for(const teacher of eligible){const option=node('option',teacher.display_name+' · '+teacher.login);option.value=teacher.id;teacherSelect.append(option);}if(eligible.some(item=>item.id===selected))teacherSelect.value=selected;teacherSelect.disabled=!eligible.length;submit.disabled=!classData.items.length||!eligible.length;const count=studentData.items.filter(item=>item.active&&item.class_id===classSelect.value).length;hint.textContent=eligible.length?'本班当前 '+count+' 名在读学生将在创建时加入应到名单；空班也会保存课次和默认扣课规则。':'该班暂无在职且已授权的教师，请先完成教师班级授权。';}
 if(!classData.items.length){classSelect.disabled=true;teacherSelect.disabled=true;submit.disabled=true;hint.textContent='尚未建立班级，不能创建课次。';}else refreshTeachers();
 classSelect.addEventListener('change',refreshTeachers);$('main').replaceChildren(...sections);
 filterForm.addEventListener('submit',async event=>{event.preventDefault();view.disabled=true;try{await adminScheduleDashboard(date.value);}catch(error){showError(error);}finally{if(view.isConnected)view.disabled=false;}});
 let previousPayload='',commandRef='';form.addEventListener('submit',async event=>{event.preventDefault();const units=Number(charge.value),startsAt=Math.floor(Date.parse(start.value+':00+08:00')/1000),endsAt=Math.floor(Date.parse(end.value+':00+08:00')/1000);
  if(!Number.isInteger(units)||units<1||units>10000000){charge.setCustomValidity('扣课课时必须是 1 至 10000000 的整数');charge.reportValidity();return;}charge.setCustomValidity('');
  if(!Number.isFinite(startsAt)){start.setCustomValidity('请选择有效的开始时间');start.reportValidity();return;}start.setCustomValidity('');if(!Number.isFinite(endsAt)||endsAt<=startsAt||endsAt-startsAt>12*3600){end.setCustomValidity('结束时间必须晚于开始时间，且时长不超过 12 小时');end.reportValidity();return;}end.setCustomValidity('');
  const payload={class_id:classSelect.value,teacher_id:teacherSelect.value,starts_at:startsAt,ends_at:endsAt,charge_units:units,command_ref:''},serialized=JSON.stringify({...payload,command_ref:undefined});if(serialized!==previousPayload){commandRef=crypto.randomUUID().replaceAll('-','');previousPayload=serialized;}payload.command_ref=commandRef;submit.disabled=true;
  try{const result=await api('/v1/admin/lessons',payload);await adminScheduleDashboard(scheduleDateKey(result.starts_at),{kind:'create',result});notify((result.duplicate?'该课次已保存，未重复创建。':'课次创建成功：')+result.class_name+'，'+result.teacher_name+'，应到 '+result.roster_count+' 人');}catch(error){showError(error);}finally{if(submit.isConnected)submit.disabled=false;}
 });
 for(const input of [start,end,charge])input.addEventListener('input',()=>input.setCustomValidity(''));
}
function scheduleConflict(error,day){if(error.status===409){$('device-dialog').close();adminScheduleDashboard(day).then(()=>notify(error.message)).catch(showError);return true;}return false;}
function scheduleReason(form,title,placeholder){const label=node('label',title),reason=node('textarea');reason.required=true;reason.maxLength=200;reason.className='directory-reason';reason.placeholder=placeholder;label.append(reason);form.append(label);return reason;}
function openScheduleReschedule(item){
 const content=directoryDialog('调整课次时间 · '+item.class_name),form=node('form',undefined,'directory-form');content.append(node('p','原时间：'+monthlyTime(item.starts_at)+'；授课教师：'+(item.teacher_names||'未安排'),'hint'));
 const start=field(form,'新开始时间（北京时间）','datetime-local'),end=field(form,'新结束时间（北京时间）','datetime-local');start.value=chinaDateTimeLocal(new Date(item.starts_at*1000));end.value=chinaDateTimeLocal(new Date(item.ends_at*1000));const reason=scheduleReason(form,'调课原因','例如：教室调整，已与教师确认');
 const checkLabel=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;checkLabel.append(check,node('span','已核对新时间；保持原名单、教师和扣课规则'));form.append(checkLabel,node('p','只能调整未开始且无签到处理的课次。改期不删除名单、不重复扣课时。','hint'));
 const submit=node('button','确认调整时间');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{event.preventDefault();const startsAt=Math.floor(Date.parse(start.value+':00+08:00')/1000),endsAt=Math.floor(Date.parse(end.value+':00+08:00')/1000);if(!Number.isFinite(startsAt)){start.setCustomValidity('请选择有效的开始时间');start.reportValidity();return;}start.setCustomValidity('');if(!Number.isFinite(endsAt)||endsAt<=startsAt||endsAt-startsAt>12*3600){end.setCustomValidity('结束时间必须晚于开始时间，且时长不超过 12 小时');end.reportValidity();return;}end.setCustomValidity('');const payload=command({starts_at:startsAt,ends_at:endsAt,expected_version:item.version,reason:reason.value.trim(),command_ref:''});submit.disabled=true;
  try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.id),payload,'PUT');$('device-dialog').close();await adminScheduleDashboard(scheduleDateKey(result.starts_at),{kind:'reschedule',result});notify('课次时间已调整；名单和课时账本未改变');}catch(error){if(!scheduleConflict(error,scheduleDateKey(item.starts_at)))showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
 for(const input of [start,end])input.addEventListener('input',()=>input.setCustomValidity(''));
}
async function openCancelledLesson(item){
 const data=await api('/v1/admin/lessons/'+encodeURIComponent(item.id)),lesson=data.lesson;if(lesson.state!=='cancelled')throw Error('课次状态已变化，请刷新后重新核对');
 const content=directoryDialog('取消课次签到处理 · '+lesson.class_name);content.append(node('p',monthlyTime(lesson.starts_at)+' · '+(lesson.course_name||'未设置课程'),'hint'),node('p','仅用于核对取消前的实际到课或请假，以及由此产生的平板签到冲突。补签按原名单规则扣课时，请假不扣课时；课次保持取消，仍不计入到课率。','hint'));
 if(!data.students.length){content.append(node('div','该课次没有学生名单','empty'));}else{
  const table=directoryTable(['学生 / 学号','签到状态','剩余课时','原扣课规则','处理']);
  for(const student of data.students){const row=node('tr'),identity=node('td');identity.append(node('strong',student.name),node('small',student.external_ref));const status={arrived:'已到',leave:'请假',missing:student.expected?'未签到':'无需到课'}[student.status]||'待核对',operations=node('td',undefined,'directory-actions');
   if(student.expected&&student.status==='missing'){const makeup=button('补签',()=>openCancelledCorrection(item,student,'makeup')),leave=button('请假',()=>openCancelledCorrection(item,student,'leave'),'secondary');makeup.dataset.action='cancelled-makeup';leave.dataset.action='cancelled-leave';makeup.dataset.studentId=student.student_id;leave.dataset.studentId=student.student_id;operations.append(makeup,leave);}else operations.append(node('span',student.status==='missing'?'无需处理':'已处理，保留记录','hint'));
   row.append(identity,node('td',status+' · v'+student.attendance_revision),node('td',student.balance_units+' 课时'),node('td',student.charge_units+' 课时'),operations);table.body.append(row);
  }content.append(table.scroll);
 }
 if(!$('device-dialog').open)$('device-dialog').showModal();
}
function openCancelledCorrection(item,student,kind){
 const content=directoryDialog((kind==='makeup'?'取消课次补签 · ':'取消课次请假 · ')+student.name),form=node('form',undefined,'directory-form');content.append(node('p','学号 '+student.external_ref+' · 当前未签到 v'+student.attendance_revision+' · 原扣课 '+student.charge_units+' 课时','hint'));
 const reason=scheduleReason(form,kind==='makeup'?'实际到课与补签原因':'请假原因',kind==='makeup'?'例如：取消前学生已到课，平板记录待核对':'例如：家长已提前请假，取消前仍需归档'),checkLabel=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;checkLabel.append(check,node('span',kind==='makeup'?'已核对实际到课；确认按原规则扣课时，平板归档不再扣一次':'已核对请假；本次不扣课时，平板仅归档结果'));form.append(checkLabel,node('p','此操作不会恢复课次，取消课次仍不进入到课率。重复点击或网络重试使用同一操作编号。','hint'));
 const submit=node('button',kind==='makeup'?'确认补签并扣课时':'确认请假');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const eventRef=crypto.randomUUID().replaceAll('-','');
 form.addEventListener('submit',async event=>{event.preventDefault();const payload={student_id:student.student_id,reason:reason.value.trim(),event_ref:eventRef,expected_revision:student.attendance_revision};submit.disabled=true;try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.id)+'/'+kind,payload);$('device-dialog').close();await adminScheduleDashboard(scheduleDateKey(item.starts_at));notify((result.duplicate?'已恢复上次处理结果：':kind==='makeup'?'补签完成：':'请假完成：')+student.name+(kind==='makeup'?'，服务器仅扣一次课时':'，未扣课时')+'；平板可核对归档');}catch(error){if(!scheduleConflict(error,scheduleDateKey(item.starts_at)))showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
function openScheduleCancel(item){
 const content=directoryDialog('取消课次 · '+item.class_name),form=node('form',undefined,'directory-form');content.append(node('p',monthlyTime(item.starts_at)+' · '+(item.teacher_names||'未安排')+' · 应到 '+item.expected_count+' 人'),node('p','取消后保留原名单和签到记录，统计排除该课次；不删除历史，也不会自动退课时。','hint'));
 const reason=scheduleReason(form,'取消原因','例如：教师停课，已与教务确认'),checkLabel=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;checkLabel.append(check,node('span','确认取消该课次，并知悉名单保留且不会自动退课时'));form.append(checkLabel);
 const submit=node('button','确认取消课次','danger');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{event.preventDefault();const payload=command({expected_version:item.version,reason:reason.value.trim(),command_ref:''});submit.disabled=true;try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.id)+'/cancel',payload);$('device-dialog').close();await adminScheduleDashboard(scheduleDateKey(result.starts_at),{kind:'cancel',result});notify('课次已取消；名单保留，统计排除，未自动退课时');}catch(error){if(!scheduleConflict(error,scheduleDateKey(item.starts_at)))showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
