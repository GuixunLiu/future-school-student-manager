'use strict';
// A local SSH forward only. No remote command, upload, business write or password storage.
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),http=require('node:http'),{spawn}=require('node:child_process');
const {loadPlan,validateResult}=require('./upload-roster-review.cjs');
const URL='http://127.0.0.1:18082';
function loadContext(dir){
 const {plan}=loadPlan(path.join(dir,'review-plan.private.json'));
 const files=fs.readdirSync(plan.output_dir).filter(n=>/^review-deployment-[0-9TZ.-]+\.json$/.test(n)).sort();
 if(!files.length)throw Error('尚无部署成功回执，请先核对部署结果。');
 const file=path.join(plan.output_dir,files.at(-1)),st=fs.lstatSync(file);
 if(!st.isFile()||st.isSymbolicLink()||st.size>1024*1024)throw Error('部署回执文件不符合预期。');
 const receipt=validateResult(fs.readFileSync(file,'utf8'),plan);
 if(receipt.migration.pre_import_backup_integrity!=='ok'||receipt.migration.source_database_modified!==false)throw Error('备份或来源保护回执不符合预期。');
 let login='owner';const display=path.join(dir,'review-access-login.private.json');
 if(fs.existsSync(display)){const info=JSON.parse(fs.readFileSync(display,'utf8'));if(info.organization!=='weilai'||!/^[a-z0-9][a-z0-9_.-]{2,63}$/.test(info.login))throw Error('网页登录提示配置不正确');login=info.login;}
 return{plan,receipt,login};
}
function checkHealth(health){
 if(health?.status!=='ready'||health.version!=='0.5.0'||health.mode!=='roster_review'||health.archive_enabled!==true||health.roster_review_only!==true||['business_ready','wechat_sending_enabled','student_sync_enabled','parent_binding_enabled'].some(k=>health[k]!==false))throw Error('网页服务状态与只读核对副本不符，尚未打开登录页。');
 return health;
}
function sshArguments(plan){
 return['-p',String(plan.port),'-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-N','-T','-L','127.0.0.1:18082:127.0.0.1:18082',plan.user+'@'+plan.host];
}
function portFree(){return new Promise((resolve,reject)=>{const server=net.createServer();server.once('error',()=>reject(Error('本机18082端口已被使用。请保留现有窗口并告诉 Codex，避免连接到其他页面。')));server.listen(18082,'127.0.0.1',()=>server.close(resolve));});}
function readHealth(){return new Promise((resolve,reject)=>{
 const request=http.get(URL+'/health/ready',{timeout:1500,headers:{'Cache-Control':'no-store'}},response=>{
  if(response.statusCode!==200){response.resume();return reject(Error('网页尚未就绪'));}
  let body='';response.on('data',bytes=>{body+=bytes;if(Buffer.byteLength(body)>32768)request.destroy(Error('服务返回过大'));});
  response.on('end',()=>{try{resolve(JSON.parse(body));}catch(error){reject(error)}});response.on('error',reject);
 });request.on('timeout',()=>request.destroy(Error('连接超时')));request.on('error',reject);
});}
async function waitReady({read=readHealth,failed=()=>null,sleep=ms=>new Promise(r=>setTimeout(r,ms)),now=Date.now}={}){
 const deadline=now()+180000;
 while(now()<deadline){
  if(failed())throw failed();
  let health;try{health=await read();}catch{}
  if(failed())throw failed();
  if(health)return checkHealth(health);
  await sleep(500);
 }
 throw Error('等待连接超时，原服务器服务不受影响。');
}
async function connect(plan,login='owner'){
 await portFree();
 console.log('请在 SSH 的 password 提示处输入服务器密码。密码不显示字符，也不会保存。');
 let failure=null,child,stop,closing=false;
 const ended=new Promise(resolve=>stop=resolve);
 try{
  child=spawn('ssh',sshArguments(plan),{windowsHide:true,stdio:['inherit','ignore','inherit']});
  child.on('error',error=>{failure=error;stop(1)});child.on('close',code=>{failure||=Error('网页连接已断开，服务器继续运行；需要时重新打开此入口。');stop(code)});
  const interrupt=()=>{closing=true;child.kill();};process.once('SIGINT',interrupt);
  try{
   await waitReady({failed:()=>failure});
   console.log('只读核对网页已连接：'+URL);
   console.log('机构：weilai；账号：'+login+'；使用当前设置的后台密码（与本次 SSH 密码分开）。');
   console.log('请保持本窗口开着。关闭后网页连接断开，服务器和平板仍继续运行。');
   const opener=spawn('powershell.exe',['-NoProfile','-NonInteractive','-Command',"Start-Process -FilePath 'http://127.0.0.1:18082'"],{windowsHide:true,stdio:'ignore'});
   opener.once('error',()=>console.log('请手动在浏览器打开：'+URL));
   opener.once('close',code=>{if(code)console.log('请手动在浏览器打开：'+URL)});
   const code=await ended;
   if(!closing&&code!==0)throw failure;
  }finally{process.removeListener('SIGINT',interrupt);}
 }finally{if(child&&child.exitCode===null)child.kill();}
}
module.exports={loadContext,checkHealth,sshArguments,waitReady};
if(require.main===module){
 (async()=>{
  const args=process.argv.slice(2);if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('不支持的启动参数');
  const {plan,receipt,login}=loadContext(__dirname);
  if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',receipt_verified:true,network_connected:false,url:URL,students:receipt.migration.students,active:receipt.migration.active,paused:receipt.migration.paused}));return;}
  console.log('将建立网页查看连接，保持只读核对，不上传或修改业务资料。');
  await connect(plan,login);
 })().catch(error=>{console.error('未完成：'+error.message);process.exitCode=1});
}
