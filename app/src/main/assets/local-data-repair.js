/* Explicit local repair plans only. No automatic startup mutation or network calls. */
(function(root){
  'use strict';
  const clone=x=>JSON.parse(JSON.stringify(x));
  function canonical(x){if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';if(x&&typeof x==='object')return '{'+Object.keys(x).sort().filter(k=>x[k]!==undefined).map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';return JSON.stringify(x);}
  async function fingerprint(data){const bytes=new TextEncoder().encode(canonical(JSON.parse(JSON.stringify(data))));if(!root.crypto?.subtle)throw Error('安全校验组件不可用，请重新打开App');return Array.from(new Uint8Array(await root.crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');}
  const validDate=x=>typeof x==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(x)&&Number.isFinite(Date.parse(x+'T12:00:00Z'))&&new Date(x+'T12:00:00Z').toISOString().slice(0,10)===x;
  const history=x=>(x||[]).map(e=>({class_name:e.className||'',start_date:e.startDate||'',end_date:e.endDate||''}));
  async function propose(data,plan){
    if(!plan||plan.format!=='future-school-local-repair-plan'||plan.schema_version!==1||typeof plan.plan_id!=='string'||!/^[a-zA-Z0-9_-]{8,80}$/.test(plan.plan_id)||!Array.isArray(plan.changes)||!plan.changes.length||plan.changes.length>100||!Array.isArray(data.students)||!data.settings)throw Error('不是支持的本地修复方案');
    if(data.deviceSync)throw Error('存在服务器同步资料，请先单独核对，不能执行本地编号修复');
    if((data.settings.localDataRepairs||[]).some(r=>r.plan_id===plan.plan_id))throw Error('此方案已经执行，无需重复修复');
    if(await fingerprint(data)!==plan.baseline_sha256)throw Error('当前资料已变化，方案已停止。请重新备份并核对，不要恢复旧备份覆盖当前资料');
    const next=clone(data),groups=new Map(),numbers=new Map(),usedIds=new Set(),changes=[],fields=new Set();
    data.students.forEach((s,i)=>{if(!Number.isSafeInteger(s.id)||s.id<=0||typeof s.studentNo!=='string'||!s.studentNo||numbers.has(s.studentNo))throw Error('学生编号或学号不符合本方案要求');numbers.set(s.studentNo,i);const rows=groups.get(s.id)||[];rows.push(i);groups.set(s.id,rows);});
    function scan(x){if(!x||typeof x!=='object')return;for(const[k,v]of Object.entries(x)){if(k==='id'&&Number.isSafeInteger(v))usedIds.add(v);if(v&&typeof v==='object')scan(v);}}scan(data);
    const c=plan.confirmation,confirmed=c&&validDate(c.paused_since)&&typeof c.class_name==='string'&&Array.isArray(c.student_numbers)&&new Set(c.student_numbers).size===c.student_numbers.length?new Set(c.student_numbers):null;
    for(const change of plan.changes){
      const index=numbers.get(change.student_no),s=data.students[index],key=change.student_no+':'+change.field;
      if(!s||s.longTermAbsent!==true||s.className||fields.has(key))throw Error('方案学生状态、班级或操作有变化');fields.add(key);
      if(change.field==='id'){
        if(s.id!==change.before||groups.get(s.id).length!==2||!Number.isSafeInteger(change.after)||change.after<=0||usedIds.has(change.after)||groups.get(s.id).some(i=>data.students[i].longTermAbsent!==true||data.students[i].className))throw Error('编号修复前置条件不满足');
        let referenced=false;function refs(x){if(!x||typeof x!=='object')return;for(const[k,v]of Object.entries(x)){if((k==='studentId'||k==='student_id')&&v===s.id)referenced=true;else if(v&&typeof v==='object')refs(v);}}refs(data);if(referenced)throw Error('冲突编号有关联记录，不能自动分配');
        usedIds.add(change.after);next.students[index].id=change.after;
      }else if(change.field==='enrollmentHistory'){
        const entries=s.enrollmentHistory||[],pause=s.longTermAbsenceHistory||[];
        if(!confirmed?.has(s.studentNo)||s.pausedClassName!==c.class_name||!Array.isArray(change.after)||change.after.length||canonical(history(entries))!==canonical(change.before)||!entries.length||!entries.some(e=>e.endDate&&e.endDate<e.startDate)||!entries.every(e=>e.className===c.class_name&&validDate(e.startDate)&&e.startDate>=c.paused_since)||pause.length!==1||pause[0].startDate!==c.paused_since||pause[0].endDate)throw Error('历史记录不符合已确认的暂停口径');
        if(data.attendance.some(a=>a.studentId===s.id&&a.date>=c.paused_since))throw Error('暂停日起存在签到，必须人工核对');
        next.students[index].enrollmentHistory=[];
      }else throw Error('方案包含不支持的修改字段');
      changes.push({student_no:s.studentNo,name:s.name,field:change.field,before:change.field==='id'?s.id:clone(s.enrollmentHistory),after:change.field==='id'?change.after:[],reason:change.field==='id'?'分离共用内部编号的独立学号档案':'按已确认暂停日期清除冲突在读段，保留原值供核对'});
    }
    if(new Set(next.students.map(s=>s.id)).size!==next.students.length)throw Error('方案未解决全部学生内部编号冲突');
    if(confirmed){const actual=changes.filter(x=>x.field==='enrollmentHistory').map(x=>x.student_no).sort();if(canonical(actual)!==canonical([...confirmed].sort()))throw Error('历史修复名单与确认范围不一致');}
    next.settings.localDataRepairs=[...(next.settings.localDataRepairs||[]),{plan_id:plan.plan_id,at:new Date().toISOString(),baseline_sha256:plan.baseline_sha256,source_sha256:plan.source_sha256,confirmation:c?{class_name:c.class_name,paused_since:c.paused_since}:null,changes}];
    return{next,changes,baseline:JSON.stringify(data)};
  }
  const api=Object.freeze({canonical,fingerprint,propose});if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.FutureSchoolLocalRepair=api;
})(globalThis);

function mountLocalDataRepairSetting(){
  if(!isAdminAuthenticated||currentView!=='settings'||$('#localDataRepairButton'))return;const anchor=$('#exportBackup');if(!anchor)return;
  const row=document.createElement('div');row.className='setting-row';row.innerHTML='<div><b>已核对资料修复</b><small>选择本机修复方案，核对后备份并执行</small></div><button type="button" class="secondary-btn" id="localDataRepairButton">打开修复</button>';anchor.closest('.setting-row').before(row);$('#localDataRepairButton').onclick=openLocalDataRepair;
}
function openLocalDataRepair(){
  if(!isAdminAuthenticated)return;let selected=null,busy=false;
  openModal('<div class="modal-head"><span>▤</span><div><h2>已核对资料修复</h2><p>仅处理已核实的编号及暂停历史</p></div></div><div class="simple-form"><p>先选择修复方案。资料有变化时会停止，不能使用旧备份覆盖当前资料。</p><label class="field"><span>选择修复方案JSON</span><input id="localRepairFile" type="file" accept=".json,application/json"></label><div id="localRepairPreview" class="warning-box">尚未选择方案</div><label><input id="localRepairConfirm" type="checkbox"> 我已核对名单和暂停日期，同意按本方案修复</label><div class="form-actions"><button id="localRepairCancel" class="secondary-btn">取消</button><button id="localRepairApply" class="primary-btn" disabled>备份并修复</button></div><p id="localRepairStatus">将保存修复前和修复结果两份完整备份，不清理旧备份。修复不会扣课、补签或发送通知。</p></div>');
  const file=$('#localRepairFile'),button=$('#localRepairApply'),preview=$('#localRepairPreview'),status=$('#localRepairStatus');let sequence=0;
  $('#localRepairCancel').onclick=()=>{if(!busy){sequence++;closeModal()}};
  file.onchange=async()=>{const ticket=++sequence;selected=null;button.disabled=true;preview.textContent='正在本机检查…';try{if(!file.files[0]||file.files[0].size>1024*1024)throw Error('请选择小于1MiB的修复方案JSON');const plan=JSON.parse(await file.files[0].text());const candidate=await FutureSchoolLocalRepair.propose(state,plan);validateBackupState(candidate.next);if(ticket!==sequence||!isAdminAuthenticated)return;selected=plan;preview.replaceChildren();const heading=document.createElement('p');heading.textContent=`共 ${candidate.changes.length} 项：编号 ${candidate.changes.filter(x=>x.field==='id').length} 项，历史 ${candidate.changes.filter(x=>x.field==='enrollmentHistory').length} 项。`;preview.append(heading);if(plan.confirmation){const p=document.createElement('p');p.textContent=`${plan.confirmation.class_name}：${plan.confirmation.paused_since}起不再上课。此前真实在读日期未知，不补造。`;preview.append(p);}const list=document.createElement('ul');candidate.changes.forEach(change=>{const li=document.createElement('li');li.textContent=`${change.name}（${change.student_no}）：${change.field==='id'?'分离内部编号':'清除冲突在读段，原值留档'}`;list.append(li)});preview.append(list);$('#localRepairConfirm').checked=false;button.disabled=false;}catch(error){if(ticket===sequence)preview.textContent=error.message||'方案读取失败，未执行修复';}};
  button.onclick=async()=>{if(busy||!selected||!isAdminAuthenticated)return;if(!$('#localRepairConfirm').checked)return toast('请先核对并勾选确认',true);busy=true;button.disabled=true;file.disabled=true;try{status.textContent='正在校验和备份，请勿关闭App…';const result=await applyLocalDataRepair(selected);closeModal();render();openModal(`<div class="modal-head"><span>✓</span><div><h2>资料修复已保存</h2><p>${result.changes}项修复已完成</p></div></div><div class="simple-form"><p>人数、学号、余额、人脸、签到、流水、请假及排课保持原样；原始修改内容已留档。</p><p>修复前备份：${esc(result.before.fileName)}</p><p>可恢复的修复结果备份：${esc(result.after.fileName)}</p><p>文件已写入并重新读取校验。请保留两份文件，重新打开App后核对。</p><button id="closeLocalRepairResult" class="primary-btn">完成</button></div>`);$('#closeLocalRepairResult').onclick=closeModal;}catch(error){status.textContent=error.message||'修复未完成';busy=false;button.disabled=false;file.disabled=false;}};
}
async function applyLocalDataRepair(plan){
  if(!isAdminAuthenticated)throw Error('请先解锁后台');
  if(typeof nativeSyncProfile==='function'&&nativeSyncProfile().configured)throw Error('平板已配置同步连接，请先单独核对');
  if(!window.AndroidBridge||typeof AndroidBridge.createVerifiedRepairBackup!=='function'||typeof AndroidBridge.commitLocalRepairCheckpoint!=='function'||typeof AndroidBridge.discardLocalRepairCheckpoint!=='function')throw Error('当前版本不支持已校验备份和异常恢复，不能执行修复');
  const beforeString=JSON.stringify(state),result=await FutureSchoolLocalRepair.propose(state,plan);validateBackupState(result.next);
  if(!isAdminAuthenticated||JSON.stringify(state)!==beforeString)throw Error('检查期间资料已变化，请重新选择方案');
  const backup=(payload,phase)=>{let answer;try{answer=JSON.parse(AndroidBridge.createVerifiedRepairBackup(payload,phase)||'{}')}catch(_){throw Error('备份未完成，资料修复已停止')}if(!answer.ok||!answer.verified||typeof answer.sha256!=='string'||!/^[a-f0-9]{64}$/.test(answer.sha256)||!answer.fileName)throw Error('备份未通过重新读取校验，资料修复已停止');return answer;};
  const before=backup(beforeString,'before'),serialized=JSON.stringify(result.next),after=backup(serialized,'candidate');
  if(JSON.stringify(state)!==beforeString)throw Error('备份期间资料已变化，未执行修复');
  if(!AndroidBridge.commitLocalRepairCheckpoint(localStorage.getItem(STORAGE_KEY),serialized))throw Error('异常恢复记录未保存，修复已停止');
  try{localStorage.setItem(STORAGE_KEY,serialized)}catch(_){AndroidBridge.discardLocalRepairCheckpoint();throw Error('修复保存失败，原资料保持不变；请保留已生成的两份备份')}
  state=result.next;committedStateSnapshot=serialized;lastGeneratedId=Math.max(lastGeneratedId,...state.students.map(s=>s.id));invalidatePageCache();syncNativeReminders();
  return{changes:result.changes.length,before,after};
}
