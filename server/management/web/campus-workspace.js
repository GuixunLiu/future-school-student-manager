'use strict';
const staffRoleNames={owner:'老板',campus_admin:'校区管理员',performance_reviewer:'绩效审核员'};
const feeReviewStatus={submitted:'待审核',returned:'已退回',approved:'审核通过',settled:'已确认结算'};
const hasStaffRole=role=>identity.roles.includes(role);

function campusHeading(title,description){
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div');
 copy.append(node('h1',title),node('p',description));heading.append(copy);return heading;
}

async function ownerStaffDashboard(){
 const [staff,campuses]=await Promise.all([api('/v1/owner/staff-access'),api('/v1/admin/campuses')]);
 adminIdentity();
 const heading=campusHeading('管理权限','校区管理员只管理绑定校区；绩效审核员可以核对全部校区的课时费。');
 const actions=node('div',undefined,'toolbar teacher-actions');
 actions.append(button('返回管理',dashboard,'secondary'),button('操作记录',ownerAccessAudit,'secondary'),
  button('分配管理权限',()=>openStaffAccess(null,campuses.items)));
 heading.append(actions);
 const section=node('section',undefined,'panel'),list=node('div',undefined,'device-list');
 for(const item of staff.items){
  const row=node('article',undefined,'item');
  row.append(node('h2',item.display_name),node('p',item.login+' · '+(staffRoleNames[item.role]||'待分配')+(item.campus_name?' · '+item.campus_name:'')));
  if(item.role!=='owner')row.append(button('调整权限',()=>openStaffAccess(item,campuses.items),'secondary'));
  list.append(row);
 }
 section.append(list);$('main').replaceChildren(heading,section);
}

async function ownerAccessAudit(){
 const {items}=await api('/v1/owner/access-audit');
 const names={staff_owner_provisioned:'启用老板账号',staff_access_set:'调整管理权限',class_campus_assigned:'确认校区归属',
  course_teaching_fee_changed:'调整课程费率',course_fee_proposed:'申请费率',course_fee_proposal_approved:'批准费率',
  course_fee_proposal_returned:'退回费率',teaching_fees_submitted:'提交课时费审核',teaching_fees_returned:'退回课时费',
  teaching_fees_approved:'课时费审核通过',teaching_fees_settled:'确认月度结算',lesson_substitution_changed:'调整代课'};
 const heading=campusHeading('操作记录','最近 100 条权限、校区归属、费率、代课与结算记录。');
 heading.append(button('返回权限管理',ownerStaffDashboard,'secondary'));
 const section=node('section',undefined,'panel teaching-fee-list');
 for(const item of items){
  const row=node('article',undefined,'item');
  row.append(node('strong',names[item.action]),node('p',scheduleDateKey(item.created_at)+' '+lessonTime(item.created_at)+' · '+(item.operator||'系统')));
  if(item.reason)row.append(node('p',item.reason));
  section.append(row);
 }
 if(!items.length)section.append(node('p','暂无操作记录。'));
 $('main').replaceChildren(heading,section);
}

function openStaffAccess(item,campuses){
 const content=directoryDialog(item?'调整管理权限':'分配管理权限'),form=node('form',undefined,'directory-form');
 const loginField=field(form,'登录账号');loginField.maxLength=64;
 if(item){loginField.value=item.login;loginField.readOnly=true;}
 const roleLabel=node('label','身份'),role=node('select');
 role.append(new Option('校区管理员','campus_admin'),new Option('绩效审核员','performance_reviewer'),new Option('撤销管理权限',''));
 role.value=item?.role||'campus_admin';roleLabel.append(role);form.append(roleLabel);
 const campus=campusField(form,campuses,item?.campus_id||'','管理校区');
 function updateCampus(){const needed=role.value==='campus_admin';campus.disabled=!needed;campus.parentElement.hidden=!needed;}
 role.addEventListener('change',updateCampus);updateCampus();
 let name,password;
 if(!item){
  form.append(node('p','为已有管理账号分配权限时，只填写登录账号。创建新管理账号时，再填写姓名和初始密码。','hint'));
  name=field(form,'姓名');name.required=false;name.maxLength=80;
  password=field(form,'初始密码','password');password.required=false;password.minLength=15;password.maxLength=128;password.autocomplete='new-password';
 }
 const reason=scheduleReason(form,'操作原因','例如：负责北校区日常教务');
 const submit=node('button','保存权限');submit.type='submit';form.append(submit);content.append(form);
 if(!$('device-dialog').open)$('device-dialog').showModal();
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   await api('/v1/owner/staff-access',{login:loginField.value.trim(),role:role.value||null,
    campus_id:campus.disabled?null:campus.value,display_name:name?.value.trim()||null,
    password:password?.value||null,reason:reason.value.trim()},'PUT');
   $('device-dialog').close();await ownerStaffDashboard();notify('管理权限已更新');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
}

async function openClassCampus(item){
 const {items}=await api('/v1/admin/campuses');
 if(!items.length){notify('请先在账号管理中创建校区');return;}
 const content=directoryDialog('核对校区归属 · '+item.name),form=node('form',undefined,'directory-form');
 const campus=campusField(form,items,item.campus_id||'','班级校区');
 if(item.campus_id)campus.disabled=true;
 const label=node('label',undefined,'check directory-confirm'),history=node('input');history.type='checkbox';
 label.append(history,node('span','已核对，尚未归属的历史课次也属于这个校区'));form.append(label);
 const reason=scheduleReason(form,'归属依据','例如：已核对班级及历史课表');
 const submit=node('button','保存校区归属');submit.type='submit';form.append(submit);content.append(form);
 if(!$('device-dialog').open)$('device-dialog').showModal();
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   await api('/v1/owner/classes/'+encodeURIComponent(item.id)+'/campus',{
    campus_id:campus.value,include_history:history.checked,reason:reason.value.trim()
   },'PUT');
   $('device-dialog').close();await adminDirectoryDashboard('classes');notify('校区归属已保存');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
}

function renderFeeSnapshot(data){
 const section=node('section',undefined,'panel');
 section.append(node('h2',data.start+' 至 '+data.end),node('p','合计 '+teachingFeeMoney(data.total_cents)));
 if(!data.items.length)section.append(node('p','本期没有授课记录。','hint'));
 for(const item of data.items){
  const list=node('ul');
  for(const lesson of item.lessons){
   const rate=lesson.rate_cents===null?'费率待核算':teachingFeeMoney(lesson.rate_cents)+' × '+lesson.arrived_count+' = '+teachingFeeMoney(lesson.total_cents);
   list.append(node('li',scheduleDateKey(lesson.starts_at)+' '+lessonTime(lesson.starts_at)+' · '+lesson.class_name+
    (lesson.is_substitute?'（代课）':'')+' · '+lesson.duration_minutes+' 分钟 · 实到 '+lesson.arrived_count+' 人 · '+rate));
  }
  const details=node('details');details.append(node('summary',item.display_name+' · '+teachingFeeMoney(item.total_cents)),list);section.append(details);
 }
 return section;
}

async function feeReviewDashboard(){
 const data=await api('/v1/fee-reviews'),reviewer=hasStaffRole('performance_reviewer'),owner=hasStaffRole('owner');
 $('identity').replaceChildren(node('span',identity.display_name+' · '+(reviewer?'绩效审核员':identity.campus_name||'老板')),button('退出',logout,'secondary'));
 const heading=campusHeading('课时费审核','按周核对，月度审核通过后由老板确认结算。');
 if(!reviewer)heading.append(button('返回管理',dashboard,'secondary'));
 const filters=node('section',undefined,'panel'),form=node('form',undefined,'directory-form');
 const campus=campusField(form,data.campuses,identity.campus_id||data.campuses[0]?.id||'','统计校区');
 const label=node('label','统计周期'),period=node('select');period.append(new Option('按周','week'),new Option('按月','month'));
 label.append(period);form.append(label);
 const date=field(form,'统计日期','date');date.value=chinaToday();
 const submit=node('button','查看绩效明细');submit.type='submit';form.append(submit);filters.append(form);
 if(!data.campuses.length){submit.disabled=true;filters.append(node('p','还没有可用校区。','hint'));}
 const preview=node('div'),list=node('section',undefined,'panel teaching-fee-list');list.append(node('h2','已提交的审核单'));
 if(!data.items.length)list.append(node('p','暂无已提交的审核单。','hint'));
 for(const item of data.items){
  const entry=node('details',undefined,'panel');
  entry.append(node('summary',item.campus_name+' · '+(item.period==='week'?'周':'月')+' · '+item.period_start+' · '+feeReviewStatus[item.status]),
   node('p','最近操作说明：'+item.reason,'hint'),renderFeeSnapshot(item.snapshot));
  if(reviewer&&['submitted','approved'].includes(item.status)){
   if(item.status==='submitted')entry.append(button('审核通过',()=>openFeeDecision(item,'approved')));
   entry.append(button('退回',()=>openFeeDecision(item,'returned'),'secondary'));
  }
  if(owner&&item.period==='month'&&item.status==='approved')entry.append(button('确认月度结算',()=>openFeeDecision(item,'settled')));
  list.append(entry);
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  const query={campus_id:campus.value,period:period.value,date:date.value};
  try{
   const report=await api('/v1/fee-reviews/preview?'+new URLSearchParams(query));
   const panel=renderFeeSnapshot(report);
   if(!reviewer&&report.closed&&report.items.length)panel.append(button('提交审核',()=>openFeeDecision(null,'submit',query)));
   preview.replaceChildren(panel);
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
 $('main').replaceChildren(heading,filters,preview,list);
}

function openFeeDecision(item,action,query){
 const title={submit:'提交课时费审核',approved:'审核通过',returned:'退回审核单',settled:'确认月度结算'}[action];
 const content=directoryDialog(title),form=node('form',undefined,'directory-form');
 const reason=scheduleReason(form,'操作说明','填写核对依据或退回原因');
 const submit=node('button',title);submit.type='submit';form.append(submit);content.append(form);
 if(!$('device-dialog').open)$('device-dialog').showModal();
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   if(action==='submit')await api('/v1/fee-reviews',{...query,reason:reason.value.trim()});
   else await api('/v1/fee-reviews/'+encodeURIComponent(item.id),{action,expected_version:item.version,reason:reason.value.trim()},'PUT');
   $('device-dialog').close();await feeReviewDashboard();notify('审核状态已更新');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
}

async function courseFeeProposalDashboard(){
 const {items}=await api('/v1/course-fee-proposals');
 const heading=campusHeading('课程费率申请','审批通过后，课程费率适用于所有使用该课程的校区，从尚未开课的课次起生效。');
 heading.append(button('返回管理',dashboard,'secondary'));
 const list=node('section',undefined,'panel teaching-fee-list');
 if(!items.length)list.append(node('p','暂无费率申请。'));
 for(const item of items){
  const panel=node('article',undefined,'item');
  panel.append(node('h2',item.course_name+' · '+item.campus_name),
   node('p',(item.rate_cents===null?'恢复默认费率':teachingFeeMoney(item.rate_cents)+' / 人')+' · '+feeReviewStatus[item.status]),
   node('p',item.reason));
  if(item.decision_reason)panel.append(node('p','审批说明：'+item.decision_reason,'hint'));
  if(hasStaffRole('owner')&&item.status==='submitted'){
   const actions=node('div',undefined,'toolbar');
   actions.append(button('批准费率',()=>openRateDecision(item,'approved')),button('退回申请',()=>openRateDecision(item,'returned'),'secondary'));
   panel.append(actions);
  }
  list.append(panel);
 }
 $('main').replaceChildren(heading,list);
}

function openRateDecision(item,action){
 const title=action==='approved'?'批准费率':'退回申请';
 const content=directoryDialog(title),form=node('form',undefined,'directory-form');
 const reason=scheduleReason(form,'审批说明','填写核对依据或退回原因');
 const submit=node('button',title);submit.type='submit';form.append(submit);content.append(form);
 if(!$('device-dialog').open)$('device-dialog').showModal();
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{
   await api('/v1/course-fee-proposals/'+encodeURIComponent(item.id),{action,reason:reason.value.trim()},'PUT');
   $('device-dialog').close();await courseFeeProposalDashboard();notify('费率申请已处理');
  }catch(error){showError(error);}finally{submit.disabled=false;}
 });
}
