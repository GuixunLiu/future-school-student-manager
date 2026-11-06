'use strict';
const $=id=>document.getElementById(id);
let token='',identity=null,classes=[],noticeTimer;
const deviceCommands=new Map();
function deviceCommand(kind,deviceId){const key=kind+':'+deviceId;if(!deviceCommands.has(key))deviceCommands.set(key,crypto.randomUUID().replaceAll('-',''));return{key,command_ref:deviceCommands.get(key)};}
function node(tag,text,className){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;}
function button(text,action,kind=''){const element=node('button',text,kind);element.type='button';element.addEventListener('click',()=>Promise.resolve().then(action).catch(showError));return element;}
function field(form,title,type='text'){const label=node('label',title),input=node('input');input.type=type;input.required=true;label.append(input);form.append(label);return input;}
function notify(text){clearTimeout(noticeTimer);$('notice').textContent=text;$('notice').style.display='block';noticeTimer=setTimeout(()=>$('notice').style.display='none',6000);}
function showError(error){notify(error.message||'操作未完成，请刷新后核对');}
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});const data=await response.json();if(!response.ok){if(response.status===401&&token){token='';identity=null;login();}const error=Error(data.error?.message||'请求未完成');error.status=response.status;error.code=data.error?.code;throw error;}return data;}
function login(){
 token='';identity=null;$('identity').replaceChildren();$('device-dialog').close();
 const panel=node('section',undefined,'panel login'),form=node('form');panel.append(node('h1','登录统一工作台'),node('p','教师进入本人课堂，管理员进入设备管理。登录状态只保留在当前页面，刷新后需要重新登录。'));
 const organization=field(form,'机构标识'),account=field(form,'账号'),password=field(form,'密码','password');account.autocomplete='username';password.autocomplete='current-password';
 const submit=node('button','登录');submit.type='submit';form.append(submit);panel.append(form);$('main').replaceChildren(panel);
 form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{const result=await api('/v1/auth/login',{organization:organization.value.trim(),login:account.value.trim(),password:password.value});password.value='';token=result.access_token;identity=await api('/v1/me');if(identity.roles.includes('teacher'))await teacherDashboard();else if(identity.roles.includes('admin'))await dashboard();else throw Error('当前账号没有教师或管理员权限');}catch(error){showError(error);}finally{submit.disabled=false;}});
}
async function dashboard(){
 const [classData,deviceData]=await Promise.all([api('/v1/admin/classes'),api('/v1/admin/devices')]);classes=classData.items;
 $('identity').replaceChildren(node('span',identity.display_name),button('退出',logout,'secondary'));
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','平板设备管理'),node('p','为一台新平板选择可签到班级并生成一次性开通码。开通码 15 分钟内有效，只在生成时显示一次。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('刷新',dashboard,'secondary'),button('全校周报',()=>adminWeeklyReport(),'secondary'),button('招新续费月报',()=>adminMonthlyReport(),'secondary'),button('续费登记',()=>adminRenewalDashboard(),'secondary'),button('开通新平板',openEnrollment));heading.append(copy,actions);const list=node('section',undefined,'device-list');
 if(!deviceData.items.length)list.append(node('div','尚未开通平板','empty'));
 for(const device of deviceData.items){const row=node('article',undefined,'item device-row'),title=node('div');title.append(node('h2',device.name),node('p','设备编号 '+device.id));
  const names=(device.class_ids||[]).map(id=>classes.find(item=>item.id===id)?.name||id),status=node('div',undefined,'device-status'),operations=node('div',undefined,'device-actions');
  status.append(node('span',device.active?'已激活':device.activation_pending?'等待开通':'已停用','badge device-state'));
  operations.append(button('重新签发',()=>reissueDevice(device),'secondary'),button('停用',()=>disableDevice(device),'secondary danger'));
  row.append(title,node('p','授权班级：'+(names.join('、')|| '未授权')),status,operations);list.append(row);}
 $('main').replaceChildren(heading,list);
}
async function reissueDevice(device){
 if(!window.confirm('重新签发后，这台设备的旧平板会立即失效。请先上传全部离线签到，再继续。'))return;
 const command=deviceCommand('reissue',device.id),result=await api('/v1/admin/devices/'+encodeURIComponent(device.id)+'/activation',{command_ref:command.command_ref});
 deviceCommands.delete(command.key);if(!result.activation_code)throw Error('开通码不可重复查看，请生成新的重新签发操作');showActivation(result);await dashboard();
}
async function disableDevice(device){
 if(!window.confirm('确认停用“'+device.name+'”？设备令牌和未使用开通码将失效，但课堂、签到和课时数据不会删除。'))return;
 const command=deviceCommand('disable',device.id);await api('/v1/admin/devices/'+encodeURIComponent(device.id)+'/disable',{command_ref:command.command_ref});
 deviceCommands.delete(command.key);notify('设备已停用，业务数据保持不变');await dashboard();
}
function openEnrollment(){
 if(!classes.length)return notify('请先建立班级，再开通平板');
 const content=$('dialog-content');content.replaceChildren(node('h2','开通新平板'));const form=node('form'),name=field(form,'平板名称');name.maxLength=80;name.placeholder='例如：前台签到平板';
 content.append(node('p','选择这台平板允许签到的班级。学生名单和课堂只来自服务器授权范围。','hint'));
 const options=node('div',undefined,'class-options'),checks=[];for(const item of classes){const label=node('label',undefined,'check'),check=node('input');check.type='checkbox';check.value=item.id;check.setAttribute('aria-label',item.name);label.append(check,node('span',item.name+(item.course_name?' · '+item.course_name:'')));options.append(label);checks.push(check);}form.append(options);
 const submit=node('button','生成一次性开通码'),commandRef=crypto.randomUUID().replaceAll('-','');submit.type='submit';form.append(submit);content.append(form);if(!$('device-dialog').open)$('device-dialog').showModal();
 form.addEventListener('submit',async event=>{event.preventDefault();const classIds=checks.filter(check=>check.checked).map(check=>check.value);if(!classIds.length)return notify('请至少选择一个授权班级');submit.disabled=true;
  try{const result=await api('/v1/admin/devices',{name:name.value.trim(),class_ids:classIds,command_ref:commandRef});if(!result.activation_code)throw Error('开通码不可重复查看，请重新创建设备');showActivation(result);}catch(error){showError(error);}finally{submit.disabled=false;}});
}
function showActivation(result){
 const content=$('dialog-content');content.replaceChildren(node('h2','请在平板上输入开通码'),node('p',result.name+' · 仅显示这一次，关闭后无法找回。','hint'),node('div',result.activation_code,'activation-code'));
 const actions=node('div',undefined,'dialog-actions');actions.append(button('复制开通码',async()=>{try{await navigator.clipboard.writeText(result.activation_code);notify('开通码已复制，请安全交给平板操作人员');}catch(error){showError(error);}}));content.append(actions,node('p','平板路径：设置 → 网页主管理连接 → 使用一次性开通码（新版）；已开通设备请选择“安全换绑”。'));
 if(!$('device-dialog').open)$('device-dialog').showModal();
}
async function logout(){try{await api('/v1/auth/logout',{});}finally{login();}}
$('close-dialog').addEventListener('click',()=>$('device-dialog').close());
login();
