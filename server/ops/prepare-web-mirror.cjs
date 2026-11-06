'use strict';
// Build local private artifacts. The raw device token exists only in memory and in RSA ciphertext.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),out=path.resolve(repo,'../../outputs'),privateDir=path.join(out,'private-web-mirror-2026-10-29'),ready=path.join(out,'tablet-web-sync-2026-10-29-ready');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex'),file=p=>({path:p,sha256:sha(fs.readFileSync(p))});
function main(){
 if(process.argv.slice(2).join()!=='--prepare')throw Error('Explicit --prepare required');if(fs.existsSync(path.join(ready,'mirror-deploy.private.json')))throw Error('Prepared enrollment exists; do not regenerate');
 const request=JSON.parse(fs.readFileSync(path.join(privateDir,'daily-device-request.json')));if(request.protocol!==1||!/^[a-f0-9]{32}$/.test(request.nonce))throw Error('Daily device request differs');
 const publicKey=crypto.createPublicKey({key:Buffer.from(request.public_key,'base64'),format:'der',type:'spki'});if(publicKey.asymmetricKeyType!=='rsa'||publicKey.asymmetricKeyDetails.modulusLength!==3072)throw Error('Device public key differs');
 const original=JSON.parse(fs.readFileSync(path.join(out,'teacher-web-2026-10-29-ready/teacher-web-deploy.private.json'))),accepted=JSON.parse(fs.readFileSync(path.join(out,'private-teacher-web-2026-10-29/role-ui-acceptance.json')));
 const previous=Object.fromEntries(Object.entries(original.files).map(([n,f])=>[n,f.sha256]));Object.assign(previous,accepted.new_files);if(Object.keys(previous).length!==8||accepted.ui_version!==3)throw Error('Current source baseline differs');
 fs.mkdirSync(ready,{recursive:true});const names=['teacher_web/api.py','teacher_web/web/workspace.js','teacher_web/mirror_store.py','teacher_web/mirror_gateway.py'],files={};
 for(const n of names){const p=path.join(ready,'source',n);fs.mkdirSync(path.dirname(p),{recursive:true});fs.copyFileSync(path.join(repo,'server',n),p);files[n]=file(p)}
 for(const name of ['install-web-mirror.py','deploy-web-mirror.cjs'])fs.copyFileSync(path.join(__dirname,name),path.join(ready,name));
 const token='wxd_'+crypto.randomBytes(32).toString('base64url'),id=crypto.randomBytes(16).toString('hex'),pin=fs.readFileSync(path.join(repo,'app/src/main/res/raw/web_mirror_server_spki.txt'),'utf8').trim();
 const profile=Buffer.from(JSON.stringify({e:'https://47.97.226.219:18083',t:token,d:id,p:pin,n:request.nonce}));if(profile.length>318)throw Error('RSA profile size');
 const pub=path.join(privateDir,'daily-device-public.der');fs.writeFileSync(pub,Buffer.from(request.public_key,'base64'));
 const java=path.resolve(repo,'../android-toolchain/jdk/jdk-17.0.20.1+1/bin/java.exe'),ciphertext=execFileSync(java,[path.join(__dirname,'MirrorCrypto.java'),'wrap',pub],{input:profile,encoding:'utf8',windowsHide:true}).trim();profile.fill(0);
 fs.writeFileSync(path.join(privateDir,'web-mirror-connection.enc.json'),JSON.stringify({ciphertext}),{flag:'wx',mode:0o600});
 const config={scope:{work_directory:'/opt/wxnotify/app/teacher-web-20261029101834-42da0325',source_sha256:accepted.source_sha256,previous_files:previous,device:{id,token_digest:sha(token)}},installer:file(path.join(ready,'install-web-mirror.py')),files,tls:Object.fromEntries(['server.crt','server.key'].map(n=>[n,file(path.join(privateDir,n))])),output_dir:privateDir};
 fs.writeFileSync(path.join(ready,'mirror-deploy.private.json'),JSON.stringify(config,null,2)+'\n',{flag:'wx',mode:0o600});
 const node=process.execPath;fs.writeFileSync(path.join(ready,'UPDATE-TABLET-SYNC.cmd'),'@echo off\r\nchcp 65001 >nul\r\n"'+node+'" "%~dp0deploy-web-mirror.cjs"\r\nif errorlevel 1 echo Update did not complete. Keep this window and report the result.\r\npause\r\n','ascii');
 console.log(JSON.stringify({prepared:true,device_id:id,profile_encrypted:true,raw_token_saved:false,ready_directory:ready}));
}
if(require.main===module)main();
