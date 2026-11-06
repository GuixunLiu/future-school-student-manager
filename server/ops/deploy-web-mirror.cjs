'use strict';
// SSH reads its password from the user's terminal. No credential is echoed or stored.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function pinned(f){if(!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('Pinned input required');const s=fs.lstatSync(f.path);if(!s.isFile()||s.isSymbolicLink()||s.size>150000)throw Error('Input scope differs');const b=fs.readFileSync(f.path);if(sha(b)!==f.sha256)throw Error('Input checksum differs');return b;}
function body(c){
 if(c.scope.work_directory!=='/opt/wxnotify/app/teacher-web-20261029101834-42da0325'||c.scope.source_sha256!=='6e7f8b864f7e4749153153df13a7d49e878737d923d3ae7ef607402b77740db9'||Object.keys(c.files).sort().join(',')!=='teacher_web/api.py,teacher_web/mirror_gateway.py,teacher_web/mirror_store.py,teacher_web/web/workspace.js'||!path.isAbsolute(c.output_dir))throw Error('Deployment scope differs');
 const p={installer:{sha256:c.installer.sha256,base64:pinned(c.installer).toString('base64')},files:Object.fromEntries(Object.entries(c.files).map(([n,f])=>[n,{sha256:f.sha256,base64:pinned(f).toString('base64')}])),tls:Object.fromEntries(Object.entries(c.tls).map(([n,f])=>[n,pinned(f).toString('base64')])),plan:c.scope};
 if(Object.keys(p.tls).sort().join(',')!=='server.crt,server.key')throw Error('TLS scope differs');
 const source="import base64,hashlib,json,os,sys\nsys.dont_write_bytecode=True\nos.umask(0o077)\nn=int(sys.stdin.buffer.readline(32));assert 0<n<=1048576\np=json.loads(sys.stdin.buffer.read(n));code=base64.b64decode(p['installer']['base64'],validate=True);assert hashlib.sha256(code).hexdigest()==p['installer']['sha256']\nns={'__name__':'web_mirror_install'};exec(compile(code,'<web-mirror-install>','exec'),ns)\nprint(json.dumps(ns['install'](p['plan'],p),separators=(',',':')),flush=True)\n";
 const b=Buffer.from(JSON.stringify(p));if(b.length>1048576)throw Error('Payload limit');return Buffer.concat([Buffer.from(Buffer.from(source).toString('base64')+'\n'+b.length+'\n'),b]);
}
async function main(dir,args){
 if(args.length>1||args.length&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=JSON.parse(fs.readFileSync(path.join(dir,'mirror-deploy.private.json'))),input=body(c);
 if(args[0]==='--self-check'){console.log(JSON.stringify({payload:'ok',bytes:input.length,encrypted_tablet_transport:true,network_connected:false}));return}
 console.log('将先备份现有网页，再加入平板加密同步。保留12个账号、身份、补签申请和历史资料；网页会短暂重启。');console.log('请在SSH提示处输入服务器密码。密码不保存；不使用教师密码，不修改其他项目或端口。');
 const boot=`env -u PYTHONPATH -u PYTHONHOME /opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python -u -c 'import base64,sys; exec(compile(base64.b64decode(sys.stdin.buffer.readline(262145).strip(),validate=True),"<mirror-bootstrap>","exec"))'`;
 const raw=await new Promise((resolve,reject)=>{const p=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2','wxnotify-deploy@47.97.226.219',boot],{windowsHide:true,stdio:['pipe','pipe','inherit']});let out='';p.on('error',reject);p.stdout.on('data',b=>{out+=b;if(out.length>65536){p.kill();reject(Error('Receipt limit'))}});p.on('close',n=>n===0?resolve(out):reject(Error('更新未完成，请保留错误，不要重复运行')));p.stdin.on('error',reject);p.stdin.end(input)});
 const r=JSON.parse(raw);if(r.format!=='future-school-web-mirror-installed'||r.device_id!==c.scope.device.id||r.accounts_preserved!==true||r.tls_verified!==true||r.dataset_unchanged!==true||r.hours_deducted!==false||r.expires_at!==1794391683||Object.entries(c.files).some(([n,f])=>r.new_files?.[n]!==f.sha256))throw Error('Receipt differs; inspect before continuing');
 fs.mkdirSync(c.output_dir,{recursive:true});const target=path.join(c.output_dir,'mirror-installed-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(r,null,2)+'\n',{flag:'wx',mode:0o600});console.log('平板同步服务更新成功，账号及资料保留，加密入口验证通过。');console.log('回执：'+target);console.log('请回到聊天告知结果，接着启用平板连接。不要再次运行本更新入口。');
}
module.exports={body,pinned};if(require.main===module)main(__dirname,process.argv.slice(2)).catch(e=>{console.error(e.message);process.exitCode=1});
