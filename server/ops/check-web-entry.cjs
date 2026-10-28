'use strict';
// Owner-run read-only inspection. Teachers will use the eventual HTTPS URL, not this SSH tool.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(dir){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'web-entry-check.private.json'),'utf8'));
 if(!/^[A-Za-z0-9.-]+$/.test(c.host)||!/^[a-z_][a-z0-9_-]{0,31}$/.test(c.user)||c.port!==22||!path.isAbsolute(c.output_dir)||!path.isAbsolute(c.helper?.path)||!/^[a-f0-9]{64}$/.test(c.helper.sha256)||fs.lstatSync(c.helper.path).isSymbolicLink()||sha(fs.readFileSync(c.helper.path))!==c.helper.sha256)throw Error('网页入口检查配置或指纹不符。');
 return c;
}
function wrapper(c){const raw=fs.readFileSync(c.helper.path);return 'import base64,hashlib\nraw=base64.b64decode("'+raw.toString('base64')+'")\nassert hashlib.sha256(raw).hexdigest()=="'+c.helper.sha256+'"\nexec(compile(raw,"<readonly-web-entry>","exec"),{"__name__":"__main__"})\n';}
function validate(text){
 const d=JSON.parse(text),keys=['format','schema_version','read_only','ports','port_inventory_available','tools','review_health','review_backend_ready','review_service','server_modified','database_opened','private_keys_read','public_entry_enabled','pending'];
 if(d.format!=='future-school-web-entry-inventory'||d.schema_version!==1||d.read_only!==true||keys.some(k=>!(k in d))||Object.keys(d).some(k=>!keys.includes(k))||['server_modified','database_opened','private_keys_read','public_entry_enabled'].some(k=>d[k]!==false)||!Array.isArray(d.ports)||!Array.isArray(d.pending))throw Error('服务器未返回预期的只读检查回执。');
 if(d.ports.some(p=>!p||![22,80,443,18080,18081,18082,18183,18443].includes(p.port)||typeof p.loopback_only!=='boolean'||Object.keys(p).some(k=>!['port','loopback_only'].includes(k))))throw Error('检查端口范围不符。');
 if(!d.tools||Object.keys(d.tools).sort().join(',')!=='caddy,certbot,nginx,openssl'||Object.values(d.tools).some(x=>typeof x!=='boolean'))throw Error('检查工具范围不符。');
 if(typeof d.review_backend_ready!=='boolean'||typeof d.port_inventory_available!=='boolean')throw Error('缺少核对服务或端口检查状态。');
 return d;
}
async function run(dir,args){
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=load(dir);
 if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',read_only:true,helper_verified:true,network_connected:false}));return;}
 console.log('这是负责人使用的一次性只读检查，教师以后只需网页登录。请输入SSH密码；本次不安装服务、不申请证书、不改端口。');
 const text=await new Promise((resolve,reject)=>{const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',c.user+'@'+c.host,'python3 -'],{windowsHide:true,stdio:['pipe','pipe','inherit']});let chunks=[],length=0,failed=false;const fail=e=>{if(!failed){failed=true;reject(e)}};child.on('error',fail);child.stdout.on('data',b=>{length+=b.length;if(length>65536){child.kill();fail(Error('服务器检查输出超限。'))}else chunks.push(b)});child.on('close',code=>{if(failed)return;code===0?resolve(Buffer.concat(chunks).toString('utf8')):fail(Error('SSH只读检查未完成。'))});child.stdin.on('error',fail);child.stdin.end(wrapper(c));});
 const result=validate(text);fs.mkdirSync(c.output_dir,{recursive:true,mode:0o700});if(fs.lstatSync(c.output_dir).isSymbolicLink())throw Error('输出目录不能是链接。');
 const file=path.join(c.output_dir,'web-entry-inventory-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(file,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
 console.log('只读检查完成；现有网页后台：'+(result.review_backend_ready?'正常':'需核对')+'。');console.log('HTTPS端口占用：'+(!result.port_inventory_available?'读取失败，状态未知':result.ports.some(p=>p.port===443)?'已有监听，需保留现有项目':'未检测到监听'));console.log('结果已保存：'+file);console.log('请返回聊天告知完成，之后按检查结果准备直接登录网址。');
}
module.exports={load,wrapper,validate};
if(require.main===module)run(__dirname,process.argv.slice(2)).catch(e=>{console.error('未完成：'+e.message);process.exitCode=1});
