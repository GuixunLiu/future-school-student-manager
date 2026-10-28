'use strict';
// Existing viewer only: code replacement without account access, database access or deadline extension.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net'),http=require('node:http'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'http-viewer-patch.private.json'),'utf8'));
 if(net.isIP(c.host)!==4||!/^[a-z_][a-z0-9_-]{0,31}$/.test(c.user)||c.port!==22||c.public_origin!=='http://'+c.host+':18083'||!/^\/opt\/wxnotify\/app\/http-viewer-\d{14}-[a-f0-9]{8}$/.test(c.work_directory)||!Number.isSafeInteger(c.expires_at)||c.expires_at<=Date.now()/1000||!path.isAbsolute(c.output_dir)||!/^[a-f0-9]{64}$/.test(c.expected_old_sha256))throw Error('现有入口修复计划不符。');
 for(const key of ['patcher','viewer']){const f=c[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('修复文件计划不符。');const st=fs.lstatSync(f.path);if(st.isSymbolicLink()||!st.isFile()||st.size>131072||sha(fs.readFileSync(f.path))!==f.sha256)throw Error('修复代码已变化，请重新核对。');}
 return c;
}
function wrapper(c){
 const config={work_directory:c.work_directory,public_origin:c.public_origin,expected_old_sha256:c.expected_old_sha256,new_sha256:c.viewer.sha256,expires_at:c.expires_at};
 const p={config,patcher:fs.readFileSync(c.patcher.path).toString('base64'),patcher_sha256:c.patcher.sha256,viewer:fs.readFileSync(c.viewer.path).toString('base64')};
 return `import base64,hashlib,json,os,sys\nsys.dont_write_bytecode=True\nos.umask(0o077)\np=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(p)).toString('base64')}'))\nraw=base64.b64decode(p['patcher'],validate=True)\nassert len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==p['patcher_sha256']\nns={'__name__':'viewer_patcher'}\nexec(compile(raw,'<viewer-patcher>','exec'),ns)\nprint(json.dumps(ns['install'](p['config'],base64.b64decode(p['viewer'],validate=True)),separators=(',',':')))\n`;
}
function validateResult(text,c){
 const r=JSON.parse(text);if(r.format!=='future-school-http-viewer-logout-fixed'||r.schema_version!==1||r.public_url!==c.public_origin||r.old_sha256!==c.expected_old_sha256||r.new_sha256!==c.viewer.sha256||r.expires_at!==c.expires_at||r.logout_without_json_reaches_backend!==true||r.anonymous_logout_rejected!==true||['unit_config_changed','database_opened','teacher_credentials_used','expiry_changed','other_services_modified'].some(k=>r[k]!==false))throw Error('修复回执未通过核对。');return r;
}
function request(c,method,route){return new Promise(resolve=>{
 const req=http.request(c.public_origin+route,{method,timeout:6000,headers:{'Content-Length':'0','Cache-Control':'no-store'}},res=>{let body='';res.on('data',b=>{body+=b;if(Buffer.byteLength(body)>32768)req.destroy()});res.on('end',()=>{try{resolve({status:res.statusCode,data:JSON.parse(body)})}catch{resolve(null)}});res.on('error',()=>resolve(null))});req.on('timeout',()=>{req.destroy();resolve(null)});req.on('error',()=>resolve(null));req.end();
});}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=load(dir);
 if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',pinned_inputs:2,network_connected:false,uses_teacher_credentials:false,changes_expiry:false}));return;}
 console.log('仅修复现有临时网页的退出兼容问题；先备份入口代码，启动失败自动恢复，不修改账号、资料、原后台或有效期。');
 console.log('请在SSH提示输入服务器密码；不保存密码，不使用教师密码。');
 const text=await new Promise((resolve,reject)=>{let chunks=[],bytes=0,failed=false;const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',c.user+'@'+c.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});const fail=e=>{if(!failed){failed=true;reject(e)}};child.on('error',fail);child.stdout.on('data',b=>{bytes+=b.length;if(bytes>65536){child.kill();fail(Error('修复回执过大。'))}else chunks.push(b)});child.on('close',code=>{if(failed)return;code===0?resolve(Buffer.concat(chunks).toString('utf8')):fail(Error('修复未完成，请保留错误并告知，不要反复运行。'))});child.stdin.on('error',fail);child.stdin.end(wrapper(c));});
 const result=validateResult(text,c);fs.mkdirSync(c.output_dir,{recursive:true,mode:0o700});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('回执目录不能是链接。');
 const target=path.join(c.output_dir,'http-viewer-logout-fixed-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
 const health=await request(c,'GET','/viewer/health'),logout=await request(c,'POST','/v1/auth/logout');
 result.external_anonymous_checks_passed=!!(health?.status===200&&health.data.gateway_sha256===c.viewer.sha256&&health.data.expires_at===c.expires_at&&health.data.backend_ready===true&&logout?.status===401);
 result.external_teacher_login_tested=false;fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{mode:0o600});
 console.log(result.external_anonymous_checks_passed?'退出兼容修复完成，外网匿名检查通过。':'服务器修复完成，但外网复测未通过，请保留回执告知。');
 console.log('网址：'+c.public_origin+'；机构weilai，使用现有教师拼音账号和当前密码登录。');console.log('结果已保存：'+target);
}
module.exports={load,wrapper,validateResult};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
