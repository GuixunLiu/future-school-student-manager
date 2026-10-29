'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),dir=path.resolve(repo,'../../outputs/private-web-mirror-2026-10-29'),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
async function main(){
 const c=JSON.parse(fs.readFileSync(path.join(dir,'connection-fix-plan.json'))),read=f=>{const b=fs.readFileSync(f.path);if(sha(b)!==f.sha256||b.length>20000)throw Error('Fixed source checksum differs');return b};
 const p={plan:{work_directory:'/opt/wxnotify/app/teacher-web-20261029101834-42da0325'},installer:{sha256:c.installer.sha256,base64:read(c.installer).toString('base64')},files:{'teacher_web/mirror_gateway.py':{sha256:c.gateway.sha256,base64:read(c.gateway).toString('base64')}}};
 if(process.argv[2]==='--self-check'){console.log(JSON.stringify({patch:'ok',database_written:false,bytes:JSON.stringify(p).length}));return}
 if(process.argv.length!==2)throw Error('Unsupported arguments');console.log('修复同步入口的连接名额释放；只更新一个入口文件，不修改账号、平板连接文件或数据库。');console.log('请在SSH提示处输入服务器密码。修复后会连续检查60次HTTP/加密连接，密码不保存。');
 const source="import base64,hashlib,json,os,sys\nos.umask(0o077);sys.dont_write_bytecode=True\nn=int(sys.stdin.buffer.readline(32));assert 0<n<131072\np=json.loads(sys.stdin.buffer.read(n));code=base64.b64decode(p['installer']['base64'],validate=True);assert hashlib.sha256(code).hexdigest()==p['installer']['sha256']\nns={'__name__':'mirror_connection_fix'};exec(compile(code,'<mirror-connection-fix>','exec'),ns)\nprint(json.dumps(ns['install'](p['plan'],p),separators=(',',':')),flush=True)\n";
 const b=Buffer.from(JSON.stringify(p)),input=Buffer.concat([Buffer.from(Buffer.from(source).toString('base64')+'\n'+b.length+'\n'),b]);
 const boot=`env -u PYTHONPATH -u PYTHONHOME /opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python -u -c 'import base64,sys; exec(compile(base64.b64decode(sys.stdin.buffer.readline(262145).strip(),validate=True),"<connection-fix-bootstrap>","exec"))'`;
 const raw=await new Promise((resolve,reject)=>{const p=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','wxnotify-deploy@47.97.226.219',boot],{windowsHide:true,stdio:['pipe','pipe','inherit']});let out='';p.on('error',reject);p.stdout.on('data',b=>{out+=b;if(out.length>32768){p.kill();reject(Error('Receipt limit'))}});p.on('close',n=>n===0?resolve(out):reject(Error('修复未完成，请保留错误，不要重复运行')));p.stdin.on('error',reject);p.stdin.end(input)});
 const r=JSON.parse(raw);if(r.format!=='future-school-mirror-connections-fixed'||r.gateway_sha256!==c.gateway.sha256||r.consecutive_connections!==60||r.tls_verified!==true||r.database_written!==false||r.device_credentials_changed!==false)throw Error('Fix receipt differs');
 fs.writeFileSync(path.join(dir,'connections-fixed-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json'),JSON.stringify(r,null,2)+'\n',{flag:'wx'});console.log('同步连接修复成功，连续60次连接通过。请回到平板，再点“连接网页 → 验证并启用”，然后告知状态。');
}
if(require.main===module)main().catch(e=>{console.error(e.message);process.exitCode=1});
