'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events'),{spawnSync}=require('node:child_process');
const client=require('../ops/start-remote-sync-fixture.cjs'),root=path.resolve(__dirname,'..'),base=path.resolve(os.tmpdir()),temp=fs.mkdtempSync(path.join(base,'remote-sync-client-'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
try{
 const c={host:'example.invalid',user:'fixture-deploy',port:22,remote_port:18183,serial:'FAKE1234',release:'/opt/wxnotify/app/roster-review-v0.5.0-aabbccdd-20261027065406-aabbccdd/release',repository:temp,temp_directory:temp};
 for(const key of ['supervisor','fixture','receipt_loss','driver','starter','apk']){const file=path.join(temp,key);const payload=key==='supervisor'?fs.readFileSync(path.join(root,'ops/remote-sync-fixture.py')):Buffer.from('# fictional '+key+'\n');fs.writeFileSync(file,payload);c[key]={path:file,sha256:sha(payload)}}
 const config=path.join(temp,'remote-sync-config.private.json');fs.writeFileSync(config,JSON.stringify(c));
 const [program,encoded]=client.messages(client.load(config));assert.equal(program.split('\n').length,2);const supervisor=Buffer.from(program.trim(),'base64').toString('utf8');const data=JSON.parse(Buffer.from(encoded.trim(),'base64'));
 assert.match(data.work,/^\/opt\/wxnotify\/app\/sync-validation-[0-9]{14}-[a-f0-9]{8}$/);assert.equal(data.port,18183);assert.deepEqual(data.files.map(f=>f.name),['serve_sync_pilot_fixture.py','serve_tablet_fixture.py']);assert(!supervisor.includes('pilot.sqlite'));assert(!JSON.stringify(data).includes('password'));
 const syntax=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c','import ast,sys;ast.parse(sys.stdin.read());print("ok")'],{input:supervisor,encoding:'utf8'});assert.equal(syntax.status,0,syntax.stderr);
 const writes=[];let calls=0;
 const fake=(command,args,options)=>{calls++;assert.equal(command,'ssh');assert(args.includes('StrictHostKeyChecking=yes'));assert(args.includes('ExitOnForwardFailure=yes'));assert(args.includes('127.0.0.1:12345:127.0.0.1:18183'));assert.equal(options.stdio[2],'inherit');assert(!args.join(' ').includes('password'));const child=new EventEmitter();child.stdin=new EventEmitter();child.stdin.write=s=>writes.push(s);child.stdout=new EventEmitter();return child};
 client.start(config,12345,{spawnImpl:fake});assert.equal(calls,1);assert.equal(writes.length,2);assert(writes.every(s=>s.endsWith('\n')));
 fs.appendFileSync(c.fixture.path,'changed');assert.throws(()=>client.load(config),/changed/);
 console.log('Remote sync client passed: pinned six inputs, new private synthetic path, framed Python syntax, one mocked strict-host-key loopback SSH, stdin kept open for disconnect cleanup, no real DB path or password arguments, tamper refusal. No network.');
}finally{if(!path.resolve(temp).startsWith(base+path.sep+'remote-sync-client-'))throw Error('Unsafe cleanup');fs.rmSync(temp,{recursive:true,force:true});}
