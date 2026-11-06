'use strict';
// Local USB step only; no server login or plaintext device secret.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),out=path.resolve(repo,'../../outputs/private-web-mirror-2026-10-29'),pkg='cn.mashangchengzhang.studentmanager';
function main(){
 const adb=path.resolve(repo,'../android-toolchain/sdk/platform-tools/adb.exe'),cmd=a=>execFileSync(adb,['-s','HVA13XVR',...a],{encoding:'utf8',windowsHide:true,timeout:30000}).trim();
 if(cmd(['get-state'])!=='device')throw Error('请连接原平板并允许 USB 调试');
 if(!/versionName=4\.11\.[01]-web-sync\b/.test(cmd(['shell','dumpsys','package',pkg])))throw Error('平板 App 版本不一致，停止传送');
 const expected=JSON.parse(fs.readFileSync(path.join(out,'daily-device-request.json'))),actual=JSON.parse(cmd(['shell','cat','/sdcard/Android/data/'+pkg+'/files/web-mirror-request.json']));
 if(JSON.stringify(expected)!==JSON.stringify(actual))throw Error('平板连接申请已变化，请先核对');
 const file=path.join(out,'web-mirror-connection.enc.json'),raw=fs.readFileSync(file);if(raw.length>4096||Object.keys(JSON.parse(raw)).join()!=='ciphertext')throw Error('连接文件范围不符');
 const installed=fs.readdirSync(out).filter(n=>/^mirror-installed-.*\.json$/.test(n));if(!installed.length)throw Error('服务器部署回执尚未就绪');
 const receipt=JSON.parse(fs.readFileSync(path.join(out,installed.sort().at(-1))));if(receipt.tls_verified!==true||receipt.accounts_preserved!==true)throw Error('服务器部署尚未通过');
 cmd(['push',file,'/sdcard/Android/data/'+pkg+'/files/web-mirror-connection.enc.json']);
 fs.writeFileSync(path.join(out,'usb-connection-delivered.json'),JSON.stringify({delivered_at:new Date().toISOString(),serial:'HVA13XVR',package:pkg,encrypted_file_sha256:crypto.createHash('sha256').update(raw).digest('hex'),device_id:receipt.device_id,raw_token_saved:false},null,2)+'\n');
 console.log('加密连接文件已送到平板。请在日常 App 后台“设置 → 网页自动同步”点“连接网页”，再点“验证并启用”。');
 console.log('看到“网页已更新”后回到聊天告知结果。若提示连接未通过，请保留提示；不要重装或清除 App。');
}
if(require.main===module)try{main()}catch(e){console.error(e.message);process.exitCode=1}
