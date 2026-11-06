/* Local, single-lesson leave. Parent requests require a future authenticated service. */
function studentLeaves(){return Array.isArray(state.settings.studentLeaves)?state.settings.studentLeaves:[]}
function approvedStudentLeaves(){return studentLeaves().filter(item=>item.status==='approved')}
function studentLeaveAt(profile,date){return approvedStudentLeaves().find(item=>item.studentId===profile.id&&String(item.classId)===profileClassId(profile)&&item.date===date)}
function studentHasLeaveOnDate(studentId,date){return approvedStudentLeaves().some(item=>item.studentId===Number(studentId)&&item.date===date)}
// Leave excuses reminders, not the scheduled obligation; attendance alone counts as arrived.
function applyStudentLeavePlan(profile,date,plan){const leave=plan.physical&&studentLeaveAt(profile,date);return leave?{...plan,leave}:plan}
function classLeaveConflict(course,dates){return approvedStudentLeaves().some(item=>String(item.classId)===String(course.id)&&dates.includes(item.date))}
function leaveActionMarkup(profile,courseId,date){return isAdminAuthenticated&&isValidLocalDate(date)?`<button type="button" class="leave-action-btn" data-student-leave="${profile.id}" data-leave-class="${esc(courseId)}" data-leave-date="${esc(date)}">请假</button>`:''}
function studentLeaveReasonMarkup(leave){return `<small>${esc(leave.reason)} · 办理老师：${esc(leave.recordedBy)}</small><small>登记时间：${esc(new Date(leave.createdAt).toLocaleString('zh-CN',{hour12:false}))}</small>`}
function leaveRecordsMarkup(courseId,date){
  const records=studentLeaves().filter(item=>String(item.classId)===String(courseId)&&item.date===date);
  return `<section class="leave-records"><div class="leave-records-heading"><b>本次请假记录</b><span>${records.filter(item=>item.status==='approved').length} 条有效</span></div>${records.map(item=>{const student=studentById(item.studentId);return `<article class="leave-record"><div><b>${esc(student&&student.name||item.studentNo)} <em>${item.status==='approved'?'已请假':'已撤销'}</em></b>${studentLeaveReasonMarkup(item)}${item.status==='cancelled'?`<small>撤销：${esc(item.cancelledBy)} · ${esc(item.cancelReason)}</small>`:''}</div>${item.status==='approved'?`<button type="button" class="mini-btn warn" data-cancel-student-leave="${item.id}">撤销请假</button>`:''}</article>`}).join('')||'<p>尚未登记请假。</p>'}</section>`;
}
function saveStudentLeave(studentId,courseId,date,reason,recordedBy){
  if(!isAdminAuthenticated)return{ok:false,error:'请老师先解锁后台，再办理请假'};
  const matches=state.students.filter(s=>s.id===Number(studentId)),student=matches[0],course=classById(courseId),cleanReason=String(reason||'').trim(),operator=String(recordedBy||'').trim();
  if(matches.length!==1||!course)return{ok:false,error:'学生或班级已变化，或存在重复内部编号，请先核对档案'};
  if(!isValidLocalDate(date))return{ok:false,error:'请选择有效的请假日期'};
  if(!cleanReason||cleanReason.length>200||!operator||operator.length>40)return{ok:false,error:'请填写200字以内的请假原因和40字以内的办理老师姓名'};
  if(studentHasLeaveOnDate(student.id,date))return{ok:false,error:'该学生当天已经请假，不会重复登记'};
  if(state.attendance.some(record=>record.studentId===student.id&&record.date===date))return{ok:false,error:'该学生当天已签到，不能再登记请假；请先核对签到记录'};
  const profile=enrolledStudentProfileAtDate(student,date);
  if(!profile||isLongTermAbsentAt(student,date)||profileClassId(profile)!==String(course.id))return{ok:false,error:'该日必须为学生在本班正常在读日期，不能在入学前、暂停或其他班级办理'};
  const plan=studentLessonPlan(profile,date);
  if(!plan.physical)return{ok:false,error:'当天没有该生明确的上课安排，请先设置排课或登记真实加课；隔周休课日无需请假'};
  state.settings.studentLeaves=studentLeaves().concat({id:uid(),studentId:student.id,studentNo:student.studentNo||'',classId:course.id,className:profile.className,teacher:reportTeacherForProfile(profile),date,reason:cleanReason,recordedBy:operator,source:'teacher_local',status:'approved',createdAt:new Date().toISOString()});
  return saveState()?{ok:true}:{ok:false,error:'请假保存失败，原有记录、签到和课时未改变'};
}
function cancelStudentLeave(id,cancelledBy,reason){
  if(!isAdminAuthenticated)return{ok:false,error:'请先解锁后台'};
  const item=studentLeaves().find(leave=>leave.id===Number(id)),operator=String(cancelledBy||'').trim(),note=String(reason||'').trim();
  if(!item||item.status!=='approved')return{ok:false,error:'请假记录已变化，请重新打开'};
  if(!operator||operator.length>40||!note||note.length>200)return{ok:false,error:'请填写办理老师姓名和撤销原因'};
  Object.assign(item,{status:'cancelled',cancelledBy:operator,cancelReason:note,cancelledAt:new Date().toISOString()});
  return saveState()?{ok:true}:{ok:false,error:'撤销保存失败，请假仍然有效，请重试'};
}
function openStudentLeaveForm(studentId,courseId,date){
  if(!isAdminAuthenticated)return openAdminLogin();
  const student=studentById(Number(studentId)),course=classById(courseId);if(!student||!course)return toast('学生或班级已变化',true);
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>老师代办请假</h2><p>${esc(student.name)} · ${esc(student.studentNo||'')} · ${esc(course.name)}</p></div></div><div class="warning-box">只请本次课的假，仍计入应到，不算实到；到课率按实际签到 ÷ 应到（含请假）计算。不扣课时、不催签到。请假期间不能直接签到，实际到课需先撤销请假。本地登记老师姓名仅作办理记录，不代表已实现独立教师账号。</div><form id="studentLeaveForm" class="simple-form"><label class="field"><span>本次上课日期</span><input id="studentLeaveDate" type="date" value="${esc(date)}" required readonly></label><label class="field"><span>办理老师 *</span><input id="studentLeaveOperator" maxlength="40" required value="${esc(course.teacher||'')}" placeholder="填写实际办理老师姓名"></label><label class="field"><span>请假原因 *</span><textarea id="studentLeaveReason" maxlength="200" required rows="3" placeholder="如：家长告知身体不适，本次课请假"></textarea></label><label class="leave-confirm"><input id="studentLeaveConfirm" type="checkbox" required>已核实学生本次未签到，并确认请假情况。</label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelLeaveForm">取消</button><button type="submit" class="primary-btn">确认请假</button></div></form>`);
  $('#cancelLeaveForm').onclick=closeModal;$('#studentLeaveForm').onsubmit=event=>{event.preventDefault();if(!$('#studentLeaveConfirm').checked)return toast('请先核实并勾选确认',true);const result=saveStudentLeave(student.id,course.id,$('#studentLeaveDate').value,$('#studentLeaveReason').value,$('#studentLeaveOperator').value);if(!result.ok)return toast(result.error,true);render();openStudentLeaveRecords(course.id,date);toast('请假已保存，未扣课时，已移出催签到名单')};
}
function openStudentLeaveRecords(courseId,date){
  if(!isAdminAuthenticated)return openAdminLogin();const course=classById(courseId),record=studentLeaves().find(item=>String(item.classId)===String(courseId)&&item.date===date);
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>请假记录</h2><p>${esc(date)} · ${esc(course&&course.name||record&&record.className||'原班级')}</p></div></div>${leaveRecordsMarkup(courseId,date)}<p class="leave-rule">已请假仍计应到，不算实到、不扣课时、不催签到；撤销后恢复未签到提醒，不自动签到或扣课。本次仅本地办理，尚未接入家长在线申请。</p><div class="form-actions"><button type="button" class="secondary-btn" id="closeLeaveRecords">关闭</button></div>`);$('#closeLeaveRecords').onclick=closeModal;bindStudentLeaveButtons($('#modal'));
}
function openCancelStudentLeave(id){
  if(!isAdminAuthenticated)return openAdminLogin();const item=studentLeaves().find(leave=>leave.id===Number(id));if(!item||item.status!=='approved')return toast('请假记录已变化',true);const student=studentById(item.studentId);
  openModal(`<div class="modal-head"><span>${uiIcon('alert')}</span><div><h2>撤销本次请假</h2><p>${esc(student&&student.name||item.studentNo)} · ${esc(item.date)} · ${esc(item.className)}</p></div></div><div class="warning-box">请假前后均计应到；撤销后恢复未签到提醒，不自动签到、不扣课时；原请假及撤销原因仍保留。</div><form id="cancelStudentLeaveForm" class="simple-form"><label class="field"><span>办理老师 *</span><input id="cancelLeaveOperator" maxlength="40" required value="${esc(item.recordedBy)}"></label><label class="field"><span>撤销原因 *</span><input id="cancelLeaveReason" maxlength="200" required placeholder="如：学生实际到课，撤销后办理签到"></label><label class="leave-confirm"><input id="cancelLeaveConfirm" type="checkbox" required>确认撤销该学生本次请假</label><div class="form-actions"><button type="button" class="secondary-btn" id="backLeaveRecords">返回</button><button type="submit" class="danger-btn">确认撤销请假</button></div></form>`);$('#backLeaveRecords').onclick=()=>openStudentLeaveRecords(item.classId,item.date);$('#cancelStudentLeaveForm').onsubmit=event=>{event.preventDefault();if(!$('#cancelLeaveConfirm').checked)return toast('请先确认撤销',true);const result=cancelStudentLeave(item.id,$('#cancelLeaveOperator').value,$('#cancelLeaveReason').value);if(!result.ok)return toast(result.error,true);render();openStudentLeaveRecords(item.classId,item.date);toast('请假已撤销，未自动签到或扣课时')};
}
function handleStudentLeaveAction(event,root){const button=event.target.closest('[data-student-leave],[data-manage-leaves],[data-cancel-student-leave]');if(!button||!root||!root.contains(button))return false;if(button.disabled)return true;event.preventDefault();event.stopPropagation();if(button.dataset.studentLeave!==undefined)openStudentLeaveForm(Number(button.dataset.studentLeave),button.dataset.leaveClass,button.dataset.leaveDate);else if(button.dataset.manageLeaves!==undefined)openStudentLeaveRecords(button.dataset.manageLeaves,button.dataset.leaveDate);else openCancelStudentLeave(Number(button.dataset.cancelStudentLeave));return true}
function bindStudentLeaveButtons(root){root.querySelectorAll('[data-student-leave],[data-manage-leaves],[data-cancel-student-leave]').forEach(button=>button.onclick=event=>handleStudentLeaveAction(event,root))}
function validateStudentLeaves(data){
  const records=data.settings&&data.settings.studentLeaves;if(records===undefined)return;if(!Array.isArray(records))throw new Error('备份中的请假记录格式错误');
  const ids=new Set(),active=new Set(),allowed=new Set(['id','studentId','studentNo','classId','className','teacher','date','reason','recordedBy','source','status','createdAt','cancelledAt','cancelledBy','cancelReason']);
  const text=(value,max)=>typeof value==='string'&&Boolean(value.trim())&&value.length<=max,time=value=>typeof value==='string'&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString()===value;
  records.forEach(item=>{
    if(!item||Object.keys(item).some(key=>!allowed.has(key))||!Number.isSafeInteger(item.id)||item.id<=0||ids.has(item.id)||!Number.isSafeInteger(item.studentId)||!data.students.some(s=>s.id===item.studentId)||typeof item.studentNo!=='string'||!Number.isSafeInteger(item.classId)||item.classId<=0||!text(item.className,200)||typeof item.teacher!=='string'||!isValidLocalDate(item.date)||!text(item.reason,200)||!text(item.recordedBy,40)||item.source!=='teacher_local'||!['approved','cancelled'].includes(item.status)||!time(item.createdAt))throw new Error('备份中的请假含无效学生、日期、老师、来源或重复编号');ids.add(item.id);
    if(item.status==='approved'){const key=`${item.studentId}:${item.date}`;if(active.has(key)||(data.attendance||[]).some(record=>record.studentId===item.studentId&&record.date===item.date)||['cancelledAt','cancelledBy','cancelReason'].some(key=>item[key]!==undefined))throw new Error('有效请假与签到、撤销信息或另一请假冲突');active.add(key)}
    else if(!time(item.cancelledAt)||Date.parse(item.cancelledAt)<Date.parse(item.createdAt)||!text(item.cancelledBy,40)||!text(item.cancelReason,200))throw new Error('请假撤销记录缺少有效办理信息');
  });
}
