'use strict';
let adminStudents=[];
function chinaDateTimeLocal(){
 const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(new Date()),values={};
 for(const part of parts)values[part.type]=part.value;
 return values.year+'-'+values.month+'-'+values.day+'T'+values.hour+':'+values.minute;
}
function renewalStudentRows(items){
 const body=node('tbody');
 if(!items.length){const row=node('tr'),cell=node('td','没有符合条件的在读学生');cell.colSpan=5;cell.className='empty';row.append(cell);body.append(row);return body;}
 for(const student of items){
  const row=node('tr'),name=node('td',undefined,'student-name');name.textContent=student.name;
  const balance=node('td',String(student.balance_units)+' 课时','renewal-balance');balance.append(node('small','余额版本 '+student.balance_revision,'revision'));
  const operations=node('td'),renew=button('续费登记',()=>openAdminRenewal(student.id));renew.dataset.action='renew';renew.dataset.studentId=student.id;operations.append(renew);
  row.append(name,node('td',student.external_ref||'—'),node('td',student.class_name),balance,operations);body.append(row);
 }
 return body;
}
async function adminRenewalDashboard(){
 const data=await api('/v1/admin/students');adminStudents=data.items;adminIdentity();
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','管理员续费登记'),node('p','选择学生并核对续费课时。服务器按最新余额累加，不覆盖其他入账；续费记录、课时流水、审计和家长通知任务同时保存。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('查看月报',()=>adminMonthlyReport(),'secondary'),button('刷新',()=>adminRenewalDashboard(),'secondary'));heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter'),label=node('label','搜索学生'),search=node('input');search.type='search';search.placeholder='姓名、学号或班级';search.setAttribute('aria-label','搜索续费学生');label.append(search);filter.append(label,node('p','仅显示在读学生。续费不记录金额；微信发送尚未启用，已绑定家长只会生成待发送任务。','weekly-note'));
 const table=node('table'),head=node('thead'),headRow=node('tr');for(const title of ['学生','学号','当前班级','剩余课时','操作'])headRow.append(node('th',title));head.append(headRow);
 const active=()=>adminStudents.filter(item=>item.active),applyFilter=()=>{const query=search.value.trim().toLocaleLowerCase();const matches=active().filter(item=>!query||[item.name,item.external_ref,item.class_name].some(value=>String(value||'').toLocaleLowerCase().includes(query)));table.replaceChildren(head,renewalStudentRows(matches));};
 search.addEventListener('input',applyFilter);table.append(head,renewalStudentRows(active()));const scroll=node('div',undefined,'table-scroll');scroll.append(table);const panel=node('section',undefined,'panel renewal-panel');panel.append(scroll);
 $('main').replaceChildren(heading,filter,panel);
}
function openAdminRenewal(studentId){
 const student=adminStudents.find(item=>item.id===studentId&&item.active);if(!student)throw Error('学生资料已变化，请刷新后重新选择');
 const content=$('dialog-content');content.replaceChildren();const title=node('h2','为学生办理续费');title.id='dialog-title';content.append(title);
 const summary=node('div',undefined,'renewal-form-summary');summary.append(node('strong',student.name+' · '+(student.external_ref||'无学号')),node('p',student.class_name+' · 页面余额 '+student.balance_units+' 课时'),node('p','最终入账以服务器锁定的最新余额为准。'));content.append(summary);
 const form=node('form'),units=field(form,'续费课时','number');units.min='1';units.max='11000000';units.step='1';units.inputMode='numeric';units.placeholder='请输入正整数课时';
 const occurred=field(form,'业务发生时间（北京时间）','datetime-local');occurred.value=chinaDateTimeLocal();
 const source=field(form,'业务来源编号');source.maxLength=100;source.placeholder='例如：收费单据或业务流水编号';
 const reasonLabel=node('label','续费说明'),reason=node('textarea');reason.className='renewal-reason';reason.required=true;reason.maxLength=200;reason.value='续费课时入账';reasonLabel.append(reason);form.append(reasonLabel);
 const confirmation=node('label',undefined,'check renewal-confirmation'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对学生、续费课时和业务来源编号'));form.append(confirmation);
 form.append(node('p','确认后将增加学生课时，并生成不可变续费记录和流水。此操作不记录金额，也不会在本环境真正发送微信。','hint'));
 const submit=node('button','确认续费并入账');submit.type='submit';form.append(submit);content.append(form);
 let previousPayload='',commandRef='';
 form.addEventListener('submit',async event=>{
  event.preventDefault();const added=Number(units.value),occurredAt=Math.floor(Date.parse(occurred.value+':00+08:00')/1000);
  if(!Number.isInteger(added)||added<1){units.setCustomValidity('续费课时必须是正整数');units.reportValidity();return;}
  units.setCustomValidity('');if(!Number.isFinite(occurredAt)){occurred.setCustomValidity('请选择有效的业务发生时间');occurred.reportValidity();return;}occurred.setCustomValidity('');
  const payload={student_id:student.id,units_added:added,occurred_at:occurredAt,source_ref:source.value.trim(),reason:reason.value.trim(),command_ref:''};
  if(!payload.source_ref){source.setCustomValidity('请填写业务来源编号');source.reportValidity();return;}source.setCustomValidity('');if(!payload.reason){reason.setCustomValidity('请填写续费说明');reason.reportValidity();return;}reason.setCustomValidity('');
  const serialized=JSON.stringify({...payload,command_ref:undefined});if(serialized!==previousPayload){commandRef=crypto.randomUUID().replaceAll('-','');previousPayload=serialized;}payload.command_ref=commandRef;submit.disabled=true;
  try{
   const result=await api('/v1/admin/renewals',payload);$('device-dialog').close();await adminRenewalDashboard();
   notify((result.duplicate?'该笔续费已登记，未重复入账。':'续费成功：')+student.name+' '+result.previous_balance_units+' → '+result.balance_units+' 课时；家长通知待发送任务 '+result.notification_tasks+' 条（微信发送未启用）');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
 units.addEventListener('input',()=>units.setCustomValidity(''));occurred.addEventListener('input',()=>occurred.setCustomValidity(''));source.addEventListener('input',()=>source.setCustomValidity(''));reason.addEventListener('input',()=>reason.setCustomValidity(''));
 if(!$('device-dialog').open)$('device-dialog').showModal();units.focus();
}
