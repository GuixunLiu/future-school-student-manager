'use strict';
async function adminEnrollmentDashboard(receipt=null){
 const data=await api('/v1/admin/classes');adminIdentity();
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','管理员招新登记'),node('p','创建在读学生并指定当前班级，同时建立期初课时、不可变流水和招新记录。学号与业务来源编号在机构内不能重复。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('续费登记',()=>adminRenewalDashboard(),'secondary'),button('查看月报',()=>adminMonthlyReport(),'secondary'));heading.append(copy,actions);
 const sections=[heading];
 if(receipt){const done=node('section',undefined,'panel enrollment-receipt'),receiptActions=node('div',undefined,'toolbar');done.append(node('h2','招新登记完成'),node('p',receipt.name+'（'+receipt.external_ref+'）已加入“'+receipt.class_name+'”，期初余额 '+receipt.balance_units+' 课时。'),node('p','学生编号：'+receipt.student_id));receiptActions.append(button('同步后续课次',()=>adminRosterSyncDashboard(receipt.student_id)),button('继续招新',()=>adminEnrollmentDashboard(),'secondary'));done.append(receiptActions);sections.push(done);}
 const panel=node('section',undefined,'panel enrollment-panel'),form=node('form',undefined,'enrollment-form');
 if(!data.items.length){panel.append(node('div','尚未建立班级，不能办理招新入班。','empty'));$('main').replaceChildren(...sections,panel);return;}
 const classLabel=node('label','入班班级'),classSelect=node('select');classSelect.required=true;classSelect.setAttribute('aria-label','入班班级');for(const item of data.items){const option=node('option',item.name+(item.course_name?' · '+item.course_name:''));option.value=item.id;classSelect.append(option);}classLabel.append(classSelect);form.append(classLabel);
 const name=field(form,'学生姓名');name.maxLength=80;name.autocomplete='off';
 const external=field(form,'学生学号');external.maxLength=80;external.placeholder='机构内唯一学号';external.autocomplete='off';
 const units=field(form,'期初课时（允许负数）','number');units.min='-1000000';units.max='10000000';units.step='1';units.value='0';units.inputMode='numeric';
 const occurred=field(form,'业务发生时间（北京时间）','datetime-local');occurred.value=chinaDateTimeLocal();
 const source=field(form,'业务来源编号');source.maxLength=100;source.placeholder='例如：报名单据或业务流水编号';
 const reasonLabel=node('label','入班说明','enrollment-wide'),reason=node('textarea');reason.className='enrollment-reason';reason.required=true;reason.maxLength=200;reason.value='新生入班期初课时';reasonLabel.append(reason);form.append(reasonLabel);
 const confirmation=node('label',undefined,'check renewal-confirmation enrollment-wide'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对学生、学号、入班班级、期初课时和来源编号'));form.append(confirmation);
 const hint=node('p','只建立学生当前班级，不回填历史签到，也不修改已生成课次的应到名单。期初欠课可填负整数；本操作不记录金额。','hint enrollment-wide');form.append(hint);
 const submit=node('button','确认招新并入班');submit.type='submit';form.append(submit);panel.append(form);sections.push(panel);$('main').replaceChildren(...sections);
 let previousPayload='',commandRef='';
 form.addEventListener('submit',async event=>{
  event.preventDefault();const opening=Number(units.value),occurredAt=Math.floor(Date.parse(occurred.value+':00+08:00')/1000);
  if(!Number.isInteger(opening)||opening < -1000000||opening>10000000){units.setCustomValidity('期初课时必须是 -1000000 至 10000000 的整数');units.reportValidity();return;}units.setCustomValidity('');
  if(!Number.isFinite(occurredAt)){occurred.setCustomValidity('请选择有效的业务发生时间');occurred.reportValidity();return;}occurred.setCustomValidity('');
  const payload={class_id:classSelect.value,name:name.value.trim(),external_ref:external.value.trim(),opening_units:opening,occurred_at:occurredAt,source_ref:source.value.trim(),reason:reason.value.trim(),command_ref:''};
  if(!payload.name){name.setCustomValidity('请填写学生姓名');name.reportValidity();return;}name.setCustomValidity('');if(!payload.external_ref){external.setCustomValidity('请填写学生学号');external.reportValidity();return;}external.setCustomValidity('');
  if(!payload.source_ref){source.setCustomValidity('请填写业务来源编号');source.reportValidity();return;}source.setCustomValidity('');if(!payload.reason){reason.setCustomValidity('请填写入班说明');reason.reportValidity();return;}reason.setCustomValidity('');
  const serialized=JSON.stringify({...payload,command_ref:undefined});if(serialized!==previousPayload){commandRef=crypto.randomUUID().replaceAll('-','');previousPayload=serialized;}payload.command_ref=commandRef;submit.disabled=true;
  try{const result=await api('/v1/admin/students',payload);await adminEnrollmentDashboard(result);notify((result.duplicate?'该新生已登记，未重复建档。':'招新成功：')+result.name+' 已加入 '+result.class_name+'，期初 '+result.balance_units+' 课时');}
  catch(error){showError(error);}finally{submit.disabled=false;}
 });
 for(const input of [name,external,units,occurred,source,reason])input.addEventListener('input',()=>input.setCustomValidity(''));
}
