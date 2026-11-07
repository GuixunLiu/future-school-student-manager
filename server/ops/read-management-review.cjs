'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {validateOptions,parseOptions}=require('./read-server-roster.cjs'),{validateSnapshot}=require('./tablet-review/core.js');
const HELPER_SHA256='6bf8b83046cd89b542723dd8bcd4b48fc77c15eb610cb2dcc43e494597833f11',MAX_BYTES=32*1024*1024;
const fail=()=>{throw Error('Incomplete or unexpected management review source');};
const id=x=>typeof x==='string'&&/^[a-f0-9]{32}$/.test(x),local=x=>typeof x==='string'&&/^[0-9]{1,20}$/.test(x);
const text=x=>typeof x==='string'&&x.length<=200&&!/[\u0000-\u001f\u007f]/.test(x),integer=Number.isSafeInteger;
function exact(row,fields){if(!row||typeof row!=='object'||Array.isArray(row)||Object.keys(row).sort().join('|')!==fields.split(' ').sort().join('|'))fail();}
function list(value,max){if(!Array.isArray(value)||value.length>max)fail();return value;}
function validateSource(d,organization){
 exact(d,'format schema_version complete generated_at organization org_id source_access contains_private_data classes students archive_available archived_students source_links_available source_links staff mirror');
 if(d.format!=='future-school-management-review-v1'||d.schema_version!==1||d.complete!==true||d.organization!==organization||d.source_access!=='read_only'||d.contains_private_data!==true||typeof d.archive_available!=='boolean'||typeof d.source_links_available!=='boolean'||!text(d.generated_at)||!Number.isFinite(Date.parse(d.generated_at)))fail();
 validateSnapshot({...d,format:'wxnotify-roster-review'});
 const ids=new Set(d.students.map(s=>s.id)),refs=new Set(d.students.map(s=>s.external_ref));
 for(const c of d.classes){exact(c,'id name course_name teachers');for(const t of c.teachers)exact(t,'id name');}
 for(const s of d.students)exact(s,'id name external_ref class_id remaining_units active');
 for(const a of list(d.archived_students,10000)){
  exact(a,'id name external_ref remaining_units previous_class_name');
  if(!id(a.id)||ids.has(a.id)||!text(a.name)||!a.name||!text(a.external_ref)||!a.external_ref||refs.has(a.external_ref)||!integer(a.remaining_units)||a.remaining_units< -1000000||a.remaining_units>10000000||!text(a.previous_class_name))fail();
  ids.add(a.id);refs.add(a.external_ref);
 }
 if(!d.archive_available&&d.archived_students.length||!d.source_links_available&&d.source_links.length)fail();
 const classIds=new Set(d.classes.map(c=>c.id)),links=new Set();
 for(const r of list(d.source_links,20000)){
  exact(r,'kind source_id entity_id bundle_sha256');const key=r.kind+':'+r.source_id;
  if(!['class','student'].includes(r.kind)||!local(r.source_id)||!id(r.entity_id)||!/^[a-f0-9]{64}$/.test(r.bundle_sha256)||links.has(key)||!(r.kind==='class'?classIds:ids).has(r.entity_id))fail();links.add(key);
 }
 const staff=new Map();
 for(const s of list(d.staff,1000)){
  exact(s,'id login display_name active roles');
  if(!id(s.id)||staff.has(s.id)||!text(s.login)||!s.login||!text(s.display_name)||!s.display_name||typeof s.active!=='boolean'||!Array.isArray(s.roles)||!s.roles.length||new Set(s.roles).size!==s.roles.length||s.roles.some(r=>!['admin','teacher','principal','assessor'].includes(r)))fail();staff.set(s.id,s);
 }
 const m=d.mirror;exact(m,'present revision received_at source_sha256 as_of_date source_captured_at source_records device_active device_expires_at classes lessons covered_weeks');
 if(typeof m.present!=='boolean')fail();
 if(m.present){if(!integer(m.revision)||m.revision<1||!integer(m.received_at)||m.received_at<0||!/^[a-f0-9]{64}$/.test(m.source_sha256)||!/^\d{4}-\d{2}-\d{2}$/.test(m.as_of_date)||!text(m.source_captured_at)||!integer(m.source_records)||m.source_records<0||typeof m.device_active!=='boolean'||!integer(m.device_expires_at))fail();}
 else if(['revision','received_at','source_sha256','as_of_date','source_captured_at','source_records','device_active','device_expires_at'].some(k=>m[k]!==null)||m.classes.length||m.lessons.length||m.covered_weeks.length)fail();
 const localClasses=new Set(),lessonKeys=new Set();
 for(const c of list(m.classes,1000)){
  exact(c,'local_id name course_name teacher teacher_ids');
  if(!local(c.local_id)||localClasses.has(c.local_id)||!text(c.name)||!text(c.course_name)||!text(c.teacher)||!Array.isArray(c.teacher_ids)||c.teacher_ids.length>50||new Set(c.teacher_ids).size!==c.teacher_ids.length||c.teacher_ids.some(w=>!staff.get(w)?.active||!staff.get(w)?.roles.includes('teacher')))fail();localClasses.add(c.local_id);
 }
 for(const l of list(m.lessons,30000)){
  exact(l,'key class_local_id class_name teacher date kind start_time end_time expected arrived leave_count needs_review members');
  if(!text(l.key)||!l.key||lessonKeys.has(l.key)||!local(l.class_local_id)||['class_name','teacher','date','kind','start_time','end_time'].some(k=>!text(l[k]))||['expected','arrived','leave_count'].some(k=>!integer(l[k])||l[k]<0)||typeof l.needs_review!=='boolean')fail();lessonKeys.add(l.key);
  const members=new Set();for(const s of list(l.members,10000)){
   exact(s,'student_local_id name external_ref remaining_units expected status attendance_time attendance_method makeup');
   if(!local(s.student_local_id)||members.has(s.student_local_id)||['name','external_ref','attendance_time','attendance_method'].some(k=>!text(s[k]))||!integer(s.remaining_units)||typeof s.expected!=='boolean'||!['arrived','missing','leave'].includes(s.status)||typeof s.makeup!=='boolean')fail();members.add(s.student_local_id);
  }
  if(l.members.filter(s=>s.status==='arrived').length!==l.arrived)fail();
 }
 for(const w of list(m.covered_weeks,262)){exact(w,'start end');if(!/^\d{4}-\d{2}-\d{2}$/.test(w.start)||!/^\d{4}-\d{2}-\d{2}$/.test(w.end))fail();}
 return d;
}
function wrapper(options,helper){
 validateOptions(options);if(crypto.createHash('sha256').update(helper).digest('hex')!==HELPER_SHA256)throw Error('Read-only management helper fingerprint changed');
 const config=Buffer.from(JSON.stringify({database:options.database,organization:options.organization})).toString('base64');
 return `import base64,hashlib,json,sys\nfrom pathlib import Path\nraw=base64.b64decode('${helper.toString('base64')}')\nassert hashlib.sha256(raw).hexdigest()=='${HELPER_SHA256}'\nnamespace={'__name__':'read_only_management_review'}\nexec(compile(raw,'<management-review>','exec'),namespace)\nconfig=json.loads(base64.b64decode('${config}'))\ntry:\n data=namespace['snapshot'](Path(config['database']),config['organization'])\n payload=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')\n print(json.dumps({'payload_base64':base64.b64encode(payload).decode('ascii'),'sha256':hashlib.sha256(payload).hexdigest()},separators=(',',':')))\nexcept Exception as e: print('Read-only export stopped: '+type(e).__name__,file=sys.stderr);sys.exit(1)\n`;
}
function decode(raw,organization){
 try{
 const e=JSON.parse(raw);exact(e,'payload_base64 sha256');
 if(typeof e.payload_base64!=='string'||e.payload_base64.length>MAX_BYTES||!/^[A-Za-z0-9+/]*={0,2}$/.test(e.payload_base64)||!/^[a-f0-9]{64}$/.test(e.sha256))fail();
 const bytes=Buffer.from(e.payload_base64,'base64');if(bytes.toString('base64')!==e.payload_base64||crypto.createHash('sha256').update(bytes).digest('hex')!==e.sha256)fail();
 return {data:validateSource(JSON.parse(new TextDecoder('utf8',{fatal:true}).decode(bytes)),organization),bytes,sha256:e.sha256};
 }catch(_){throw Error('Management review envelope, encoding or schema verification failed');}
}
async function read(options,{spawnImpl=spawn}={}){
 validateOptions(options);const script=wrapper(options,fs.readFileSync(path.join(__dirname,'export-management-review.py')));
 const repo=fs.realpathSync(path.resolve(__dirname,'../..'));
 const inside=p=>{const r=path.relative(repo,p);return !r||r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
 if(inside(path.resolve(options.outputDir)))throw Error('Private review output must be outside the repository');
 fs.mkdirSync(options.outputDir,{recursive:true,mode:0o700});if(fs.lstatSync(options.outputDir).isSymbolicLink())throw Error('Use a private ordinary output directory');
 if(inside(fs.realpathSync(options.outputDir)))throw Error('Private review output resolves inside the repository');
 const raw=await new Promise((resolve,reject)=>{let chunks=[],size=0;const child=spawnImpl('ssh',['-p',String(options.port),'-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','NumberOfPasswordPrompts=2',options.user+'@'+options.host,'python3 -'],{stdio:['pipe','pipe','inherit'],windowsHide:false});
 child.on('error',()=>reject(Error('SSH could not start')));child.stdin.on('error',()=>reject(Error('SSH input ended')));child.stdout.on('data',part=>{size+=part.length;if(size>MAX_BYTES){child.kill();reject(Error('Review output limit exceeded'));}else chunks.push(part);});child.on('close',code=>code===0?resolve(Buffer.concat(chunks).toString('utf8')):reject(Error('Read-only source export failed; nothing imported')));child.stdin.end(script);});
 const verified=decode(raw,options.organization),name='management-review-'+new Date().toISOString().replace(/[:.]/g,'-')+'-'+crypto.randomBytes(4).toString('hex')+'.private.json',target=path.join(options.outputDir,name);
 fs.writeFileSync(target,verified.bytes,{flag:'wx',mode:0o600});fs.writeFileSync(target+'.sha256',verified.sha256+'  '+name+'\n',{flag:'wx',mode:0o600});
 return {path:target,sha256:verified.sha256,classes:verified.data.classes.length,base_students:verified.data.students.length,archives:verified.data.archived_students.length,staff:verified.data.staff.length,mirror_present:verified.data.mirror.present,mirror_revision:verified.data.mirror.revision,source_access:'read_only',server_modified:false,contains_private_data:true};
}
module.exports={HELPER_SHA256,validateSource,wrapper,decode,read};
if(require.main===module){(async()=>{
 const{o,connect}=parseOptions(process.argv.slice(2));
 if(!connect){console.log('Management source review: provide explicit SSH/database/organization/output options and --connect. Read only; no import or deployment.');return;}
 console.log('本次只读核对当前基础名册、归档、账号任课与网页镜像，不导入、不部署、不修改权限。');
 console.log('请在SSH提示处输入服务器密码；密码不会保存在文件或参数中。');
 const result=await read(o);console.log(JSON.stringify(result,null,2));console.log('完整核对包已保存，请回聊天告知；未修改服务器或平板。');
})().catch(e=>{console.error('STOP: '+e.message);process.exitCode=1;});}
