'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {EventEmitter}=require('node:events'),{Writable,PassThrough}=require('node:stream');
const {load,wrapper,run}=require('../ops/run-management-rehearsal.cjs');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'rehearsal-runner-'));
 t.after(()=>{const actual=fs.realpathSync(root),base=fs.realpathSync(os.tmpdir());assert(actual.startsWith(base+path.sep+'rehearsal-runner-'));fs.rmSync(actual,{recursive:true,force:true});});
 const review=Buffer.from(JSON.stringify({organization:'fictional'})),archive=Buffer.from('{}');
 const proposal=Buffer.from(JSON.stringify({organization:'fictional',activation_authorized:false,proposal_only:true,baseline:{server_review_sha256:sha(review),history_archive_sha256:sha(archive)}}));
 const plan={format:'future-school-remote-rehearsal-v1',options:{host:'example.invalid',user:'fixture-deploy',port:22,database:'/opt/wxnotify/app/fictional/business.sqlite',organization:'fictional',outputDir:path.join(root,'receipts')}};
 for(const[name,raw]of Object.entries({code:Buffer.from('fictional code placeholder'),plan:proposal,archive,review})){const file=path.join(root,name+'.fixture');fs.writeFileSync(file,raw);plan[name]={path:file,sha256:sha(raw)};}
 const target=path.join(root,'operator.private.json');fs.writeFileSync(target,JSON.stringify(plan));return{root,target,plan};
}
function receipt(){return{format:'future-school-initialization-rehearsal-v1',rehearsal_only:true,activation_authorized:false,source_written:false,events_replayed:0,accounts_preserved:true,legacy_applications_preserved:true,
 scope:{active_students:366,archived_students:159,current_classes:43},history_counts:{classes:43,students:525,attendance:577,transactions:580,leaves:5,extra_lessons:0,class_reschedules:0,student_reschedules:3},copy_sha256:'a'.repeat(64),status:'ok',remote_directory:'/opt/wxnotify/app/.management-rehearsal-fixture_1',boundary_sha256:'b'.repeat(64),service_switched:false,tablet_modified:false};}
function fake(result,code,inspect=()=>{}){return(command,args,options)=>{
 const child=new EventEmitter();child.stdout=new PassThrough();child.kill=()=>{};let source='';
 child.stdin=new Writable({write(part,_,done){source+=part.toString();done();},final(done){inspect({command,args,options,source});process.nextTick(()=>{child.stdout.write(JSON.stringify(result));child.emit('close',code);});done();}});return child;
};}
test('self check pins all four files and rejects changed inputs and shell-like connection fields',t=>{
 const f=fixture(t);assert.equal(Object.keys(load(f.target).payloads).length,4);
 fs.appendFileSync(f.plan.archive.path,' ');assert.throws(()=>load(f.target),/changed/);
 f.plan.options.host='example.invalid;bad';fs.writeFileSync(f.target,JSON.stringify(f.plan));assert.throws(()=>load(f.target),/Explicit/);
});
test('one strict SSH call returns only a bounded receipt, never a credential or private full database',async t=>{
 const f=fixture(t);let inspected=false;
 const result=await run(f.target,{spawnImpl:fake(receipt(),0,({command,args,options,source})=>{
  inspected=true;assert.equal(command,'ssh');assert(args.includes('StrictHostKeyChecking=yes'));assert.equal(args.at(-1),'python3 -');assert.deepEqual(options.stdio,['pipe','pipe','inherit']);assert(!args.join(' ').includes('password'));assert(source.includes('rehearse('));assert(!source.includes('systemctl'));assert(!source.includes('sudo'));
 })});
 assert(inspected);assert.equal(result.scope.active_students,366);assert(fs.existsSync(result.path));assert(!fs.readFileSync(result.path,'utf8').includes('base64'));
});
test('unexpected returned fields and changed source stop without saving a successful receipt',async t=>{
 const f=fixture(t);await assert.rejects(run(f.target,{spawnImpl:fake({...receipt(),token:'PRIVATE'},0)}),/Unexpected/);
 await assert.rejects(run(f.target,{spawnImpl:fake({status:'stopped',source_changed:true},1)}),/资料已有变化/);
 assert.deepEqual(fs.readdirSync(f.plan.options.outputDir),[]);
});
test('generated remote Python is syntax checked without running an SSH session',t=>{
 const f=fixture(t),source=wrapper(load(f.target));
 const python=path.resolve(__dirname,'../.venv/Scripts/python.exe');
 if(!fs.existsSync(python)){t.skip('Local Python path not present');return;}
 require('node:child_process').execFileSync(python,['-c','import ast,sys; ast.parse(sys.stdin.read())'],{input:source});
});
