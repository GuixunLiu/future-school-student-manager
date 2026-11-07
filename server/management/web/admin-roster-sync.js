'use strict';
function rosterSyncReason(item){
 if(item.reason==='no_charge_rule')return '空课次，暂无扣课规则';
 if(item.reason==='mixed_charge_rule')return '现有扣课单位不一致（'+item.observed_charge_units.join('、')+' 课时）';
 return '请管理员核对';
}
function rosterSyncTable(items,kind,studentId=null){
 if(!items.length)return node('div',kind==='ready'?'没有可同步的未来课次':kind==='skipped'?'没有待核对课次':'没有已存在名单','empty');
 const table=node('table'),head=node('thead'),headRow=node('tr'),titles=['课次开始时间','扣课单位','状态或原因'];if(kind==='skipped')titles.push('操作');for(const title of titles)headRow.append(node('th',title));head.append(headRow);
 const body=node('tbody');for(const item of items){const row=node('tr'),units=kind==='skipped'?(item.observed_charge_units.length?item.observed_charge_units.join('、')+' 课时':'—'):item.charge_units+' 课时',status=kind==='ready'?'可同步':kind==='skipped'?rosterSyncReason(item):'已在名单';row.append(node('td',monthlyTime(item.starts_at)),node('td',units),node('td',status,kind==='skipped'?'roster-reason':''));if(kind==='skipped'){const operations=node('td'),edit=button('设置规则',()=>openLessonChargeRule(studentId,item),'secondary');edit.dataset.action='set-charge-rule';edit.dataset.lessonId=item.lesson_id;operations.append(edit);row.append(operations);}body.append(row);}table.append(head,body);const scroll=node('div',undefined,'table-scroll');scroll.append(table);return scroll;
}
function openLessonChargeRule(studentId,item){
 const content=$('dialog-content');content.replaceChildren();const title=node('h2','设置课次扣课规则');title.id='dialog-title';content.append(title);
 const summary=node('div',undefined,'renewal-form-summary');summary.append(node('strong',monthlyTime(item.starts_at)),node('p',rosterSyncReason(item)),node('p','规则版本 '+item.charge_rule_revision));content.append(summary);
 const form=node('form'),units=field(form,'统一扣课课时','number');units.min='1';units.max='10000000';units.step='1';units.inputMode='numeric';units.placeholder='请输入正整数课时';
 const confirmation=node('label',undefined,'check renewal-confirmation'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span',item.reason==='mixed_charge_rule'?'确认统一本节课所有现有学生的扣课单位，并用于后续加入学生':'确认建立本节课默认扣课规则，并用于后续加入学生'));form.append(confirmation);
 form.append(node('p','仅允许修改尚未开始且未取消的课次。保存规则不会立即扣课时，也不修改签到状态、历史事件、余额或课时流水。','hint'));
 const submit=node('button','保存扣课规则');submit.type='submit';form.append(submit);content.append(form);let previousUnits=null,commandRef='';
 form.addEventListener('submit',async event=>{event.preventDefault();const value=Number(units.value);if(!Number.isInteger(value)||value<1||value>10000000){units.setCustomValidity('扣课课时必须是 1 至 10000000 的整数');units.reportValidity();return;}units.setCustomValidity('');if(value!==previousUnits){commandRef=crypto.randomUUID().replaceAll('-','');previousUnits=value;}submit.disabled=true;
  try{const result=await api('/v1/admin/lessons/'+encodeURIComponent(item.lesson_id)+'/charge-rule',{charge_units:value,expected_revision:item.charge_rule_revision,command_ref:commandRef});$('device-dialog').close();await adminRosterSyncDashboard(studentId);notify((result.duplicate?'该规则已保存，未重复修改。':'扣课规则已保存：')+'本节课统一为 '+result.charge_units+' 课时，现有名单更新 '+result.roster_students_updated+' 人');}
  catch(error){if(error.status===409){$('device-dialog').close();await adminRosterSyncDashboard(studentId);notify(error.message);}else showError(error);}finally{if(submit.isConnected)submit.disabled=false;}
 });
 units.addEventListener('input',()=>units.setCustomValidity(''));if(!$('device-dialog').open)$('device-dialog').showModal();units.focus();
}
function rosterSyncSection(title,note,items,kind,studentId=null){const section=node('section',undefined,'panel roster-table');section.append(node('h2',title),node('p',note),rosterSyncTable(items,kind,studentId));return section;}
async function adminRosterSyncDashboard(studentId,receipt=null){
 const data=await api('/v1/admin/students/'+encodeURIComponent(studentId)+'/future-lessons');adminIdentity();
 const student=data.student,heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','后续课次名单同步'),node('p','先预览再确认。只处理该学生当前班级中尚未开始且未取消的课次，不修改历史签到、课时余额或扣课流水。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回学生列表',()=>adminRenewalDashboard(),'secondary'),button('招新登记',()=>adminEnrollmentDashboard(),'secondary'),button(receipt?'再次检查':'刷新预览',()=>adminRosterSyncDashboard(studentId),'secondary'));heading.append(copy,actions);
 const sections=[heading],summary=node('section',undefined,'panel roster-student');summary.append(node('h2',student.name+' · '+student.external_ref),node('p','当前班级：'+student.class_name),node('p','服务器预览时间：'+monthlyTime(data.server_time)));sections.push(summary);
 if(receipt){const done=node('section',undefined,'panel roster-receipt');done.append(node('h2',receipt.synced_count?'名单同步完成':'核对完成，本次未新增名单'),node('p','本次同步 '+receipt.synced_count+' 节课；待核对 '+receipt.skipped_count+' 节；原已存在 '+receipt.already_present_count+' 节。'),node('p','余额和历史签到均未改变，操作已记录审计。'));if(receipt.synced_lessons.length)done.append(rosterSyncTable(receipt.synced_lessons,'ready'));sections.push(done);}
 const metrics=node('div',undefined,'roster-metrics');for(const [label,count] of [['可同步',data.ready_count],['待核对',data.skipped_count],['已存在',data.already_present_count]]){const metric=node('section',undefined,'panel roster-metric');metric.append(node('span',label),node('strong',String(count)));metrics.append(metric);}sections.push(metrics);
 const confirm=node('section',undefined,'panel roster-confirm'),form=node('form'),check=node('input'),label=node('label',undefined,'check'),submit=node('button','确认同步 '+data.ready_count+' 节课');check.type='checkbox';check.required=true;label.append(check,node('span','已核对学生、当前班级及下方可同步课次'));submit.type='submit';submit.disabled=true;form.append(node('p',data.ready_count?'确认后将建立应到名单，签到状态为“未签到”、版本为 0。沿用每节课现有且一致的扣课单位，不会立即扣课时。':'当前没有可同步课次。待核对课次需要先由管理员明确扣课规则，已存在名单不会重复添加。','hint'));if(data.ready_count)form.append(label);form.append(submit);confirm.append(form);sections.push(confirm);
 sections.push(rosterSyncSection('可同步课次','仅限服务器时间之后开始的有效课次。',data.ready_lessons,'ready'),rosterSyncSection('待核对课次','空课次没有扣课规则；扣课单位不一致时不会自动选择。可点击“设置规则”逐项核对。',data.skipped_lessons,'skipped',studentId),rosterSyncSection('已存在名单','这些课次无需重复添加。',data.already_present_lessons,'already'));
 $('main').replaceChildren(...sections);check.addEventListener('change',()=>{submit.disabled=!check.checked||!data.ready_count;});const commandRef=crypto.randomUUID().replaceAll('-','');
 form.addEventListener('submit',async event=>{event.preventDefault();if(!data.ready_count||!check.checked)return;submit.disabled=true;try{const result=await api('/v1/admin/students/'+encodeURIComponent(studentId)+'/future-lessons',{command_ref:commandRef});await adminRosterSyncDashboard(studentId,result);notify((result.duplicate?'该同步操作已保存，未重复添加。':'同步完成：')+student.name+' 新增 '+result.synced_count+' 节课的应到名单；余额与历史未改变');}catch(error){showError(error);}finally{if(submit.isConnected)submit.disabled=!check.checked||!data.ready_count;}});
}
