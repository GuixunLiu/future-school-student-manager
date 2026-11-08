'use strict';
let adminScheduleDate='';
function scheduleDateKey(seconds){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(seconds*1000)),values={};for(const part of parts)values[part.type]=part.value;return values.year+'-'+values.month+'-'+values.day;}
function shiftScheduleDay(day,offset){const [year,month,date]=day.split('-').map(Number);return new Date(Date.UTC(year,month-1,date+offset)).toISOString().slice(0,10);}
async function openLessonSubstitute(item){
 const {items}=await api('/v1/admin/teachers');
 const content=directoryDialog('临时代课 · '+item.class_name),form=node('form',undefined,'directory-form');
 content.append(node('p',item.teacher_names?'当前授课教师：'+item.teacher_names:'当前未安排授课教师','hint'));
 const label=node('label','代课教师'),teacher=node('select');teacher.required=true;
 teacher.append(new Option('请选择代课教师',''));
 if(item.substitute_id)teacher.append(new Option('恢复原任课安排',':restore'));
 for(const candidate of items)teacher.append(new Option(candidate.display_name+' · '+candidate.login,candidate.id));
 teacher.value=item.substitute_id||'';
 label.append(teacher);form.append(label);
 const reason=scheduleReason(form,'代课原因','例如：原授课教师临时请假');
 const submit=node('button','确认代课安排');submit.type='submit';form.append(submit);content.append(form);
 if(!$('device-dialog').open)$('device-dialog').showModal();
 const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   await api('/v1/admin/lessons/'+encodeURIComponent(item.id)+'/substitute',command({
    teacher_id:teacher.value===':restore'?null:teacher.value,expected_version:item.version,reason:reason.value.trim(),command_ref:''
   }),'PUT');
   $('device-dialog').close();await adminScheduleDashboard();notify('代课安排已更新');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
}
function scheduleOperations(item,serverTime,desktop=false){
 const operations=node(desktop?'td':'div',undefined,'directory-actions'),editable=item.state==='scheduled'&&item.starts_at>serverTime&&item.processed_count===0;
 if(item.state==='scheduled'&&item.starts_at>serverTime){
  const substitute=button(item.substitute_id?'调整代课':'安排代课',()=>openLessonSubstitute(item),'secondary');
  operations.append(substitute);
 }
 if(editable){const reschedule=button('调整时间',()=>openScheduleReschedule(item),'secondary'),cancel=button('取消课次',()=>openScheduleCancel(item),'secondary danger');if(desktop){reschedule.dataset.action='reschedule-lesson';cancel.dataset.action='cancel-lesson';reschedule.dataset.lessonId=item.id;cancel.dataset.lessonId=item.id;}operations.append(reschedule,cancel);}
 else if(item.state==='cancelled'){const review=button('处理签到',()=>openCancelledLesson(item),'secondary');review.dataset.action='review-cancelled';review.dataset.lessonId=item.id;operations.append(review);}
 else if(!operations.childElementCount)operations.append(node('span',item.processed_count?'已有 '+item.processed_count+' 人处理，保留原课次':'课次已开始，仅可查看','hint'));
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
let adminScheduleView='daily';
const scheduleWeekdays=['','周一','周二','周三','周四','周五','周六','周日'];
function scheduleTemplateDate(item,fromDay){
 let day=fromDay<item.effective_date?item.effective_date:fromDay;if(day<item.anchor_date)day=item.anchor_date;
 const [year,month,date]=day.split('-').map(Number),current=new Date(Date.UTC(year,month-1,date)),weekday=current.getUTCDay()||7;
 current.setUTCDate(current.getUTCDate()+(item.weekday-weekday+7)%7);const [ay,am,ad]=item.anchor_date.split('-').map(Number),anchor=new Date(Date.UTC(ay,am-1,ad));
 const weeks=Math.floor((current-anchor)/604800000),remainder=((weeks%item.interval_weeks)+item.interval_weeks)%item.interval_weeks;
 if(remainder)current.setUTCDate(current.getUTCDate()+(item.interval_weeks-remainder)*7);return current.toISOString().slice(0,10);
}
function scheduleTemplateTimes(item,fromDay){
 let day=scheduleTemplateDate(item,fromDay),start=Date.parse(day+'T'+item.start_time+':00+08:00');
 if(start<=Date.now()){day=scheduleTemplateDate(item,shiftScheduleDay(day,1));start=Date.parse(day+'T'+item.start_time+':00+08:00');}
 const endDay=shiftScheduleDay(day,item.end_day_offset);return{start:day+'T'+item.start_time,end:endDay+'T'+item.end_time};
}
function scheduleTemplateOperations(item,desktop=false){
 const operations=node(desktop?'td':'div',undefined,'directory-actions'),edit=button('编辑模板',()=>openScheduleTemplate(item),'secondary');edit.dataset.action='edit-schedule';edit.dataset.scheduleId=item.id;operations.append(edit);
 if(item.linked_lesson_count===0){const remove=button('删除模板',()=>openScheduleTemplateDelete(item),'secondary danger');remove.dataset.action='delete-schedule';remove.dataset.scheduleId=item.id;operations.append(remove);}else operations.append(node('span','已关联 '+item.linked_lesson_count+' 节课，仅可编辑','hint'));return operations;
}
function scheduleTemplateTable(items){
 const section=node('section',undefined,'panel'),table=directoryTable(['班级与课程','星期与时间','周期与锚点','生效日期','授课教师','关联课次','操作']),cards=node('div',undefined,'schedule-mobile-list');
 section.append(node('h2','每周排课模板'),node('p','模板只作为排课规则，不会自动生成整学期课次；本周停课、临时加课和调课仍在“按日课次”中逐节处理。'));
 if(!items.length){section.append(node('div','尚未建立排课模板。点击“新建排课模板”设置固定时段和轮课教师。','empty'));return section;}
 for(const item of items){const time=scheduleWeekdays[item.weekday]+' '+item.start_time+'–'+item.end_time+(item.end_day_offset?'（次日）':''),cycle=item.interval_weeks===1?'每周':'隔周',course=item.class_name+' · '+item.course_name+(item.course_active?'':' · 课程已停用'),row=node('tr');
  row.append(node('td',course),node('td',time),node('td',cycle+' · 锚点 '+item.anchor_date),node('td',item.effective_date),node('td',item.teacher_name),node('td',item.linked_lesson_count+' 节'),scheduleTemplateOperations(item,true));table.body.append(row);
  const card=node('article',undefined,'item schedule-mobile-card');card.append(node('h3',course),node('p',time+' · '+cycle),node('p','锚点 '+item.anchor_date+' · '+item.effective_date+' 起生效'),node('p','授课教师：'+item.teacher_name+' · 已关联 '+item.linked_lesson_count+' 节'),scheduleTemplateOperations(item));cards.append(card);
 }
 table.scroll.classList.add('schedule-desktop');section.append(table.scroll,cards);return section;
}
function adminScheduleTemplateDashboard(){adminScheduleView='templates';return adminScheduleDashboard();}
function openScheduleTemplate(item=null,classItems=window.adminScheduleClasses||[],teacherItems=window.adminScheduleTeachers||[]){
 const classes=classItems.filter(row=>row.course_active||(item&&row.id===item.class_id));if(!classes.length){notify('没有可排课的启用课程班级，请先核对课程与班级。');return;}
 const content=directoryDialog(item?'编辑排课模板 · '+item.class_name:'新建排课模板'),form=node('form',undefined,'directory-form');content.append(node('p','模板不会批量生成或改写课次。编辑后只影响后续人工选课；已有课次的教师、时间、名单与课时账本保持不变。','hint'));
 const classLabel=node('label','排课班级'),classSelect=node('select');classSelect.required=true;classSelect.setAttribute('aria-label','排课班级');for(const row of classes){const option=node('option',row.name+' · '+row.course_name+(row.course_active?'':' · 课程已停用，仅可修正模板'));option.value=row.id;classSelect.append(option);}if(item){classSelect.value=item.class_id;classSelect.disabled=true;}classLabel.append(classSelect);form.append(classLabel);
 const weekdayLabel=node('label','每周星期'),weekday=node('select');weekday.setAttribute('aria-label','每周星期');for(let value=1;value<=7;value++){const option=node('option',scheduleWeekdays[value]);option.value=String(value);weekday.append(option);}weekday.value=String(item?.weekday||1);weekdayLabel.append(weekday);form.append(weekdayLabel);
 const intervalLabel=node('label','上课周期'),interval=node('select');interval.setAttribute('aria-label','上课周期');for(const [value,text] of [['1','每周'],['2','隔周']]){const option=node('option',text);option.value=value;interval.append(option);}interval.value=String(item?.interval_weeks||1);intervalLabel.append(interval);form.append(intervalLabel);
 const anchor=field(form,'周期锚点日期','date'),effective=field(form,'生效日期','date');anchor.required=true;effective.required=true;anchor.value=item?.anchor_date||adminScheduleDate;effective.value=item?.effective_date||adminScheduleDate;
 const start=field(form,'开始时间','time'),end=field(form,'结束时间','time');start.required=true;end.required=true;start.value=item?.start_time||'14:00';end.value=item?.end_time||'15:30';
 const offsetLabel=node('label','结束日期'),offset=node('select');offset.setAttribute('aria-label','结束日期');for(const [value,text] of [['0','当天'],['1','次日（跨日课程）']]){const option=node('option',text);option.value=value;offset.append(option);}offset.value=String(item?.end_day_offset||0);offsetLabel.append(offset);form.append(offsetLabel);
 const duration=field(form,'课次时长（分钟）','number');duration.required=true;duration.min='1';duration.max='720';duration.step='1';duration.value=String(item?.duration_minutes||90);
 const teacherLabel=node('label','模板授课教师'),teacher=node('select');teacher.required=true;teacher.setAttribute('aria-label','模板授课教师');teacherLabel.append(teacher);form.append(teacherLabel);
 const reason=scheduleReason(form,'排课操作原因',item?'例如：下一轮起调整固定时段':'例如：两名教师隔周轮课，规则已确认');
 const confirmation=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对星期、锚点、生效日期、教师和起止时间；知悉已有课次不会改变'));form.append(confirmation);
 const submit=node('button',item?'保存排课模板':'创建排课模板');submit.type='submit';form.append(submit);content.append(form);
 function refreshTeachers(){const selected=item?.teacher_id||teacher.value,eligible=teacherItems.filter(row=>(row.class_ids||[]).includes(classSelect.value));teacher.replaceChildren();for(const person of eligible){const option=node('option',person.display_name+' · '+person.login);option.value=person.id;teacher.append(option);}if(eligible.some(row=>row.id===selected))teacher.value=selected;teacher.disabled=!eligible.length;submit.disabled=!eligible.length;}
 function calculateDuration(){const startParts=start.value.split(':').map(Number),endParts=end.value.split(':').map(Number);if(startParts.length===2&&endParts.length===2&&startParts.every(Number.isInteger)&&endParts.every(Number.isInteger)){const minutes=(endParts[0]*60+endParts[1]+Number(offset.value)*1440)-(startParts[0]*60+startParts[1]);if(minutes>0&&minutes<=720)duration.value=String(minutes);}}
 classSelect.addEventListener('change',refreshTeachers);for(const input of [start,end,offset])input.addEventListener('change',calculateDuration);refreshTeachers();if(!$('device-dialog').open)$('device-dialog').showModal();const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{event.preventDefault();const minutes=Number(duration.value),values={weekday:Number(weekday.value),interval_weeks:Number(interval.value),anchor_date:anchor.value,start_time:start.value,end_time:end.value,duration_minutes:minutes,end_day_offset:Number(offset.value),effective_date:effective.value,teacher_id:teacher.value,reason:reason.value.trim(),command_ref:''};if(!item)values.class_id=classSelect.value;const payload=command(values);submit.disabled=true;
  try{const result=await api(item?'/v1/admin/schedule-templates/'+encodeURIComponent(item.id):'/v1/admin/schedule-templates',payload,item?'PUT':'POST');$('device-dialog').close();await adminScheduleTemplateDashboard();notify(item?'排课模板已保存；已有课次与课时账本未改变':'排课模板已创建，可在按日课次中选用');}catch(error){if(error.status===409){$('device-dialog').close();await adminScheduleTemplateDashboard();notify(error.message);}else showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
function openScheduleTemplateDelete(item){
 const content=directoryDialog('删除排课模板 · '+item.class_name),form=node('form',undefined,'directory-form');content.append(node('p',scheduleWeekdays[item.weekday]+' '+item.start_time+'–'+item.end_time+' · '+(item.interval_weeks===1?'每周':'隔周')+' · '+item.teacher_name),node('p','仅允许删除尚未被任何课次引用的模板。删除不会生成、取消或修改任何课次。','hint'));
 const reason=scheduleReason(form,'删除原因','例如：误建模板，尚未用于任何课次'),confirmation=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','确认该模板没有关联课次，删除后不再用于后续排课'));form.append(confirmation);const submit=node('button','确认删除模板','danger');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{event.preventDefault();const payload=command({reason:reason.value.trim(),command_ref:''});submit.disabled=true;try{await api('/v1/admin/schedule-templates/'+encodeURIComponent(item.id)+'/delete',payload);$('device-dialog').close();await adminScheduleTemplateDashboard();notify('未使用的排课模板已删除；课次和课时账本未改变');}catch(error){if(error.status===409){$('device-dialog').close();await adminScheduleTemplateDashboard();notify(error.message);}else showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
async function adminScheduleDashboard(day=adminScheduleDate||chinaToday(),receipt=null){
 const [lessonData,classData,teacherData,studentData,templateData]=await Promise.all([api('/v1/admin/lessons?date='+encodeURIComponent(day)),api('/v1/admin/classes'),api('/v1/admin/teachers'),api('/v1/admin/students'),api('/v1/admin/schedule-templates')]);adminScheduleDate=lessonData.date;window.adminScheduleClasses=classData.items;window.adminScheduleTeachers=teacherData.items;adminIdentity();
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','课表管理'),node('p','每周模板维护固定时段和轮课教师；按日课次处理实际开课、临时调课、本周取消和下周加课。创建课次不立即扣课时。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('新建排课模板',()=>openScheduleTemplate(),'secondary'),button('刷新',()=>adminScheduleDashboard(adminScheduleDate),'secondary'));heading.append(copy,actions);const sections=[heading];
 const done=scheduleReceipt(receipt);if(done)sections.push(done);const tabs=node('nav',undefined,'directory-tabs');tabs.setAttribute('aria-label','课表管理视图');for(const [key,label] of [['daily','按日课次'],['templates','每周模板']]){const tab=button(label,()=>{adminScheduleView=key;return adminScheduleDashboard(adminScheduleDate);},key===adminScheduleView?'':'secondary');tab.setAttribute('aria-pressed',String(key===adminScheduleView));tab.dataset.scheduleView=key;tabs.append(tab);}sections.push(tabs);
 if(adminScheduleView==='templates'){sections.push(scheduleTemplateTable(templateData.items));$('main').replaceChildren(...sections);return;}
 const filter=node('section',undefined,'panel teacher-filter'),filterForm=node('form',undefined,'classroom-date'),dateLabel=node('label','课表日期'),date=node('input');date.type='date';date.required=true;date.value=adminScheduleDate;date.setAttribute('aria-label','课表日期');dateLabel.append(date);const view=node('button','查看课表');view.type='submit';filterForm.append(dateLabel,view);
 const navigation=node('nav',undefined,'weekly-navigation');navigation.setAttribute('aria-label','课表日期切换');navigation.append(button('前一天',()=>{adminScheduleView='daily';return adminScheduleDashboard(shiftScheduleDay(adminScheduleDate,-1));},'secondary'),button('回到今天',()=>{adminScheduleView='daily';return adminScheduleDashboard(chinaToday());},'secondary'),button('后一天',()=>{adminScheduleView='daily';return adminScheduleDashboard(shiftScheduleDay(adminScheduleDate,1));},'secondary'));filter.append(filterForm,navigation);sections.push(filter);
 const create=node('section',undefined,'panel schedule-create'),form=node('form',undefined,'enrollment-form schedule-form'),activeClasses=classData.items.filter(item=>item.course_active);create.append(node('h2','创建未来课次'),node('p','可选择模板自动带出教师和时间，也可创建临时课次。只能创建未来课次，时长不超过 12 小时；同班同一时间不能重复开课。'));
 const classLabel=node('label','开课班级'),classSelect=node('select');classSelect.required=true;classSelect.setAttribute('aria-label','开课班级');for(const item of activeClasses){const option=node('option',item.name+' · '+item.course_name);option.value=item.id;classSelect.append(option);}classLabel.append(classSelect);form.append(classLabel);
 const templateLabel=node('label','排课模板（可选）'),templateSelect=node('select');templateSelect.setAttribute('aria-label','排课模板');templateLabel.append(templateSelect);form.append(templateLabel);
 const teacherLabel=node('label','授课教师'),teacherSelect=node('select');teacherSelect.required=true;teacherSelect.setAttribute('aria-label','授课教师');teacherLabel.append(teacherSelect);form.append(teacherLabel);
 const start=field(form,'开始时间（北京时间）','datetime-local'),end=field(form,'结束时间（北京时间）','datetime-local');start.value=chinaDateTimeLocal(new Date(Date.now()+3600000));end.value=chinaDateTimeLocal(new Date(Date.now()+2*3600000));
 const charge=field(form,'统一扣课课时','number');charge.min='1';charge.max='10000000';charge.step='1';charge.value='1';charge.inputMode='numeric';
 const confirmation=node('label',undefined,'check renewal-confirmation enrollment-wide'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对班级、模板、教师、时间和扣课单位'));form.append(confirmation);
 const hint=node('p','','hint enrollment-wide'),submit=node('button','确认创建课次','enrollment-wide');submit.type='submit';form.append(hint,submit);create.append(form);sections.push(create,scheduleLessonTable(lessonData.items,lessonData.server_time));
 function refreshTeachers(){const selected=teacherSelect.value,eligible=teacherData.items.filter(item=>(item.class_ids||[]).includes(classSelect.value));teacherSelect.replaceChildren();for(const teacher of eligible){const option=node('option',teacher.display_name+' · '+teacher.login);option.value=teacher.id;teacherSelect.append(option);}if(eligible.some(item=>item.id===selected))teacherSelect.value=selected;teacherSelect.disabled=!eligible.length;submit.disabled=!activeClasses.length||!eligible.length;const count=studentData.items.filter(item=>item.active&&(item.class_ids||[]).includes(classSelect.value)).length;hint.textContent=eligible.length?'本班当前 '+count+' 名在读学生将在创建时加入应到名单；空班也会保存课次和默认扣课规则。':'该班暂无在职且已授权的教师，请先完成教师班级授权。';}
 function refreshTemplates(){const selected=templateSelect.value,eligible=templateData.items.filter(item=>item.class_id===classSelect.value&&item.course_active);templateSelect.replaceChildren();const empty=node('option','不使用模板（临时课次）');empty.value='';templateSelect.append(empty);for(const item of eligible){const option=node('option',scheduleWeekdays[item.weekday]+' '+item.start_time+' · '+(item.interval_weeks===1?'每周':'隔周')+' · '+item.teacher_name);option.value=item.id;templateSelect.append(option);}if(eligible.some(item=>item.id===selected))templateSelect.value=selected;}
 function applyTemplate(){const item=templateData.items.find(row=>row.id===templateSelect.value);if(!item)return;teacherSelect.value=item.teacher_id;const times=scheduleTemplateTimes(item,adminScheduleDate);start.value=times.start;end.value=times.end;hint.textContent='已按模板带出 '+times.start.slice(0,10)+' 的课次；可继续手动调整，提交前请再次核对。';}
 if(!activeClasses.length){classSelect.disabled=true;templateSelect.disabled=true;teacherSelect.disabled=true;submit.disabled=true;hint.textContent='没有启用课程的班级，不能创建课次。';}else{refreshTeachers();refreshTemplates();}
 classSelect.addEventListener('change',()=>{refreshTeachers();refreshTemplates();applyTemplate();});templateSelect.addEventListener('change',applyTemplate);$('main').replaceChildren(...sections);
 filterForm.addEventListener('submit',async event=>{event.preventDefault();view.disabled=true;try{await adminScheduleDashboard(date.value);}catch(error){showError(error);}finally{if(view.isConnected)view.disabled=false;}});
 let previousPayload='',commandRef='';form.addEventListener('submit',async event=>{event.preventDefault();const units=Number(charge.value),startsAt=Math.floor(Date.parse(start.value+':00+08:00')/1000),endsAt=Math.floor(Date.parse(end.value+':00+08:00')/1000);
  if(!Number.isInteger(units)||units<1||units>10000000){charge.setCustomValidity('扣课课时必须是 1 至 10000000 的整数');charge.reportValidity();return;}charge.setCustomValidity('');
  if(!Number.isFinite(startsAt)){start.setCustomValidity('请选择有效的开始时间');start.reportValidity();return;}start.setCustomValidity('');if(!Number.isFinite(endsAt)||endsAt<=startsAt||endsAt-startsAt>12*3600){end.setCustomValidity('结束时间必须晚于开始时间，且时长不超过 12 小时');end.reportValidity();return;}end.setCustomValidity('');
  const payload={class_id:classSelect.value,teacher_id:teacherSelect.value,starts_at:startsAt,ends_at:endsAt,charge_units:units,command_ref:''};if(templateSelect.value)payload.schedule_id=templateSelect.value;const serialized=JSON.stringify({...payload,command_ref:undefined});if(serialized!==previousPayload){commandRef=crypto.randomUUID().replaceAll('-','');previousPayload=serialized;}payload.command_ref=commandRef;submit.disabled=true;
  try{const result=await api('/v1/admin/lessons',payload);adminScheduleView='daily';await adminScheduleDashboard(scheduleDateKey(result.starts_at),{kind:'create',result});notify((result.duplicate?'该课次已保存，未重复创建。':'课次创建成功：')+result.class_name+'，'+result.teacher_name+'，应到 '+result.roster_count+' 人');}catch(error){showError(error);}finally{if(submit.isConnected)submit.disabled=false;}
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
 const content=directoryDialog('取消课次签到处理 · '+lesson.class_name);content.append(node('p',monthlyTime(lesson.starts_at)+' · '+(lesson.course_name||'未设置课程'),'hint'),node('p','可查看已有签到记录，并为未签到学生登记请假。请假不扣课时，取消课次仍不计入到课率。','hint'));
 if(!data.students.length){content.append(node('div','该课次没有学生名单','empty'));}else{
  const table=directoryTable(['学生 / 学号','签到状态','剩余课时','原扣课规则','处理']);
  for(const student of data.students){const row=node('tr'),identity=node('td');identity.append(node('strong',student.name),node('small',student.external_ref));const status={arrived:'已到',leave:'请假',missing:student.expected?'未签到':'无需到课'}[student.status]||'待核对',operations=node('td',undefined,'directory-actions');
   if(student.expected&&student.status==='missing'){const leave=button('请假',()=>openCancelledLeave(item,student),'secondary');leave.dataset.action='cancelled-leave';leave.dataset.studentId=student.student_id;operations.append(leave);}else operations.append(node('span',student.status==='missing'?'无需处理':'已处理，保留记录','hint'));
   row.append(identity,node('td',status+' · v'+student.attendance_revision),node('td',student.balance_units==null?'—':student.balance_units+' 课时'),node('td',student.charge_units+' 课时'),operations);table.body.append(row);
  }content.append(table.scroll);
 }
 if(!$('device-dialog').open)$('device-dialog').showModal();
}
function openCancelledLeave(item,student){
 const content=directoryDialog('取消课次请假 · '+student.name),form=node('form',undefined,'directory-form');content.append(node('p','学号 '+student.external_ref+' · 当前未签到 v'+student.attendance_revision+' · 原扣课 '+student.charge_units+' 课时','hint'));
 const reason=scheduleReason(form,'请假原因','例如：家长已提前请假，取消前仍需归档'),checkLabel=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;checkLabel.append(check,node('span','已核对请假；本次不扣课时，平板仅归档结果'));form.append(checkLabel,node('p','此操作不会恢复课次，取消课次仍不进入到课率。重复点击或网络重试使用同一操作编号。','hint'));
 const submit=node('button','确认请假');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const eventRef=crypto.randomUUID().replaceAll('-','');
 form.addEventListener('submit',async event=>{event.preventDefault();const payload={student_id:student.student_id,reason:reason.value.trim(),event_ref:eventRef,expected_revision:student.attendance_revision};submit.disabled=true;try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.id)+'/leave',payload);$('device-dialog').close();await adminScheduleDashboard(scheduleDateKey(item.starts_at));notify((result.duplicate?'已恢复上次处理结果：':'请假完成：')+student.name+'，未扣课时；平板可核对归档');}catch(error){if(!scheduleConflict(error,scheduleDateKey(item.starts_at)))showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
function openScheduleCancel(item){
 const content=directoryDialog('取消课次 · '+item.class_name),form=node('form',undefined,'directory-form');content.append(node('p',monthlyTime(item.starts_at)+' · '+(item.teacher_names||'未安排')+' · 应到 '+item.expected_count+' 人'),node('p','取消后保留原名单和签到记录，统计排除该课次；不删除历史，也不会自动退课时。','hint'));
 const reason=scheduleReason(form,'取消原因','例如：教师停课，已与教务确认'),checkLabel=node('label',undefined,'check directory-confirm'),check=node('input');check.type='checkbox';check.required=true;checkLabel.append(check,node('span','确认取消该课次，并知悉名单保留且不会自动退课时'));form.append(checkLabel);
 const submit=node('button','确认取消课次','danger');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{event.preventDefault();const payload=command({expected_version:item.version,reason:reason.value.trim(),command_ref:''});submit.disabled=true;try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.id)+'/cancel',payload);$('device-dialog').close();await adminScheduleDashboard(scheduleDateKey(result.starts_at),{kind:'cancel',result});notify('课次已取消；名单保留，统计排除，未自动退课时');}catch(error){if(!scheduleConflict(error,scheduleDateKey(item.starts_at)))showError(error);}finally{if(submit.isConnected)submit.disabled=false;}});
}
