'use strict';
const $ = id => document.getElementById(id);
const roles = {teacher:'教师',admin:'管理员',assessor:'绩效考核专员',principal:'校长'};
const kinds = {weekly_plan:'周计划',weekly_report:'教学周报',monthly_plan:'月计划',monthly_report:'教学月报',enrolment_list:'招生名单',renewal_list:'续费名单'};
const statuses = {submitted:'已提交',late:'补交',overdue:'逾期未提交',upcoming:'待提交',pending:'待审批',approved:'已批准',rejected:'已驳回'};
let token = '', me = null, role = '', tab = 'materials', generation = 0, timer;
let attendanceDate='',attendanceLesson='',attendanceStatus='all',attendanceSearch='';
function chinaToday(){const parts=new Intl.DateTimeFormat('en',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());const p=Object.fromEntries(parts.map(v=>[v.type,v.value]));return p.year+'-'+p.month+'-'+p.day;}
function requestRef(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,fn,cls=''){const n=el('button',text,cls);n.type='button';n.addEventListener('click',()=>Promise.resolve().then(fn).catch(fail));return n;}
function notice(text){clearTimeout(timer);$('notice').textContent=text;$('notice').style.display='block';timer=setTimeout(()=>$('notice').style.display='none',6500);}
function fail(e){notice(e.message||'操作未完成，请刷新后核对');}
function stamp(t){return t==null?'—':new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
function badge(value){return el('span',statuses[value]||value,'badge '+value);}
function field(form,title,type='text',value=''){const label=el('label',title);const n=type==='textarea'?el('textarea'):el('input');if(type!=='textarea')n.type=type;n.value=value;label.append(n);form.append(label);return n;}
function select(form,title,options){const label=el('label',title),n=el('select');for(const [v,text] of Object.entries(options)){const o=el('option',text);o.value=v;n.append(o);}label.append(n);form.append(label);return n;}
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});const data=await response.json();if(!response.ok){if(response.status===401&&token){token='';me=null;login();}throw Error(data.error?.message||'请求未完成');}return data;}
function query(path){return path+'?workspace_role='+encodeURIComponent(role);}
function dialog(title){const box=$('dialog-content');box.replaceChildren(el('h2',title));if(!$('dialog').open)$('dialog').showModal();return box;}
$('close-dialog').addEventListener('click',()=>$('dialog').close());
function mutation(form,text,path,payload,refName='command_ref',done=async()=>{ $('dialog').close();await dashboard();}){
  const submit=el('button',text);submit.type='submit';form.append(submit);let last='',ref='';
  form.addEventListener('submit',async event=>{event.preventDefault();if(submit.disabled)return;submit.disabled=true;
    try{const body=payload(), key=JSON.stringify(body);if(key!==last){last=key;ref=requestRef();}body[refName]=ref;await api(path,body);notice('已保存');await done();}catch(e){fail(e);}finally{submit.disabled=false;}
  });
}
function login(){generation++;role='';$('identity').replaceChildren();$('dialog').close();const box=el('section',undefined,'panel login');box.append(el('h1','登录工作台'),el('p','使用人员账号登录，随后选择工作身份。'));const form=el('form');const org=field(form,'机构'),name=field(form,'账号'),pass=field(form,'密码','password');org.required=name.required=pass.required=true;name.autocomplete='username';pass.autocomplete='current-password';const submit=el('button','登录');submit.type='submit';form.append(submit);form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{const result=await api('/v1/auth/login',{organization:org.value.trim(),login:name.value.trim(),password:pass.value});pass.value='';token=result.access_token;me=await api('/v1/me');choose();}catch(error){fail(error);}finally{submit.disabled=false;}});box.append(form);$('main').replaceChildren(box);}
function enterRole(value){role=value;tab=role==='teacher'?'attendance':'materials';attendanceDate=chinaToday();attendanceLesson='';attendanceStatus='all';attendanceSearch='';return dashboard();}
function choose(){generation++;$('dialog').close();const held=me.roles.filter(r=>roles[r]);if(held.length===1){enterRole(held[0]).catch(fail);return;}$('identity').replaceChildren(el('span',me.display_name));const grid=el('div',undefined,'grid');for(const r of held){const n=button(roles[r],()=>enterRole(r),'role');n.append(el('small',{teacher:'查看今日课程、历史签到，提交请假与补签申请',admin:'管理考核周期、审批申请、退回与解锁',assessor:'查阅材料、录分与管辖范围审批',principal:'查阅全校教师考核材料'}[r]));grid.append(n);}$('main').replaceChildren(el('h1','选择本次工作身份'),el('p','同一个账号可切换身份，各工作台按对应权限操作。'),grid);}
async function dashboard(){
  const run=++generation,selectedRole=role,workspaceInfo=await api(query('/v1/management/workspace'));if(run!==generation||role!==selectedRole)return;
  if(tab==='people'&&!(role==='admin'&&workspaceInfo.personnel_api_enabled))tab='materials';
  if(tab==='classes'&&!workspaceInfo.class_teachers_enabled)tab='materials';
  $('dialog').close();$('identity').replaceChildren(el('span',me.display_name),el('span',roles[role],'badge'),button('切换身份',choose,'secondary'),button('退出',async()=>{await api('/v1/auth/logout',{});token='';me=null;login();},'secondary'));
  const nav=el('nav',undefined,'toolbar');if(['admin','teacher','principal'].includes(role))nav.append(button('课程与签到',()=>{tab='attendance';return dashboard();},tab==='attendance'?'gold':'secondary'));nav.append(button('考核材料',()=>{tab='materials';return dashboard();},tab==='materials'?'gold':'secondary'));if(role!=='principal')nav.append(button('请假与补签审批',()=>{tab='requests';return dashboard();},tab==='requests'?'gold':'secondary'));if(role==='principal'&&tab==='requests')tab='materials';
  nav.append(button('周报与历史周报',()=>{tab='reports';return dashboard();},tab==='reports'?'gold':'secondary'));
  if(role!=='principal')nav.append(button('接入前补签申请',()=>{tab='legacyRequests';return dashboard();},tab==='legacyRequests'?'gold':'secondary'));
  if(role==='admin'&&workspaceInfo.personnel_api_enabled)nav.append(button('人员与身份',()=>{tab='people';return dashboard();},tab==='people'?'gold':'secondary'));
  if(workspaceInfo.class_teachers_enabled)nav.append(button('班级与任课',()=>{tab='classes';return dashboard();},tab==='classes'?'gold':'secondary'));
  const tools=el('div',undefined,'toolbar');tools.append(button('刷新',dashboard,'secondary'));if(role==='admin'&&tab==='materials')tools.append(button('安排考核周期',openPeriod),button('截止时间规则',ruleEditor,'secondary'));
  const titles={materials:'计划与考核材料',attendance:!attendanceDate||attendanceDate===chinaToday()?'今日课程':'课程与签到',reports:'周报与历史周报',legacyRequests:'接入前补签申请',requests:'请假与补签审批',people:'人员与身份',classes:'班级与任课'};
  const list=el('div',undefined,'list');list.append(el('p','正在读取资料…'));$('main').replaceChildren(el('h1',titles[tab]),el('p',tab==='attendance'?'按上课日期选择班级，再查看已到、未到和请假学生。':tab==='people'?'维护人员账号、兼任身份与审批范围，修改保留操作记录。':tab==='classes'?'按课程查看班级，办理教师任课交接。':tab==='materials'?'截止时间均为北京时间。提交后保留版本，补交保留实际提交时间。':tab==='reports'?'请假保留在应到人数中。接入前历史与接入后课次按边界分开列示。':tab==='legacyRequests'?'查阅并核对接入前保存的原申请。':'请假和补签经网页审批后生效，平板收到后显示结果。'),nav,tools,list);
  if(tab==='reports'){await weeklyReportsView(list,run);return;}
  if(tab==='legacyRequests'){await legacyRequestsView(list,run);return;}
  if(tab==='attendance'){await attendanceView(list,run);return;}
  if(tab==='people'){await import('/personnel.js');if(run!==generation||role!=='admin')return;await window.FutureSchoolPersonnel.mount({container:list,identity:me,workspaceRole:role,request:api,onSessionInvalid:()=>{token='';me=null;login();}});return;}
  if(tab==='classes'){await import('/class-teachers.js');if(run!==generation||role!==selectedRole)return;await window.FutureSchoolClassTeachers.mount({container:list,workspaceRole:role,request:api});return;}
  const path=tab==='materials'?'/v1/management/materials':'/v1/management/requests';
  async function load(before){const data=await api(query(path)+(before?'&before='+encodeURIComponent(before):''));if(run!==generation)return;if(!before)list.replaceChildren();if(!data.items.length&&!before)list.append(el('div','当前没有资料','empty'));for(const row of data.items)list.append(tab==='materials'?materialCard(row):requestCard(row));if(data.next_before){const more=button('继续加载',async()=>{more.disabled=true;try{await load(data.next_before);more.remove();}catch(e){more.disabled=false;throw e;}},'secondary');list.append(more);}}
  await load();
}
function materialCard(row){const card=el('article',undefined,'item'),head=el('div',undefined,'item-head');head.append(el('h3',(row.display_name||'教师')+' · '+kinds[row.kind]),badge(row.status));card.append(head,el('p','周期开始 '+row.starts_on+'　截止 '+stamp(row.due_at)));if(row.state==='returned')card.append(el('p','已退回，请补充后重新提交。','error'));if(row.submitted_at)card.append(el('p','最近提交 '+stamp(row.submitted_at)));card.append(button(role==='teacher'&&row.editable?'填写材料':'查看材料',()=>materialDetail(row.id),'secondary'));return card;}
function requestCard(row){const card=el('article',undefined,'item'),head=el('div',undefined,'item-head');head.append(el('h3',row.student_name+' · '+(row.kind==='leave'?'请假':'补签')),badge(row.status));card.append(head,el('p',row.class_name+'　申请时间 '+stamp(row.created_at)),el('div',row.reason,'content'));if(row.decision_reason)card.append(el('p','审批意见：'+row.decision_reason));if(row.status==='approved')card.append(el('p',({unassigned:'已生效 · 尚无可接收的平板',pending:'已生效 · 等待平板接收',conflict:'已生效 · 平板记录有差异，待核对',applied:'已生效 · 平板已保存'})[row.tablet_delivery]||'已生效 · 接收状态待核对'));if(row.status==='pending'&&['admin','assessor'].includes(role)){const bar=el('div',undefined,'toolbar');for(const approve of [true,false])bar.append(button(approve?'批准':'驳回',()=>{const box=dialog(approve?'批准申请':'驳回申请'),f=el('form'),reason=field(f,'审批意见');reason.required=true;reason.maxLength=200;box.append(f);mutation(f,'确认'+(approve?'批准':'驳回'),'/v1/management/requests/'+row.id+'/decision',()=>({workspace_role:role,approve,reason:reason.value}),'decision_ref');},approve?'':'secondary'));card.append(bar);}return card;}
async function materialDetail(id){const requestGeneration=generation;const data=await api(query('/v1/management/materials/'+id));if(requestGeneration!==generation)return;const box=dialog(kinds[data.kind]+' · '+data.starts_on);box.append(badge(data.status),el('p','截止 '+stamp(data.due_at)+(data.unlocked_until?'　解锁至 '+stamp(data.unlocked_until):'')));if(data.state==='returned')box.append(el('p','退回原因：'+(data.return_reason||'请按审批意见补充')+'；超过截止需管理员解锁后重交。','error'));
  if(role==='teacher'&&data.editable){const form=el('form'),content=field(form,'材料内容（最多2000字）','textarea',data.content);content.maxLength=2000;box.append(form);mutation(form,'保存草稿','/v1/management/materials/'+id,()=>({workspace_role:role,action:'save',expected_revision:data.revision,content:content.value}),'command_ref',async()=>{await dashboard();await materialDetail(id);});if(data.content.trim()){const submit=el('form');box.append(submit);mutation(submit,'提交已保存的材料','/v1/management/materials/'+id,()=>({workspace_role:role,action:'submit',expected_revision:data.revision}),'command_ref',async()=>{await dashboard();await materialDetail(id);});}box.append(el('p','修改后请先保存；提交后内容锁定，需要退回才能修改。'));}else box.append(el('div',data.content||'尚未填写内容','content'));
  const bar=el('div',undefined,'toolbar');if(['admin','assessor'].includes(role)&&data.state==='submitted')bar.append(button('退回重交',()=>materialAction(data,'return'),'secondary'));if(role==='admin'&&data.state!=='submitted'&&data.seconds_remaining===0)bar.append(button('限时解锁',()=>materialAction(data,'unlock')));if(role==='assessor'&&data.state==='submitted')bar.append(button('录入考核分数',()=>scoreEditor(data)));box.append(bar);
  if(data.scores.length){box.append(el('h3','评分记录'));for(const score of data.scores)box.append(el('p',score.value+' 分 · '+(score.current?'当前评分':'历史评分')+' · '+stamp(score.recorded_at)+' · '+score.basis));}
  if(data.versions.length){box.append(el('h3','提交历史'));for(const version of data.versions){const details=el('details');details.append(el('summary','版本 '+version.revision+' · '+stamp(version.submitted_at)+(version.late?' · 补交':'')),el('div',version.content,'content'));box.append(details);}}
}
function materialAction(data,action){const box=dialog(action==='unlock'?'限时解锁材料':'退回材料'),form=el('form'),reason=field(form,'原因');reason.required=true;reason.maxLength=500;let until;if(action==='unlock'){until=field(form,'允许编辑至（北京时间）','datetime-local');until.required=true;box.append(el('p','最多解锁31天，补交仍按原截止时间标记。'));}box.append(form);mutation(form,'确认','/v1/management/materials/'+data.id,()=>({workspace_role:role,action,expected_revision:data.revision,reason:reason.value,...(until?{unlocked_until:Math.floor(Date.parse(until.value+':00+08:00')/1000)}:{})}));}
function scoreEditor(data){const box=dialog('录入考核分数'),form=el('form'),value=field(form,'分数（最多两位小数）','number'),basis=field(form,'评分依据');value.step='.01';value.required=basis.required=true;basis.maxLength=500;box.append(el('p','按已确定的评分标准人工核算后录入。此处不预设自动评分公式。'),form);mutation(form,'保存分数','/v1/management/materials/'+data.id+'/scores',()=>({workspace_role:role,expected_revision:data.revision,value:value.value,basis:basis.value}));}
async function openPeriod(){const teachers=await api(query('/v1/management/assessment-teachers')),box=dialog('安排考核周期'),form=el('form'),kind=select(form,'材料类型',kinds),start=field(form,'周期开始日期（周一／每月1日）','date'),due=field(form,'名单项目截止时间（北京时间）','datetime-local');start.required=true;due.disabled=true;kind.addEventListener('change',()=>{due.disabled=!['enrolment_list','renewal_list'].includes(kind.value);due.required=!due.disabled;});form.append(el('h3','本期提交教师'));const checks=[];for(const t of teachers.items){const label=el('label',undefined,'check'),check=el('input');check.type='checkbox';check.value=t.id;check.checked=true;checks.push(check);label.append(check,document.createTextNode(t.display_name));form.append(label);}box.append(form);mutation(form,'创建周期','/v1/management/assessment-periods',()=>({workspace_role:role,kind:kind.value,starts_on:start.value,owner_ids:checks.filter(n=>n.checked).map(n=>n.value),...(due.disabled?{}:{due_at:Math.floor(Date.parse(due.value+':00+08:00')/1000)})}));}
async function ruleEditor(){const rules=await api(query('/v1/management/assessment-rules')),box=dialog('考核截止时间规则');box.append(el('p','修改只影响之后创建的周期，已有周期的截止时间保持不变。'));for(const item of rules.items){const form=el('form',undefined,'panel');form.append(el('h3',kinds[item.kind]));const weekly=item.rule.frequency==='weekly',options=weekly?{'0':'周一','1':'周二','2':'周三','3':'周四','4':'周五','5':'周六','6':'周日'}:Object.fromEntries([...Array.from({length:31},(_,i)=>[String(i+1),'每月'+(i+1)+'日']),['last','每月最后一天']]);const day=select(form,'截止日期',options);day.value=String(item.rule.day);const time=field(form,'时间（北京时间）','time',String(item.rule.hour).padStart(2,'0')+':'+String(item.rule.minute).padStart(2,'0'));time.required=true;box.append(form);mutation(form,'保存规则','/v1/management/assessment-rules',()=>({workspace_role:role,kind:item.kind,expected_revision:item.revision,rule:{frequency:item.rule.frequency,day:day.value==='last'?'last':Number(day.value),hour:Number(time.value.split(':')[0]),minute:Number(time.value.split(':')[1])}}),'command_ref',ruleEditor);}}
async function attendanceView(list,run){
  const form=el('form',undefined,'panel classroom-date'),date=field(form,'上课日期（北京时间）','date');
  attendanceDate||=chinaToday();date.value=attendanceDate;date.required=true;
  const search=el('button','查看课程');search.type='submit';form.append(search,button('回到今天',()=>{attendanceDate=chinaToday();attendanceLesson='';attendanceStatus='all';attendanceSearch='';return dashboard();},'secondary'));
  const summary=el('div'),results=el('div',undefined,'list');list.replaceChildren(form,summary,results);
  let requestSequence=0;
  async function load(before){const sequence=++requestSequence,selectedDate=attendanceDate,selectedLesson=attendanceLesson;
    const data=await api(query('/v1/management/attendance')+'&on='+selectedDate+(selectedLesson?'&lesson_id='+encodeURIComponent(selectedLesson):'')+'&status='+attendanceStatus+'&q='+encodeURIComponent(attendanceSearch)+(before?'&before='+before:''));
    if(run!==generation||sequence!==requestSequence||selectedDate!==attendanceDate||selectedLesson!==attendanceLesson)return;
    if(!before){summary.replaceChildren();results.replaceChildren();
      if(data.read_only)summary.append(el('p','接入前签到明细 · 只读保留原记录。课时为接入前快照，不代表该次课后的余额。','hint'));
      if(!selectedLesson){
        if(!data.lessons.length){results.append(el('div',selectedDate===chinaToday()?'今天没有课程，可选择其他上课日期查看签到。':'这一天没有可查看的课堂名单。','empty'));return;}
        const grid=el('div',undefined,'grid course-grid');
        for(const lesson of data.lessons){const card=el('article',undefined,'item course-card');card.append(el('h2',lesson.class_name),el('p',lesson.starts_at==null?'原上课时间未记录':stamp(lesson.starts_at)));
          if(lesson.teacher_name)card.append(el('p','任课教师 '+lesson.teacher_name));
          const counts=el('div',undefined,'course-counts');for(const [key,title]of [['expected','应到'],['arrived','已到'],['missing','未到'],['leave_count','请假']]){const n=el('div');n.append(el('small',title),el('strong',lesson[key]==null?'—':String(lesson[key])));counts.append(n);}card.append(counts,button('查看 '+lesson.class_name+' 名单',()=>{attendanceLesson=lesson.lesson_id;attendanceStatus='all';attendanceSearch='';return dashboard();}));grid.append(card);
        }results.append(grid);return;
      }
      const lesson=data.lessons.find(v=>v.lesson_id===selectedLesson);summary.append(button('返回课程列表',()=>{attendanceLesson='';attendanceStatus='all';attendanceSearch='';return dashboard();},'secondary'));
      if(!lesson){results.append(el('div','当前身份已无权查看该课堂，请返回课程列表重新选择。','empty'));return;}
      summary.append(el('h2',lesson.class_name),el('p','应到 '+(lesson.expected??'—')+' · 已到 '+(lesson.arrived??'—')+' · 未到 '+(lesson.missing??'—')+' · 请假 '+(lesson.leave_count??'—')));
      const filters=el('div',undefined,'toolbar');for(const [value,title]of [['all','全部'],['arrived','已签到'],['missing','未签到'],['leave','已请假']])filters.append(button(title,()=>{attendanceStatus=value;return dashboard();},attendanceStatus===value?'gold':'secondary'));summary.append(filters);
      const find=el('form',undefined,'student-search'),input=field(find,'搜索姓名或学号','search',attendanceSearch),submit=el('button','搜索学生');input.maxLength=80;submit.type='submit';find.append(submit);find.addEventListener('submit',event=>{event.preventDefault();attendanceSearch=input.value.trim();dashboard().catch(fail);});summary.append(find,el('p','读取时间 '+stamp(data.server_time)+' · 点击页面上方“刷新”更新状态','muted'));
      if(!data.items.length)results.append(el('div','没有符合条件的学生','empty'));
    }
    for(const row of data.items){const card=el('article',undefined,'item'),head=el('div',undefined,'item-head');head.append(el('h3',row.student_name),el('span',{missing:'未签到',leave:'已请假',arrived:'已签到',unknown:'原状态待核对'}[row.status]||'原状态待核对','badge '+row.status));card.append(head,el('p',row.student_no||'未记录学号'));
      card.append(el('p',row.balance_units==null?'未记录课时':(row.read_only?'接入前快照课时 ':'当前剩余课时 ')+(row.balance_units/100)+(row.charge_units==null?'':' · 本节课 '+(row.charge_units/100)+' 课时')));
      if(row.read_only)card.append(el('p','原签到时间：'+(row.attendance_time||'未记录')+' · 方式：'+(row.attendance_method||'未记录')+(row.makeup?' · 补签':'')));
      if(row.has_pending_request)card.append(el('p','已有待审批申请，请到审批列表查看进度。'));
      else if(!row.read_only&&['teacher','admin'].includes(role)&&row.expected&&row.status==='missing'){const bar=el('div',undefined,'toolbar');for(const kind of ['leave','makeup']){if(kind==='makeup'&&data.server_time<row.starts_at)continue;bar.append(button(kind==='leave'?'申请请假':'申请补签',()=>{const box=dialog(row.student_name+' · '+(kind==='leave'?'请假申请':'补签申请')),f=el('form'),reason=field(f,'申请原因');reason.required=true;reason.maxLength=200;box.append(el('p','经网页审批通过后生效。请假保留在应到人数中，不扣课时。'),f);mutation(f,'提交申请','/v1/management/requests',()=>({workspace_role:role,lesson_id:row.lesson_id,student_id:row.student_id,kind,reason:reason.value,expected_revision:row.revision}),'request_ref');},'secondary'));}card.append(bar);}results.append(card);
    }if(data.next_before){const more=button('继续加载',async()=>{more.disabled=true;try{await load(data.next_before);more.remove();}catch(e){more.disabled=false;throw e;}},'secondary');results.append(more);}
  }
  form.addEventListener('submit',e=>{e.preventDefault();attendanceDate=date.value;attendanceLesson='';attendanceStatus='all';attendanceSearch='';dashboard().catch(fail);});await load();
}
async function legacyRequestsView(list,run){
 list.replaceChildren(el('h2','接入前补签申请'),el('p','这些是旧系统保存的申请，需结合原签到历史核对。此列表只供查阅，不会自动补签或扣课时。','hint'));
 async function load(before){const data=await api(query('/v1/management/legacy-requests')+(before?'&before='+before:''));if(run!==generation)return;
  if(!data.items.length&&!before)list.append(el('div','没有当前身份可查看的原申请','empty'));
  for(const row of data.items){const card=el('article',undefined,'item');card.append(el('h3',row.student_name),el('p',row.class_name+' · '+row.date),el('p',row.reason),badge(row.status),el('p',stamp(row.created_at)+(row.requires_review?' · 待核对原始记录':'')));list.append(card);}
  if(data.next_before){const more=button('继续加载',async()=>{more.disabled=true;try{await load(data.next_before);more.remove();}catch(e){more.disabled=false;throw e;}},'secondary');list.append(more);}
 }await load();
}
async function weeklyReportsView(list,run){
 const form=el('form',undefined,'panel'),week=select(form,'选择周次',[]),submit=el('button','查看周报');submit.type='submit';form.append(submit);
 const results=el('div',undefined,'list');list.replaceChildren(form,results);
 async function load(start){
  const data=await api(query('/v1/management/weekly-reports')+(start?'&start='+encodeURIComponent(start):''));if(run!==generation)return;
  week.replaceChildren();for(const value of data.weeks){const option=el('option',value+' 起');option.value=value;week.append(option);}week.value=data.start;
  results.replaceChildren(el('p',data.start+' 至 '+data.end+(role==='teacher'?' · 仅本人任课班级':'')));
  if(data.transition_week)results.append(el('p','本周跨越系统接入日期。两部分分别统计，不相加为一个到课率。','hint'));
  if(!data.sections.length)results.append(el('div','这周尚无可显示的资料','empty'));
  for(const section of data.sections){
   const report=section.report,card=el('article',undefined,'item');card.append(el('h2',section.label),el('p','应到 '+Number(report.expected).toLocaleString('zh-CN',{maximumFractionDigits:2})+' 人次 · 已到 '+report.arrived+' 人次 · 请假 '+report.leave_count+' 人次 · 到课率 '+report.rate));
   if(section.detail_scope_only)card.append(el('p','只汇总接入前有课堂明细的部分；旧系统估算总数可在完整历史周查看。','hint'));
   for(const [key,title]of [['classes','班级'],['teachers','教师']]){
    const table=el('table'),head=el('tr');for(const titleText of [title,'应到人次','已到人次','请假人次','到课率'])head.append(el('th',titleText));table.append(head);
    for(const r of report[key]||[]){const tr=el('tr');for(const v of [r.name+(r.historical_class_name?'（历史班名）':''),Number(r.expected).toLocaleString('zh-CN',{maximumFractionDigits:2}),r.arrived,r.leave_count,r.rate])tr.append(el('td',String(v)));table.append(tr);}const wrap=el('div',undefined,'table-scroll');wrap.append(table);card.append(wrap);
   }results.append(card);
  }
 }
 form.addEventListener('submit',async e=>{e.preventDefault();submit.disabled=true;try{await load(week.value);}catch(error){fail(error);}finally{submit.disabled=false;}});await load();
}
api('/health/ready').then(data=>{const banner=document.querySelector('.preview');if(!banner)return;if(data.runtime_admitted)banner.textContent=data.tablet_delivery_enabled?'网页主管理 · 已连接平板':'网页主管理 · 等待平板确认连接';}).catch(()=>{});
login();
