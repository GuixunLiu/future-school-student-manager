'use strict';
// Explicit owner-approved HTTP deployment. No teacher passwords or database files are uploaded.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net'),http=require('node:http'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'http-viewer-plan.private.json'),'utf8'));
 if(net.isIP(c.host)!==4||!/^[a-z_][a-z0-9_-]{0,31}$/.test(c.user)||c.port!==22||c.public_origin!=='http://'+c.host+':18083'||c.owner_accepted_plain_http!==true||c.expires_after_days!==14||!path.isAbsolute(c.output_dir))throw Error('显式HTTP部署计划不符。');
 for(const key of ['installer','viewer']){const f=c[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('部署文件计划不符。');const st=fs.lstatSync(f.path);if(st.isSymbolicLink()||!st.isFile()||st.size>131072||sha(fs.readFileSync(f.path))!==f.sha256)throw Error('部署文件已变化，请重新核对。');}
 return c;
}
function wrapper(c){
 const stamp=new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14),work='/opt/wxnotify/app/http-viewer-'+stamp+'-'+crypto.randomBytes(4).toString('hex');
 const payload={work,config:{public_origin:c.public_origin,owner_accepted_plain_http:true,expires_after_days:14,viewer_sha256:c.viewer.sha256},files:[['installer','install-http-viewer.py'],['viewer','temporary-http-viewer.py']].map(([key,name])=>({name,sha256:c[key].sha256,base64:fs.readFileSync(c[key].path).toString('base64')}))};
 return `import base64,hashlib,json,os,re,sys\nfrom pathlib import Path\nsys.dont_write_bytecode=True\nos.umask(0o077)\np=json.loads(base64.b64decode('${Buffer.from(JSON.stringify(payload)).toString('base64')}'))\nwork=Path(p['work'])\nif os.geteuid()==0 or not re.fullmatch(r'/opt/wxnotify/app/http-viewer-[0-9]{14}-[a-f0-9]{8}',str(work)) or any(x.is_symlink() for x in (work,*work.parents)):raise ValueError('New ordinary-account directory required')\nwork.mkdir(mode=0o700,exist_ok=False)\nfor f in p['files']:\n raw=base64.b64decode(f['base64'],validate=True)\n assert f['name'] in {'install-http-viewer.py','temporary-http-viewer.py'} and len(raw)<=131072 and hashlib.sha256(raw).hexdigest()==f['sha256']\n with os.fdopen(os.open(work/f['name'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:stream.write(raw)\nns={'__name__':'viewer_installer','__file__':str(work/'install-http-viewer.py')}\nexec(compile((work/'install-http-viewer.py').read_bytes(),str(work/'install-http-viewer.py'),'exec'),ns)\nprint(json.dumps(ns['install'](work,p['config']),separators=(',',':')))\n`;
}
function validateResult(text,c){
 const r=JSON.parse(text);if(r.format!=='future-school-temporary-http-viewer-deployed'||r.schema_version!==1||r.public_url!==c.public_origin||r.service!=='wxnotify-http-viewer.service'||r.viewer_port!==18083||r.loopback_backend_port!==18082||r.viewer_sha256!==c.viewer.sha256||r.owner_accepted_plain_http!==true||r.local_ready!==true||r.existing_services_unchanged!==true||r.runtime_source_unchanged!==true||!/^\/opt\/wxnotify\/app\/http-viewer-\d{14}-[a-f0-9]{8}$/.test(r.work_directory)||['business_writes_enabled','data_realtime','database_directly_opened','nginx_or_firewall_modified','passwords_saved','external_access_verified'].some(k=>r[k]!==false)||!Number.isSafeInteger(r.expires_at)||r.expires_at<Date.now()/1000||r.expires_at>Date.now()/1000+15*86400||typeof r.service_status!=='string'||!r.service_status.includes('ActiveState=active')||!r.service_status.includes('SubState=running')||!r.service_status.includes('UnitFileState=enabled'))throw Error('服务器部署回执未通过，不能宣称入口可用。');return r;
}
function probe(url,expected){return new Promise(resolve=>{
 const req=http.get(url+'/viewer/health',{timeout:6000,headers:{'Cache-Control':'no-store'}},res=>{let body='';res.on('data',b=>{body+=b;if(Buffer.byteLength(body)>32768)req.destroy()});res.on('end',()=>{try{const h=JSON.parse(body);resolve(res.statusCode===200&&h.status==='ready'&&h.viewer==='temporary_http_read_only'&&h.gateway_sha256===expected.viewer_sha256&&h.expires_at===expected.expires_at&&h.backend_ready===true&&h.data_realtime===false&&h.wechat_enabled===false)}catch{resolve(false)}});res.on('error',()=>resolve(false))});req.on('timeout',()=>{req.destroy();resolve(false)});req.on('error',()=>resolve(false));
});}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=load(dir);
 if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',owner_accepted_plain_http:true,read_only_viewer:true,pinned_inputs:2,network_connected:false}));return;}
 console.log('将安装独立临时HTTP只读网页服务，沿用现有教师账号；HTTP不加密，此次按你确认的短期测试方案部署，有效14天。');
 console.log('请输入服务器SSH密码。不会修改原80网站/18082后台、开放防火墙或保存密码。');
 const text=await new Promise((resolve,reject)=>{let chunks=[],bytes=0,failed=false;const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',c.user+'@'+c.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});const fail=e=>{if(!failed){failed=true;reject(e)}};child.on('error',fail);child.stdout.on('data',b=>{bytes+=b.length;if(bytes>65536){child.kill();fail(Error('部署回执过大。'))}else chunks.push(b)});child.on('close',code=>{if(failed)return;code===0?resolve(Buffer.concat(chunks).toString('utf8')):fail(Error('部署未完成，请保留错误，不要反复运行。'))});child.stdin.on('error',fail);child.stdin.end(wrapper(c));});
 const result=validateResult(text,c);fs.mkdirSync(c.output_dir,{recursive:true,mode:0o700});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('回执目录不能是链接。');
 const target=path.join(c.output_dir,'http-viewer-deployed-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
 console.log('临时网页服务已启动，服务器本机验证通过。正在检查外网连接…');result.external_access_verified=await probe(result.public_url,result);result.external_teacher_login_tested=false;fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{mode:0o600});
 if(result.external_access_verified){console.log('外网入口已连通：'+result.public_url);console.log('网页登录沿用现有教师拼音账号及当前密码，机构weilai。此处显示服务器快照，尚未实时同步平板。');}
 else console.log('服务本机正常，但外网尚未连通。需要核对云安全组/防火墙是否放行TCP 18083；不要重新部署或关闭原网站。');
 console.log('结果已保存：'+target);console.log('回到聊天告知结果，再验收实际教师登录。');
}
module.exports={load,wrapper,validateResult,probe};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
