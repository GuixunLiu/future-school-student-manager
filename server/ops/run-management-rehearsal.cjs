'use strict';
// One operator SSH password prompt. Upload reviewed inputs into a NEW private
// directory, rehearse a complete copy, return a small receipt, never switch service.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {validateOptions}=require('./read-server-roster.cjs');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(planPath){
 const plan=JSON.parse(fs.readFileSync(planPath,'utf8'));validateOptions(plan.options);
 if(plan.format!=='future-school-remote-rehearsal-v1')throw Error('Explicit rehearsal plan required');
 const payloads={};
 for(const [name,max]of [['code',2*1024*1024],['plan',8*1024*1024],['archive',10*1024*1024],['review',32*1024*1024]]){
  const item=plan[name];if(!item||!path.isAbsolute(item.path)||!/^[a-f0-9]{64}$/.test(item.sha256)||fs.lstatSync(item.path).isSymbolicLink()||fs.statSync(item.path).size>max)throw Error('Explicit bounded ordinary input required');
  const raw=fs.readFileSync(item.path);if(sha(raw)!==item.sha256)throw Error('Reviewed input changed');payloads[name]=raw;
 }
 const proposal=JSON.parse(payloads.plan),review=JSON.parse(payloads.review);
 if(proposal.baseline.server_review_sha256!==sha(payloads.review)||proposal.baseline.history_archive_sha256!==sha(payloads.archive)||proposal.organization!==plan.options.organization||review.organization!==plan.options.organization||proposal.activation_authorized!==false||proposal.proposal_only!==true)throw Error('Rehearsal baseline differs');
 const output=path.resolve(plan.options.outputDir),repo=path.resolve(__dirname,'../..'),relative=path.relative(repo,output);
 if(!relative||relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))throw Error('Private receipts must stay outside Git');
 return{plan,payloads};
}
function wrapper(checked){
 const {plan,payloads}=checked;
 const config=Buffer.from(JSON.stringify({database:plan.options.database,organization:plan.options.organization,
   inputs:Object.fromEntries(Object.entries(payloads).map(([k,b])=>[k,{base64:b.toString('base64'),sha256:sha(b)}]))})).toString('base64');
 return `import base64,hashlib,io,json,os,sqlite3,sys,tempfile,zipfile\nfrom pathlib import Path\nfrom contextlib import closing\nconfig=json.loads(base64.b64decode('${config}'))\ntry:\n inputs={}\n for name,item in config['inputs'].items():\n  raw=base64.b64decode(item['base64'],validate=True)\n  if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Input checksum differs')\n  inputs[name]=raw\n with zipfile.ZipFile(io.BytesIO(inputs['code'])) as z:\n  manifest=json.loads(z.read('MANIFEST.json'))\n  if manifest['format']!='future-school-rehearsal-code-v1' or manifest['data_included'] is not False or manifest['service_installer'] is not False:raise ValueError('Code-only package required')\n  if len(z.namelist())!=len(set(z.namelist())) or set(z.namelist())!=set(manifest['files'])|{'MANIFEST.json'} or sum(i.file_size for i in z.infolist())>1048576:raise ValueError('Unexpected code package')\n  files={}\n  for name,checksum in manifest['files'].items():\n   if name not in {'README.md'} and (not name.startswith(('server/business/','server/management/','server/ops/')) or not name.endswith('.py')):raise ValueError('Unexpected code file')\n   if any(p in ('','.', '..') for p in name.split('/')) or '\\\\' in name:raise ValueError('Unexpected code path')\n   raw=z.read(name)\n   if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Code checksum differs')\n   files[name]=raw\n root=Path(tempfile.mkdtemp(prefix='.management-rehearsal-',dir='/opt/wxnotify/app'))\n os.chmod(root,0o700)\n for name,raw in files.items():\n  dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)\n  fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n  with os.fdopen(fd,'wb') as f:f.write(raw)\n sys.path.insert(0,str(root/'server'))\n from management.initialization import rehearse\n from management.history_boundary import load\n receipt=rehearse(Path(config['database']),root/'result',plan_raw=inputs['plan'],plan_sha256=config['inputs']['plan']['sha256'],archive_raw=inputs['archive'],review_raw=inputs['review'])\n with closing(sqlite3.connect((root/'result/rehearsal.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:\n  db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')\n  org=db.execute('SELECT id FROM organizations WHERE slug=?',(config['organization'],)).fetchone()\n  boundary=load(db,org['id'])\n fd=os.open(root/'result/history-boundary.private.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n with os.fdopen(fd,'w') as f:json.dump(boundary,f,sort_keys=True,separators=(',',':'))\n result={k:receipt[k] for k in ('format','rehearsal_only','activation_authorized','source_written','events_replayed','accounts_preserved','legacy_applications_preserved','scope','history_counts','copy_sha256')}\n result.update(status='ok',remote_directory=str(root),boundary_sha256=boundary['boundary_sha256'],service_switched=False,tablet_modified=False)\n print(json.dumps(result,sort_keys=True))\nexcept Exception as error:\n print(json.dumps(dict(status='stopped',error=type(error).__name__,source_changed=str(error).startswith('Current copied database differs'),service_switched=False,tablet_modified=False)))\n sys.exit(1)\n`;
}
async function run(planPath,{spawnImpl=spawn}={}){
 const checked=load(planPath),o=checked.plan.options,script=wrapper(checked);
 fs.mkdirSync(o.outputDir,{recursive:true,mode:0o700});if(fs.lstatSync(o.outputDir).isSymbolicLink())throw Error('Ordinary private output required');
 const relative=path.relative(fs.realpathSync(path.resolve(__dirname,'../..')),fs.realpathSync(o.outputDir));
 if(!relative||relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))throw Error('Receipt output resolves inside Git');
 const bytes=await new Promise((resolve,reject)=>{let chunks=[],size=0;const child=spawnImpl('ssh',['-p',String(o.port),'-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',o.user+'@'+o.host,'python3 -'],{stdio:['pipe','pipe','inherit'],windowsHide:false});
  child.on('error',()=>reject(Error('SSH could not start')));child.stdin.on('error',()=>reject(Error('SSH input ended')));
  child.stdout.on('data',part=>{size+=part.length;if(size>65536){child.kill();reject(Error('Unexpected large receipt'));}else chunks.push(part);});
  child.on('close',code=>{const raw=Buffer.concat(chunks).toString('utf8');if(code!==0){try{if(JSON.parse(raw).source_changed)return reject(Error('服务器资料已有变化，原服务未切换；需要核对最新资料后再演练。'));}catch(_){}return reject(Error('独立副本演练停止，原服务未切换；请保留窗口结果。'));}resolve(raw);});child.stdin.end(script);
 });
 const result=JSON.parse(bytes);
 const keys='format rehearsal_only activation_authorized source_written events_replayed accounts_preserved legacy_applications_preserved scope history_counts copy_sha256 status remote_directory boundary_sha256 service_switched tablet_modified';
 if(Object.keys(result).sort().join('|')!==keys.split(' ').sort().join('|')||Object.keys(result.scope||{}).sort().join('|')!=='active_students|archived_students|current_classes'||Object.values(result.scope).some(v=>!Number.isSafeInteger(v)||v<0)||Object.keys(result.history_counts||{}).sort().join('|')!=='attendance|class_reschedules|classes|extra_lessons|leaves|student_reschedules|students|transactions'||Object.values(result.history_counts).some(v=>!Number.isSafeInteger(v)||v<0||v>10000))throw Error('Unexpected receipt fields');
 if(result.status!=='ok'||result.rehearsal_only!==true||result.activation_authorized!==false||result.source_written!==false||result.events_replayed!==0||result.accounts_preserved!==true||result.legacy_applications_preserved!==true||result.service_switched!==false||result.tablet_modified!==false||!result.scope||!/^[a-f0-9]{64}$/.test(result.copy_sha256)||!/^[a-f0-9]{64}$/.test(result.boundary_sha256)||!/^\/opt\/wxnotify\/app\/\.management-rehearsal-[a-z0-9_]+$/.test(result.remote_directory))throw Error('Incomplete rehearsal receipt');
 const name='full-copy-rehearsal-'+new Date().toISOString().replace(/[:.]/g,'-')+'.private.json',target=path.join(o.outputDir,name);
 fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});return{...result,path:target};
}
module.exports={load,wrapper,run};
if(require.main===module){(async()=>{
 const args=process.argv.slice(2);if(args.length!==2||!['--self-check','--connect'].includes(args[0]))throw Error('Use --self-check or --connect with an explicit private plan');
 const checked=load(args[1]);if(args[0]==='--self-check'){console.log(JSON.stringify({files:4,checksums_verified:true,network_connected:false,service_switched:false}));return;}
 console.log('只在服务器新建私有完整副本并演练期初和历史封存，原网页继续使用。');
 console.log('请在SSH提示处输入服务器密码；密码不保存，不使用教师密码。');
 const result=await run(args[1]);console.log('真实完整副本演练完成：'+result.scope.active_students+'名在读、'+result.scope.archived_students+'名归档、'+result.scope.current_classes+'个班；账号和历史保留。');
 console.log('回执已保存：'+result.path);console.log('原网页未切换，平板仍正常使用。请回聊天告知结果。');
})().catch(e=>{console.error('未完成：'+e.message);process.exitCode=1;});}
