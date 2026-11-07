'use strict';
const fs=require('node:fs'),path=require('node:path'),https=require('node:https'),http=require('node:http'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),dir=path.resolve(repo,'../../outputs/private-web-mirror-2026-10-29'),pkg='cn.mashangchengzhang.studentmanager';
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function probe(tls){return new Promise(resolve=>{const options={host:'47.97.226.219',port:18083,path:'/viewer/health',method:'GET',timeout:10000,...(tls?{ca:fs.readFileSync(path.join(dir,'server.crt')),rejectUnauthorized:true}:{})};const q=(tls?https:http).request(options,r=>{let out='';r.on('data',b=>{out+=b;if(out.length>32768)q.destroy()});r.on('end',()=>{let h={};try{h=JSON.parse(out)}catch(_){}resolve({status:r.statusCode,ready:h.status==='ready',mirror_tls_enabled:h.mirror_tls_enabled===true,gateway_sha256:h.gateway_sha256||null})})});q.on('error',e=>resolve({error:e.code||e.name}));q.on('timeout',()=>q.destroy(Object.assign(Error('timeout'),{code:'TIMEOUT'})));q.end()})}
(async()=>{const result={checked_at:new Date().toISOString(),http:await probe(false),tls:await probe(true),passwords_used:false,student_data_read:false};
 try{const adb=path.resolve(repo,'../android-toolchain/sdk/platform-tools/adb.exe'),cmd=a=>execFileSync(adb,['-s','HVA13XVR',...a],{encoding:'utf8',windowsHide:true,timeout:30000}).trim();
 const original=JSON.parse(fs.readFileSync(path.join(dir,'daily-device-request.json'))),current=JSON.parse(cmd(['shell','cat','/sdcard/Android/data/'+pkg+'/files/web-mirror-request.json']));result.device_request_unchanged=JSON.stringify(original)===JSON.stringify(current);
 try{result.encrypted_file_matches=hash(Buffer.from(cmd(['shell','cat','/sdcard/Android/data/'+pkg+'/files/web-mirror-connection.enc.json'])))===hash(fs.readFileSync(path.join(dir,'web-mirror-connection.enc.json')))}catch(_){result.encrypted_file_present=false}
 }catch(_){result.usb_check='not_available'}
 const target=path.join(dir,'connection-diagnostic.json');fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n');console.log(result.tls.ready?'服务器加密连接正常。':'服务器加密连接待检查：'+(result.tls.error||result.tls.status));console.log('检查完成，请回聊天告知；诊断已保存在本机，不含密码或学生资料。');
})().catch(e=>{console.error('诊断未完成：'+e.name);process.exitCode=1});
