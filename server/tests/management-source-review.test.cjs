'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),{EventEmitter}=require('node:events'),{spawnSync}=require('node:child_process');
const reader=require('../ops/read-management-review.cjs'),server=path.resolve(__dirname,'..');
function source(){const c='c'.repeat(32),s='a'.repeat(32),t='d'.repeat(32);return{format:'future-school-management-review-v1',schema_version:1,complete:true,generated_at:new Date().toISOString(),organization:'fictional',org_id:'f'.repeat(32),source_access:'read_only',contains_private_data:true,
 classes:[{id:c,name:'Fictional Class',course_name:'Course',teachers:[{id:t,name:'Teacher'}]}],students:[{id:s,name:'Fictional Student',external_ref:'001',class_id:c,remaining_units:1000,active:true}],
 archive_available:true,archived_students:[{id:'b'.repeat(32),name:'Archive',external_ref:'002',remaining_units:2500,previous_class_name:''}],source_links_available:true,source_links:[{kind:'class',source_id:'11',entity_id:c,bundle_sha256:'a'.repeat(64)},{kind:'student',source_id:'22',entity_id:s,bundle_sha256:'a'.repeat(64)}],
 staff:[{id:t,login:'teacher',display_name:'Teacher',active:true,roles:['teacher']}],mirror:{present:true,revision:2,received_at:1000,source_sha256:'e'.repeat(64),as_of_date:'2026-11-01',source_captured_at:'Fictional source',source_records:1,device_active:true,device_expires_at:2000,
 classes:[{local_id:'11',name:'Fictional Class',course_name:'Course',teacher:'Teacher',teacher_ids:[t]}],lessons:[{key:'11:2026-11-01',class_local_id:'11',class_name:'Fictional Class',teacher:'Teacher',date:'2026-11-01',kind:'regular',start_time:'14:00',end_time:'16:00',expected:1,arrived:0,leave_count:0,needs_review:false,members:[{student_local_id:'22',name:'Fictional Student',external_ref:'001',remaining_units:900,expected:true,status:'missing',attendance_time:'',attendance_method:'',makeup:false}]}],covered_weeks:[{start:'2026-10-26',end:'2026-11-01'}]}};}
function envelope(d){const b=Buffer.from(JSON.stringify(d));return JSON.stringify({payload_base64:b.toString('base64'),sha256:crypto.createHash('sha256').update(b).digest('hex')});}
test('base, unassigned archive and current mirror balances remain distinct',()=>{
 const d=reader.decode(envelope(source()),'fictional').data;assert.equal(d.students[0].external_ref,'001');assert.equal(d.students[0].remaining_units,1000);assert.equal(d.mirror.lessons[0].members[0].remaining_units,900);assert.equal(d.archived_students[0].remaining_units,2500);assert.equal(d.archived_students[0].previous_class_name,'');
});
test('rejects private nested fields, wrong organization and broken identity links',()=>{
 const changes=[d=>d.staff[0].password_hash='SECRET',d=>d.archived_students[0].contact='SECRET',d=>d.mirror.lessons[0].members[0].face='SECRET',d=>d.source_links[0].entity_id='0'.repeat(32),d=>d.mirror.classes[0].teacher_ids=['0'.repeat(32)],d=>d.organization='other'];
 for(const change of changes){const d=source();change(d);assert.throws(()=>reader.decode(envelope(d),'fictional'),e=>!e.message.includes('SECRET'));}
});
test('explicit missing mirror differs from incomplete or truncated export',()=>{
 const d=source();d.mirror={present:false,revision:null,received_at:null,source_sha256:null,as_of_date:null,source_captured_at:null,source_records:null,device_active:null,device_expires_at:null,classes:[],lessons:[],covered_weeks:[]};assert.equal(reader.decode(envelope(d),'fictional').data.mirror.present,false);
 d.complete=false;assert.throws(()=>reader.decode(envelope(d),'fictional'));d.complete=true;d.archive_available=false;assert.throws(()=>reader.decode(envelope(d),'fictional'));
});
test('checksum, standard base64 and UTF-8 are checked before schema or any saved output',()=>{
 const e=JSON.parse(envelope(source()));e.sha256='0'.repeat(64);assert.throws(()=>reader.decode(JSON.stringify(e),'fictional'));
 e.payload_base64='@@@@';assert.throws(()=>reader.decode(JSON.stringify(e),'fictional'));
 const bytes=Buffer.from([255,254]);assert.throws(()=>reader.decode(JSON.stringify({payload_base64:bytes.toString('base64'),sha256:crypto.createHash('sha256').update(bytes).digest('hex')}),'fictional'));
});
test('one mocked SSH read uses pinned helper, strict host identity and private output integrity',async()=>{
 const root=path.resolve(os.tmpdir()),dir=fs.mkdtempSync(path.join(root,'management-source-'));
 try{
  const options={host:'example.invalid',user:'deployment-test',port:22,database:'/opt/wxnotify/app/fictional/business.sqlite',organization:'fictional',outputDir:dir};let calls=0,script='';
  await assert.rejects(reader.read({...options,outputDir:path.resolve(server,'..')},{spawnImpl:()=>{throw Error('Must not connect');}}),/outside the repository/);
  const spawnImpl=(command,args,config)=>{calls++;assert.equal(command,'ssh');assert(args.includes('StrictHostKeyChecking=yes'));assert.equal(args.at(-1),'python3 -');assert.equal(config.stdio[2],'inherit');const child=new EventEmitter();child.stdout=new EventEmitter();child.stdin=new EventEmitter();child.stdin.end=s=>{script=s;queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(envelope(source())));child.emit('close',0);});};return child;};
  const result=await reader.read(options,{spawnImpl});assert.equal(calls,1);assert.equal(result.server_modified,false);assert.equal(result.archives,1);assert.equal(crypto.createHash('sha256').update(fs.readFileSync(result.path)).digest('hex'),result.sha256);assert(fs.readFileSync(result.path+'.sha256','utf8').startsWith(result.sha256));
  assert.throws(()=>reader.wrapper(options,Buffer.from('changed')),/fingerprint/);
  const python=process.env.PYTHON_BIN||path.join(server,'.venv/Scripts/python.exe');const checked=spawnSync(python,['-c','import ast,sys;ast.parse(sys.stdin.read());print("ok")'],{input:script,encoding:'utf8'});assert.equal(checked.status,0,checked.stderr);
 }finally{const resolved=path.resolve(dir);if(!resolved.startsWith(root+path.sep+'management-source-'))throw Error('Unsafe cleanup');fs.rmSync(resolved,{recursive:true,force:true});}
});
