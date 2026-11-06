'use strict';
// Explicit operator invocation; one SSH connection, read-only SQLite, no remote script/output files.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {validateSnapshot}=require('./tablet-review/core.js');
const HELPER_SHA256='60ef91fb40faffff4c57edf95c31db0cdcd6bf72d097dbcebbc88ab8aada1383',MAX_BYTES=32*1024*1024;
function validateOptions(o){
  if(!o||typeof o.host!=='string'||!/^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$/.test(o.host)||typeof o.user!=='string'||!/^[a-z_][a-z0-9_-]{0,31}$/.test(o.user)||!Number.isInteger(o.port)||o.port<1||o.port>65535||typeof o.organization!=='string'||!/^[a-z0-9][a-z0-9_.-]{2,63}$/.test(o.organization)||typeof o.database!=='string'||!o.database.startsWith('/opt/wxnotify/app/')||!/^\/[A-Za-z0-9_./-]+\.sqlite$/.test(o.database)||o.database.split('/').some(x=>x==='.'||x==='..')||typeof o.outputDir!=='string'||!path.isAbsolute(o.outputDir))throw Error('Explicit host, deployment user, project database, organization and absolute local output directory are required');
}
function makeWrapper(o,helper){
  validateOptions(o);if(crypto.createHash('sha256').update(helper).digest('hex')!==HELPER_SHA256)throw Error('Read-only helper checksum changed; stopped');
  const configuration=Buffer.from(JSON.stringify({database:o.database,organization:o.organization})).toString('base64');
  return `import base64, hashlib, json\nfrom pathlib import Path\nraw=base64.b64decode('${helper.toString('base64')}')\nassert hashlib.sha256(raw).hexdigest()=='${HELPER_SHA256}'\nconfig=json.loads(base64.b64decode('${configuration}'))\nnamespace={'__name__':'readonly_roster_operator'}\nexec(compile(raw,'<readonly-roster-helper>','exec'),namespace)\ndata=namespace['snapshot'](Path(config['database']),config['organization'])\npayload=(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\\n').encode('utf-8')\nprint(json.dumps({'payload_base64':base64.b64encode(payload).decode('ascii'),'sha256':hashlib.sha256(payload).hexdigest()},separators=(',',':')))\n`;
}
function validateEnvelope(text,organization){
  let envelope;try{envelope=JSON.parse(text)}catch(_){throw Error('Server output was not a complete review envelope; no roster saved')}
  if(!envelope||typeof envelope.payload_base64!=='string'||envelope.payload_base64.length>MAX_BYTES||!/^[A-Za-z0-9+/]*={0,2}$/.test(envelope.payload_base64)||!/^[a-f0-9]{64}$/.test(envelope.sha256||''))throw Error('Server envelope is invalid');
  const bytes=Buffer.from(envelope.payload_base64,'base64');if(crypto.createHash('sha256').update(bytes).digest('hex')!==envelope.sha256)throw Error('Server payload checksum failed');
  let data;try{data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes))}catch(_){throw Error('Server roster encoding or JSON was invalid')}
  validateSnapshot(data);if(data.organization!==organization||data.source_access!=='read_only')throw Error('Server organization or read-only source marker differs');
  const rootKeys=['format','schema_version','complete','generated_at','organization','org_id','source_access','contains_private_data','classes','students'];
  const exact=(row,keys)=>row&&typeof row==='object'&&!Array.isArray(row)&&Object.keys(row).every(k=>keys.includes(k));
  if(!exact(data,rootKeys)||data.classes.some(c=>!exact(c,['id','name','course_name','teachers'])||c.teachers.some(t=>!exact(t,['id','name'])))||data.students.some(s=>!exact(s,['id','name','external_ref','class_id','remaining_units','active'])))throw Error('Unexpected fields in minimal server roster; nothing saved');
  return{data,bytes,sha256:envelope.sha256};
}
async function readServerRoster(options,{spawnImpl=spawn,helperBytes}={}){
  validateOptions(options);const helper=helperBytes||fs.readFileSync(path.join(__dirname,'export-roster-review.py')),wrapper=makeWrapper(options,helper);
  fs.mkdirSync(options.outputDir,{recursive:true,mode:0o700});if(fs.lstatSync(options.outputDir).isSymbolicLink()||!fs.statSync(options.outputDir).isDirectory())throw Error('Use an ordinary local private directory');
  const args=['-p',String(options.port),'-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',options.user+'@'+options.host,'python3 -'];
  const stdout=await new Promise((resolve,reject)=>{let chunks=[],total=0,failed=false;const child=spawnImpl('ssh',args,{stdio:['pipe','pipe','inherit'],windowsHide:false});
    const fail=error=>{if(!failed){failed=true;reject(error)}};child.on('error',()=>fail(Error('SSH could not start')));child.stdout.on('data',chunk=>{total+=chunk.length;if(total>MAX_BYTES){child.kill();fail(Error('Server output exceeded review limit'))}else chunks.push(chunk)});child.on('close',code=>{if(failed)return;if(code!==0)return fail(Error('SSH read-only check failed; no roster was saved'));resolve(Buffer.concat(chunks).toString('utf8'))});child.stdin.on('error',()=>fail(Error('SSH input ended before the read-only check completed')));child.stdin.end(wrapper);
  });
  const verified=validateEnvelope(stdout,options.organization),stamp=new Date().toISOString().replace(/[:.]/g,'-'),target=path.join(options.outputDir,'server-roster-review-'+stamp+'-'+crypto.randomBytes(4).toString('hex')+'.json');
  fs.writeFileSync(target,verified.bytes,{flag:'wx',mode:0o600});fs.writeFileSync(target+'.sha256',verified.sha256+'  '+path.basename(target)+'\n',{flag:'wx',mode:0o600});
  return{path:target,sha256:verified.sha256,organization:verified.data.organization,classes:verified.data.classes.length,students:verified.data.students.length,source_access:'read_only',remote_files_written:false,server_modified:false,contains_private_data:true};
}
function parseOptions(args){const o={port:22},keys={'--host':'host','--user':'user','--port':'port','--database':'database','--organization':'organization','--output-dir':'outputDir'};let connect=false;for(let i=0;i<args.length;i++){if(args[i]==='--connect'){connect=true;continue}const key=keys[args[i]];if(!key||!args[i+1]||Object.prototype.hasOwnProperty.call(o,key)&&key!=='port')throw Error('Unknown, repeated or missing argument');o[key]=key==='port'?Number(args[++i]):args[++i];}return{o,connect};}
module.exports={HELPER_SHA256,validateOptions,makeWrapper,validateEnvelope,readServerRoster,parseOptions};
if(require.main===module){(async()=>{const{o,connect}=parseOptions(process.argv.slice(2));if(!connect){console.log('只读服务器名册核对：显式提供连接参数及 --connect 后运行。经SSH标准输入执行只读程序，不部署业务代码、写数据库、停止服务或更改权限。');return}console.log('本次只读取指定机构的最小名册。终端如提示密码，请输入服务器密码；密码不会写入文件或参数。');const result=await readServerRoster(o);console.log(JSON.stringify(result,null,2));console.log('核对清单已在本机保存，请返回聊天告诉我完成。未执行导入或发送微信。')})().catch(e=>{console.error('STOP: '+e.message);process.exitCode=1})}
