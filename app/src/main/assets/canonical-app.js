/* Dormant until an explicitly reconciled server-authoritative migration.
 * No credentials, automatic enrolment, legacy snapshot import, or mode switch.
 */
let canonicalStore=null,canonicalRuntime=null,canonicalTimer=null,canonicalRebindPending=false;
const canonicalRequests=new Map();
function canonicalModeActive(){return typeof state!=='undefined'&&state.managementSync?.enabled===true&&state.managementSync.mode==='server_authoritative';}
function canonicalQueueStatus(){
  if(!canonicalModeActive())return null;
  const rows=Array.isArray(state.managementSync.queue)?state.managementSync.queue:[];
  return{pending:rows.filter(entry=>entry.status==='pending').length,blocked:rows.filter(entry=>entry.status==='blocked').length,snapshotAt:Number(state.managementSync.snapshotAt||0)};
}
function canonicalProfile(){try{return JSON.parse(AndroidBridge.getCanonicalSyncProfile());}catch(_){return{configured:false};}}
function canonicalStoreForApp(){
  if(!canonicalStore){
    canonicalStore=ManagementSnapshotStore.create({storage:localStorage,key:STORAGE_KEY,onPublish:(next,raw)=>{
      state=next;committedStateSnapshot=raw;invalidatePageCache();
      // Preserve existing local backups/reminders without invoking legacy save
      // or mirror capture hooks. These observers do not change commit success.
      try{syncNativeReminders();createNativeBackup('每日自动备份',false);}catch(_){}
    }});
  }
  return canonicalStore;
}
function canonicalGuardedStore(){
  const base=canonicalStoreForApp();
  const clean=()=>{if(JSON.stringify(state)!==committedStateSnapshot){const e=new Error('unsaved_changes');e.code='unsaved_changes';throw e;}};
  return{snapshot:()=>base.snapshot(),read(){clean();return base.read();},compareAndSwap(a,b){clean();return base.compareAndSwap(a,b);}};
}
function canonicalOrdinarySave(){
  try{
    const store=canonicalStoreForApp();if(!canonicalProfile().configured)throw Error('activation_pending');
    const edit=store.beginEdit();
    try{
      Object.keys(edit.value).forEach(k=>delete edit.value[k]);Object.assign(edit.value,JSON.parse(JSON.stringify(state)));
      store.saveEdit(edit.token);scheduleCanonicalSync();return true;
    }catch(error){store.cancelEdit(edit.token);throw error;}
  }catch(_){
    // Do not restore a stale pre-sync committedStateSnapshot over newer disk.
    try{const raw=canonicalStore.read();state=JSON.parse(raw);committedStateSnapshot=raw;invalidatePageCache();}catch(_){}
    toast('本次修改未保存。学生、班级和课时请在网页处理；资料有差异时请先核对。',true);return false;
  }
}
function canonicalRecordStudentAttendance(studentId,lessonId=''){
  if(canonicalRebindPending||state.managementSync?.rebindPending)return{ok:false,error:'设备正在安全换绑，请完成名单核对后再签到'};
  try{
    const sync=state.managementSync,p=canonicalProfile();
    if(!p.configured||p.orgId!==sync.orgId||p.deviceId!==sync.deviceId||p.generation!==sync.generation||p.profileId!==sync.profileId)throw Error('profile');
    const students=Object.entries(sync.students).filter(([,b])=>b.localId===studentId);
    if(students.length!==1)throw Error('mapping');
    const remoteId=students[0][0],today=localDate(),candidates=Object.entries(sync.lessons).filter(([,l])=>l.date===today&&(sync.protocol===2||l.classId===students[0][1].classId)&&l.studentIds?.includes(remoteId));
    const available=candidates.filter(([id,l])=>l.state==='scheduled'&&sync.facts?.[id]?.[remoteId]?.status==='missing');
    const lessons=lessonId?candidates.filter(([id])=>id===lessonId):available;
    if(!lessonId&&lessons.length>1)return{ok:false,code:'lesson_choice',error:'这位学生今天有多个课次，请选择本节课'};
    if(lessons.length!==1)throw Error('lesson');
    // Ref is generated BEFORE the durable write; it remains in the saved queue.
    const eventRef='checkin:'+syncNonce(),now=Math.floor(Date.now()/1000);
    const result=ManagementCheckinQueue.record(canonicalGuardedStore(),{studentId:remoteId,lessonId:lessons[0][0],occurredAt:now,eventRef},now);
    if(!result.ok)return{ok:false,error:result.code==='attendance_changed'?'该学生已签到、已请假或应到资料已变化，请刷新核对':'签到未保存，原资料保留，请核对同步状态或存储空间'};
    scheduleCanonicalSync();listPages.attendance=1;
    const student=studentById(studentId),localClass=sync.classes?.[lessons[0][1].classId];
    const course=localClass&&classById(localClass.localId);
    return{ok:true,attendanceId:result.attendanceId,student:course?{...student,className:course.name,teacher:course.teacher}:student};
  }catch(_){return{ok:false,error:'本机与网页连接或课堂名单未核对，尚未扣课，请联系管理员'};}
}
function canonicalNativeRequest(kind,payload){
  const profile=canonicalProfile();if(!profile.configured)return Promise.reject(Error('profile'));
  return new Promise((resolve,reject)=>{
    const id='canonical:'+syncNonce(),timer=setTimeout(()=>{canonicalRequests.delete(id);reject(Error('timeout'));},30000);
    canonicalRequests.set(id,{resolve,reject,timer});
    try{AndroidBridge.requestCanonicalSync(id,profile.profileId,kind,JSON.stringify(payload));}
    catch(_){clearTimeout(timer);canonicalRequests.delete(id);reject(Error('native'));}
  });
}
window.onCanonicalSyncNativeResult=(id,raw)=>{
  if(id==='runtime-activation'){
    try{const r=typeof raw==='string'?JSON.parse(raw):raw;if(r.ok&&r.data?.activated){finishRuntimeActivation();return;}
      toast('开通码无效、已使用或已过期，请由管理员重新核对',true);}catch(_){}return;
  }
  if(id==='runtime-reissue'){
    try{const r=typeof raw==='string'?JSON.parse(raw):raw;if(r.ok&&r.data?.activated&&r.data?.reissued){finishRuntimeReissue();return;}
      canonicalRebindPending=false;if(!r.data?.cancelled)toast('换绑未完成，请核对新开通码；原设备资料仍保留',true);}catch(_){canonicalRebindPending=false;}return;
  }
  if(id==='activation'){
    try{const r=typeof raw==='string'?JSON.parse(raw):raw;
      if(r.ok&&r.data?.prepared){finishCanonicalActivation();return;}
      const message=r.ok&&r.data?.activated?'网页与平板同步已启用':'连接尚未完成，当前资料已保留，请点“启用／完成同步”重试';
      toast(message,!r.ok);const el=document.getElementById('canonicalEnrollmentStatus');if(el)el.textContent=message;
      if(r.ok&&r.data?.activated){canonicalRuntime=null;scheduleCanonicalSync();}
    }catch(_){}return;
  }
  if(id==='capture'){
    try{const r=typeof raw==='string'?JSON.parse(raw):raw;const message=r.ok?'当前资料核对包已保存到本机，尚未上传或切换同步':'资料采集未完成，请重新保存并核对';toast(message,!r.ok);const el=document.getElementById('canonicalEnrollmentStatus');if(el)el.textContent=message;}catch(_){}return;
  }
  if(id==='enrollment'){
    try{const r=typeof raw==='string'?JSON.parse(raw):raw;const message=r.ok?(r.data?.staged?'新连接已核对并暂存，尚未切换资料':'连接申请已准备，请交由管理员核对'):'连接未通过核对，原资料和连接保持不变';toast(message,!r.ok);const el=document.getElementById('canonicalEnrollmentStatus');if(el)el.textContent=message;}catch(_){}return;
  }
  const task=canonicalRequests.get(id);if(!task)return;clearTimeout(task.timer);canonicalRequests.delete(id);
  try{task.resolve(typeof raw==='string'?JSON.parse(raw):raw);}catch(_){task.reject(Error('response'));}
};
function canonicalEnrollmentSettingsMarkup(){
  const p=canonicalProfile(),active=canonicalModeActive();
  return `<details class="settings-card"><summary>网页主管理连接</summary><p id="canonicalEnrollmentStatus">${active?(p.configured?'已启用网页主管理':'新资料已保存，连接确认尚未完成'):p.configured?'设备已开通，等待下载课堂名单':p.staged?'新连接已暂存，尚未切换资料':'尚未准备新连接'}</p><p>新版开通只需管理员生成一次性开通码；原有资料核对流程仍保留供旧迁移使用。成功后网页负责管理，平板负责签到。</p><button data-canonical-enrollment="runtime" ${active?'disabled':''}>${p.configured?'完成新版名单下载':'使用一次性开通码（新版）'}</button> ${active?'<button class="secondary-btn" data-canonical-enrollment="reissue">安全换绑</button>':''} <button class="secondary-btn" data-canonical-enrollment="capture" ${active?'disabled':''}>采集当前核对资料</button> <button class="secondary-btn" data-canonical-enrollment="prepare" ${active?'disabled':''}>准备连接申请</button> <button class="secondary-btn" data-canonical-enrollment="stage" ${active?'disabled':''}>核对加密连接</button> <button class="secondary-btn" data-canonical-enrollment="activate" ${!p.staged&&!p.activationPending||p.configured?'disabled':''}>启用／完成同步</button></details>`;
}
document.addEventListener('click',event=>{
  const action=event.target?.closest?.('[data-canonical-enrollment]')?.getAttribute('data-canonical-enrollment');
  if(action==='runtime')startRuntimeActivation();if(action==='reissue')startRuntimeReissue();if(action==='capture')captureCanonicalEnrollment();if(action==='prepare')prepareCanonicalEnrollment();if(action==='stage')stageCanonicalEnrollment();if(action==='activate')startCanonicalActivation();
  if(event.target?.closest?.('[data-tablet-refresh]')){if(!isAdminAuthenticated)return;canonicalRuntime=null;scheduleCanonicalSync();toast('正在先上传本机待办，再核对最新课堂名单');}
});
async function startRuntimeActivation(){
  if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);
  if(canonicalModeActive())return toast('这台平板已完成开通');
  if(canonicalProfile().configured){await finishRuntimeActivation();return;}
  try{
    const raw=localStorage.getItem(STORAGE_KEY);if(!raw||raw!==committedStateSnapshot||JSON.stringify(state)!==raw)throw Error('unsaved');
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
    if(localStorage.getItem(STORAGE_KEY)!==raw||committedStateSnapshot!==raw||JSON.stringify(state)!==raw)throw Error('changed');
    AndroidBridge.activateRuntime(Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join(''));
  }catch(_){toast('资料尚未保存或已变化，请先保存后重试',true);}
}
async function finishRuntimeActivation(){
  try{
    const response=await canonicalNativeRequest('bootstrap',{});
    if(!response?.ok)throw Error('bootstrap');
    const profile=canonicalProfile(),store=canonicalGuardedStore();
    const result=canonicalModeActive()?ManagementRuntimeBootstrap.apply(store,response.data,profile):ManagementRuntimeBootstrap.initialize(store,response.data,profile);
    if(!result.ok)throw Error(result.code);
    canonicalRuntime=null;toast('平板开通成功，课堂名单已更新');scheduleCanonicalSync();
  }catch(_){toast('设备已保留，名单尚未下载；请核对本地学生姓名、学号和班级后重试',true);}
}
async function startRuntimeReissue(){
  if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);
  if(!canonicalModeActive()||state.managementSync.protocol!==2)return toast('当前设备未使用新版开通方式',true);
  if(canonicalRebindPending)return;
  const profile=canonicalProfile(),sync=state.managementSync;
  if((!profile.configured&&!profile.rebindAvailable)||profile.orgId!==sync.orgId||profile.deviceId!==sync.deviceId)return toast('设备连接资料不一致，请联系管理员',true);
  if(profile.configured&&(profile.generation!==sync.generation||profile.profileId!==sync.profileId)){finishRuntimeReissue();return;}
  try{
    const raw=canonicalGuardedStore().read();
    if(raw!==committedStateSnapshot||JSON.stringify(state)!==raw)throw Error('unsaved');
    canonicalRebindPending=true;AndroidBridge.reissueRuntime();
  }catch(_){canonicalRebindPending=false;toast('资料有未保存变化，请先核对后再换绑',true);}
}
function finishRuntimeReissue(){
  canonicalRebindPending=true;
  try{
    const profile=canonicalProfile();if(!profile.configured)throw Error('profile');
    const result=ManagementRuntimeBootstrap.rebind(canonicalGuardedStore(),null,profile);if(!result.ok)throw Error(result.code);
    canonicalRuntime=null;canonicalRebindPending=false;
    toast('已更新设备凭据，将先补传原签到，再刷新名单');scheduleCanonicalSync();
  }catch(_){toast('新凭据已保留，请重新打开设备设置继续换绑；本机记录仍保留',true);}
  finally{canonicalRebindPending=false;}
}
async function startCanonicalActivation(){
  if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);
  if(canonicalProfile().configured)return toast('网页主管理连接已启用');
  try{
    const raw=localStorage.getItem(STORAGE_KEY);if(!raw||raw!==committedStateSnapshot||JSON.stringify(state)!==raw)throw Error('unsaved');
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
    if(localStorage.getItem(STORAGE_KEY)!==raw||committedStateSnapshot!==raw||JSON.stringify(state)!==raw)throw Error('changed');
    AndroidBridge.prepareCanonicalActivation(Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join(''));
  }catch(_){toast('资料已变化或尚未保存，请保留原记录并重新核对',true);}
}
async function finishCanonicalActivation(){
  try{
    const payload=JSON.parse(AndroidBridge.getCanonicalActivationBootstrap());
    const result=await ManagementActivation.persist(canonicalGuardedStore(),payload,canonicalProfile());
    AndroidBridge.commitCanonicalActivation(result.bootstrapSha256);
  }catch(_){toast('新连接尚未完成，当前资料已保留，请核对后重试',true);}
}
async function captureCanonicalEnrollment(){
  if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);
  if(canonicalModeActive())return toast('新模式已启用，不能重新准备迁移资料',true);
  try{
    const raw=localStorage.getItem(STORAGE_KEY),capturedAt=Math.floor(Date.now()/1000);
    if(!raw||raw!==committedStateSnapshot||JSON.stringify(state)!==raw)throw Error('unsaved');
    const hashed=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
    if(localStorage.getItem(STORAGE_KEY)!==raw||committedStateSnapshot!==raw||JSON.stringify(state)!==raw)throw Error('changed');
    AndroidBridge.captureCanonicalSyncEnrollment(raw,Array.from(new Uint8Array(hashed),b=>b.toString(16).padStart(2,'0')).join(''),capturedAt);
  }catch(_){toast('当前资料尚未保存或已变化，请先保存后重新采集',true);}
}
function prepareCanonicalEnrollment(){if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);try{AndroidBridge.prepareCanonicalSyncEnrollment();}catch(_){toast('当前版本暂不支持新连接准备',true);}}
async function stageCanonicalEnrollment(){
  if(typeof isAdminAuthenticated==='undefined'||!isAdminAuthenticated)return toast('请先解锁后台',true);
  if(canonicalModeActive())return toast('新模式已启用，不能覆盖当前连接',true);
  try{
    const raw=localStorage.getItem(STORAGE_KEY);if(!raw||raw!==committedStateSnapshot||JSON.stringify(state)!==raw)throw Error('unsaved');
    const hashed=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));
    if(localStorage.getItem(STORAGE_KEY)!==raw||committedStateSnapshot!==raw||JSON.stringify(state)!==raw)throw Error('changed');
    AndroidBridge.stageCanonicalSyncEnrollment(Array.from(new Uint8Array(hashed),b=>b.toString(16).padStart(2,'0')).join(''));
  }catch(_){toast('资料尚未保存或已变化，请先保存并重新核对备份',true);}
}
function scheduleCanonicalSync(){
  if(canonicalRebindPending||!canonicalModeActive()||canonicalTimer!==null||!canonicalProfile().configured)return;
  canonicalTimer=setTimeout(async()=>{
    canonicalTimer=null;
    if(canonicalRebindPending||!canonicalModeActive())return;
    try{
      const profile=canonicalProfile(),sync=state.managementSync;
      if(sync.protocol===2&&profile.configured&&profile.orgId===sync.orgId&&profile.deviceId===sync.deviceId&&
          (profile.generation!==sync.generation||profile.profileId!==sync.profileId))return finishRuntimeReissue();
      if(!canonicalRuntime)canonicalRuntime=ManagementSyncRuntime.create({store:canonicalGuardedStore(),profile:canonicalProfile,request:canonicalNativeRequest,metadata:true,historical:true});
      const result=await canonicalRuntime.tick();
      const node=document.getElementById('canonicalSyncStatus');
      if(node&&!result.busy)node.textContent=result.paused?'管理员正在核对迁移，本机记录已保留':
        result.blocked?'有同步冲突待核对，本机记录已保留':
        result.phase==='faces'?'课堂名单已更新；人脸档案暂未取得，可先用姓名或学号签到':
        result.more?'还有记录待同步':result.ok?'网页同步已核对':
        result.retry?'同步暂未完成，本机记录已保留':'同步资料需核对，本机记录已保留';
    }catch(_){}
  },600);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleCanonicalSync();});
document.addEventListener('click',event=>{
  const button=event.target?.closest?.('[data-retry-canonical-checkin]');
  if(!button||!isAdminAuthenticated||!canonicalModeActive())return;
  const result=ManagementCheckinQueue.retryConflict(canonicalGuardedStore(),button.dataset.retryCanonicalCheckin);
  if(!result.ok)return toast('这条签到状态已变化，请重新打开设置核对',true);
  button.disabled=true;
  toast('已用原签到记录重新排队');
  scheduleCanonicalSync();
});
async function resolveCanonicalConflict(eventRef,button){
  if(!isAdminAuthenticated||!canonicalModeActive())return;
  const entry=state.managementSync.queue.find(item=>item.eventRef===eventRef&&item.status==='blocked');
  if(!entry)return toast('这条签到状态已变化，请刷新后核对',true);
  button.disabled=true;
  try{
    const response=await canonicalNativeRequest('resolve',JSON.parse(entry.wire));
    if(!response?.ok){toast(response?.data?.error?.code==='resolution_pending'?'网页尚未完成补签或请假，冲突仍保留':'尚未取得服务器处理回执，原记录仍保留',true);button.disabled=false;return;}
    const result=ManagementCheckinQueue.resolveConflict(canonicalGuardedStore(),eventRef,response.data);
    if(!result.ok)throw Error(result.code);
    canonicalRuntime=null;toast(result.status==='leave'?'网页已请假，本机原始签到证据已归档':'网页已确认到课，本机记录已归档，不会重复扣课');
    if(typeof render==='function')render();scheduleCanonicalSync();
  }catch(_){toast('处理回执未通过核验，原冲突和签到证据仍保留',true);button.disabled=false;}
}
document.addEventListener('click',event=>{
  const button=event.target?.closest?.('[data-resolve-canonical-checkin]');
  if(button)resolveCanonicalConflict(button.dataset.resolveCanonicalCheckin,button);
});
window.addEventListener('focus',scheduleCanonicalSync);
setTimeout(scheduleCanonicalSync,1500);
setInterval(()=>{if(!document.hidden)scheduleCanonicalSync();},30000);
