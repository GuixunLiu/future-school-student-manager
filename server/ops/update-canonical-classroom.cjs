'use strict';
// Fixed code-only updater for the active canonical service, not a migration.
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{spawn}=require('child_process');
const root=path.resolve(__dirname,'../..'),names=['management/api.py','management/reports.py','management/web/workspace.css','management/web/workspace.js'];
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function pinned(item,max){if(!item||!path.isAbsolute(item.path)||fs.lstatSync(item.path).isSymbolicLink()||fs.statSync(item.path).size>max)throw Error('Pinned ordinary source required');const bytes=fs.readFileSync(item.path);if(sha(bytes)!==item.sha256)throw Error('Reviewed update source changed');return bytes;}
function load(file){const cfg=JSON.parse(fs.readFileSync(file,'utf8'));if(cfg.format!=='future-school-classroom-operator-v1'||Object.keys(cfg.files||{}).sort().join('|')!==names.join('|'))throw Error('Exact classroom update required');
 const files={};for(const name of names){const v=cfg.files[name];if(path.resolve(v.path)!==path.resolve(root,'server',name)||!/^[a-f0-9]{64}$/.test(v.previous_sha256))throw Error('Only reviewed classroom sources allowed');const raw=pinned(v,200000);files[name]={previous_sha256:v.previous_sha256,sha256:v.sha256,base64:raw.toString('base64')};}
 if(path.resolve(cfg.broker.path)!==path.join(root,'server/ops/update-canonical-classroom.py'))throw Error('Fixed update broker required');const code=pinned(cfg.broker,100000);
 const output=path.resolve(cfg.output);if(!path.isAbsolute(cfg.output)||!path.relative(root,output).startsWith('..'+path.sep)||fs.existsSync(output)||fs.lstatSync(path.dirname(output)).isSymbolicLink())throw Error('New private receipt path outside Git required');
 return{code,payload:{format:'future-school-classroom-update-v1',files},output};
}
async function run(file){const c=load(file),hash=sha(c.code),loader="import sys,json,base64,hashlib; v=json.loads(sys.stdin.readline(200000)); b=base64.b64decode(v['code'],validate=True); assert hashlib.sha256(b).hexdigest()=='"+hash+"'; exec(compile(b,'classroom-update','exec'))";
 const command="python3 -u -c '"+loader.replaceAll("'","'\\''")+"'";
 console.log('只更新今日课程和历史签到网页；先在服务器备份，不重建账号、不重新迁移、不恢复旧数据库。');console.log('请在 SSH password 提示处输入服务器密码；密码不会保存。');
 const child=spawn('ssh',['-p','22','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','wxnotify-deploy@47.97.226.219',command],{stdio:['pipe','pipe','inherit'],windowsHide:false});
 const result=await new Promise((resolve,reject)=>{let raw='';const timer=setTimeout(()=>{child.stdin.end();reject(Error('更新超时，请保留窗口，不要重复运行'));},180000);child.on('error',e=>{clearTimeout(timer);reject(e);});child.stdout.on('data',b=>{raw+=b.toString();if(raw.length>200000){child.stdin.end();reject(Error('结果过大，请核对'));}});child.on('close',code=>{clearTimeout(timer);try{const value=JSON.parse(raw);if(code!==0||value.ok!==true)throw Error('更新停止，请保留窗口供核对，未恢复旧数据库：'+String(value.detail||value.kind||'check failed').replace(/[\r\n]/g,' ').slice(0,160));resolve(value.result);}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({code:c.code.toString('base64')})+'\n'+JSON.stringify(c.payload)+'\n');});
 fs.writeFileSync(c.output,JSON.stringify({checked_at:new Date().toISOString(),...result},null,2),{flag:'wx',mode:0o600});
 console.log('教师今日课程与历史签到更新成功。');console.log('请刷新原网页并重新登录，教师默认进入今日课程。');console.log('回执已保存在本机：'+c.output);
}
if(require.main===module){const [mode,file]=process.argv.slice(2);if(!['--self-check','--run'].includes(mode)||!file)throw Error('Explicit reviewed update plan required');if(mode==='--self-check'){load(file);console.log('更新包自检通过；未连接服务器。');}else run(file).catch(e=>{console.error(e.message);process.exitCode=1;});}
module.exports={load};
