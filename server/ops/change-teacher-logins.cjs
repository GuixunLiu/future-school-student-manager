'use strict';
// Existing-account changes only; transmits salted hashes, never plaintext passwords.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(dir){
 const config=JSON.parse(fs.readFileSync(path.join(dir,'login-change-config.private.json'),'utf8'));
 if(!/^[a-zA-Z0-9.-]{1,253}$/.test(config.host)||!/^[a-z_][a-z0-9_-]{0,31}$/.test(config.user)||!Number.isInteger(config.port)||config.port<1||config.port>65535||!/^\/opt\/wxnotify\/app\/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}$/.test(config.work_directory)||!path.isAbsolute(config.output_dir)||!/^[a-f0-9]{64}$/.test(config.plan_digest))throw Error('Invalid explicit access configuration');
 if(!path.isAbsolute(config.browser_directory)||!/^[a-z]{3,64}$/.test(config.owner_new_login))throw Error('Invalid login display configuration');
 const files=['plan','helper','shared'].map(k=>{const f=config[k];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('Invalid access file');const st=fs.lstatSync(f.path);if(!st.isFile()||st.isSymbolicLink()||st.size>1024*1024)throw Error('Invalid access file type or size');const bytes=fs.readFileSync(f.path);if(hash(bytes)!==f.sha256)throw Error('Access file checksum changed');return{bytes,sha256:f.sha256}});
 const p=JSON.parse(files[0].bytes.toString('utf8'));
 if(p.confirmed_by_owner!==true||p.format!=='future-school-confirmed-login-change'||p.expected.accounts!==config.expected.accounts||p.expected.classes!==config.expected.classes||p.owner_new_login!==config.owner_new_login)throw Error('Owner-confirmed login change required');
 return{config,files};
}
function wrapper(config,files){
 const stamp=new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14),suffix=crypto.randomBytes(4).toString('hex');
 const data={directory:config.work_directory+'/login-change-'+stamp+'-'+suffix,release:config.work_directory+'/release',database:config.work_directory+'/data/roster.sqlite',files:files.map((f,i)=>({name:['login-plan.json','change-teacher-logins.py','teacher-access.py'][i],sha256:f.sha256,base64:f.bytes.toString('base64')}))};
 const encoded=Buffer.from(JSON.stringify(data)).toString('base64');
 return `import base64,hashlib,json,os,runpy,sys\nfrom pathlib import Path\nc=json.loads(base64.b64decode('${encoded}'))\nos.umask(0o077)\nw=Path(c['directory'])\nif any(p.is_symlink() for p in (w,*w.parents)): raise SystemExit('STOP: linked private path')\nw.mkdir(mode=0o700,exist_ok=False)\nfor f in c['files']:\n b=base64.b64decode(f['base64'],validate=True)\n if hashlib.sha256(b).hexdigest()!=f['sha256']: raise SystemExit('STOP: input checksum differs')\n fd=os.open(w/f['name'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n with os.fdopen(fd,'wb') as stream: stream.write(b)\nsys.argv=[str(w/'change-teacher-logins.py'),'--database',c['database'],'--release',c['release'],'--plan',str(w/'login-plan.json'),'--plan-file-sha256',c['files'][0]['sha256'],'--backup',str(w/'before-login-change.sqlite'),'--apply-confirmed-login-change']\nrunpy.run_path(str(w/'change-teacher-logins.py'),run_name='__main__')\n`;
}
function result(raw,config){
 const r=JSON.parse(raw);
 if(r.plan_sha256!==config.plan_digest||r.accounts_changed!==config.expected.accounts||r.new_accounts!==0||r.classes_preserved!==config.expected.classes||r.roles_and_user_ids_preserved!==true||r.sessions_revoked!==true||r.owner_login!==config.owner_new_login||r.business_data_unchanged!==true||r.roster_review_only!==true||r.wechat_sending_enabled!==false||r.backup_integrity!=='ok'||!/^[a-f0-9]{64}$/.test(r.backup_sha256))throw Error('Login-change receipt differs from approved scope; inspect before retrying');
 return r;
}
async function apply(dir,{spawnImpl=spawn}={}){
 const {config,files}=load(dir),script=wrapper(config,files);
 const raw=await new Promise((resolve,reject)=>{
  let text='',done=false;const child=spawnImpl('ssh',['-p',String(config.port),'-o','StrictHostKeyChecking=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',config.user+'@'+config.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});
  const timeout=setTimeout(()=>{child.kill();fail(Error('Access connection timed out; inspect before retrying'))},180000);
  function fail(e){if(!done){done=true;clearTimeout(timeout);reject(e)}}
  child.on('error',fail);child.stdin.on('error',fail);child.stdout.on('data',b=>{text+=b;if(Buffer.byteLength(text)>1024*1024){child.kill();fail(Error('Unexpected server output'))}});
  child.on('close',code=>{if(done)return;done=true;clearTimeout(timeout);code===0?resolve(text):reject(Error('账号操作尚未确认完成。请保留结果，不要重新生成密码或反复运行。'))});child.stdin.end(script);
 });
 const receipt=result(raw,config);fs.mkdirSync(config.output_dir,{recursive:true,mode:0o700});
 const target=path.join(config.output_dir,'teacher-login-changed-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');
 fs.writeFileSync(target,JSON.stringify(receipt,null,2)+'\n',{flag:'wx',mode:0o600});
 const display=path.join(config.browser_directory,'review-access-login.private.json'),temp=display+'.tmp-'+crypto.randomBytes(4).toString('hex');
 fs.writeFileSync(temp,JSON.stringify({organization:'weilai',login:config.owner_new_login})+'\n',{flag:'wx',mode:0o600});fs.renameSync(temp,display);
 return{...receipt,receipt_path:target};
}
module.exports={load,wrapper,result,apply};
if(require.main===module){
 (async()=>{const args=process.argv.slice(2);if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');
  const {config}=load(__dirname);
  if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',accounts:config.expected.accounts,classes:config.expected.classes,network_connected:false}));return;}
  console.log('将先备份，再把8位正式教师改为姓名拼音账号及你指定的统一密码；保留身份和43班任课。');console.log('请在 SSH 提示处输入服务器密码；这里只修改网页登录账号，不改服务器SSH密码。');
  const r=await apply(__dirname);console.log('账号调整成功：'+r.accounts_changed+'个账号，身份和'+r.classes_preserved+'班任课保持一致。');
  console.log('你的新账号：'+r.owner_login+'；请用你指定的新密码重新登录。旧账号不再使用。');console.log('回执已保存：'+r.receipt_path);console.log('微信仍未开启。');
 })().catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
}
