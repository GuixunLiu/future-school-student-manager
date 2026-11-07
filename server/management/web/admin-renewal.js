'use strict';
let adminStudents=[],adminClasses=[],adminCourses=[];
function chinaDateTimeLocal(value=new Date()){
 const parts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(value),values={};
 for(const part of parts)values[part.type]=part.value;
 return values.year+'-'+values.month+'-'+values.day+'T'+values.hour+':'+values.minute;
}
function renewalStudentRows(items){
 const body=node('tbody');
 if(!items.length){const row=node('tr'),cell=node('td','没有符合条件的在读学生');cell.colSpan=5;cell.className='empty';row.append(cell);body.append(row);return body;}
 for(const student of items){
  const row=node('tr'),name=node('td',undefined,'student-name');name.textContent=student.name;
  const balance=node('td',String(student.balance_units)+' 课时','renewal-balance');balance.append(node('small','余额版本 '+student.balance_revision,'revision'));
  const operations=node('td',undefined,'roster-operation'),renew=button('续费登记',()=>openAdminRenewal(student.id)),sync=button('同步课次',()=>adminRosterSyncDashboard(student.id),'secondary');renew.dataset.action='renew';renew.dataset.studentId=student.id;sync.dataset.action='sync-roster';sync.dataset.studentId=student.id;operations.append(renew,sync);
  row.append(name,node('td',student.external_ref||'—'),node('td',(student.class_names||[]).join('、')||'未分班'),balance,operations);body.append(row);
 }
 return body;
}
function renewalCourses(student){const courses=new Map();for(const item of adminClasses){const course=adminCourses.find(row=>row.id===item.course_id&&row.active);if((student.class_ids||[]).includes(item.id)&&course&&!courses.has(course.id))courses.set(course.id,course);}return [...courses.values()];}
async function adminRenewalDashboard(){
 const [data,classData,courseData]=await Promise.all([api('/v1/admin/students'),api('/v1/admin/classes'),api('/v1/admin/courses')]);adminStudents=data.items;adminClasses=classData.items;adminCourses=courseData.items;adminIdentity();
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','管理员续费登记'),node('p','选择学生、续费课程并核对课时、实收金额和成交单价。服务器按最新余额累加；续费记录、课时流水、审计和家长通知任务同时保存。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('招新登记',()=>adminEnrollmentDashboard(),'secondary'),button('查看月报',()=>adminMonthlyReport(),'secondary'),button('刷新',()=>adminRenewalDashboard(),'secondary'));heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter'),label=node('label','搜索学生'),search=node('input');search.type='search';search.placeholder='姓名、学号或班级';search.setAttribute('aria-label','搜索续费学生');label.append(search);filter.append(label,node('p','仅显示在读学生。金额按元录入、按整数分保存；微信发送尚未启用，已绑定家长只会生成待发送任务。','weekly-note'));
 const table=node('table'),head=node('thead'),headRow=node('tr');for(const title of ['学生','学号','当前班级','剩余课时','操作'])headRow.append(node('th',title));head.append(headRow);
 const active=()=>adminStudents.filter(item=>item.active),applyFilter=()=>{const query=search.value.trim().toLocaleLowerCase();const matches=active().filter(item=>!query||[item.name,item.external_ref,...(item.class_names||[])].some(value=>String(value||'').toLocaleLowerCase().includes(query)));table.replaceChildren(head,renewalStudentRows(matches));};
 search.addEventListener('input',applyFilter);table.append(head,renewalStudentRows(active()));const scroll=node('div',undefined,'table-scroll');scroll.append(table);const panel=node('section',undefined,'panel renewal-panel');panel.append(scroll);
 $('main').replaceChildren(heading,filter,panel);
}
function openAdminRenewal(studentId){
 const student=adminStudents.find(item=>item.id===studentId&&item.active);if(!student)throw Error('学生资料已变化，请刷新后重新选择');
 const courses=renewalCourses(student),content=$('dialog-content');content.replaceChildren();const title=node('h2','为学生办理续费');title.id='dialog-title';content.append(title);
 const summary=node('div',undefined,'renewal-form-summary');summary.append(node('strong',student.name+' · '+(student.external_ref||'无学号')),node('p',(student.class_names||[]).join('、')+' · 页面余额 '+student.balance_units+' 课时'),node('p','最终入账以服务器锁定的最新余额为准；历史续费单价不受以后调价影响。'));content.append(summary);
 const form=node('form'),courseLabel=node('label','续费课程'),course=node('select');course.required=true;course.setAttribute('aria-label','续费课程');for(const item of courses){const option=node('option',item.name);option.value=item.id;course.append(option);}courseLabel.append(course);form.append(courseLabel);
 const units=field(form,'续费课时','number');units.min='1';units.max='11000000';units.step='1';units.inputMode='numeric';units.placeholder='请输入正整数课时';
 const price=field(form,'成交单价（元 / 课时）','number');price.min='0';price.max='1000000';price.step='0.01';price.required=true;price.inputMode='decimal';
 const amount=field(form,'实收金额（元）','number');amount.min='0';amount.max='1000000';amount.step='0.01';amount.required=true;amount.inputMode='decimal';
 const occurred=field(form,'业务发生时间（北京时间）','datetime-local');occurred.value=chinaDateTimeLocal();
 const source=field(form,'业务来源编号');source.maxLength=100;source.placeholder='例如：收费单据或业务流水编号';
 const reasonLabel=node('label','续费说明'),reason=node('textarea');reason.className='renewal-reason';reason.required=true;reason.maxLength=200;reason.value='续费课时入账';reasonLabel.append(reason);form.append(reasonLabel);
 const confirmation=node('label',undefined,'check renewal-confirmation'),check=node('input');check.type='checkbox';check.required=true;confirmation.append(check,node('span','已核对学生、课程、续费课时、实收金额、成交单价和业务来源编号'));form.append(confirmation);
 form.append(node('p','确认后将增加学生共用课时余额，并永久保存本笔课程、实收金额与成交单价快照；本环境不会真正发送微信。','hint'));
 const submit=node('button','确认续费并入账');submit.type='submit';submit.disabled=!courses.length;form.append(submit);content.append(form);
 let previousPayload='',commandRef='',amountTouched=false;
 function selectedCourse(){return courses.find(item=>item.id===course.value);}
 function defaultPrice(){const item=selectedCourse();price.value=item?(item.unit_price_cents/100).toFixed(2):'';if(!amountTouched)defaultAmount();}
 function defaultAmount(){const added=Number(units.value),unitPrice=Number(price.value);if(Number.isInteger(added)&&added>0&&Number.isFinite(unitPrice))amount.value=(added*unitPrice).toFixed(2);}
 course.addEventListener('change',defaultPrice);units.addEventListener('input',()=>{units.setCustomValidity('');if(!amountTouched)defaultAmount();});price.addEventListener('input',()=>{price.setCustomValidity('');if(!amountTouched)defaultAmount();});amount.addEventListener('input',()=>{amountTouched=true;amount.setCustomValidity('');});defaultPrice();
 form.addEventListener('submit',async event=>{
  event.preventDefault();const added=Number(units.value),occurredAt=Math.floor(Date.parse(occurred.value+':00+08:00')/1000),amountCents=Math.round(Number(amount.value)*100),priceCents=Math.round(Number(price.value)*100);
  if(!Number.isInteger(added)||added<1){units.setCustomValidity('续费课时必须是正整数');units.reportValidity();return;}units.setCustomValidity('');
  if(!Number.isFinite(occurredAt)){occurred.setCustomValidity('请选择有效的业务发生时间');occurred.reportValidity();return;}occurred.setCustomValidity('');
  if(!/^\d+(?:\.\d{1,2})?$/.test(amount.value)||!Number.isSafeInteger(amountCents)||amountCents<0||amountCents>100000000){amount.setCustomValidity('实收金额须为 0 至 1000000 元，最多两位小数');amount.reportValidity();return;}amount.setCustomValidity('');
  if(!/^\d+(?:\.\d{1,2})?$/.test(price.value)||!Number.isSafeInteger(priceCents)||priceCents<0||priceCents>100000000){price.setCustomValidity('成交单价须为 0 至 1000000 元，最多两位小数');price.reportValidity();return;}price.setCustomValidity('');
  const payload={student_id:student.id,course_id:course.value,units_added:added,amount_cents:amountCents,unit_price_cents:priceCents,occurred_at:occurredAt,source_ref:source.value.trim(),reason:reason.value.trim(),command_ref:''};
  if(!payload.course_id){course.setCustomValidity('学生当前没有可续费课程');course.reportValidity();return;}course.setCustomValidity('');
  if(!payload.source_ref){source.setCustomValidity('请填写业务来源编号');source.reportValidity();return;}source.setCustomValidity('');if(!payload.reason){reason.setCustomValidity('请填写续费说明');reason.reportValidity();return;}reason.setCustomValidity('');
  const serialized=JSON.stringify({...payload,command_ref:undefined});if(serialized!==previousPayload){commandRef=crypto.randomUUID().replaceAll('-','');previousPayload=serialized;}payload.command_ref=commandRef;submit.disabled=true;
  try{
   const result=await api('/v1/admin/renewals',payload);$('device-dialog').close();await adminRenewalDashboard();
   notify((result.duplicate?'该笔续费已登记，未重复入账。':'续费成功：')+student.name+' · '+result.course_name+'，实收 '+(result.amount_cents/100).toFixed(2)+' 元，余额 '+result.previous_balance_units+' → '+result.balance_units+' 课时；家长通知待发送任务 '+result.notification_tasks+' 条（微信发送未启用）');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
 occurred.addEventListener('input',()=>occurred.setCustomValidity(''));source.addEventListener('input',()=>source.setCustomValidity(''));reason.addEventListener('input',()=>reason.setCustomValidity(''));
 if(!$('device-dialog').open)$('device-dialog').showModal();units.focus();
}
