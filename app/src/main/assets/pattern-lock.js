/* Local administrator pattern lock. No pattern is stored or sent over a network. */
const PATTERN_ITERATIONS=120000;
function patternAddPoint(sequence,point){
  if(!Number.isInteger(point)||point<0||point>8||sequence.includes(point))return sequence;
  if(sequence.length){const previous=sequence[sequence.length-1],x=previous%3,y=Math.floor(previous/3),dx=point%3-x,dy=Math.floor(point/3)-y;
    if((Math.abs(dx)===2||Math.abs(dy)===2)&&dx%2===0&&dy%2===0){const middle=(y+dy/2)*3+x+dx/2;if(!sequence.includes(middle))sequence.push(middle)}
  }
  sequence.push(point);return sequence;
}
function patternHex(bytes){return Array.from(new Uint8Array(bytes),value=>value.toString(16).padStart(2,'0')).join('')}
function patternBytes(hex){return Uint8Array.from(hex.match(/../g)||[],value=>parseInt(value,16))}
function validPatternRecord(record){return Boolean(record&&record.version===1&&record.algorithm==='PBKDF2-SHA256'&&Number.isInteger(record.iterations)&&record.iterations>=120000&&record.iterations<=500000&&/^[a-f0-9]{32}$/.test(record.salt)&&/^[a-f0-9]{64}$/.test(record.hash))}
async function patternDigest(sequence,salt,iterations){
  if(!window.crypto||!crypto.subtle)throw new Error('安全解锁组件不可用，请重新打开应用，不要卸载或清除数据');
  const input=new TextEncoder().encode('future-school-pattern-v1:'+sequence.join('-'));
  const key=await crypto.subtle.importKey('raw',input,'PBKDF2',false,['deriveBits']);
  return patternHex(await crypto.subtle.deriveBits({name:'PBKDF2',salt:patternBytes(salt),iterations,hash:'SHA-256'},key,256));
}
async function createPatternRecord(sequence){const salt=patternHex(crypto.getRandomValues(new Uint8Array(16)));return{version:1,algorithm:'PBKDF2-SHA256',iterations:PATTERN_ITERATIONS,salt,hash:await patternDigest(sequence,salt,PATTERN_ITERATIONS)}}
async function verifyPattern(sequence,record=state.settings.adminPattern){
  if(!validPatternRecord(record))throw new Error('解锁资料格式异常，请联系管理员，勿卸载应用');
  const actual=await patternDigest(sequence,record.salt,record.iterations);let different=0;for(let i=0;i<actual.length;i++)different|=actual.charCodeAt(i)^record.hash.charCodeAt(i);return different===0;
}
function patternGuard(){try{const value=JSON.parse(localStorage.getItem(STORAGE_KEY+'_pattern_guard')||'{}');return{failures:Math.max(0,Number(value.failures)||0),until:Math.max(0,Number(value.until)||0)}}catch(_){return{failures:0,until:0}}}
function writePatternGuard(value){try{localStorage.setItem(STORAGE_KEY+'_pattern_guard',JSON.stringify(value))}catch(_){}}
function patternFailure(){const guard=patternGuard();guard.failures++;if(guard.failures>=5){guard.failures=0;guard.until=Date.now()+30000}writePatternGuard(guard);return guard.until>Date.now()?'连续验证失败，请等待 30 秒后重试':'验证未通过，请重试'}
function patternWaitMessage(){const remaining=patternGuard().until-Date.now();return remaining>0?`请等待 ${Math.ceil(remaining/1000)} 秒后重试`:''}
function enterPatternAdmin(){isAdminAuthenticated=true;currentView='dashboard';invalidatePageCache();closeModal();render();window.scrollTo(0,0);toast('已进入教务管理后台')}
function openPatternBoard({title,subtitle,onPattern,extra=''}){
  openModal(`<div class="modal-head"><span>${uiIcon('lock')}</span><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div></div><section class="pattern-panel"><p id="patternMessage" class="pattern-message" aria-live="polite">连接至少 4 个点，松手后自动确认</p><div id="patternBoard" class="pattern-board" role="group" aria-label="九宫格图案，支持按数字键1到9选择，回车确认" tabindex="0"><svg viewBox="0 0 300 300" aria-hidden="true"><polyline id="patternTrail" points=""></polyline><line id="patternTail" style="display:none"></line></svg>${Array.from({length:9},(_,index)=>`<button type="button" class="pattern-node" data-pattern-point="${index}" aria-label="第 ${index+1} 个点"><span></span></button>`).join('')}</div><p class="pattern-privacy">图案仅在本机验证，不上传网络</p>${extra}<div class="pattern-actions"><button type="button" class="secondary-btn" id="patternRedraw">重新绘制</button><button type="button" class="primary-btn" id="patternSubmit">确认图案</button></div><button type="button" class="pattern-help" id="patternForgot">忘记图案怎么办？</button></section>`);
  $('#modal').classList.add('pattern-modal');const modal=$('#modal'),board=$('#patternBoard'),token=faceScanToken,message=$('#patternMessage');
  let points=[],drawing=false,pointerId=null,busy=false,suppressClickUntil=0;
  const alive=()=>token===faceScanToken&&$('#modal')===modal&&Boolean($('#patternBoard'))&&!$('#modalBackdrop').classList.contains('hidden');
  const status=(text,error=false)=>{message.textContent=text;message.classList.toggle('error',error);board.classList.toggle('error',error)};
  const paint=tail=>{board.querySelectorAll('[data-pattern-point]').forEach(node=>node.classList.toggle('selected',points.includes(Number(node.dataset.patternPoint))));const center=index=>`${50+index%3*100},${50+Math.floor(index/3)*100}`;$('#patternTrail').setAttribute('points',points.map(center).join(' '));const line=$('#patternTail');if(tail&&points.length){const last=points[points.length-1];line.setAttribute('x1',50+last%3*100);line.setAttribute('y1',50+Math.floor(last/3)*100);line.setAttribute('x2',tail.x);line.setAttribute('y2',tail.y);line.style.display=''}else line.style.display='none'};
  const clear=()=>{points=[];drawing=false;pointerId=null;if(alive())paint()};
  const controller={alive,status,clear,onRedraw:null};
  const submit=async()=>{if(busy||!alive())return;if(points.length<4){status('至少连接 4 个不同的点',true);clear();return}busy=true;board.setAttribute('aria-busy','true');const selected=points.slice();try{await onPattern(selected,controller)}catch(error){if(alive())status(error.message||'解锁失败，请重试',true)}finally{busy=false;board.removeAttribute('aria-busy');if(alive())clear()}};
  const locate=event=>{const rect=board.getBoundingClientRect();return{x:(event.clientX-rect.left)/rect.width*300,y:(event.clientY-rect.top)/rect.height*300}};
  const pick=position=>{let best=-1,distance=33;for(let i=0;i<9;i++){const d=Math.hypot(position.x-(50+i%3*100),position.y-(50+Math.floor(i/3)*100));if(d<distance){distance=d;best=i}}if(best>=0)patternAddPoint(points,best);paint(position)};
  const start=(event,point,id)=>{
    if(busy||drawing)return false;
    event.preventDefault();event.stopPropagation();
    points=[];drawing=true;pointerId=id;suppressClickUntil=Date.now()+500;
    board.classList.remove('error');pick(locate(point));return true;
  };
  const move=(event,point,id)=>{if(drawing&&id===pointerId){event.preventDefault();pick(locate(point))}};
  const finish=(event,point,id)=>{
    if(!drawing||id!==pointerId)return;
    event.preventDefault();event.stopPropagation();
    pick(locate(point));drawing=false;paint();submit();
  };
  const cancel=()=>{if(drawing){clear();status('已取消本次绘制，请重新连接')}};
  if(window.PointerEvent){
    board.onpointerdown=event=>{
      if(event.isPrimary!==false&&event.button===0&&start(event,event,event.pointerId))board.setPointerCapture(event.pointerId);
    };
    board.onpointermove=event=>move(event,event,event.pointerId);
    board.onpointerup=event=>finish(event,event,event.pointerId);
    board.onpointercancel=cancel;board.onlostpointercapture=cancel;
  }else{
    const activeTouch=event=>Array.from(event.changedTouches).find(touch=>touch.identifier===pointerId);
    board.addEventListener('touchstart',event=>{
      const touch=event.changedTouches[0];
      if(event.touches.length===1&&touch)start(event,touch,touch.identifier);
    },{passive:false});
    board.addEventListener('touchmove',event=>{
      const touch=activeTouch(event);if(touch)move(event,touch,touch.identifier);
    },{passive:false});
    board.addEventListener('touchend',event=>{
      const touch=activeTouch(event);if(touch)finish(event,touch,touch.identifier);
    },{passive:false});
    board.addEventListener('touchcancel',event=>{if(activeTouch(event))cancel()});
  }
  board.onclick=event=>{const node=event.target.closest('[data-pattern-point]');if(!node||busy||event.detail&&Date.now()<suppressClickUntil)return;patternAddPoint(points,Number(node.dataset.patternPoint));paint()};
  board.onkeydown=event=>{if(busy)return;if(/^[1-9]$/.test(event.key)){event.preventDefault();patternAddPoint(points,Number(event.key)-1);paint()}else if(event.key==='Enter'){event.preventDefault();submit()}else if(event.key==='Escape'){clear();status('已清除，请重新绘制')}};
  $('#patternSubmit').onclick=submit;$('#patternRedraw').onclick=()=>{if(busy)return;clear();if(controller.onRedraw)controller.onRedraw();else status('连接至少 4 个点，松手后自动确认')};
  $('#patternForgot').onclick=()=>status('请联系机构管理员或维护人员核验后重设。不要卸载应用或清除数据。',true);
  return controller;
}
function openPatternSetup(onSaved=enterPatternAdmin){
  let first=null;const controller=openPatternBoard({title:'设置后台解锁图案',subtitle:'连续绘制两次相同图案，至少连接 4 个点',onPattern:async(sequence,ui)=>{
    if(!first){first=sequence.slice();ui.status('请再次绘制相同图案');return}
    if(sequence.join('-')!==first.join('-')){ui.status('两次图案不一致，请再次确认，或点击重新绘制',true);return}
    ui.status('正在安全保存图案…');const record=await createPatternRecord(sequence);if(!ui.alive())return;
    state.settings.adminPattern=record;state.settings.adminAuthMode='pattern';delete state.settings.adminPassword;delete state.settings.adminUser;
    if(!saveState()){ui.status('保存失败，原解锁方式未改变，请重试',true);return}writePatternGuard({failures:0,until:0});onSaved();
  }});controller.onRedraw=()=>{first=null;controller.status('重新设置：连接至少 4 个点')};
}
function openPatternVerify(onVerified=enterPatternAdmin,title='图案解锁进入后台'){
  openPatternBoard({title,subtitle:'请绘制已设置的后台解锁图案',onPattern:async(sequence,ui)=>{
    const wait=patternWaitMessage();if(wait){ui.status(wait,true);return}ui.status('正在验证…');const matches=await verifyPattern(sequence);if(!ui.alive())return;
    if(!matches){ui.status(patternFailure(),true);return}writePatternGuard({failures:0,until:0});onVerified();
  }});
}
function openPatternEntry(){
  if(state.settings.adminPattern||state.settings.adminAuthMode==='pattern')return openPatternVerify();
  const hasLegacyData=state.students.length||state.classes.length||state.attendance.length||state.transactions.length||(state.settings.adminPassword||'123456')!=='123456'||(state.settings.adminUser||'admin')!=='admin';
  if(!hasLegacyData)return openPatternSetup();
  openModal(`<div class="modal-head"><span>${uiIcon('lock')}</span><div><h2>首次升级：设置图案解锁</h2><p>仅本次需要验证原后台密码，以后只画图案</p></div></div><form id="legacyPatternForm" class="simple-form"><label class="field"><span>原后台密码</span><input id="legacyPatternPassword" type="password" autocomplete="current-password" required></label><p class="pattern-privacy">无需输入账号。原初始密码未修改时为 123456。</p><div class="form-actions"><button type="button" class="secondary-btn" id="cancelPatternMigration">返回签到</button><button type="submit" class="primary-btn">验证并设置图案</button></div><p id="legacyPatternMessage" class="pattern-message" aria-live="polite"></p></form>`);
  $('#cancelPatternMigration').onclick=closeModal;$('#legacyPatternForm').onsubmit=event=>{event.preventDefault();const wait=patternWaitMessage();if(wait){$('#legacyPatternMessage').textContent=wait;return}if($('#legacyPatternPassword').value!==String(state.settings.adminPassword||'123456')){$('#legacyPatternMessage').textContent=patternFailure();return}writePatternGuard({failures:0,until:0});openPatternSetup()};
}
function openPatternChange(){if(!isAdminAuthenticated)return openPatternEntry();if(!state.settings.adminPattern)return openPatternEntry();openPatternVerify(()=>openPatternSetup(()=>{closeModal();render();toast('后台解锁图案已修改')}),'验证原图案后修改')}
