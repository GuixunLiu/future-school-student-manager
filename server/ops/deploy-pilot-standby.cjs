'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'standby-deploy.private.json'),'utf8'));
 if(net.isIP(c.host)!==4||!/^[a-z_][a-z0-9_-]{0,31}$/.test(c.user)||c.port!==22||c.standby_only!==true||!path.isAbsolute(c.output_dir))throw Error('独立待启用服务范围不符。');
 for(const key of ['installer','runtime','helper','receipt']){const f=c[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('缺少固定输入。');const st=fs.lstatSync(f.path);if(st.isSymbolicLink()||!st.isFile()||st.size>131072||sha(fs.readFileSync(f.path))!==f.sha256)throw Error('部署输入已变化，请重新核对。');}
 const r=JSON.parse(fs.readFileSync(c.receipt.path,'utf8'));if(r.format!=='future-school-offline-pilot-stage'||r.stage_complete!==true||r.service_started!==false||r.device_enabled!==false||r.wechat_enabled!==false||!/^\/opt\/wxnotify\/app\/single-class-offline-\d{14}-[a-f0-9]{8}$/.test(r.work_directory))throw Error('尚无真实离线副本回执。');
 return c;
}
function wrapper(c,pause=false){
 const r=JSON.parse(fs.readFileSync(c.receipt.path,'utf8')),config={stage_directory:r.work_directory,stage_receipt:Object.fromEntries(Object.entries(r).filter(([k])=>k!=='work_directory'))};
 const files={};for(const [key,name] of [['runtime','pilot-standby.py'],['helper','stage-single-class-pilot.py']])files[name]={sha256:c[key].sha256,base64:fs.readFileSync(c[key].path).toString('base64')};
 const p={config,files,installer:fs.readFileSync(c.installer.path).toString('base64'),installer_sha256:c.installer.sha256};
 return `import base64,hashlib,json,os,sys\nsys.dont_write_bytecode=True\nos.umask(0o077)\np=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(p)).toString('base64')}'))\nraw=base64.b64decode(p['installer'],validate=True)\nassert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==p['installer_sha256']\nns={'__name__':'standby_installer'}\nexec(compile(raw,'<standby-installer>','exec'),ns)\nprint(json.dumps(ns['${pause?'pause':'install'}'](p['config']${pause?'':",p['files']"}),separators=(',',':')))\n`;
}
function validateResult(text,c,pause=false){
 const r=JSON.parse(text),old=JSON.parse(fs.readFileSync(c.receipt.path,'utf8'));
 if(r.schema_version!==1||r.stage_directory!==old.work_directory||r.unit!=='wxnotify-single-class-standby.service'||r.existing_services_modified!==false)throw Error('服务回执范围不符。');
 if(pause){if(r.format!=='future-school-pilot-standby-paused'||!r.service_state?.includes('ActiveState=inactive')||!r.service_state?.includes('UnitFileState=disabled'))throw Error('暂停状态未确认。');return r;}
 if(r.format!=='future-school-pilot-standby-installed'||r.runtime_sha256!==c.runtime.sha256||r.port!==18084||r.loopback_only!==true||r.standby_ready!==true||r.source_candidate_unchanged!==true||r.selected_students!==old.selected_students||['business_writes_enabled','lesson_created','device_enabled','wechat_enabled'].some(k=>r[k]!==false)||!r.service_state?.includes('ActiveState=active')||!r.service_state?.includes('SubState=running')||!r.service_state?.includes('UnitFileState=enabled'))throw Error('待启用服务未通过验收。');return r;
}
async function run(dir,args){
 if(args.length>1||args.length===1&&!['--self-check','--pause'].includes(args[0]))throw Error('Unsupported arguments');const c=load(dir),pause=args[0]==='--pause';
 if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',pinned_inputs:4,loopback_only:true,standby_only:true,network_connected:false}));return;}
 console.log(pause?'仅暂停新建的单班待启用服务，保留全部资料。':'将从已核对候选制作独立工作库，启动只读待启用服务；原教师网页继续使用。暂不安排课次或连接平板。');
 console.log('请在SSH提示输入服务器密码；不保存密码，不使用教师凭证。');
 const text=await new Promise((resolve,reject)=>{let chunks=[],bytes=0,failed=false;const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',c.user+'@'+c.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});const fail=e=>{if(!failed){failed=true;reject(e)}};child.on('error',fail);child.stdout.on('data',b=>{bytes+=b.length;if(bytes>65536){child.kill();fail(Error('回执过大。'))}else chunks.push(b)});child.on('close',code=>{if(failed)return;code===0?resolve(Buffer.concat(chunks).toString('utf8')):fail(Error('操作未完成，请保留提示，不要重复执行或修改旧服务。'))});child.stdin.on('error',fail);child.stdin.end(wrapper(c,pause));});
 const result=validateResult(text,c,pause);fs.mkdirSync(c.output_dir,{recursive:true,mode:0o700});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('回执目录不能为链接。');const file=path.join(c.output_dir,(pause?'standby-paused-':'standby-installed-')+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(file,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
 console.log(pause?'单班待启用服务已暂停，原教师网页继续运行。':'单班独立服务部署成功：只读待启用，服务器本机验证通过；平板同步和微信仍关闭。');console.log('回执已保存：'+file);
}
module.exports={load,wrapper,validateResult};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
