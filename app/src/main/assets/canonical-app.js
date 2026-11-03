/* Dormant until an explicitly reconciled server-authoritative migration.
 * No credentials, automatic enrolment, legacy snapshot import, or mode switch.
 */
let canonicalStore=null,canonicalRuntime=null,canonicalTimer=null;
const canonicalRequests=new Map();
function canonicalModeActive(){return typeof state!=='undefined'&&state.managementSync?.enabled===true&&state.managementSync.mode==='server_authoritative';}
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
function canonicalRecordStudentAttendance(studentId){
  try{
    const sync=state.managementSync,p=canonicalProfile();
    if(!p.configured||p.orgId!==sync.orgId||p.deviceId!==sync.deviceId||p.generation!==sync.generation||p.profileId!==sync.profileId)throw Error('profile');
    const students=Object.entries(sync.students).filter(([,b])=>b.localId===studentId);
    if(students.length!==1)throw Error('mapping');
    const remoteId=students[0][0],today=localDate(),lessons=Object.entries(sync.lessons).filter(([,l])=>l.date===today&&l.classId===students[0][1].classId&&l.studentIds?.includes(remoteId));
    if(lessons.length!==1)throw Error('lesson');
    // Ref is generated BEFORE the durable write; it remains in the saved queue.
    const eventRef='checkin:'+syncNonce(),now=Math.floor(Date.now()/1000);
    const result=ManagementCheckinQueue.record(canonicalGuardedStore(),{studentId:remoteId,lessonId:lessons[0][0],occurredAt:now,eventRef},now);
    if(!result.ok)return{ok:false,error:result.code==='attendance_changed'?'该学生已签到、已请假或应到资料已变化，请刷新核对':'签到未保存，原资料保留，请核对同步状态或存储空间'};
    scheduleCanonicalSync();listPages.attendance=1;
    return{ok:true,attendanceId:result.attendanceId,student:studentById(studentId)};
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
  return `<details class="settings-card"><summary>网页主管理连接</summary><p id="canonicalEnrollmentStatus">${active?(p.configured?'已启用网页主管理':'新资料已保存，连接确认尚未完成'):p.staged?'新连接已暂存，尚未切换资料':'尚未准备新连接'}</p><p>先采集当前资料，再由管理员核对并准备加密连接和完整名单。两端确认成功后，网页负责管理，平板负责签到。</p><button class="secondary-btn" data-canonical-enrollment="capture" ${active?'disabled':''}>采集当前核对资料</button> <button class="secondary-btn" data-canonical-enrollment="prepare" ${active?'disabled':''}>准备连接申请</button> <button class="secondary-btn" data-canonical-enrollment="stage" ${active?'disabled':''}>核对加密连接</button> <button class="secondary-btn" data-canonical-enrollment="activate" ${!p.staged&&!p.activationPending||p.configured?'disabled':''}>启用／完成同步</button></details>`;
}
document.addEventListener('click',event=>{const action=event.target?.closest?.('[data-canonical-enrollment]')?.getAttribute('data-canonical-enrollment');if(action==='capture')captureCanonicalEnrollment();if(action==='prepare')prepareCanonicalEnrollment();if(action==='stage')stageCanonicalEnrollment();if(action==='activate')startCanonicalActivation();});
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
  if(!canonicalModeActive()||canonicalTimer!==null||!canonicalProfile().configured)return;
  canonicalTimer=setTimeout(async()=>{
    canonicalTimer=null;
    if(!canonicalModeActive())return;
    try{
      if(!canonicalRuntime)canonicalRuntime=ManagementSyncRuntime.create({store:canonicalGuardedStore(),profile:canonicalProfile,request:canonicalNativeRequest,metadata:true,historical:true});
      const result=await canonicalRuntime.tick();
      const node=document.getElementById('canonicalSyncStatus');if(node)node.textContent=result.paused?'管理员正在核对迁移，本机记录已保留':result.ok?'网页同步已核对':result.retry?'同步暂未完成，本机记录已保留':'同步资料需核对，本机记录已保留';
    }catch(_){}
  },600);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleCanonicalSync();});
window.addEventListener('focus',scheduleCanonicalSync);
setTimeout(scheduleCanonicalSync,1500);
setInterval(()=>{if(!document.hidden)scheduleCanonicalSync();},30000);
