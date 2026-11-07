(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const h = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles = {admin:'管理员',teacher:'老师',parent:'家长（测试）'};
  const noticeNames = {attendance:'签到成功',low_hours_parent:'孩子课时不足',lesson_content:'课后学习内容',absence_parent:'孩子未到提醒',absence_teacher:'开课5分钟未到提醒',low_hours_teacher:'本班课时不足',weekly_teacher:'我的教学周报',weekly_admin:'全校教学周报',monthly_admin:'新生与续费月报'};
  const statusNames = {pending_simulation:'待演练',simulated:'已演练',cancelled:'已取消',expired:'已过期'};
  const state = {token:null,me:null,epoch:0,pageEpoch:0,view:'lessons',day:'',scope:'authorized',lesson:null,classes:[],people:[],students:[],lessonRows:[],cursor:null,dirty:false,busy:false,busyId:0,allNotices:false};
  let returnFocus = null;
  const has = role => !!state.me?.roles.includes(role);
  const stamp = (seconds, options={}) => new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,...options}).format(new Date(seconds*1000));
  const dayAt = seconds => { const p = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(seconds*1000));return ['year','month','day'].map(k=>p.find(x=>x.type===k).value).join('-'); };
  const monday = value => {const d=new Date(value+'T00:00:00Z');d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));return d.toISOString().slice(0,10);};
  const hours = units => String((units/100).toFixed(2)).replace(/\.?0+$/,'') || '0';
  const percent = value => value === null ? '—' : h(value)+'%';
  const badge = (text, color='gray') => `<span class="badge ${color}">${h(text)}</span>`;
  const empty = (title, body) => `<div class="empty"><h3>${h(title)}</h3><div>${h(body)}</div></div>`;
  const heading = (title, subtitle, actions='') => `<div class="page-heading"><div><p class="eyebrow">未来学校 / 教务管理</p><h1>${h(title)}</h1><p>${h(subtitle)}</p></div><div class="actions">${actions}</div></div>`;
  const button = (text, action, id='', cls='') => `<button type="button" data-action="${action}" data-id="${h(id)}" class="${cls}">${h(text)}</button>`;
  const metrics = data => `<div class="metrics">${data.map(([label,value,note],i)=>`<div class="metric ${i===0?'emphasis':''}"><small>${h(label)}</small><strong>${h(value)}</strong><em>${h(note||'')}</em></div>`).join('')}</div>`;
  const checked = value => value ? ' checked' : '';
  const option = (id,label,selected) => `<option value="${h(id)}"${id===selected?' selected':''}>${h(label)}</option>`;
  const classOptions = selected => state.classes.map(c=>option(c.id,(c.course_name ? c.course_name+' / ' : '')+c.name,selected)).join('');
  function message(text,error=false){const node=$('#message');node.textContent=text;node.className=error?'error':'';node.hidden=false;}
  function clearMessage(){$('#message').hidden=true;$('#message').textContent='';}

  async function api(path, method='GET', data, overrides={}) {
    const epoch=state.epoch, token=state.token, controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(path,{method,credentials:'omit',cache:'no-store',redirect:'error',signal:controller.signal,
        headers:{...(data===undefined?{}:{'Content-Type':'application/json'}),...(token?{Authorization:'Bearer '+token}:{})},
        ...(data===undefined?{}:{body:JSON.stringify(data)})});
      if(epoch!==state.epoch)throw new Error('页面已切换，请重新操作');
      const result=await response.json();
      if(!response.ok){
        if(response.status===401&&token){clearSession();message('登录已失效，请重新登录。',true);}
        const error=new Error(result.error?.message||'操作未完成，请刷新后核对');error.status=response.status;throw error;
      }
      return result;
    } catch(error) {
      if(error.name==='AbortError'||error instanceof TypeError)throw new Error(method==='GET'?'暂时无法连接，请稍后重试。':'连接中断，保存结果尚未确认。请先刷新核对，避免重复登记。');
      if(overrides.allow404&&error.status===404)return null;
      throw error;
    } finally {clearTimeout(timer);}
  }
  async function collect(path) {
    const result=[];let after='';
    for(let i=0;i<20;i++){
      const page=await api(path+(path.includes('?')?'&':'?')+'limit=100&after='+after);result.push(...page.items);
      if(!page.next_cursor)return result;after=page.next_cursor;
    }
    throw new Error('当前选择范围较大，超过联调版的加载上限；尚未使用不完整名单保存。');
  }
  function closeDialog(){const d=$('#editorDialog');if(d.open)d.close();$('#dialogBody').replaceChildren();if(returnFocus?.isConnected)returnFocus.focus();returnFocus=null;}
  function dialog(title, body){returnFocus=document.activeElement;$('#dialogBody').innerHTML=`<h2 id="dialogTitle">${h(title)}</h2>${body}`;$('#editorDialog').showModal();}
  function clearSession(){state.epoch++;state.pageEpoch++;state.busyId++;state.token=null;state.me=null;state.dirty=false;state.busy=false;state.lesson=null;state.classes=[];state.people=[];state.students=[];state.archives=[];state.devices=[];state.lessonRows=[];for(const k of ['restoreArchive','editPerson','editDevice','assignment','leaveStudent','cancelLeave','prefs','schedule','noticePath'])delete state[k];closeDialog();$('#main').replaceChildren();$('#accountName').textContent='';$('#accountRoles').replaceChildren();$('#navigation').replaceChildren();$('#appScreen').hidden=true;$('#loginScreen').hidden=false;$('#loginForm').reset();$('#loginForm button').disabled=false;}
  function navigation(){
    const items=[['lessons','课堂','◫'],['classes','班级与任课','▤'],['students','学生名册','♧'],...(has('admin')?[['archives','长期不来归档','▱'],['people','人员与身份','♙'],['devices','平板设备','▣']]:[]),['weekly','教学周报','▥'],...(has('admin')?[['monthly','新生与续费','◷']]:[]),['notices','通知记录','◌'],['settings','通知设置','⚙']];
    $('#navigation').innerHTML=items.map(([view,label,symbol])=>`<button type="button" class="nav-button ${state.view===view||state.view==='lesson'&&view==='lessons'?'active':''}" data-action="navigate" data-id="${view}"${state.view===view?' aria-current="page"':''}><span class="nav-symbol">${symbol}</span>${label}</button>`).join('');
    $('#breadcrumb').textContent=state.view==='lesson'?'课堂 / 课堂详情':items.find(i=>i[0]===state.view)?.[1]||'工作台';
  }
  async function navigate(view, force=false){
    if(state.dirty&&!force&&!window.confirm('课堂内容尚未保存，确定离开吗？'))return;
    state.dirty=false;closeDialog();clearMessage();state.view=view;navigation();
    const generation=++state.pageEpoch;$('#main').innerHTML='<div class="loading" role="status">正在加载…</div>';
    try {
      const html=await views[view]();
      if(generation!==state.pageEpoch||!state.me)return;
      $('#main').innerHTML=html;$('#main').focus({preventScroll:true});
      if(state.reviewOnly){
        $('#main').insertAdjacentHTML('afterbegin','<p class="help">当前是名册核对副本：可查阅、筛选和核对；业务修改暂未开放，平板继续正常使用。</p>');
        $('#main').querySelectorAll('[data-action]').forEach(node=>{if(!['navigate','reload','more-lessons','open-lesson','more-notices'].includes(node.dataset.action))node.disabled=true;});
        $('#main').querySelectorAll('form').forEach(form=>{if(!['lessonFilter','weeklyFilter','monthlyFilter'].includes(form.id))form.querySelectorAll('input,select,textarea,button').forEach(node=>node.disabled=true);});
      }
    } catch(error){if(generation===state.pageEpoch&&state.me){$('#main').innerHTML=empty('暂时未能加载',error.message)+button('重新加载','reload');message(error.message,true);}}
  }
  async function perform(callback, form){
    if(state.busy)return;
    state.busy=true;const operation=++state.busyId, submit=form?.querySelector('button[type="submit"]');if(submit)submit.disabled=true;
    try{await callback();}catch(error){if(operation===state.busyId)message(error.message,true);}finally{if(operation===state.busyId){state.busy=false;if(submit?.isConnected)submit.disabled=false;}}
  }
  const views={};

  views.archives=async()=>{
    const [rows,summary,classes]=await Promise.all([collect('/v1/admin/archives'),api('/v1/admin/roster-summary'),collect('/v1/classes')]);
    state.archives=rows;state.classes=classes;
    return heading('长期不来归档','独立保留档案和余额，不计入在读、应到和课时不足提醒。')+
      metrics([['归档人数',summary.archived_students,'包含原班级未知的档案'],['归档剩余课时',hours(summary.archived_remaining_units),'完整保留，不清零'],['在读人数',summary.active_students,'含保留的服务器测试资料'],['班级数量',summary.classes,'含保留的服务器测试班级']])+
      `<div class="help">${summary.review_only?'当前是名册核对副本，只读查看；暂不在这里恢复上课。':'恢复上课必须明确选择班级；保持原余额，不登记为新生或续费。'}资料日期：${h(summary.imports.at(-1)?.source_date||'未导入')}。未进行平板历史签到、流水和请假的回放，后续变化仍以平板为准。</div>`+
      `<section class="panel"><label>搜索姓名、编号或原班级<input id="archiveSearch" type="search" placeholder="输入关键词"></label><div class="table-wrap" id="archiveRows">${archiveRows('',summary.review_only)}</div></section>`;
  };
  function archiveRows(query='',reviewOnly=state.reviewOnly){
    const rows=(state.archives||[]).filter(s=>[s.name,s.external_ref,s.previous_class_name].some(v=>v.includes(query.trim())));
    return `<table><thead><tr><th>学生</th><th>原班级记录</th><th>剩余课时</th><th>状态</th>${reviewOnly?'':'<th>操作</th>'}</tr></thead><tbody>${rows.map(s=>`<tr><td>${h(s.name)}<small class="muted small">${h(s.external_ref)}</small></td><td>${h(s.previous_class_name||'未记录，不推测班级')}</td><td>${h(hours(s.remaining_units))} 课时</td><td>${badge('长期不来','amber')}</td>${reviewOnly?'':`<td>${button('恢复上课','restore-archive',s.id)}</td>`}</tr>`).join('')||`<tr><td colspan="${reviewOnly?4:5}">没有符合条件的归档。</td></tr>`}</tbody></table><p class="small muted">当前显示 ${rows.length} 人 / 归档 ${state.archives.length} 人。原班级仅供核对，不代表当前任课关系。</p>`;
  }

  views.lessons=async()=>{
    const [page,classes]=await Promise.all([api('/v1/lessons?day='+state.day+'&scope='+state.scope),collect('/v1/classes')]);
    state.classes=classes;state.lessonRows=page.items;state.cursor=page.next_cursor;
    return heading('课堂工作台','先选上课日期，再处理当次名单和课堂内容。',has('admin')?button('安排一次课','new-lesson','','primary'):'')+
      `<form id="lessonFilter" class="toolbar"><label>上课日期<input type="date" name="day" value="${h(state.day)}" required></label>${has('teacher')&&has('admin')?`<label>查看范围<select name="scope">${option('teaching','我的任课课堂',state.scope)}${option('authorized','全校课堂',state.scope)}</select></label>`:''}<button type="submit">查看课堂</button></form><div id="lessonCards">${lessonCards()}</div><button type="button" data-action="more-lessons" class="load-more"${state.cursor?'':' hidden'}>加载更多课堂</button><div class="help">这里只显示已安排的实际课次。隔周上课只安排实际日期，中间休课不计应到；平板历史课表尚未同步。</div>`;
  };
  function lessonCards(){return state.lessonRows.length?`<div class="lesson-grid">${state.lessonRows.map(row=>`<article class="lesson-card"><div class="actions">${badge(row.course_name||'未分课程','green')}${badge(row.leave_count+'人请假')}</div><h2>${h(row.class_name)}</h2><div class="meta">${h(stamp(row.starts_at))} – ${h(stamp(row.ends_at,{hour:'2-digit',minute:'2-digit',month:undefined,day:undefined}))}<br>任课老师：${h(row.teacher_name)}</div><div>已到 <strong>${row.arrived}</strong> / 应到 <strong>${row.expected}</strong> 人</div><div class="lesson-card-footer"><span class="muted small">请假仍计入应到</span>${button('进入课堂','open-lesson',row.id,'primary')}</div></article>`).join('')}</div>`:empty('这一天还没有课堂','可切换日期查看；管理员可以安排实际的一次课。');}
  views.lesson=async()=>{
    const data=await api('/v1/lessons/'+state.lesson.id+'/workspace');state.lesson=data;
    const leaveMap=new Map(data.leaves.map(l=>[l.student_id,l]));
    const rows=data.members.map(s=>{const leave=leaveMap.get(s.id),pending=!s.arrived&&!leave;
      return `<tr><td>${pending?`<input type="checkbox" name="missing" value="${s.id}" aria-label="选择${h(s.name)}"${data.absence_window_open?'':' disabled'}>`:''}</td><td><strong>${h(s.name)}</strong><small class="muted small">${h(s.external_ref)}</small></td><td>${s.arrived?badge('已签到','green'):leave?badge('已请假','amber'):badge('未签到')}</td><td>${h(hours(s.remaining_units))} 课时</td><td>${leave?`<div class="small">${h(leave.reason)}<span class="muted small">${h(leave.recorded_by)} · ${h(stamp(leave.created_at))}</span></div>`:'—'}</td><td><div class="row-actions">${pending?button('办理请假','leave',s.id):leave?button('撤销请假','cancel-leave',leave.id):'—'}</div></td></tr>`;
    }).join('');
    const content=data.content, locked=content?.queued_at!==null&&content?.queued_at!==undefined;
    return heading(data.class_name,`${stamp(data.starts_at)} · 任课老师 ${data.teacher_name}`,button('返回课堂','navigate','lessons'))+
      metrics([['实际到课率',data.rate_percent===null?'—':data.rate_percent+'%','实到 ÷ 应到（含请假）'],['应到人数',data.expected,'本次已确认的名单'],['实际签到',data.arrived,'请假不算实际签到'],['已请假',data.leave_count,'不扣课时，不催签到']])+
      `<section class="panel"><div class="panel-heading"><h2>本次课堂名单</h2><span class="small muted">未到 ${data.unexcused.length} 人 · 已请假 ${data.leave_count} 人</span></div><form id="absenceForm"><div class="table-wrap"><table><thead><tr><th>选择</th><th>学生</th><th>状态</th><th>剩余课时</th><th>请假记录</th><th>操作</th></tr></thead><tbody>${rows||'<tr><td colspan="6">本次确认名单为空。</td></tr>'}</tbody></table></div><div class="help">请假保留在应到人数中，不算实际签到，也不扣课时。需要实际到课时，先明确撤销请假。</div><div class="actions"><label class="inline"><input type="checkbox" name="confirmed"${data.absence_window_open?'':' disabled'}>我已核实所选学生仍未到课</label><button type="submit"${data.absence_window_open?'':' disabled'}>为所选家长生成提醒演练</button></div><p class="small muted">${data.absence_window_open?'仅选择尚未签到且未请假的学生；本地演练不会发送微信。':'开课5分钟后、下课24小时内才可确认未到提醒。'}</p></form></section>`+
      `<section class="panel"><div class="panel-heading"><h2>本节课学习内容</h2>${locked?badge('已生成演练记录','green'):badge('草稿')}</div><form id="contentForm" class="stack"><label>课堂内容<textarea name="body" maxlength="4000" required${locked?' readonly':''} placeholder="可以提前写好本节课讲解的内容、练习与课后建议。">${h(content?.body||'')}</textarea></label><div class="form-grid"><label>通知对象<select name="audience"${locked?' disabled':''}>${option('arrived','本节实际签到学生的家长',content?.audience||'arrived')}${option('roster','本节全部应到学生的家长（含请假）',content?.audience||'arrived')}</select></label><label class="inline"><input type="checkbox" name="publish_after_class"${checked(content?.publish_after_class)}${locked?' disabled':''}>下课后生成内容通知演练</label></div><p class="small muted">${locked?'本次内容已生成演练记录，保留原文以便核对；本版暂未提供更正重发。':'默认只保存草稿。勾选后还需管理员开启并手动运行到期演练；不会自动发微信。'}</p>${locked?'':'<div class="actions"><button type="submit" class="primary">保存课堂内容</button><span class="muted small" id="draftState">修改后请保存</span></div>'}</form></section>`;
  };
  views.people=async()=>{
    state.people=await collect('/v1/admin/people');
    return heading('人员与身份','老师与管理员由你指定；同一个人可同时拥有多种身份。',button('新增人员','new-person','','primary'))+`<div class="panel"><div class="table-wrap"><table><thead><tr><th>人员</th><th>登录账号</th><th>身份</th><th>状态</th><th>操作</th></tr></thead><tbody>${state.people.map(p=>`<tr><td><div class="person-title"><span class="avatar">${h(p.display_name.slice(0,1))}</span><strong>${h(p.display_name)}</strong></div></td><td>${h(p.login)}</td><td>${p.roles.map(r=>badge(roles[r],r==='admin'?'green':'gray')).join(' ')||'未分配身份'}</td><td>${badge(p.active?'启用中':'已停用',p.active?'green':'gray')}</td><td>${button('调整身份','edit-person',p.id)}</td></tr>`).join('')}</tbody></table></div></div><div class="help">修改身份或停用账号后，原登录会失效，通知接收选择需重新确认。系统会保护最后一位在用管理员。</div>`;
  };
  views.classes=async()=>{
    state.classes=await collect('/v1/classes');
    return heading('班级与任课','按课程查看班级，由管理员分配任课老师。',has('admin')?button('新增班级','new-class','','primary'):'')+`<div class="toolbar"><label class="search">搜索课程或班级<input id="classSearch" type="search" placeholder="例如：信息学奥赛、周六下午"></label></div><div id="classGroups">${classGroups('')}</div>`;
  };
  function classGroups(query){
    const groups=new Map();state.classes.filter(c=>(c.course_name+' '+c.name).includes(query.trim())).forEach(c=>{const name=c.course_name||'未分课程';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(c);});
    return [...groups].map(([name,classes])=>`<section class="course-group"><h2>${h(name)} <span class="muted small">${classes.length} 个班级</span></h2>${classes.map(c=>`<div class="class-row"><div><strong>${h(c.name)}</strong><p>${has('admin')?'为这个班级指定任课老师':'已授权给当前账号的班级'}</p></div>${has('admin')?button('任课授权','assign-teachers',c.id):badge('可管理','green')}</div>`).join('')}</section>`).join('')||empty('没有找到班级','换个关键词试试，或由管理员新增班级。');
  }
  views.students=async()=>{
    [state.students,state.classes]=await Promise.all([collect('/v1/students'),collect('/v1/classes')]);
    return heading('学生名册','仅展示当前账号有权查看的学生。重名学生请结合编号与班级核对。',has('admin')?button('新增联调学生','new-student','','primary'):'')+`<div class="panel"><div class="table-wrap"><table><thead><tr><th>学生</th><th>班级</th><th>所属课程</th><th>剩余课时</th><th>状态</th></tr></thead><tbody>${state.students.map(s=>`<tr><td>${h(s.name)}<small class="muted small">${h(s.external_ref)}</small></td><td>${h(s.class_name)}</td><td>${h(s.course_name||'未分课程')}</td><td>${h(hours(s.remaining_units))} 课时</td><td>${s.active?'在用':'停用'}</td></tr>`).join('')||'<tr><td colspan="5">尚未录入学生；平板中的资料还没有同步。</td></tr>'}</tbody></table></div></div><div class="help">这里的余额供核对查看，不会从服务器再次扣课时。尚未启用平板资料同步；新增联调资料不等于新生或续费记录。</div>`;
  };
  views.weekly=async()=>{
    const week=state.week||monday(state.day);const own=state.reportScope?state.reportScope==='teaching':!has('admin');
    const report=await api((own?'/v1/me':'/v1')+'/reports/weekly?week_start='+week);
    const classNames=new Map(report.classes.map(c=>[c.id,c.name]));
    return heading(own?'我的教学周报':'全校教学周报','按已结束的确认课次统计；请假计应到，不算实到。')+`<form id="weeklyFilter" class="toolbar"><label>选择本周任意日期<input type="date" name="week" value="${h(week)}" required></label>${has('teacher')&&has('admin')?`<label>报告范围<select name="scope">${option('all','全校管理报告',own?'teaching':'all')}${option('teaching','我的任课报告',own?'teaching':'all')}</select></label>`:''}<button type="submit">查看周报</button></form>`+
      metrics([['实际到课率',report.rate_percent===null?'—':report.rate_percent+'%','按人次加权，非百分比平均'],['应到人次',report.expected,'包含请假'],['实到人次',report.arrived,'实际签到'],['请假人次',report.leave_count,'单列记录，不扣课时']])+
      `<div class="split"><section class="panel"><h2>各班到课率</h2>${reportTable(report.classes)}</section><section class="panel"><h2>各老师到课率</h2>${reportTable(report.teachers)}</section></div><section class="panel"><h2>逐次课堂与未到学生</h2>${report.sessions.map(s=>`<div class="notice-card"><h3>${h(classNames.get(s.class_id)||'课堂')} <span class="small muted">${h(stamp(s.starts_at))}</span></h3><p>实到 ${s.arrived} / 应到 ${s.expected} · 到课率 ${percent(s.rate_percent)} · 请假 ${s.leave_count}</p><p><span class="muted">未到且未请假：</span>${h(s.unexcused.map(x=>x.name).join('、')||'无')}</p><p><span class="muted">已请假：</span>${h(s.leave_students.map(x=>x.name).join('、')||'无')}</p></div>`).join('')||empty('本周暂无已结束课次','尚未结束或尚未安排的课次不参与当前统计。')}</section><section class="panel"><h2>当前课时不足学生</h2><div class="table-wrap"><table><thead><tr><th>学生</th><th>班级</th><th>任课老师</th><th>剩余课时</th></tr></thead><tbody>${report.low_hours.map(s=>`<tr><td>${h(s.name)}</td><td>${h(s.class_name)}</td><td>${h(s.teachers.map(t=>t.display_name).join('、')||'未分配')}</td><td>${h(hours(s.remaining_units))}</td></tr>`).join('')||'<tr><td colspan="4">当前没有符合阈值的学生。</td></tr>'}</tbody></table></div>${report.low_hours_truncated?'<p class="help warning">人数超出显示上限，此处不是完整名单。</p>':''}</section><p class="small muted">未结束课次 ${report.pending_lessons} 节。当前统计范围为已确认的课堂资料，不代表平板全校历史资料已经同步。</p>`;
  };
  function reportTable(rows){return `<div class="table-wrap"><table><thead><tr><th>名称</th><th>应到</th><th>实到</th><th>请假</th><th>到课率</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${h(r.name)}</td><td>${r.expected}</td><td>${r.arrived}</td><td>${r.leaves}</td><td>${percent(r.rate_percent)}</td></tr>`).join('')||'<tr><td colspan="5">暂无已结束课次</td></tr>'}</tbody></table></div>`;}
  views.monthly=async()=>{
    const month=state.month||state.day.slice(0,7), report=await api('/v1/reports/monthly?month='+month);
    return heading('新生与续费','按中国时间的自然月查看明确登记的业务记录。',button('登记业务记录','new-activity','','primary'))+`<form id="monthlyFilter" class="toolbar"><label>统计月份<input type="month" name="month" value="${h(month)}" required></label><button type="submit">查看月报</button>${badge(report.final_period?'完整月份':'本月尚未结束',report.final_period?'green':'amber')}</form>`+
      metrics([['新生人数',report.new_students,'按学生去重'],['续费人数',report.renewal_students,'按学生去重'],['续费笔数',report.renewal_events,'按业务记录计算'],['统计状态',report.final_period?'已结束':'进行中','完整月报在自然月结束后生成']])+`<section class="panel"><h2>本月登记记录</h2><div class="table-wrap"><table><thead><tr><th>学生</th><th>业务类型</th><th>登记班级</th><th>业务时间</th></tr></thead><tbody>${report.items.map(i=>`<tr><td>${h(i.name)}</td><td>${badge(i.kind==='new'?'新生':'续费',i.kind==='new'?'green':'gray')}</td><td>${h(i.class_name)}</td><td>${h(stamp(i.occurred_at))}</td></tr>`).join('')||'<tr><td colspan="4">本月尚无登记记录。</td></tr>'}</tbody></table></div></section><div class="help">此处只登记新生、续费的业务标记，不证明资金到账，也不会增加或扣减课时。余额变化不会被自动猜成续费。</div>`;
  };
  views.notices=async()=>{
    const path=has('admin')&&state.allNotices?'/v1/admin/outbox':'/v1/me/notifications';const page=await api(path+'?limit=40');state.noticePath=path;state.cursor=page.next_cursor;
    return heading('通知演练记录','“已演练”只表示本地流程完成，不代表微信已发送或家长已收到。')+`${has('admin')?`<label class="inline"><input id="allNotices" type="checkbox"${checked(state.allNotices)}>查看全校演练记录</label><br>`:''}<section class="panel" id="noticeList">${noticeCards(page.items)}</section><button type="button" class="load-more" data-action="more-notices"${state.cursor?'':' hidden'}>加载更多记录</button><p class="muted small">只显示当前身份、授权及接收选择允许查看的记录。若本页为空但还有更多记录，可以继续加载。</p>`;
  };
  function noticeCards(items){return items.length?items.map(n=>{
    const p=n.payload.data;const lines=[];
    if(p.student_name)lines.push(p.student_name+(p.class_name?' · '+p.class_name:''));
    if(p.remaining_units!==undefined)lines.push('剩余 '+hours(p.remaining_units)+' 课时');
    if(p.body)lines.push(p.body);
    if(p.missing_count!==undefined)lines.push('本节未到且未请假 '+p.missing_count+' 人');
    if(p.missing_students?.length)lines.push('未到学生：'+p.missing_students.map(s=>s.name).join('、'));
    if(p.rate_percent!==undefined)lines.push('到课率 '+(p.rate_percent===null?'—':p.rate_percent+'%')+' · 实到 '+p.arrived+' / 应到 '+p.expected);
    if(p.new_students!==undefined)lines.push(p.month+' · 新生 '+p.new_students+' 人 · 续费 '+p.renewal_students+' 人 / '+p.renewal_events+' 笔');
    if(p.lesson_id&&!lines.length)lines.push('课堂提醒演练，可在对应课堂核对名单。');
    return `<article class="notice-card"><div class="notice-title"><strong>${h(noticeNames[n.notice_type]||'通知演练')}</strong>${badge(statusNames[n.status]||n.status,n.status==='simulated'?'green':n.status==='pending_simulation'?'amber':'gray')}</div><p class="text-content">${h(lines.join('\n')||'演练已记录，可在对应报告中查看详情。')}</p><small class="muted">${h(stamp(n.created_at))}${n.recipient_name?' · 接收人 '+h(n.recipient_name):''}</small></article>`;
  }).join(''):empty('暂无可查看的演练记录','通知接收默认关闭，开启后不会补发过去的通知。');}
  views.settings=async()=>{
    const prefs=await api('/v1/me/notification-preferences');state.prefs=prefs.preferences;
    const config=has('admin')?(await api('/v1/admin/notification-schedule')).settings:null;state.schedule=config;
    return heading('通知设置','先确认各身份的接收选择，再进行本地通知演练。')+`<section class="panel"><h2>我的通知接收选择</h2><form id="preferencesForm" class="stack"><div class="form-grid">${Object.entries(state.prefs).map(([key,value])=>`<label class="check-row"><input type="checkbox" name="${key}"${checked(value)}><span>${h(noticeNames[key])}<small>仅对开启后的符合条件事件生效</small></span></label>`).join('')}</div><div class="actions"><button class="primary" type="submit">保存接收选择</button></div></form></section>`+
    (config?`<section class="panel"><h2>全校通知演练安排</h2><form id="scheduleForm" class="stack"><label class="inline"><input type="checkbox" name="enabled"${checked(config.enabled)}>启用到期通知演练</label><div class="form-grid"><label>每周汇总星期<select name="weekly_weekday">${['周一','周二','周三','周四','周五','周六','周日'].map((label,i)=>option(String(i),label,String(config.weekly_weekday))).join('')}</select></label><label>每周汇总时间（中国时间）<input type="time" name="weekly_time" value="${String(config.weekly_hour).padStart(2,'0')}:${String(config.weekly_minute).padStart(2,'0')}" required></label><label>月末结束后延迟（分钟）<input type="number" name="monthly_delay_minutes" min="0" max="1440" value="${config.monthly_delay_minutes}" required></label><label>课时不足阈值（含本数）<input name="low_hours" type="number" min="0" max="1000" step="0.01" value="${h(config.low_hours_units/100)}" required></label></div><div class="help">周报会等待该周已登记的最后一节课结束；月报只在完整自然月结束后生成。这里保存的是演练安排，正式时间和阈值仍需确认。</div><div class="actions"><button type="submit" class="primary">保存演练安排</button></div></form></section><section class="panel"><h2>手动演练</h2><p class="muted">先生成当前到期的提醒，再处理待演练记录。不会自动定时运行，也不会发微信。</p><div class="actions">${button('生成到期通知','jobs-tick')}${button('处理待演练记录','simulate')}</div></section>`:'');
  };

  views.devices=async()=>{
    const [devices,classes]=await Promise.all([api('/v1/admin/devices'),collect('/v1/classes')]);state.devices=devices.items;state.classes=classes;
    return heading('平板设备','先限定设备可访问的班级，再明确启用同步权限。',button('注册平板设备','new-device','','primary'))+`<section class="panel"><div class="table-wrap"><table><thead><tr><th>设备</th><th>设备状态</th><th>同步授权</th><th>代办请假</th><th>操作</th></tr></thead><tbody>${state.devices.map(d=>`<tr><td>${h(d.name)}<small class="muted small">凭证到期 ${h(stamp(d.expires_at))}</small></td><td>${badge(d.active?'启用中':'已停用',d.active?'green':'gray')}</td><td>${badge(d.sync_access.enabled?'已启用':'关闭',d.sync_access.enabled?'green':'gray')}</td><td>${d.sync_access.allow_local_leaves?'已委托设备':'未委托'}</td><td>${button('同步权限','device-grant',d.id)} ${button('轮换凭证','rotate-device',d.id)}</td></tr>`).join('')||'<tr><td colspan="5">尚未注册平板设备。</td></tr>'}</tbody></table></div></section><div class="help">凭证只在签发或轮换时显示一次，不与家长共享。同步代办请假须额外委托设备；平板填写的办理老师姓名只作备注，不代表该老师已经登录服务器。</div>`;
  };
  const actions={
    'restore-archive':async node=>{
      if(state.reviewOnly)throw new Error('当前是名册核对副本，暂不恢复上课。');
      const s=state.archives.find(s=>s.id===node.dataset.id);if(!s||!state.classes.length)throw new Error('请刷新核对学生和班级。');state.restoreArchive=s;
      dialog('恢复上课',`<form id="restoreArchiveForm" class="stack"><p><strong>${h(s.name)}</strong> · ${h(s.external_ref)} · 剩余 ${h(hours(s.remaining_units))} 课时</p><label>恢复到哪个班级<select name="class_id" required><option value="">请明确选择班级</option>${classOptions()}</select></label><p class="help">保留编号和余额，不自动签到、扣课时或计入新生续费。此操作需要与平板资料协调。</p><label class="inline"><input name="confirmed" type="checkbox" required>已核对学生、班级和余额</label><div class="dialog-actions actions"><button type="submit" class="primary">确认恢复上课</button></div></form>`);
    },
    'new-device':async()=>{if(!state.classes.length)throw new Error('请先建立并核对班级。');dialog('注册平板设备',`<form id="deviceForm" class="stack"><label>设备名称<input name="name" maxlength="80" required placeholder="例如：前台签到平板"></label><fieldset><legend>允许访问的班级</legend><div class="check-list">${state.classes.map(c=>`<label class="check-row"><input type="checkbox" name="class_id" value="${c.id}"><span>${h(c.name)}</span></label>`).join('')}</div></fieldset><p class="help">注册后仍默认关闭新事件同步，请在列表中明确授予同步及代办请假权限，再由平板核对学号与课次。</p><div class="dialog-actions actions"><button class="primary" type="submit">注册并显示本次凭证</button></div></form>`);},
    'device-grant':async node=>{const d=state.devices.find(d=>d.id===node.dataset.id);state.editDevice=d;dialog('设备同步权限',`<form id="deviceGrantForm" class="stack"><p>${h(d.name)}</p><label class="inline"><input type="checkbox" name="enabled"${checked(d.sync_access.enabled)}>启用这台设备的新事件同步</label><label class="inline"><input type="checkbox" name="allow_local_leaves"${checked(d.sync_access.allow_local_leaves)}>委托此设备同步平板老师代办的请假与撤销</label><div class="help warning">设备只能访问已分配班级。平板代办会在记录中标明来源，不冒充经过验证的独立教师账号。关闭同步会阻止旧待办重试，但不会改平板本机签到或课时。</div><label class="inline"><input type="checkbox" required>我已核对设备及授权范围</label><div class="dialog-actions actions"><button type="submit" class="primary">保存设备同步权限</button></div></form>`);},
    'rotate-device':async node=>{const d=state.devices.find(d=>d.id===node.dataset.id);state.editDevice=d;dialog('轮换设备凭证',`<form id="rotateDeviceForm" class="stack"><p>${h(d.name)}</p><p class="help warning">轮换后旧凭证立即失效，平板会暂停提交并保留待办。请在同一设备上重新授权、核对原映射后恢复，不要把旧待办转发给另一台设备。</p><label class="inline"><input type="checkbox" required>确认使这台设备的旧凭证失效</label><div class="dialog-actions actions"><button type="submit" class="primary">轮换并显示本次凭证</button></div></form>`);},
    navigate: async node=>navigate(node.dataset.id),reload:async()=>navigate(state.view,true),
    'open-lesson':async node=>{state.lesson={id:node.dataset.id};await navigate('lesson');},
    'more-lessons':async node=>{const page=await api('/v1/lessons?day='+state.day+'&scope='+state.scope+'&after='+state.cursor);state.lessonRows.push(...page.items);state.cursor=page.next_cursor;$('#lessonCards').innerHTML=lessonCards();node.hidden=!state.cursor;},
    'more-notices':async node=>{const page=await api(state.noticePath+'?limit=40&after='+state.cursor);state.cursor=page.next_cursor;const list=$('#noticeList');if(list.querySelector('.empty'))list.replaceChildren();list.insertAdjacentHTML('beforeend',noticeCards(page.items));node.hidden=!state.cursor;},
    'new-person':async()=>dialog('新增人员',`<form id="personForm" class="stack"><label>姓名<input name="display_name" maxlength="80" required></label><label>登录账号<input name="login" minlength="3" maxlength="64" pattern="[a-z0-9][a-z0-9_.-]{2,63}" autocomplete="off" required><small class="muted">3–64位小写字母、数字、点、下划线或短横线。</small></label><label>初始密码<input name="password" type="password" minlength="15" maxlength="128" autocomplete="new-password" required></label><fieldset><legend>分配身份（可以多选）</legend><div class="actions"><label class="inline"><input type="checkbox" name="role" value="teacher">老师</label><label class="inline"><input type="checkbox" name="role" value="admin">管理员</label></div></fieldset><div class="dialog-actions actions"><button type="submit" class="primary">创建人员</button></div></form>`),
    'edit-person':async node=>{const p=state.people.find(p=>p.id===node.dataset.id);state.editPerson=p;dialog('调整 '+p.display_name+' 的身份',`<form id="accessForm" class="stack"><fieldset><legend>分配身份（可以多选）</legend><div class="actions">${['teacher','admin',...(p.roles.includes('parent')?['parent']:[])].map(r=>`<label class="inline"><input type="checkbox" name="role" value="${r}"${checked(p.roles.includes(r))}>${h(roles[r])}</label>`).join('')}</div></fieldset><label class="inline"><input type="checkbox" name="active"${checked(p.active)}>账号可登录</label><div class="help warning">保存后该人员需要重新登录并重新确认通知接收选择。${p.id===state.me.id?'你正在修改自己的账号，保存成功后将退出登录。':''}</div><label class="inline"><input type="checkbox" name="confirmed" required>我已核对人员及权限变更</label><div class="dialog-actions actions"><button type="submit" class="primary">确认保存</button></div></form>`);},
    'new-class':async()=>dialog('新增班级',`<form id="classForm" class="stack"><label>所属课程<input name="course_name" maxlength="80" placeholder="例如：信息学奥赛"></label><label>完整班级名称<input name="name" maxlength="80" placeholder="例如：信息学奥赛周六下午1" required></label><p class="small muted">不同课程下班级的完整名称需要保持唯一，方便重名核对。</p><div class="dialog-actions actions"><button class="primary" type="submit">创建班级</button></div></form>`),
    'assign-teachers':async node=>{
      const classId=node.dataset.id;const [people,assignment]=await Promise.all([collect('/v1/admin/people'),api('/v1/admin/classes/'+classId+'/teachers')]);state.assignment={classId,expected:assignment.teacher_ids};
      const teachers=people.filter(p=>p.active&&p.roles.includes('teacher'));dialog('分配任课老师',`<p class="muted">${h(state.classes.find(c=>c.id===classId)?.name)}</p><form id="assignmentForm" class="stack"><div class="check-list">${teachers.map(p=>`<label class="check-row"><input type="checkbox" name="teacher" value="${p.id}"${checked(assignment.teacher_ids.includes(p.id))}><span>${h(p.display_name)}<small>${h(p.login)}</small></span></label>`).join('')||'<p>暂无启用的教师账号，请先新增人员。</p>'}</div><label class="inline"><input type="checkbox" name="confirmed" required>我已核对本班任课名单（可以不分配）</label><div class="dialog-actions actions"><button type="submit" class="primary">保存任课授权</button></div></form>`);
    },
    'new-student':async()=>{
      if(!state.classes.length)throw new Error('请先创建班级。');
      dialog('新增联调学生',`<form id="studentForm" class="stack"><p class="help">仅在当前联调资料中建立名册，不会从平板导入资料，也不会生成新生统计。</p><label>学生姓名<input name="name" maxlength="80" required></label><label>学生核对编号<input name="external_ref" maxlength="80" required><small class="muted">同一机构内唯一，用于区分重名学生。</small></label><label>所属班级<select name="class_id">${classOptions()}</select></label><label>当前剩余课时<input name="hours" type="number" min="-10000" max="100000" step="0.01" value="0" required></label><div class="dialog-actions actions"><button type="submit" class="primary">创建学生</button></div></form>`);
    },
    'new-lesson':async()=>{
      if(!state.classes.length)throw new Error('请先创建班级并分配任课老师。');
      dialog('安排一次实际课堂',`<form id="lessonForm" class="stack"><label>上课班级<select id="newLessonClass" name="class_id">${classOptions()}</select></label><label>任课老师<select id="newLessonTeacher" name="teacher_id" required><option value="">正在加载任课老师…</option></select></label><div class="form-grid"><label>实际课日<input name="day" type="date" value="${h(state.day)}" required></label><label>开始时间<input name="start" type="time" required></label><label>结束时间<input name="end" type="time" required></label></div><p class="help">按当前班级的在用学生确认本次应到名单。隔周课只选择实际课日；不自动安排中间的休课周。</p><label class="inline"><input type="checkbox" name="confirmed" required>我已核对课日、老师与当前学生名单</label><div class="dialog-actions actions"><button type="submit" class="primary">安排本次课堂</button></div></form>`);loadLessonTeachers().catch(error=>message(error.message,true));
    },
    leave:async node=>{const s=state.lesson.members.find(s=>s.id===node.dataset.id);state.leaveStudent=s;dialog('办理本次请假',`<p><strong>${h(s.name)}</strong> · ${h(s.external_ref)}<br><span class="muted">${h(state.lesson.class_name)} · ${h(stamp(state.lesson.starts_at))}</span></p><form id="leaveForm" class="stack"><label>请假原因<input name="reason" maxlength="200" required></label><div class="help">仅为这一次课请假；计入应到、不计实到、不扣课时。</div><label class="inline"><input type="checkbox" required>我已核对学生和课次</label><div class="dialog-actions actions"><button class="primary" type="submit">确认办理请假</button></div></form>`);},
    'cancel-leave':async node=>{state.cancelLeave=node.dataset.id;dialog('撤销本次请假',`<form id="cancelLeaveForm" class="stack"><p class="muted">撤销后恢复待签到资格，不会自动签到或扣课时。</p><label>撤销原因<input name="reason" maxlength="200" required></label><div class="dialog-actions actions"><button type="submit" class="primary">确认撤销请假</button></div></form>`);},
    'new-activity':async()=>{state.students=await collect('/v1/students');if(!state.students.length)throw new Error('请先建立学生名册。');dialog('登记新生或续费记录',`<form id="activityForm" class="stack"><p class="help">登记业务标记供月报统计，不修改课时，也不作为资金到账证明。</p><label>学生<select name="student_id">${state.students.map(s=>option(s.id,s.name+' / '+s.external_ref+' / '+s.class_name)).join('')}</select></label><label>业务类型<select name="kind"><option value="new">新生</option><option value="renewal">续费</option></select></label><label>业务发生时间（中国时间）<input type="datetime-local" name="occurred_at" value="${h(state.day)}T09:00" required></label><label class="inline"><input type="checkbox" required>我已核实该笔业务，请勿重复登记</label><div class="dialog-actions actions"><button type="submit" class="primary">登记业务记录</button></div></form>`);},
    'jobs-tick':async()=>{const r=await api('/v1/admin/jobs/tick','POST');message(r.enabled?'已生成 '+r.simulation_notices_queued+' 条到期通知演练；未发送微信。':'到期通知演练尚未启用，请先保存演练安排。');},
    simulate:async()=>{const r=await api('/v1/test/simulate','POST');message(`本次已演练 ${r.simulated} 条，取消 ${r.cancelled} 条，过期 ${r.expired} 条；未发送微信。`);}
  };
  async function loadLessonTeachers(){
    const c=$('#newLessonClass')?.value;if(!c)return;const target=$('#newLessonTeacher'),submit=$('#lessonForm button[type=submit]'),sequence=(state.teacherLoad||0)+1;state.teacherLoad=sequence;target.disabled=true;submit.disabled=true;
    const [people,assignment]=await Promise.all([collect('/v1/admin/people'),api('/v1/admin/classes/'+c+'/teachers')]);
    if(!target.isConnected||$('#newLessonClass').value!==c||state.teacherLoad!==sequence)return;
    const teachers=people.filter(p=>assignment.teacher_ids.includes(p.id)&&p.active&&p.roles.includes('teacher'));
    target.innerHTML='<option value="">'+(teachers.length?'请选择任课老师':'本班尚未分配老师，请先到「班级与任课」授权')+'</option>'+teachers.map(p=>option(p.id,p.display_name)).join('');target.disabled=false;submit.disabled=!teachers.length;
  }
  function units(value){if(!/^-?\d+(?:\.\d{1,2})?$/.test(value))throw new Error('课时请填写最多两位小数。');return Math.round(Number(value)*100);}
  const forms={
    restoreArchiveForm:async(f,d)=>{const s=state.restoreArchive;await api('/v1/admin/archives/'+s.id+'/restore','POST',{class_id:d.get('class_id'),expected_remaining_units:s.remaining_units,confirmed:d.has('confirmed')});await navigate('archives',true);message('已恢复到所选班级，原余额保留，未登记新生或续费。');},
    deviceForm:async(f,d)=>{if(!d.getAll('class_id').length)throw new Error('请至少选择一个已核对班级。');const device=await api('/v1/admin/devices','POST',{name:d.get('name'),class_ids:d.getAll('class_id')});await navigate('devices',true);showDeviceCredential(device);},
    deviceGrantForm:async(f,d)=>{await api('/v1/admin/devices/'+state.editDevice.id+'/sync-access','PUT',{enabled:d.has('enabled'),allow_local_leaves:d.has('allow_local_leaves'),expected_enabled:state.editDevice.sync_access.enabled,expected_allow_local_leaves:state.editDevice.sync_access.allow_local_leaves});await navigate('devices',true);message('设备同步权限已保存，尚未自动上传平板资料。');},
    rotateDeviceForm:async()=>{const d=await api('/v1/admin/devices/'+state.editDevice.id+'/rotate','POST');await navigate('devices',true);showDeviceCredential(d);},
    lessonFilter:async(f,d)=>{state.day=d.get('day');if(d.get('scope'))state.scope=d.get('scope');await navigate('lessons');},
    personForm:async(f,d)=>{const password=d.get('password');f.elements.password.value='';await api('/v1/admin/people','POST',{login:d.get('login'),display_name:d.get('display_name'),password,roles:d.getAll('role')});await navigate('people',true);message('人员已创建，请通过私下渠道交接账号并核对任课授权。');},
    accessForm:async(f,d)=>{const p=state.editPerson;await api('/v1/admin/people/'+p.id,'PATCH',{roles:d.getAll('role'),active:d.has('active'),expected_roles:p.roles,expected_active:!!p.active});if(p.id===state.me.id){clearSession();message('身份已保存，请重新登录。');}else{await navigate('people',true);message('人员身份已更新，原登录已失效。');}},
    classForm:async(f,d)=>{await api('/v1/admin/classes','POST',{name:d.get('name'),course_name:d.get('course_name')});await navigate('classes',true);message('班级已创建，接下来可以分配任课老师。');},
    assignmentForm:async(f,d)=>{const a=state.assignment;await api('/v1/admin/classes/'+a.classId+'/teachers','PUT',{teacher_ids:d.getAll('teacher'),expected_teacher_ids:a.expected});await navigate('classes',true);message('任课授权已保存。');},
    studentForm:async(f,d)=>{await api('/v1/admin/students','POST',{name:d.get('name'),external_ref:d.get('external_ref'),class_id:d.get('class_id'),remaining_units:units(d.get('hours'))});await navigate('students',true);message('联调学生已创建，尚未与平板同步。');},
    lessonForm:async(f,d)=>{if(d.get('end')<=d.get('start'))throw new Error('结束时间必须晚于开始时间，本版不安排跨午夜课堂。');await api('/v1/admin/lessons','POST',{class_id:d.get('class_id'),teacher_id:d.get('teacher_id'),starts_at:d.get('day')+'T'+d.get('start')+':00+08:00',ends_at:d.get('day')+'T'+d.get('end')+':00+08:00'});state.day=d.get('day');await navigate('lessons',true);message('本次课堂及应到名单已确认。');},
    leaveForm:async(f,d)=>{await api('/v1/lessons/'+state.lesson.id+'/leaves','POST',{student_id:state.leaveStudent.id,reason:d.get('reason')});await navigate('lesson',true);message('本次请假已保存，不扣课时。');},
    cancelLeaveForm:async(f,d)=>{await api('/v1/leaves/'+state.cancelLeave+'/cancel','POST',{reason:d.get('reason')});await navigate('lesson',true);message('请假已撤销，学生恢复待签到资格。');},
    absenceForm:async(f,d)=>{if(!d.getAll('missing').length)throw new Error('请先选择仍未到课的学生。');if(!d.has('confirmed'))throw new Error('请先确认已经核实未到情况。');const r=await api('/v1/lessons/'+state.lesson.id+'/absence-notices','POST',{student_ids:d.getAll('missing')});await navigate('lesson',true);message('已生成 '+r.simulation_notices_queued+' 条家长提醒演练；未发送微信。');},
    contentForm:async(f,d)=>{await api('/v1/lessons/'+state.lesson.id+'/content','PUT',{body:d.get('body'),audience:d.get('audience'),publish_after_class:d.has('publish_after_class')});state.dirty=false;await navigate('lesson',true);message('课堂内容已保存。');},
    weeklyFilter:async(f,d)=>{state.week=monday(d.get('week'));state.reportScope=d.get('scope')||(!has('admin')?'teaching':'all');await navigate('weekly');},
    monthlyFilter:async(f,d)=>{state.month=d.get('month');await navigate('monthly');},
    activityForm:async(f,d)=>{f.dataset.reference||='ui-'+crypto.randomUUID();await api('/v1/admin/student-activity','POST',{student_id:d.get('student_id'),kind:d.get('kind'),occurred_at:d.get('occurred_at')+':00+08:00',source_ref:f.dataset.reference});state.month=d.get('occurred_at').slice(0,7);await navigate('monthly',true);message('业务记录已登记，学生课时未改变。');},
    preferencesForm:async(f,d)=>{const changes=Object.fromEntries(Object.keys(state.prefs).filter(k=>d.has(k)!==state.prefs[k]).map(k=>[k,d.has(k)]));if(!Object.keys(changes).length){message('接收选择没有变更。');return;}await api('/v1/me/notification-preferences','PUT',{preferences:changes});await navigate('settings',true);message('接收选择已保存；不会补发过去的通知。');},
    scheduleForm:async(f,d)=>{const [hour,minute]=d.get('weekly_time').split(':').map(Number);await api('/v1/admin/notification-schedule','PUT',{enabled:d.has('enabled'),weekly_weekday:Number(d.get('weekly_weekday')),weekly_hour:hour,weekly_minute:minute,monthly_delay_minutes:Number(d.get('monthly_delay_minutes')),low_hours_units:units(d.get('low_hours'))});await navigate('settings',true);message('演练安排已保存，尚未启动自动任务或真实微信发送。');}
  };
  function showDeviceCredential(device){dialog('本次设备凭证',`<p class="help warning">此凭证仅本次显示。请通过私下渠道在对应平板的安卓配置窗口输入，不要发给家长或放入备份。关闭窗口后不会再次展示。</p><label>设备凭证<input id="deviceCredentialOnce" readonly autocomplete="off"></label><div class="dialog-actions actions"><button type="button" id="closeDeviceCredential">已安全交接，关闭</button></div>`);$('#deviceCredentialOnce').value=device.device_token;$('#closeDeviceCredential').onclick=closeDialog;}
  document.addEventListener('submit',event=>{
    if(event.target.id==='loginForm')return;
    const handler=forms[event.target.id];if(!handler)return;event.preventDefault();if(event.target.id==='absenceForm'&&state.dirty){message('请先保存课堂内容，再确认未到提醒。',true);return;}const f=event.target,d=new FormData(f);perform(()=>handler(f,d),f);
  });
  document.addEventListener('click',event=>{const target=event.target.closest('[data-action]');if(target&&!target.disabled&&actions[target.dataset.action]){if(state.dirty&&['leave','cancel-leave'].includes(target.dataset.action)){message('请先保存课堂内容，再办理请假或撤销。',true);return;}perform(()=>actions[target.dataset.action](target));}});
  document.addEventListener('input',event=>{if(event.target.id==='archiveSearch')$('#archiveRows').innerHTML=archiveRows(event.target.value);if(event.target.id==='classSearch')$('#classGroups').innerHTML=classGroups(event.target.value);if(event.target.closest('#contentForm')){state.dirty=true;const node=$('#draftState');if(node)node.textContent='有尚未保存的修改';}});
  document.addEventListener('change',event=>{if(event.target.id==='allNotices'){state.allNotices=event.target.checked;perform(()=>navigate('notices'));}if(event.target.id==='newLessonClass')loadLessonTeachers().catch(error=>message(error.message,true));});
  $('#closeDialog').addEventListener('click',closeDialog);
  $('#editorDialog').addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
  $('#logoutButton').addEventListener('click',async()=>{if(state.dirty&&!window.confirm('课堂内容尚未保存，确定退出吗？'))return;const token=state.token;clearSession();message('已退出工作台。');try{await fetch('/v1/auth/logout',{method:'POST',headers:{Authorization:'Bearer '+token},credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(5000)});}catch{if(!state.me)message('已退出本机；暂时无法连接，会话将按有效期失效。',true);}});
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>{state.token=null;});
  window.addEventListener('pageshow',event=>{if(event.persisted)clearSession();});
  $('#loginForm').addEventListener('submit',event=>{event.preventDefault();const form=event.target,data=new FormData(form),password=data.get('password');form.elements.password.value='';clearMessage();perform(async()=>{
    const result=await api('/v1/auth/login','POST',{organization:data.get('organization'),login:data.get('login'),password});state.token=result.access_token;
    try{
      const [me,health]=await Promise.all([api('/v1/me'),api('/health/ready')]);
      if(me.kind!=='user'||!me.roles.some(r=>r==='admin'||r==='teacher')){await api('/v1/auth/logout','POST');throw new Error('当前账号尚无老师或管理员身份，请联系管理员分配。家长入口将在微信接入后提供。');}
      state.me=me;state.epoch++;state.day=dayAt(health.server_time||Date.now()/1000);state.scope=has('teacher')?'teaching':'authorized';state.week=null;state.month=null;state.reportScope=null;state.allNotices=false;
      state.reviewOnly=health.roster_review_only!==false;
      form.reset();$('#loginScreen').hidden=true;$('#appScreen').hidden=false;$('#accountName').textContent=me.display_name;$('#accountRoles').innerHTML=me.roles.map(r=>`<span class="role-tag">${h(roles[r])}</span>`).join('');await navigate('lessons',true);
    }catch(error){clearSession();message(error.message,true);}
  },form);});
})();
