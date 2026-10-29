let webMirrorTimer=null;
function webMirrorStatus(){try{return JSON.parse(window.AndroidBridge?.getWebMirrorStatus()||'{}')}catch(_){return{}}}
function scheduleWebMirror(){
  clearTimeout(webMirrorTimer);webMirrorTimer=setTimeout(()=>{
    if(!webMirrorStatus().enabled)return;
    try{window.AndroidBridge.submitWebMirror(JSON.stringify(buildWebMirrorDataset()))}
    catch(_){const el=document.getElementById('webMirrorStatus');if(el)el.textContent='资料生成未完成，本机保存不受影响，请联系管理员'}
  },2000);
}
function webMirrorSettingsMarkup(){const s=webMirrorStatus();return `<section class="settings-card"><h3>网页自动同步</h3><p id="webMirrorStatus">${esc(s.state||'尚未连接网页')}${s.received_at?' · 最近更新 '+new Date(s.received_at*1000).toLocaleString():''}</p><p>平板保存后自动更新网页；断网时保存在本机，恢复连接后重试。只同步教学资料，不上传人脸、照片或联系方式。</p><button class="secondary-btn" id="openWebMirror">${s.configured?'管理同步连接':'连接网页'}</button></section>`}
function openWebMirror(){window.AndroidBridge?.configureWebMirror()}
window.onWebMirrorChanged=()=>{const s=webMirrorStatus(),el=document.getElementById('webMirrorStatus'),button=document.getElementById('openWebMirror');if(el)el.textContent=(s.state||'尚未连接')+(s.received_at?' · 最近更新 '+new Date(s.received_at*1000).toLocaleString():'');if(button)button.textContent=s.configured?'管理同步连接':'连接网页';scheduleWebMirror()};
setInterval(()=>{if(document.visibilityState==='visible')scheduleWebMirror()},60000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')scheduleWebMirror()});
