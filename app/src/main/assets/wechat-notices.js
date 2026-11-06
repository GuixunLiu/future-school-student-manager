/* Local preparation only. No WeChat secrets, network calls or sending claims. */
const WECHAT_PREPARATION_LIMIT=2000;
let wechatNoticeTab='setup',wechatNoticeSearch='',wechatNoticePage=1;
function wechatNoticeStore(create=false){
  if(!state.wechatNotices&&create)state.wechatNotices={version:1,prepareEnabled:false,includeMakeup:false,preferences:[],events:[],skipped:0};
  return state.wechatNotices||{version:1,prepareEnabled:false,includeMakeup:false,preferences:[],events:[],skipped:0};
}
function wechatStudentPreference(student){
  if(!student||state.students.filter(item=>item.id===student.id).length!==1)return null;
  return wechatNoticeStore().preferences.find(item=>item.studentId===student.id&&item.studentNo===String(student.studentNo||''))||null;
}
function prepareWechatAttendance(record,student){
  const store=wechatNoticeStore(),preference=wechatStudentPreference(student);
  if(!store.prepareEnabled||!preference||!preference.consent||record.makeup&&!store.includeMakeup)return false;
  if(store.events.some(item=>item.attendanceId===record.id))return false;
  if(store.events.length>=WECHAT_PREPARATION_LIMIT){store.skipped++;toast('微信本地待办已满，本次签到和课时仍正常保存；请在后台清理待办',true);return false}
  const text=value=>String(value||'').slice(0,200);
  store.events.push({id:`prep:${record.id}`,attendanceId:record.id,studentId:student.id,studentNo:text(student.studentNo),studentName:text(student.name),className:text(record.className),classroom:text(record.classroom),date:record.date,time:record.makeup?'':text(record.time),makeup:Boolean(record.makeup),createdAt:new Date().toISOString(),consentAt:preference.updatedAt,status:'prepared',cancelReason:''});
  return true;
}
function cancelWechatAttendance(attendanceId,reason='attendance_revoked'){
  wechatNoticeStore().events.forEach(item=>{if(item.attendanceId===attendanceId&&item.status==='prepared'){item.status='cancelled';item.cancelReason=reason}});
}
function removeWechatStudent(studentId){
  if(!state.wechatNotices)return;
  state.wechatNotices.preferences=state.wechatNotices.preferences.filter(item=>item.studentId!==studentId);
  state.wechatNotices.events=state.wechatNotices.events.filter(item=>item.studentId!==studentId);
}
function mountWechatStudentButtons(){
  if(!isAdminAuthenticated||currentView!=='students')return;
  $$('[data-edit]',$('#content')).forEach(edit=>{if(edit.parentNode.querySelector('[data-wechat-student]'))return;const button=document.createElement('button');button.type='button';button.className='mini-btn alt';button.dataset.wechatStudent=edit.dataset.edit;button.textContent='家长通知';edit.parentNode.appendChild(button)});
}
function setWechatPreparation(enabled,includeMakeup){
  const store=wechatNoticeStore(true);store.prepareEnabled=Boolean(enabled);store.includeMakeup=Boolean(includeMakeup);
  store.events.forEach(item=>{if(item.status==='prepared'&&(!store.prepareEnabled||item.makeup&&!store.includeMakeup)){item.status='cancelled';item.cancelReason=store.prepareEnabled?'makeup_disabled':'preparation_disabled'}});
  return saveState();
}
function setWechatStudentConsent(studentId,consent,guardian){
  const student=studentById(studentId);
  if(!student||state.students.filter(item=>item.id===student.id).length!==1)return{ok:false,error:'学生内部编号不唯一或档案已变化，暂不能登记通知意向，请先核对档案'};
  if(!['家长','父亲','母亲','监护人'].includes(guardian))return{ok:false,error:'请选择有效的家长称呼'};
  const store=wechatNoticeStore(true);
  store.preferences=store.preferences.filter(item=>item.studentId!==student.id);
  if(consent)store.preferences.push({studentId:student.id,studentNo:String(student.studentNo||''),consent:true,guardian,updatedAt:new Date().toISOString()});
  else store.events.forEach(item=>{if(item.studentId===student.id&&item.status==='prepared'){item.status='cancelled';item.cancelReason='consent_revoked'}});
  return saveState()?{ok:true}:{ok:false,error:'通知意向保存失败，原有设置已保留'};
}
function validateWechatNotices(data){
  const store=data.wechatNotices;if(store===undefined)return;
  const invalid=()=>{throw new Error('备份中的微信通知准备资料无效')};
  const onlyKeys=(item,keys)=>{if(!item||typeof item!=='object'||Array.isArray(item)||Object.keys(item).some(key=>!keys.includes(key)))invalid()};
  onlyKeys(store,['version','prepareEnabled','includeMakeup','preferences','events','skipped']);
  if(!store||store.version!==1||typeof store.prepareEnabled!=='boolean'||typeof store.includeMakeup!=='boolean'||!Array.isArray(store.preferences)||!Array.isArray(store.events)||store.events.length>WECHAT_PREPARATION_LIMIT||!Number.isSafeInteger(store.skipped)||store.skipped<0)invalid();
  const preferences=new Set(),events=new Set(),students=new Map(data.students.map(item=>[item.id,item])),attendance=new Map(data.attendance.map(item=>[item.id,item]));
  store.preferences.forEach(item=>{
    onlyKeys(item,['studentId','studentNo','consent','guardian','updatedAt']);
    const student=item&&students.get(item.studentId);
    if(!student||preferences.has(item.studentId)||typeof item.studentNo!=='string'||item.consent!==true||!['家长','父亲','母亲','监护人'].includes(item.guardian)||typeof item.updatedAt!=='string'||!Number.isFinite(Date.parse(item.updatedAt)))invalid();
    preferences.add(item.studentId);
  });
  const reasons=['','attendance_revoked','consent_revoked','preparation_disabled','makeup_disabled','manual_cancel'];
  store.events.forEach(item=>{
    onlyKeys(item,['id','attendanceId','studentId','studentNo','studentName','className','classroom','date','time','makeup','createdAt','consentAt','status','cancelReason']);
    if(!item||!Number.isSafeInteger(item.attendanceId)||item.attendanceId<=0||item.id!==`prep:${item.attendanceId}`||events.has(item.id)||!students.has(item.studentId)||!['prepared','cancelled'].includes(item.status)||!reasons.includes(item.cancelReason)||typeof item.makeup!=='boolean'||!isValidLocalDate(item.date))invalid();
    ['studentNo','studentName','className','classroom','time','createdAt','consentAt'].forEach(key=>{if(typeof item[key]!=='string'||item[key].length>200)invalid()});
    if(!Number.isFinite(Date.parse(item.createdAt))||!Number.isFinite(Date.parse(item.consentAt)))invalid();
    if(item.status==='prepared'){
      const record=attendance.get(item.attendanceId),preference=store.preferences.find(pref=>pref.studentId===item.studentId&&pref.studentNo===item.studentNo);
      if(!store.prepareEnabled||!preference||item.makeup&&!store.includeMakeup||item.cancelReason||!record||record.studentId!==item.studentId||record.date!==item.date||Boolean(record.makeup)!==item.makeup)invalid();
    }else if(!item.cancelReason)invalid();
    events.add(item.id);
  });
  // Restore may retain local drafts but must never create a network sending job.
}
function wechatNoticeSettingsMarkup(){
  const store=wechatNoticeStore(),count=store.events.filter(item=>item.status==='prepared').length;
  return `<section class="panel wx-settings-panel"><div class="panel-head"><div><h2>微信家长通知</h2><p>本地优先 · 联网能力分步接入</p></div><span class="wx-status">尚未接通</span></div><div class="wx-settings-summary"><b>${store.prepareEnabled?'正在准备本地通知待办':'当前仍为本地运行模式'}</b><p>模板审核、通知服务与家长微信绑定尚未完成，不会向家长发送消息。签到、消课、人脸和周报继续离线使用。</p></div><div class="setting-row"><div><b>通知管理</b><small>${count} 条本地待办 · 人脸资料不进入通知</small></div><button type="button" class="primary-btn" id="openWechatNotices">管理微信通知</button></div></section>`;
}
function openWechatNotices(tab='setup'){
  if(!isAdminAuthenticated)return toast('请先解锁后台',true);
  wechatNoticeTab=typeof tab==='string'?tab:'setup';wechatNoticeSearch='';wechatNoticePage=1;renderWechatNotices();
}
function wechatNoticeRowsMarkup(){
  const store=wechatNoticeStore(),query=wechatNoticeSearch.trim().toLowerCase();
  let rows=wechatNoticeTab==='students'?state.students.filter(student=>[student.name,student.studentNo,student.className].join(' ').toLowerCase().includes(query)):store.events.slice().reverse().filter(item=>[item.studentName,item.studentNo,item.className,item.date].join(' ').toLowerCase().includes(query));
  const total=rows.length,pages=Math.max(1,Math.ceil(total/40));wechatNoticePage=Math.max(1,Math.min(wechatNoticePage,pages));rows=rows.slice((wechatNoticePage-1)*40,wechatNoticePage*40);
  const contents=wechatNoticeTab==='students'?rows.map(student=>{const pref=wechatStudentPreference(student);return `<article class="wx-list-row"><div><b>${esc(student.name)}</b><small>${esc(student.studentNo||'未设置学号')} · ${esc(student.className||(isLongTermAbsent(student)?'长期不来':'未分班'))}</small></div><div><span class="wx-status ${pref?'wx-intent':''}">${pref?'已登记意向 · 未绑定微信':'未登记 · 未绑定微信'}</span><button type="button" class="mini-btn" data-wx-student="${student.id}">通知设置</button></div></article>`}).join(''):rows.map(item=>`<article class="wx-list-row"><div><b>${esc(item.studentName)} <span class="wx-row-method">${item.makeup?'老师补签':'签到'}</span></b><small>${esc(item.date)} ${esc(item.time)} · ${esc(item.className||'未分班')}</small><small>${esc(item.studentNo)}${item.status==='cancelled'?` · ${esc(wechatCancelReason(item.cancelReason))}`:''}</small></div><div><span class="wx-status ${item.status==='cancelled'?'':'wx-intent'}">${item.status==='prepared'?'仅本地待办 · 尚未发送':'已取消 · 未发送'}</span>${item.status==='prepared'?`<button type="button" class="mini-btn warn" data-wx-cancel="${esc(item.id)}">取消待办</button>`:''}</div></article>`).join('');
  return `${contents||'<div class="wx-empty">暂无记录。系统不会自动补发以前的签到。</div>'}<div class="wx-pagination"><button type="button" class="secondary-btn" data-wx-page="-1" ${wechatNoticePage<=1?'disabled':''}>上一页</button><span>${total} 条 · ${wechatNoticePage} / ${pages}</span><button type="button" class="secondary-btn" data-wx-page="1" ${wechatNoticePage>=pages?'disabled':''}>下一页</button></div>`;
}
function wechatCancelReason(reason){return({attendance_revoked:'原签到已撤销',consent_revoked:'家长接收意向已取消',preparation_disabled:'本地准备已关闭',makeup_disabled:'补签准备已关闭',manual_cancel:'管理员取消待办'})[reason]||'已取消'}
function renderWechatNotices(){
  if(!isAdminAuthenticated)return;
  const store=wechatNoticeStore(),prepared=store.events.filter(item=>item.status==='prepared').length;
  openModal(`<div class="modal-head"><span>${uiIcon('bell')}</span><div><h2>微信家长通知</h2><p>通知接入准备 · 不影响本地签到</p></div></div><div class="wx-offline-banner"><span>尚未接通微信</span><p>当前不会发送任何消息。这里登记的家长意向不等于微信授权或绑定，后续需由家长本人完成微信绑定。已有本地待办以后也不会直接批量发送。</p></div><div class="wx-steps"><div><b>1</b><span>模板审核中</span></div><div><b>2</b><span>通知服务待配置</span></div><div><b>3</b><span>家长微信待绑定</span></div><div><b>4</b><span>测试通过后启用</span></div></div><div class="wx-tabs"><button type="button" data-wx-tab="setup" class="${wechatNoticeTab==='setup'?'active':''}">接入设置</button><button type="button" data-wx-tab="students" class="${wechatNoticeTab==='students'?'active':''}">家长接收意向</button><button type="button" data-wx-tab="events" class="${wechatNoticeTab==='events'?'active':''}">本地待办 ${prepared}</button></div>${wechatNoticeTab==='setup'?`<form id="wxPreparationForm" class="simple-form"><label class="wx-option"><input type="checkbox" id="wxPrepareEnabled" ${store.prepareEnabled?'checked':''}><div><b>生成本地通知待办</b><small>仅对启用后、已登记家长同意的学生新签到生效。不联网、不发送，也不补录旧签到。</small></div></label><label class="wx-option"><input type="checkbox" id="wxIncludeMakeup" ${store.includeMakeup?'checked':''}><div><b>老师补签也生成本地待办</b><small>默认关闭。补签会明确标记为补签，不把补签录入时间当成实际到校时间。</small></div></label><div class="warning-box">关闭准备功能或取消家长意向，会取消相关未发送待办；重新开启不恢复已取消记录。待办最多保留 ${WECHAT_PREPARATION_LIMIT} 条，满额不影响签到，需管理员手动清理。${store.skipped?`已有 ${store.skipped} 次因满额未生成待办。`:''}</div><div class="form-actions"><button type="submit" class="primary-btn">保存本地准备设置</button></div></form>`:`<label class="wx-search"><span>搜索${wechatNoticeTab==='students'?'学生、学号或班级':'学生、班级或日期'}</span><input id="wxNoticeSearch" value="${esc(wechatNoticeSearch)}" placeholder="输入关键词查找"></label><section id="wxNoticeRows">${wechatNoticeRowsMarkup()}</section>${wechatNoticeTab==='events'?'<button type="button" class="secondary-btn" id="wxClearCancelled">清理已取消待办</button>':''}`}<div class="wx-privacy-note">本阶段仅本机保存。不收集 AppSecret，不上传照片、人脸特征、联系方式、完整学生档案或课时流水。</div><div class="form-actions"><button type="button" class="secondary-btn" id="closeWechatNotices">关闭</button></div>`);
  $('#modal').classList.add('wx-notice-modal');$('#closeWechatNotices').onclick=closeModal;
  $$('[data-wx-tab]',$('#modal')).forEach(button=>button.onclick=()=>{wechatNoticeTab=button.dataset.wxTab;wechatNoticeSearch='';wechatNoticePage=1;renderWechatNotices()});
  const form=$('#wxPreparationForm');if(form)form.onsubmit=event=>{event.preventDefault();if(!isAdminAuthenticated)return;if(setWechatPreparation($('#wxPrepareEnabled').checked,$('#wxIncludeMakeup').checked)){render();renderWechatNotices();toast('本地准备设置已保存，尚未向微信发送')}};
  const search=$('#wxNoticeSearch');if(search)search.oninput=()=>{wechatNoticeSearch=search.value;wechatNoticePage=1;$('#wxNoticeRows').innerHTML=wechatNoticeRowsMarkup()};
  const rows=$('#wxNoticeRows');if(rows)rows.onclick=event=>{
    if(!isAdminAuthenticated)return;
    const button=event.target.closest('[data-wx-student],[data-wx-cancel],[data-wx-page]');if(!button||button.disabled)return;
    if(button.dataset.wxStudent!==undefined)return openWechatStudentSettings(Number(button.dataset.wxStudent),true);
    if(button.dataset.wxPage!==undefined){wechatNoticePage+=Number(button.dataset.wxPage);rows.innerHTML=wechatNoticeRowsMarkup();return}
    const item=store.events.find(item=>item.id===button.dataset.wxCancel);if(!item||item.status!=='prepared')return;
    openDangerConfirmation({icon:'—',title:'取消本地通知待办',description:'仅取消通知准备，不撤销签到或返还课时',subject:`${item.studentName} · ${item.date}`,impactTitle:'这条待办将不再使用',impactText:'原签到、周报和课时记录保持不变。',confirmLabel:'取消待办',onConfirm:()=>{cancelWechatAttendance(item.attendanceId,'manual_cancel');if(!saveState())return false;render();renderWechatNotices()}});
  };
  const clear=$('#wxClearCancelled');if(clear)clear.onclick=()=>openDangerConfirmation({icon:'—',title:'清理已取消待办',description:'仅清理本地通知准备记录',subject:`${store.events.filter(item=>item.status==='cancelled').length} 条已取消记录`,impactTitle:'已取消待办将从列表删除',impactText:'未取消的待办、学生、签到和课时均不受影响。此操作不会向家长发送消息。',confirmLabel:'确认清理',onConfirm:()=>{store.events=store.events.filter(item=>item.status!=='cancelled');if(!saveState())return false;render();renderWechatNotices()}});
}
function openWechatStudentSettings(studentId,returnToCenter=false){
  if(!isAdminAuthenticated)return toast('请先解锁后台',true);
  const student=studentById(studentId);if(!student)return;
  const preference=wechatStudentPreference(student);
  openModal(`<div class="modal-head"><span>${uiIcon('users')}</span><div><h2>家长通知意向</h2><p>${esc(student.name)} · ${esc(student.studentNo)}</p></div></div><div class="wx-offline-banner"><span>未绑定微信 · 不会发送</span><p>这里只登记家长接收意向，不会通过姓名或手机号自动找到家长微信。正式接入后还需家长本人关注服务号、验证并确认绑定。</p></div><form id="wxStudentConsentForm" class="simple-form"><label class="field"><span>家长称呼</span><select id="wxGuardian">${['家长','父亲','母亲','监护人'].map(label=>`<option ${preference&&preference.guardian===label?'selected':''}>${label}</option>`).join('')}</select></label><label class="wx-option"><input type="checkbox" id="wxStudentConsent" ${preference?'checked':''}><div><b>家长已知情并同意接收该生签到通知</b><small>请在真实取得家长同意后登记。取消勾选将取消该生所有本地未发送待办，重新登记不补发历史签到。</small></div></label><div class="form-actions"><button type="button" class="secondary-btn" id="wxConsentCancel">返回</button><button type="submit" class="primary-btn">保存意向</button></div></form>`);
  $('#modal').classList.add('wx-notice-modal');$('#wxConsentCancel').onclick=()=>returnToCenter?openWechatNotices('students'):closeModal();
  $('#wxStudentConsentForm').onsubmit=event=>{event.preventDefault();if(!isAdminAuthenticated)return;const result=setWechatStudentConsent(student.id,$('#wxStudentConsent').checked,$('#wxGuardian').value);if(!result.ok)return toast(result.error,true);render();if(returnToCenter)openWechatNotices('students');else closeModal();toast('家长意向已保存在本机，尚未绑定或发送微信')};
}
