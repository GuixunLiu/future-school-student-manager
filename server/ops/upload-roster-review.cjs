'use strict';
// One strict-host-key SSH connection. Password stays in the user's terminal prompt.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const names=['release.zip','bundle.json','server-review.json','stage.py'];
function loadPlan(file){
 const plan=JSON.parse(fs.readFileSync(file,'utf8'));
 if(plan.version!=='0.5.0'||!/^[a-zA-Z0-9.-]{1,253}$/.test(plan.host)||!/^[a-z_][a-z0-9_-]{0,31}$/.test(plan.user)||!Number.isInteger(plan.port)||plan.port<1||plan.port>65535||!path.isAbsolute(plan.output_dir)||!/^[a-f0-9]{64}$/.test(plan.manifest_sha256)||!Array.isArray(plan.files)||plan.files.length!==4||plan.files.some((f,i)=>f.name!==names[i]||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256)))throw Error('Invalid explicit review deployment plan');
 const payloads=plan.files.map(f=>{const st=fs.lstatSync(f.path);if(!st.isFile()||st.isSymbolicLink()||st.size>4*1024*1024)throw Error('Unsupported or linked deployment file');const bytes=fs.readFileSync(f.path);if(hash(bytes)!==f.sha256)throw Error('Deployment file checksum changed; nothing uploaded');return{...f,bytes}});
 if(!plan.expected||!['students','active','paused','classes','active_remaining_units','paused_remaining_units'].every(k=>Number.isSafeInteger(plan.expected[k])))throw Error('Reviewed migration totals required');
 return{plan,payloads};
}
function makeWrapper(plan,payloads,{suffix}={}){
 const stamp=new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14),nonce=suffix||crypto.randomBytes(4).toString('hex');
 if(!/^[a-f0-9]{8}$/.test(nonce))throw Error('Invalid deployment nonce');
 const work='/opt/wxnotify/app/roster-review-v0.5.0-'+payloads[0].sha256.slice(0,8)+'-'+stamp+'-'+nonce;
 const data={work,manifest:plan.manifest_sha256,files:payloads.map(p=>({name:p.name,sha256:p.sha256,base64:p.bytes.toString('base64')}))};
 const encoded=Buffer.from(JSON.stringify(data)).toString('base64');
 return `import base64,hashlib,json,os,runpy,sys\nfrom pathlib import Path\nc=json.loads(base64.b64decode('${encoded}'))\nos.umask(0o077)\nw=Path(c['work'])\nif any(p.is_symlink() for p in (w,*w.parents)): raise SystemExit('STOP: linked upload path')\nif w.parent != Path('/opt/wxnotify/app'): raise SystemExit('STOP: unexpected upload parent')\nw.mkdir(mode=0o700,exist_ok=False)\nfor f in c['files']:\n b=base64.b64decode(f['base64'],validate=True)\n if hashlib.sha256(b).hexdigest()!=f['sha256']: raise SystemExit('STOP: upload checksum differs')\n fd=os.open(w/f['name'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n with os.fdopen(fd,'wb') as out: out.write(b)\nsys.argv=[str(w/'stage.py'),'--work-directory',str(w),'--package-sha256',c['files'][0]['sha256'],'--manifest-sha256',c['manifest'],'--bundle-sha256',c['files'][1]['sha256'],'--review-sha256',c['files'][2]['sha256'],'--install-separate-review-service']\nrunpy.run_path(str(w/'stage.py'),run_name='__main__')\n`;
}
function validateResult(raw,plan){
 const r=JSON.parse(raw);
 if(r.review_service!=='wxnotify-roster-review.service'||r.loopback_port!==18082||r.original_pilot_still_active!==true||r.original_pilot_replaced!==false||r.health?.version!=='0.5.0'||r.health?.roster_review_only!==true||r.migration?.integrity!=='ok'||r.migration.bundle_sha256!==plan.files[1].sha256||Object.entries(plan.expected).some(([k,v])=>r.migration[k]!==v))throw Error('Server result differs from reviewed totals; inspect before continuing');
 return r;
}
async function upload(file,{spawnImpl=spawn}={}){
 const {plan,payloads}=loadPlan(file),script=makeWrapper(plan,payloads);
 fs.mkdirSync(plan.output_dir,{recursive:true,mode:0o700});
 const raw=await new Promise((resolve,reject)=>{
  const child=spawnImpl('ssh',['-p',String(plan.port),'-o','StrictHostKeyChecking=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',plan.user+'@'+plan.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});
  let result='',done=false;const timer=setTimeout(()=>{child.kill();reject(Error('Connection timed out; inspect review service before retrying'))},300000);
  const fail=e=>{if(!done){done=true;clearTimeout(timer);reject(e)}};
  child.on('error',fail);child.stdin.on('error',fail);child.stdout.on('data',b=>{result+=b;if(Buffer.byteLength(result)>1024*1024){child.kill();fail(Error('Unexpectedly large server output'))}});
  child.on('close',code=>{if(done)return;done=true;clearTimeout(timer);if(code!==0)reject(Error('Review setup did not finish; existing pilot was not replaced. Preserve the new private folder for inspection.'));else resolve(result)});child.stdin.end(script);
 });
 const receipt=validateResult(raw,plan),name='review-deployment-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json',target=path.join(plan.output_dir,name);
 fs.writeFileSync(target,JSON.stringify(receipt,null,2)+'\n',{flag:'wx',mode:0o600});
 return{path:target,students:receipt.migration.students,active:receipt.migration.active,paused:receipt.migration.paused,classes:receipt.migration.classes,loopback_port:18082,original_pilot_replaced:false};
}
module.exports={loadPlan,makeWrapper,validateResult,upload};
if(require.main===module){
 const args=process.argv.slice(2);
 if(args.length!==3||args[0]!=='--plan'||args[2]!=='--connect')console.log('需显式指定 --plan <私有计划文件> --connect。上传最小名册并建立独立的只读核对服务；不替换现有服务。密码只在 SSH 自己的终端提示中输入。');
 else upload(args[1]).then(r=>{console.log('核对副本部署成功：'+r.active+'名在读、'+r.paused+'名归档；原服务继续运行。');console.log('部署回执已在本机保存：'+r.path)}).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
}
