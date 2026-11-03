/* Durable, opt-in minimal events. Credentials stay in the native Android layer. */
const DEVICE_SYNC_LIMIT=2000,DEVICE_SYNC_REF_LIMIT=5000;
const deviceSyncRequests=new Map();
let deviceSyncTimer=null,deviceSyncFlight=false,deviceSyncPreview=null;
function syncNonce(){
  if(!window.crypto||typeof window.crypto.getRandomValues!=='function')throw Error('当前环境不能安全生成同步编号');
  return Array.from(window.crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
}
function nativeSyncProfile(){try{return window.AndroidBridge&&typeof AndroidBridge.getDeviceSyncProfile==='function'?JSON.parse(AndroidBridge.getDeviceSyncProfile()):{configured:false}}catch(_){return{configured:false}}}
function syncChinaDay(seconds){return new Date((seconds+8*3600)*1000).toISOString().slice(0,10)}
function syncUnits(value){const units=Math.round(Number(value)*100);if(!Number.isFinite(Number(value))||!Number.isSafeInteger(units)||Math.abs(Number(value)*100-units)>.00001||units< -1000000||units>10000000)throw Error('课时不在同步支持范围内');return units}
function syncPlanMapping(plan){
  if(!plan||plan.protocol_version!==1||!plan.access||!plan.access.enabled||!plan.nativeProfile||!plan.nativeProfile.configured||plan.device_id!==plan.nativeProfile.deviceId||!Array.isArray(plan.classes)||!Array.isArray(plan.students)||!Array.isArray(plan.lessons)||plan.classes.length>100||plan.students.length>2000||plan.lessons.length>500)throw Error('服务器授权清单无效');
  const id=value=>typeof value==='string'&&/^[0-9a-f]{32}$/.test(value),unique=(rows,key)=>rows.every(r=>r&&id(r[key]))&&new Set(rows.map(r=>r[key])).size===rows.length;
  if(!id(plan.org_id)||!id(plan.device_id)||!unique(plan.classes,'id')||!unique(plan.students,'id')||!unique(plan.lessons,'id'))throw Error('清单编号不完整或重复');
  const classes={},students={},lessons={},rosters={};let unmatched=0;
  plan.classes.forEach(remote=>{const local=state.classes.filter(c=>c.name===remote.name);if(local.length===1&&state.classes.filter(c=>c.id===local[0].id).length===1)classes[String(local[0].id)]={id:remote.id,name:remote.name};else unmatched++});
  plan.students.forEach(remote=>{
    if(remote.source_device_id&&remote.source_device_id!==plan.device_id){unmatched++;return;}
    const local=state.students.filter(s=>String(s.studentNo||'')===remote.external_ref&&remote.external_ref);
    if(local.length!==1||state.students.filter(s=>s.id===local[0].id).length!==1||local[0].name!==remote.name){unmatched++;return;}
    const course=state.classes.filter(c=>c.name===local[0].className),s=local[0];
    if(course.length!==1||!classes[course[0].id]||classes[course[0].id].id!==remote.class_id||!Number.isSafeInteger(remote.source_revision)||remote.source_revision<0){unmatched++;return;}
    const outstanding=state.deviceSync&&state.deviceSync.deviceId===plan.device_id&&state.deviceSync.queue.some(e=>e.status!=='accepted'&&(e.lane===remote.id||e.payload?.student_id===remote.id));
    if(!Number.isSafeInteger(remote.remaining_units)||(!outstanding&&syncUnits(s.hours)!==remote.remaining_units)){unmatched++;return;}
    students[String(s.id)]={id:remote.id,studentNo:s.studentNo,name:s.name,classLocalId:course[0].id,className:s.className,classId:remote.class_id,revision:remote.source_revision};
  });
  let rosterTotal=0;
  plan.lessons.forEach(remote=>{
    if(!Number.isSafeInteger(remote.starts_at)||!Number.isSafeInteger(remote.ends_at)||remote.ends_at<=remote.starts_at||!Array.isArray(remote.student_ids)||remote.student_ids.some(s=>!id(s))||new Set(remote.student_ids).size!==remote.student_ids.length)throw Error('课次确认名单无效');
    rosterTotal+=remote.student_ids.length;if(rosterTotal>10000)throw Error('确认名单过多');
    const localIds=Object.keys(classes).filter(key=>classes[key].id===remote.class_id);if(localIds.length!==1)return;
    const key=localIds[0]+':'+syncChinaDay(remote.starts_at);if(lessons[key])throw Error('同一班级同一天有多个服务器课次，请先核对，不自动合并');
    lessons[key]={id:remote.id,startsAt:remote.starts_at,endsAt:remote.ends_at};rosters[remote.id]=remote.student_ids;
  });
  return{classes,students,lessons,rosters,unmatched};
}
function activateDeviceSync(plan){
  if(!isAdminAuthenticated)return{ok:false,error:'请先解锁后台'};
  try{
    const mapping=syncPlanMapping(plan),old=state.deviceSync,profile=plan.nativeProfile;
    if(!Object.keys(mapping.students).length)throw Error('没有学号、姓名、班级均核对一致的学生，未启用同步');
    const outstanding=old&&old.queue.some(e=>e.status!=='accepted');
    if(outstanding&&(old.deviceId!==plan.device_id||old.orgId!==plan.org_id||old.endpoint!==profile.endpoint))throw Error('原设备仍有未完成记录，不能换到另一台设备或服务器');
    if(outstanding){
      for(const [key,value]of Object.entries(old.students)){if(mapping.students[key]&&['id','studentNo','name','classId'].some(k=>mapping.students[key][k]!==value[k]))throw Error('原学生映射已变化，请先核对未完成记录');}
    }
    const carry=old&&old.deviceId===plan.device_id&&old.orgId===plan.org_id&&old.endpoint===profile.endpoint;
    const revisions=carry?{...old.revisions}:{};Object.entries(mapping.students).forEach(([k,s])=>{revisions[k]=Math.max(revisions[k]||0,s.revision)});
    state.deviceSync={version:1,generation:syncNonce(),profileId:profile.profileId,deviceId:plan.device_id,orgId:plan.org_id,endpoint:profile.endpoint,captureEnabled:true,paused:false,allowLocalLeaves:Boolean(plan.access.allow_local_leaves),...mapping,revisions,queue:carry?old.queue:[],attendanceRefs:carry?old.attendanceRefs:{},leaveRefs:carry?old.leaveRefs:{},skipped:carry?old.skipped:0,problem:'',activatedAt:new Date().toISOString()};
    return saveState()?{ok:true}:{ok:false,error:'配置未能保存，原同步设置已保留'};
  }catch(error){return{ok:false,error:error.message||'未能启用同步'}}
}
function prepareDeviceSyncForSave(before){
  const store=state.deviceSync;if(!store||!store.captureEnabled||!before.deviceSync||before.deviceSync.generation!==store.generation)return;
  try{
    const pending=[],revisions={...store.revisions},now=new Date().toISOString();let skipped=0;
    const emit=(kind,origin,payload,dependency=null,lane='')=>{if(store.queue.some(e=>e.origin===origin)||pending.some(e=>e.origin===origin))return;if(store.queue.filter(e=>e.status!=='accepted').length+pending.length>=DEVICE_SYNC_LIMIT){skipped++;return;}const id=syncNonce();pending.push({id,origin,kind,lane:payload.student_id||lane,payload:{event_ref:'ds:'+id,...payload},dependency,status:'pending',attempts:0,nextAt:0,error:'',wire:''})};
    const binding=id=>{const b=store.students[String(id)],local=state.students.filter(s=>s.id===Number(id));if(!b||local.length!==1||local[0].studentNo!==b.studentNo||local[0].name!==b.name||local[0].className!==b.className)throw Error('学生档案或所属班级已变化，需重新核对映射');return{binding:b,student:local[0]}};
    const lesson=(b,date)=>{const found=store.lessons[b.classLocalId+':'+date];if(!found||!store.rosters[found.id]||!store.rosters[found.id].includes(b.id))throw Error('没有匹配的实际课次或应到名单，需核对服务器课表');return found};
    const revision=id=>{revisions[id]=(revisions[id]||0)+1;return revisions[id]};
    const oldAttendance=new Map(before.attendance.map(a=>[a.id,a])),newAttendance=new Map(state.attendance.map(a=>[a.id,a]));
    const attendanceChanged=new Set();
    state.attendance.forEach(a=>{if(oldAttendance.has(a.id))return;attendanceChanged.add(a.studentId);try{const {binding:b,student}=binding(a.studentId),l=lesson(b,a.date),occurred=a.makeup?null:a.date+'T'+a.time+':00+08:00';if(occurred!==null&&!Number.isFinite(Date.parse(occurred)))throw Error('签到时间不完整，需核对');emit('attendance','attendance:'+a.id,{lesson_id:l.id,student_id:b.id,occurred_at:occurred,consumed_units:100,remaining_units:syncUnits(student.hours),student_revision:revision(a.studentId),source:a.makeup?'makeup':'live'},null);}catch(error){skipped++;store.problem=error.message}});
    before.attendance.forEach(a=>{if(newAttendance.has(a.id))return;attendanceChanged.add(a.studentId);try{const {binding:b,student}=binding(a.studentId);if(!store.attendanceRefs[a.id]&&!store.queue.some(e=>e.origin==='attendance:'+a.id))throw Error('原签到不是本次联网后生成的记录，撤销需人工核对');emit('revoke','revoke:'+a.id,{reason:'平板撤销原签到'},'attendance:'+a.id,b.id);emit('balance','refund:'+a.id,{student_id:b.id,occurred_at:now,remaining_units:syncUnits(student.hours),student_revision:revision(a.studentId),source:'live'});}catch(error){skipped++;store.problem=error.message}});
    state.students.forEach(s=>{const old=before.students.find(x=>x.id===s.id);if(!old||Number(old.hours)===Number(s.hours)||attendanceChanged.has(s.id))return;try{const {binding:b}=binding(s.id);emit('balance','balance:'+s.id+':'+revision(s.id),{student_id:b.id,occurred_at:now,remaining_units:syncUnits(s.hours),student_revision:revisions[s.id],source:'live'});}catch(error){skipped++;store.problem=error.message}});
    const previous=new Map((before.settings.studentLeaves||[]).map(l=>[l.id,l]));
    (state.settings.studentLeaves||[]).forEach(l=>{const old=previous.get(l.id);if(old&&old.status===l.status)return;try{const {binding:b}=binding(l.studentId),session=lesson(b,l.date);if(!store.allowLocalLeaves)throw Error('管理员尚未委托此设备同步代办请假');if(!old&&l.status==='approved')emit('leave','leave:'+l.id,{lesson_id:session.id,student_id:b.id,local_leave_ref:String(l.id),occurred_at:l.createdAt,reason:l.reason,operator_label:l.recordedBy});else if(old&&old.status==='approved'&&l.status==='cancelled'){if(!store.leaveRefs[l.id]&&!store.queue.some(e=>e.origin==='leave:'+l.id))throw Error('原请假来自联网前，撤销需人工核对');emit('cancel_leave','cancel_leave:'+l.id,{occurred_at:l.cancelledAt,reason:l.cancelReason,operator_label:l.cancelledBy},'leave:'+l.id,b.id);}}catch(error){skipped++;store.problem=error.message}});
    store.queue=store.queue.filter(e=>e.status!=='accepted').concat(pending);store.revisions=revisions;store.skipped+=skipped;
  }catch(_){store.skipped++;store.problem='未能完整生成同步待办，请核对；本机业务记录仍正常保存'}
}
function nativeDeviceSyncRequest(kind,payload){
  const profile=nativeSyncProfile();if(!profile.configured)return Promise.reject(Error('尚未配置安卓同步连接'));
  return new Promise((resolve,reject)=>{const id='r'+syncNonce(),timer=setTimeout(()=>{deviceSyncRequests.delete(id);reject(Error('连接超时，本机记录已保留'))},20000);deviceSyncRequests.set(id,{resolve,reject,timer});try{AndroidBridge.requestDeviceSync(id,profile.profileId,kind,JSON.stringify(payload||{}))}catch(_){clearTimeout(timer);deviceSyncRequests.delete(id);reject(Error('安卓同步通道暂不可用'))}});
}
window.onDeviceSyncNativeResult=(id,raw)=>{
  let result;try{result=typeof raw==='string'?JSON.parse(raw):raw}catch(_){return;}
  if(id==='configure'){
    if(!result.ok){toast(result.error||'连接配置未通过',true);return;}
    deviceSyncPreview=result.data;if(isAdminAuthenticated)showDeviceSyncPlan(deviceSyncPreview);else toast('连接已验证，请解锁后台后核对映射再启用');return;
  }
  const task=deviceSyncRequests.get(id);if(!task)return;clearTimeout(task.timer);deviceSyncRequests.delete(id);task.resolve(result);
};
function scheduleDeviceSync(){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive())return;
  if(deviceSyncTimer!==null||typeof window.setTimeout!=='function')return;
  const s=state.deviceSync;if(!s||!s.captureEnabled||s.paused)return;
  deviceSyncTimer=window.setTimeout(()=>{deviceSyncTimer=null;pumpDeviceSync()},400);
}
async function pumpDeviceSync(){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive())return;
  const s=state.deviceSync,profile=nativeSyncProfile();if(deviceSyncFlight||!s||!s.captureEnabled||s.paused||!profile.configured||profile.deviceId!==s.deviceId||profile.profileId!==s.profileId)return;
  deviceSyncFlight=true;const generation=s.generation;
  try{
    for(let i=0;i<25;i++){
      if(!state.deviceSync||state.deviceSync.generation!==generation||state.deviceSync.paused)break;
      const current=state.deviceSync,entry=current.queue.find((e,index)=>e.status==='pending'&&e.nextAt<=Date.now()&&!current.queue.slice(0,index).some(old=>old.status!=='accepted'&&old.lane===e.lane));if(!entry)break;
      if(entry.dependency&&!entry.wire){const [kind,localId]=entry.dependency.split(':'),ref=(kind==='attendance'?current.attendanceRefs:current.leaveRefs)[localId];if(!ref){entry.status='blocked';entry.error='等待原签到或请假先同步成功';if(!saveState())break;continue;}entry.payload[kind==='attendance'?'attendance_id':'leave_id']=ref;}
      if(!entry.wire){entry.wire=JSON.stringify({kind:entry.kind,payload:entry.payload});if(!saveState())break;}
      let result;try{result=await nativeDeviceSyncRequest('event',JSON.parse(entry.wire))}catch(error){result={ok:false,status:0,error:error.message}}
      if(!state.deviceSync||state.deviceSync.generation!==generation)break;
      const receipt=result.data;
      if(result.ok&&receipt&&receipt.accepted===true&&receipt.device_id===s.deviceId&&receipt.event_ref===entry.payload.event_ref&&receipt.kind===entry.kind&&(!['attendance','leave'].includes(entry.kind)||/^[0-9a-f]{32}$/.test(receipt.id||''))){
        const [kind,localId]=entry.origin.split(':');
        if(kind==='attendance'||kind==='leave'){const refs=kind==='attendance'?current.attendanceRefs:current.leaveRefs;if(Object.keys(refs).length>=DEVICE_SYNC_REF_LIMIT&&!refs[localId]){entry.status='blocked';entry.error='回执保留数量已到上限，请管理员核对';if(!saveState())break;continue;}refs[localId]=receipt.id;}
        entry.status='accepted';entry.error='';current.lastSuccess=new Date().toISOString();
        current.queue.forEach(e=>{if(e.status==='blocked'&&e.dependency===entry.origin){e.status='pending';e.error='';e.nextAt=0}});
      }else if(result.status===401||result.status===403){entry.status='blocked';entry.error='设备授权已失效或范围已变，请管理员核对';current.paused=true;}
      else if(result.status>=400&&result.status<500&&result.status!==429){entry.status='blocked';entry.error='服务器资料存在差异或请求未通过，请核对后再重试';}
      else{entry.attempts++;entry.nextAt=Date.now()+Math.min(300000,5000*Math.pow(2,Math.min(entry.attempts-1,6)))+Math.floor(Math.random()*1000);entry.error=result.ok?'服务器回执不完整，未标记成功':result.error||'连接暂不可用，稍后重试';}
      if(!saveState())break;if(!result.ok&&(result.status===0||result.status>=500||result.status===429))break;
    }
  }finally{deviceSyncFlight=false;refreshDeviceSyncStatus();}
}
function deviceSyncSummary(){
  const canonical=state.managementSync;
  if(canonical?.enabled===true&&canonical.mode==='server_authoritative'){
    const rows=Array.isArray(canonical.queue)?canonical.queue:[],waiting=rows.filter(e=>e.status==='pending').length,blocked=rows.filter(e=>e.status==='blocked').length;
    return `网页主管理已启用 · 待上传 ${waiting} 条 · 待核对 ${blocked} 条`;
  }
  const s=state.deviceSync;if(!s||s.captureEnabled!==true)return'尚未启用 · 不上传资料';
  const rows=Array.isArray(s.queue)?s.queue:[],waiting=rows.filter(e=>e.status==='pending').length,blocked=rows.filter(e=>e.status==='blocked').length;
  return`${s.paused?'已暂停':'已启用'} · 待同步 ${waiting} 条 · 待核对 ${blocked} 条${s.skipped?` · 未完整生成 ${s.skipped} 次`:''}`;
}
function deviceSyncSettingsMarkup(){
  if(state.managementSync?.enabled===true&&state.managementSync.mode==='server_authoritative')return `<section class="panel"><div class="panel-head"><div><h2>网页与平板同步</h2><p>学生、班级和课时由网页管理，平板负责签到</p></div></div><div class="setting-row"><div><b id="canonicalSyncStatus">${esc(deviceSyncSummary())}</b><small>签到先保存在平板，联网后核对上传；网页审批和任课变更保存到平板后生效。</small></div></div></section>`;
  return `<section class="panel"><div class="panel-head"><div><h2>平板与服务器同步</h2><p>本机先保存，后台再提交；默认关闭</p></div><span class="tag">微信仍未接通</span></div><div class="setting-row"><div><b id="deviceSyncStatus">${esc(deviceSyncSummary())}</b><small>只同步核对后的签到、课时及获委托的代办请假，不上传照片、人脸、联系方式或整库。</small></div><button type="button" class="primary-btn" id="openDeviceSync">管理同步</button></div></section>`;
}
function refreshDeviceSyncStatus(){const n=$('#deviceSyncStatus');if(n)n.textContent=deviceSyncSummary();const m=$('#syncQueueSummary');if(m)m.textContent=deviceSyncSummary()}
function showDeviceSyncPlan(plan){
  if(!isAdminAuthenticated)return;
  let mapping;try{mapping=syncPlanMapping(plan)}catch(error){toast(error.message,true);return;}
  deviceSyncPreview=plan;
  openModal(`<div class="modal-head"><span>⇄</span><div><h2>核对服务器映射</h2><p>相同姓名不会自动合并，以唯一学号、姓名及班级共同核对。</p></div></div><div class="warning-box">可匹配 ${Object.keys(mapping.students).length} 名学生、${Object.keys(mapping.classes).length} 个班级、${Object.keys(mapping.lessons).length} 个实际课次；${mapping.unmatched} 项未匹配，不会猜测关联。核对启用后只记录新变化，不自动补传已有签到或历史请假。</div><div class="sync-map-list">${Object.entries(mapping.students).slice(0,40).map(([id,s])=>`<p><b>${esc(s.name)}</b> · ${esc(s.studentNo)} · ${esc(s.className)}</p>`).join('')}${Object.keys(mapping.students).length>40?'<p>仅预览前40人，请与管理员的完整名单核对。</p>':''}</div><p>平板代办请假同步：${plan.access.allow_local_leaves?'已由管理员委托；办理姓名仅作本机备注，不冒充服务器教师登录':'尚未授予，不会自动同步请假'}</p><form id="activateDeviceSyncForm" class="simple-form"><label class="leave-confirm"><input type="checkbox" id="syncMappingConfirmed" required>我已核对学号、班级、课次和设备授权，确认开始记录并同步后续新变化</label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelSyncMapping">暂不启用</button><button type="submit" class="primary-btn">确认启用新事件同步</button></div></form>`);
  $('#cancelSyncMapping').onclick=openDeviceSync;$('#activateDeviceSyncForm').onsubmit=e=>{e.preventDefault();if(!$('#syncMappingConfirmed').checked)return;const r=activateDeviceSync(plan);if(!r.ok)return toast(r.error,true);openDeviceSync();toast('已启用新事件同步，未补发旧签到')};
}
function openDeviceSync(){
  if(!isAdminAuthenticated)return toast('请先解锁后台',true);
  const s=state.deviceSync,profile=nativeSyncProfile(),pending=s?s.queue.filter(e=>e.status!=='accepted'):[];
  openModal(`<div class="modal-head"><span>⇄</span><div><h2>平板同步管理</h2><p id="syncQueueSummary">${esc(deviceSyncSummary())}</p></div></div><div class="warning-box">签到、请假和课时先保存在本机。网络失败不会撤销本机业务或重复扣课时；恢复备份后默认停止同步，需重新核对。当前仍不发送真实微信。</div><p>安卓连接：${profile.configured?esc(profile.endpoint)+' · 已保存设备凭证（不显示）':'尚未配置'}<br>${s?`映射学生 ${Object.keys(s.students).length} 人 · 最近成功 ${esc(s.lastSuccess||'尚无')}`:'核对服务器映射之前，不创建上传队列。'}</p>${s&&s.skipped?`<p class="warning-box">同步资料不完整：已有 ${s.skipped} 次变化未生成待办。${esc(s.problem||'请核对，不自动猜测重建历史记录。')}</p>`:''}<div class="form-actions"><button type="button" class="secondary-btn" id="configureDeviceSync">配置或重新授权连接</button><button type="button" class="secondary-btn" id="reviewDeviceSyncPlan" ${profile.configured?'':'disabled'}>刷新并核对映射</button>${s?`<button type="button" class="secondary-btn" id="pauseDeviceSync">${s.paused?'恢复后台提交':'暂停后台提交'}</button><button type="button" class="secondary-btn" id="retryDeviceSync">核对后重试待办</button>`:''}</div><section class="sync-map-list">${pending.slice(0,40).map(e=>`<p><b>${esc(({attendance:'签到',balance:'课时快照',revoke:'撤销签到',leave:'平板代办请假',cancel_leave:'撤销本机请假'})[e.kind])}</b> · ${e.status==='blocked'?'待核对':'等待重试'}<small>${esc(e.error||'已在本机持久保存')}</small></p>`).join('')||'<p>暂无等待上传的事件。</p>'}</section><div class="form-actions"><button type="button" class="secondary-btn" id="closeDeviceSync">关闭</button></div>`);
  $('#configureDeviceSync').onclick=()=>{if(!isAdminAuthenticated)return;if(!window.AndroidBridge||typeof AndroidBridge.configureDeviceSync!=='function')return toast('请在安卓App中配置连接；浏览器演示不会保存设备凭证',true);AndroidBridge.configureDeviceSync()};
  $('#reviewDeviceSyncPlan').onclick=async()=>{try{const result=await nativeDeviceSyncRequest('plan');if(!result.ok)return toast(result.error,true);showDeviceSyncPlan(result.data)}catch(error){toast(error.message,true)}};
  const pause=$('#pauseDeviceSync');if(pause)pause.onclick=()=>{if(!isAdminAuthenticated)return;s.paused=!s.paused;if(saveState())openDeviceSync()};
  const retry=$('#retryDeviceSync');if(retry)retry.onclick=()=>{if(!isAdminAuthenticated)return;const p=nativeSyncProfile();if(p.profileId!==s.profileId)return toast('连接已变更，请先刷新核对映射',true);s.queue.forEach(e=>{if(e.status!=='accepted'){e.status='pending';e.nextAt=0;e.error=''}});s.paused=false;if(saveState())openDeviceSync()};
  $('#closeDeviceSync').onclick=closeModal;
}
function validateDeviceSyncBackup(data){
  const s=data.deviceSync;if(s===undefined)return;
  if(!s||typeof s!=='object'||s.version!==1||!Array.isArray(s.queue)||s.queue.length>DEVICE_SYNC_LIMIT||typeof s.students!=='object'||typeof s.attendanceRefs!=='object'||typeof s.leaveRefs!=='object')throw Error('备份中的同步资料格式无效');
  // Never restore credentials, bindings or jobs. A new explicit review starts at the restored state.
  const encoded=JSON.stringify(s);if(/"(?:token|password|appSecret|secret|ciphertext)"\s*:/i.test(encoded))throw Error('备份不能携带服务器凭证');
}
if(typeof window.addEventListener==='function'){
  window.addEventListener('online',scheduleDeviceSync);window.addEventListener('focus',scheduleDeviceSync);
  if(typeof window.setInterval==='function')window.setInterval(()=>{if(document.visibilityState!=='hidden')scheduleDeviceSync()},10000);
  window.addEventListener('load',scheduleDeviceSync);
}
