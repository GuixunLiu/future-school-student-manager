'use strict';
function openBalanceAdjustment(student){
 const owner=hasStaffRole('owner'),content=directoryDialog((owner?'调整课时 · ':'申请调课时 · ')+student.name),form=node('form',undefined,'directory-form');
 content.append(node('p','当前余额 '+teacherLessonHours(student.balance_units)+' · 版本 '+student.balance_revision));
 const units=field(form,'调整后的剩余课时','number');hourInput(units,-1000000,10000000);units.value=String(lessonHours(student.balance_units));
 const reason=scheduleReason(form,owner?'调整原因':'申请原因','请填写核对依据');
 form.append(node('p',owner?'保存后更新余额并保留课时流水和操作记录。':'提交申请不会改变余额，须由老板核对并批准。','hint'));
 const submit=node('button',owner?'确认调整课时':'提交调整申请');submit.type='submit';form.append(submit);content.append(form);
 const command=directoryCommandState(form);
 form.addEventListener('submit',async event=>{
  event.preventDefault();const value=lessonUnits(units.value);
  if(!Number.isInteger(value)||value < -1000000||value>10000000){units.setCustomValidity('请输入允许范围内的课时');units.reportValidity();return;}units.setCustomValidity('');
  submit.disabled=true;
  try{
   await api((owner?'/v1/owner/students/':'/v1/admin/students/')+encodeURIComponent(student.id)+(owner?'/balance':'/balance-request'),
    command({units:value,expected_revision:student.balance_revision,reason:reason.value.trim(),command_ref:''}),owner?'PUT':'POST');
   $('device-dialog').close();await adminDirectoryDashboard();notify(owner?'课时已调整并记录流水':'申请已提交，课时余额未改变');
  }catch(error){if(error.status===409){$('device-dialog').close();await adminDirectoryDashboard();}showError(error);}
  finally{if(submit.isConnected)submit.disabled=false;}
 });
 units.addEventListener('input',()=>units.setCustomValidity(''));if(!$('device-dialog').open)$('device-dialog').showModal();
}
async function balanceRequestsDashboard(){
 const {items}=await api('/v1/balance-adjustment-requests');adminIdentity();
 const heading=campusHeading('课时调整申请','校区管理员提交申请，老板核对审批。批准前余额不变；余额已变化时须重新核对申请。');
 heading.append(button('返回管理',dashboard,'secondary'),button('刷新',balanceRequestsDashboard,'secondary'));
 const list=node('section',undefined,'panel teaching-fee-list'),names={pending:'待审批',approved:'已批准',rejected:'已拒绝'};
 if(!items.length)list.append(node('p','暂无课时调整申请。','hint'));
 for(const item of items){
  const card=node('article',undefined,'item');card.append(node('h2',item.student_name+' · '+item.campus_name),
   node('p',teacherLessonHours(item.previous_units)+' → '+teacherLessonHours(item.units)+' · '+names[item.status]),
   node('p','申请人 '+item.requested_by_name+' · '+monthlyTime(item.requested_at)),node('p','申请原因：'+item.reason));
  if(item.decision_reason)card.append(node('p','审批说明：'+item.decision_reason));
  if(hasStaffRole('owner')&&item.status==='pending')card.append(button('批准调整',()=>openBalanceDecision(item,'approved')),button('拒绝申请',()=>openBalanceDecision(item,'rejected'),'secondary'));
  list.append(card);
 }
 $('main').replaceChildren(heading,list);
}
function openBalanceDecision(item,action){
 const title=action==='approved'?'批准调整':'拒绝申请',content=directoryDialog(title+' · '+item.student_name),form=node('form',undefined,'directory-form');
 content.append(node('p',teacherLessonHours(item.previous_units)+' → '+teacherLessonHours(item.units)),node('p','申请原因：'+item.reason));
 const reason=scheduleReason(form,'审批说明','请填写核对依据或拒绝原因'),submit=node('button',title);submit.type='submit';form.append(submit);content.append(form);
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   await api('/v1/owner/balance-adjustment-requests/'+encodeURIComponent(item.id),{action,expected_version:item.version,reason:reason.value.trim()},'PUT');
   $('device-dialog').close();await balanceRequestsDashboard();notify(action==='approved'?'已批准，课时与流水同步更新':'申请已拒绝，余额未改变');
  }catch(error){if(error.status===409){$('device-dialog').close();await balanceRequestsDashboard();}showError(error);}finally{if(submit.isConnected)submit.disabled=false;}
 });if(!$('device-dialog').open)$('device-dialog').showModal();
}
