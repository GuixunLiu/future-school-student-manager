'use strict';
// One interactive SSH login, fixed ordered actions, no password files and no
// arbitrary remote commands. Plain device material is encrypted for this tablet.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn,execFileSync}=require('node:child_process');
const {encoded,digest}=require('./pack-enrollment-review.cjs'),history=require('./build-legacy-history-archive.cjs');
const {build:initialization}=require('./build-management-initialization-plan.cjs'),{build:schedule}=require('./build-canonical-schedule.cjs'),{build:bootstrap}=require('./build-canonical-bootstrap.cjs');
const root=path.resolve(__dirname,'../..'),tool=path.resolve(root,'../android-toolchain'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function read(item,max){
 if(!item||!path.isAbsolute(item.path)||fs.lstatSync(item.path).isSymbolicLink()||fs.statSync(item.path).size>max||!/^[a-f0-9]{64}$/.test(item.sha256))throw Error('Pinned ordinary private input required');
 const raw=fs.readFileSync(item.path);if(sha(raw)!==item.sha256)throw Error('Pinned input changed');return raw;
}
function load(configPath,now=Math.floor(Date.now()/1000)){
 const cfg=JSON.parse(fs.readFileSync(configPath,'utf8'));
 if(cfg.format!=='future-school-controlled-cutover-v1'||cfg.organization!=='weilai'||cfg.deviceSerial!=='HVA13XVR'||cfg.webLogin!=='liuguixun')throw Error('Explicit current production cutover required');
 for(const [name,checksum]of Object.entries(cfg.sourcePins||{})){
  const p=path.resolve(root,name),relative=path.relative(root,p);if(relative.startsWith('..')||path.isAbsolute(relative)||fs.lstatSync(p).isSymbolicLink()||sha(fs.readFileSync(p))!==checksum)throw Error('Reviewed publishing source changed');
 }
 if(!Object.hasOwn(cfg.sourcePins||{},'server/ops/run-canonical-cutover.cjs')||!Object.hasOwn(cfg.sourcePins,'server/ops/canonical-cutover-broker.py'))throw Error('Publishing driver must be pinned');
 const code=read(cfg.code,2*1024*1024),capture=JSON.parse(read(cfg.capture,20*1024*1024)),request=JSON.parse(read(cfg.request,16384)),reserved=JSON.parse(read(cfg.reservedPlan,8*1024*1024));
 if(Object.keys(capture).sort().join('|')!=='bytes|captured_at|format|payload_base64|sha256'||capture.format!=='future-school-native-capture-v1'||!Number.isSafeInteger(capture.captured_at)||now-capture.captured_at>=900||capture.captured_at>now+120)throw Error('Fresh actual native capture required; do not retimestamp an old backup');
 const bytes=Buffer.from(capture.payload_base64,'base64');if(bytes.toString('base64')!==capture.payload_base64||bytes.length!==capture.bytes||sha(bytes)!==capture.sha256)throw Error('Native capture differs');
 const raw=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
 if(request.protocol!==1||!/^[a-f0-9]{32}$/.test(request.nonce)||Object.keys(request).sort().join('|')!=='nonce|protocol|public_key')throw Error('Actual tablet enrollment request required');
 const publicKey=Buffer.from(request.public_key,'base64'),key=crypto.createPublicKey({key:publicKey,type:'spki',format:'der'});
 if(key.asymmetricKeyType!=='rsa'||key.asymmetricKeyDetails.modulusLength!==3072)throw Error('Actual tablet RSA identity required');
 const output=path.resolve(cfg.outputDir),relative=path.relative(root,output);
 if(!relative||!relative.startsWith('..'+path.sep)||!path.isAbsolute(cfg.outputDir)||fs.existsSync(output)||fs.lstatSync(path.dirname(output)).isSymbolicLink())throw Error('Use a new private attempt directory outside Git');
 return{cfg,code,capture,request,publicKey,raw,reserved,output};
}
function rpcProcess(broker,{spawnImpl=spawn}={}){
 const hash=sha(broker),loader="import sys,json,base64,hashlib; v=json.loads(sys.stdin.readline(1048576)); b=base64.b64decode(v['code'],validate=True); assert hashlib.sha256(b).hexdigest()=='"+hash+"'; exec(compile(b,'controlled-cutover','exec'))";
 const command="python3 -u -c '"+loader.replaceAll("'","'\\''")+"'";
 const child=spawnImpl('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2','wxnotify-deploy@47.97.226.219',command],{stdio:['pipe','pipe','inherit'],windowsHide:false});
 let sequence=0,pending=null,buffer='',closed=false;
 const stop=e=>{if(pending){clearTimeout(pending.timer);pending.reject(e);pending=null;}};
 child.once('error',()=>stop(Error('SSH could not start')));child.stdin.on('error',()=>stop(Error('SSH input closed; preserve the result for review')));
 child.stdout.on('data',chunk=>{buffer+=chunk.toString('utf8');if(Buffer.byteLength(buffer)>40*1024*1024){stop(Error('SSH result limit'));child.stdin.end();return;}
  for(;;){const n=buffer.indexOf('\n');if(n<0)break;const line=buffer.slice(0,n);buffer=buffer.slice(n+1);let v;try{v=JSON.parse(line);}catch{stop(Error('Unexpected SSH result'));child.stdin.end();return;}
   if(!pending||v.sequence!==pending.sequence){stop(Error('SSH sequence differs'));child.stdin.end();return;}
   const p=pending;pending=null;clearTimeout(p.timer);if(v.ok!==true){const e=Error('服务器受控发布已停止：'+v.recovery+' ('+(v.error_phase||'unknown')+'/'+(v.error_code||'check_failed')+')');e.recovery=v.recovery;e.remoteDirectory=v.remote_directory;e.errorPhase=v.error_phase;e.errorCode=v.error_code;p.reject(e);}else p.resolve(v.data);
  }
 });
 child.once('close',code=>{closed=true;stop(Error('SSH ended before a complete receipt ('+code+')'));});
 child.stdin.write(JSON.stringify({code:broker.toString('base64')})+'\n');
 return{child,call(action,body={}){if(pending||closed)throw Error('One ordered cutover action at a time');return new Promise((resolve,reject)=>{const current=++sequence;const timer=setTimeout(()=>{stop(Error('SSH action timed out; do not repeat before reconciliation'));child.stdin.end();},120000);pending={sequence:current,resolve,reject,timer};child.stdin.write(JSON.stringify({sequence:current,action,body})+'\n');});},end(){child.stdin.end();}};
}
function save(dir,name,value){const raw=Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value,null,2)+'\n');fs.writeFileSync(path.join(dir,name),raw,{flag:'wx',mode:0o600});}
function seal(checked,profile,payload){
 const java=path.join(tool,'jdk/jdk-17.0.20.1+1/bin/java.exe'),javac=path.join(tool,'jdk/jdk-17.0.20.1+1/bin/javac.exe'),classes=path.join(checked.output,'java');fs.mkdirSync(classes);save(checked.output,'tablet-public-key.der',checked.publicKey);
 execFileSync(javac,['-d',classes,path.join(root,'server/ops/CanonicalEnvelope.java')],{windowsHide:true,timeout:30000});
 const encrypt=(value,large)=>{const args=['-cp',classes,'CanonicalEnvelope',...(large?['--bootstrap']:[]),path.join(checked.output,'tablet-public-key.der'),checked.request.nonce];
  const raw=execFileSync(java,args,{input:Buffer.from(JSON.stringify(value)),windowsHide:true,timeout:30000,maxBuffer:20*1024*1024});const obj=JSON.parse(raw);if(obj.nonce!==checked.request.nonce)throw Error('Device envelope differs');return raw;};
 save(checked.output,'canonical-sync-connection.enc.json',encrypt(profile,false));save(checked.output,'canonical-bootstrap.enc.json',encrypt(payload,true));
}
async function run(configPath,{rpcFactory=rpcProcess,sealImpl=seal,adbImpl=execFileSync,rpcSession=null,webPasswordInput=null,preserveSessionOnError=false}={}){
 const checked=load(configPath),{cfg,capture,request,raw,reserved,output}=checked;
 if(!webPasswordInput&&!process.env.CANONICAL_WEB_PASSWORD)throw Error('Missing short-lived local account authentication; return to chat');
 const webPassword=webPasswordInput||process.env.CANONICAL_WEB_PASSWORD;delete process.env.CANONICAL_WEB_PASSWORD;
 const adb=path.join(tool,'sdk/platform-tools/adb.exe');if(adbImpl(adb,['-s',cfg.deviceSerial,'get-state'],{encoding:'utf8',windowsHide:true,timeout:10000}).trim()!=='device')throw Error('Current tablet must remain connected');
 fs.mkdirSync(output,{mode:0o700});const broker=fs.readFileSync(path.join(__dirname,'canonical-cutover-broker.py')),rpc=rpcSession||rpcFactory(broker);let published=false;
 try{
  const copied=await rpc.call('copy',{organization:cfg.organization,accept_service_cutover:true,source_captured_at:capture.captured_at,code_base64:checked.code.toString('base64'),code_sha256:sha(checked.code)});
  const reviewRaw=Buffer.from(copied.review_base64,'base64');if(sha(reviewRaw)!==copied.review_sha256)throw Error('Current server source checksum differs');save(output,'current-server-review.private.json',reviewRaw);
  const reservedIds=new Map(reserved.students.filter(s=>s.identity_action==='reserve_new_base_id').map(s=>[s.local_id,s.server_id]));
  const built=initialization(raw,reviewRaw.toString('utf8'),{organization:cfg.organization,newId:local=>{const id=reservedIds.get(local);if(!id)throw Error('Unreviewed new identity; no automatic allocation');return id;}}),plan=built.plan;
  const artifact=history.build(raw),planRaw=Buffer.from(encoded(plan)),archiveRaw=Buffer.from(history.encoded(artifact.artifact));
  save(output,'initialization-plan.private.json',planRaw);save(output,'history-archive.private.json',archiveRaw);
  const initialized=await rpc.call('initialize',{plan_base64:planRaw.toString('base64'),plan_sha256:sha(planRaw),archive_base64:archiveRaw.toString('base64'),source_captured_at:capture.captured_at,web_login:cfg.webLogin,web_password:webPassword});
  const projected=schedule(raw,plan,initialized.boundary,{now:Math.floor(Date.now()/1000)});save(output,'future-schedule.private.json',projected);
  const staged=await rpc.call('stage',{schedule:projected,nonce:request.nonce});
  const p=staged.profile,payload=bootstrap(raw,staged.source,{org_id:p.o,device_id:p.d,generation:p.g,profile_id:p.id,baseline_sha256:p.b});
  sealImpl(checked,p,payload);
  const receipt=await rpc.call('publish',{source_map_sha256:digest(staged.source),bootstrap_sha256:payload.bootstrap_sha256});
  if(receipt.status!=='awaiting_device_commit'||receipt.original_source_written!==false||receipt.accounts_preserved!==true||receipt.legacy_applications_preserved!==true||receipt.old_mirror_writable!==false||receipt.scope.active_students!==projected.summary.students)throw Error('Incomplete publish receipt');
  save(output,'publish-receipt.private.json',receipt);published=true;
  const remote='/sdcard/Android/data/cn.mashangchengzhang.studentmanager/files/';
  for(const name of ['canonical-sync-connection.enc.json','canonical-bootstrap.enc.json'])adbImpl(adb,['-s',cfg.deviceSerial,'push',path.join(output,name),remote+name],{stdio:'pipe',windowsHide:true,timeout:30000});
  save(output,'tablet-delivery-receipt.private.json',{encrypted_files_delivered:true,device_serial:cfg.deviceSerial,activation_id:receipt.activation_id,expires_at:receipt.expires_at,server_committed:false});
  await rpc.call('finish');rpc.end();return{directory:output,scope:receipt.scope,expires_at:receipt.expires_at,status:'awaiting_tablet_confirmation'};
 }catch(error){save(output,'stopped.private.json',{status:'stopped',published,recovery:error.recovery||'inspect_receipts_before_retry',remote_directory:error.remoteDirectory||null,error_phase:error.errorPhase||null,error_code:error.errorCode||null});if(!preserveSessionOnError)rpc.end();throw error;}
}
async function waitSession(configPath){
 const checked=load(configPath),password=process.env.CANONICAL_WEB_PASSWORD;delete process.env.CANONICAL_WEB_PASSWORD;
 if(!password)throw Error('Missing short-lived local authentication');
 const dir=path.resolve(checked.cfg.sessionDir||'');if(path.dirname(dir)!==path.dirname(checked.output)||fs.existsSync(dir))throw Error('Use a new private bounded session directory');fs.mkdirSync(dir,{mode:0o700});
 const rpc=rpcProcess(fs.readFileSync(path.join(__dirname,'canonical-cutover-broker.py')));let failed=false;
 try{
  for(let attempt=0;attempt<3;attempt++){
   const diagnostics=await rpc.call('diagnose');save(dir,'diagnostics-'+attempt+'.private.json',diagnostics);
   console.log('启动诊断已保存，同一SSH连接等待本机继续处理。请保持窗口打开。');
   const control=path.join(dir,'CONTINUE-'+attempt+'.private.json'),deadline=Date.now()+900000;
   while(!fs.existsSync(control)){if(Date.now()>deadline)throw Error('等待继续超时，未自动重试');await new Promise(r=>setTimeout(r,500));}
   const order=JSON.parse(fs.readFileSync(control,'utf8'));
   if(order.operation!=='deploy'||!path.isAbsolute(order.operatorPath)||path.dirname(order.operatorPath)!==path.dirname(dir)||sha(fs.readFileSync(order.operatorPath))!==order.sha256)throw Error('Explicit pinned local continuation required');
   if(failed)await rpc.call('reset');
   try{return await run(order.operatorPath,{rpcSession:rpc,webPasswordInput:password,preserveSessionOnError:true});}
   catch(error){save(dir,'attempt-'+attempt+'-stopped.private.json',{recovery:error.recovery||null,remote_directory:error.remoteDirectory||null});if(!['original_services_restored','original_not_paused'].includes(error.recovery))throw error;failed=true;console.log('原服务已恢复，同一连接保留，等待核对修正；不会自动重复发布。');}
  }
  throw Error('受控重试次数已用完，请保留回执核对');
 }finally{rpc.end();}
}
module.exports={load,rpcProcess,run};
if(require.main===module){(async()=>{
 const [mode,file]=process.argv.slice(2);if(!['--self-check','--connect','--connect-wait'].includes(mode)||!file)throw Error('Use --self-check or --connect and the explicit current private plan');
 if(mode==='--self-check'){const c=load(file);console.log(JSON.stringify({checksums_verified:true,actual_capture:c.capture.captured_at,contains_private_data:true,network_connected:false,services_changed:false}));return;}
 console.log('将备份并完整复制当前数据，保留账号、历史和申请，再切换新网页与加密平板入口。');console.log('请在 SSH 提示处输入服务器密码。密码不会保存在文件中。保持窗口及平板 USB 连接。');
 const r=await (mode==='--connect-wait'?waitSession(file):run(file));console.log('新网页服务已上线，平板密文已送达，等待平板双端确认。');console.log('回执目录：'+r.directory);console.log('请保留窗口，回聊天告知完成；不要重复运行或给真实学生做测试签到。');
})().catch(e=>{console.error(e.message);process.exitCode=1;});}
