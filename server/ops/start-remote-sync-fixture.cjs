'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const digest=b=>crypto.createHash('sha256').update(b).digest('hex');
function load(file){
 const config=JSON.parse(fs.readFileSync(file,'utf8'));
 if(!/^[a-zA-Z0-9.-]{1,253}$/.test(config.host)||!/^[a-z_][a-z0-9_-]{0,31}$/.test(config.user)||config.port!==22||config.remote_port!==18183||!/^\/opt\/wxnotify\/app\/roster-review-v0\.5\.0-[a-f0-9]{8}-[0-9]{14}-[a-f0-9]{8}\/release$/.test(config.release)||!path.isAbsolute(config.repository))throw Error('Invalid remote synthetic fixture configuration');
 if(!path.isAbsolute(config.temp_directory)||!fs.statSync(config.temp_directory).isDirectory()||!/^[A-Za-z0-9_-]{4,64}$/.test(config.serial))throw Error('Invalid explicit tablet or temporary directory');
 const keys=['supervisor','fixture','receipt_loss','driver','starter','apk'];
 for(const key of keys){const f=config[key];if(!f||!path.isAbsolute(f.path)||!/^[a-f0-9]{64}$/.test(f.sha256))throw Error('Invalid fixture input');const st=fs.lstatSync(f.path);if(!st.isFile()||st.isSymbolicLink()||st.size>(key==='apk'?100*1024*1024:512*1024)||digest(fs.readFileSync(f.path))!==f.sha256)throw Error('Fixture input changed: '+key);}
 return config;
}
function messages(config){
 const stamp=new Date().toISOString().replace(/[^0-9]/g,'').slice(0,14),work='/opt/wxnotify/app/sync-validation-'+stamp+'-'+crypto.randomBytes(4).toString('hex');
 const data={release:config.release,work,port:config.remote_port,files:[['fixture','serve_sync_pilot_fixture.py'],['receipt_loss','serve_tablet_fixture.py']].map(([k,name])=>({name,sha256:config[k].sha256,base64:fs.readFileSync(config[k].path).toString('base64')}))};
 return [fs.readFileSync(config.supervisor.path).toString('base64')+'\n',Buffer.from(JSON.stringify(data)).toString('base64')+'\n'];
}
function start(file,localPort,{spawnImpl=spawn}={}){
 const config=load(file);if(!Number.isInteger(localPort)||localPort<1024||localPort>65535)throw Error('Invalid local forward port');
 const boot='import base64,sys;exec(compile(base64.b64decode(sys.stdin.buffer.readline()),"<sync-validation>","exec"),{"__name__":"__main__"})';
 const quoted="'"+boot.replace(/'/g,"'\"'\"'")+"'";
 const child=spawnImpl('ssh',['-p',String(config.port),'-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-T','-L','127.0.0.1:'+localPort+':127.0.0.1:'+config.remote_port,config.user+'@'+config.host,'python3 -u -c '+quoted],{windowsHide:true,stdio:['pipe','pipe','inherit']});
 child.stdin.on('error',()=>{});for(const line of messages(config))child.stdin.write(line);return child;
}
module.exports={load,messages,start};
