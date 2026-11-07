'use strict';
// User-run launcher: installs only the isolated package, delegates SSH auth to ssh, cleans it afterwards.
const fs=require('node:fs'),path=require('node:path'),{spawn,execFileSync}=require('node:child_process');
const {load}=require('./start-remote-sync-fixture.cjs');
const PACKAGE='cn.mashangchengzhang.studentmanager.synctest',PRODUCTION='cn.mashangchengzhang.studentmanager';
function configAt(dir){return path.join(dir,'remote-sync-config.private.json');}
function metadata(text){return Object.fromEntries(['versionCode','versionName','dataDir','firstInstallTime','lastUpdateTime'].map(key=>{const m=text.match(new RegExp('(?:^|\\n)\\s*'+key+'=([^\\r\\n]+)'));return[key,m?m[1].trim():'']}));}
async function run(dir){
 const file=configAt(dir),config=load(file),root=config.repository;
 if(!/^[A-Za-z0-9_-]{4,64}$/.test(config.serial))throw Error('Invalid explicitly selected tablet');
 const adb=path.resolve(root,'../android-toolchain/sdk/platform-tools/adb.exe');
 const command=args=>execFileSync(adb,['-s',config.serial,...args],{encoding:'utf8',windowsHide:true,timeout:120000}).trim();
 if(command(['get-state'])!=='device')throw Error('请重新连接原平板并授权USB调试。');
 if(command(['shell','pm','list','packages',PACKAGE]).split(/\r?\n/).some(s=>s==='package:'+PACKAGE))throw Error('已有独立测试包，请先让 Codex 核对，不覆盖已有测试资料。');
 const badging=execFileSync(path.resolve(root,'../android-toolchain/sdk/build-tools/35.0.0/aapt2.exe'),['dump','badging',config.apk.path],{encoding:'utf8',windowsHide:true,timeout:30000});
 if(!badging.includes("package: name='"+PACKAGE+"'")||!badging.includes("versionCode='77'"))throw Error('测试APK包名或版本不符。');
 const before=metadata(command(['shell','dumpsys','package',PRODUCTION]));
 if(!before.versionName||!before.dataDir.endsWith('/'+PRODUCTION))throw Error('日常App元数据无法确认，停止测试。');
 let installed=false,passed=false,child;
 try{
  console.log('正在安装独立同步测试App，日常App保持原样。');
  if(!command(['install','-r',config.apk.path]).includes('Success'))throw Error('独立测试包未安装成功');installed=true;
  console.log('远程链路仅使用临时虚构资料。出现 SSH password 提示后输入服务器密码，保持窗口与USB连接。');
  const code=await new Promise((resolve,reject)=>{child=spawn(process.execPath,[config.driver.path],{cwd:root,windowsHide:true,stdio:'inherit',env:{...process.env,TABLET_SERIAL:config.serial,SYNC_REMOTE_CONFIG:file,TEMP:config.temp_directory,TMP:config.temp_directory}});child.on('error',reject);child.on('close',resolve)});
  if(code!==0)throw Error('远程联调尚未全部通过；请保留窗口结果。');passed=true;
 }finally{
  if(child&&child.exitCode===null)child.kill();
  if(installed){const info=command(['shell','dumpsys','package',PACKAGE]);if(!info.includes('4.10.2-lesson-duration-isolated-test'))throw Error('测试包版本变化，未自动移除，请核对。');if(!command(['uninstall',PACKAGE]).includes('Success'))throw Error('独立测试包清理未确认成功');}
  const after=metadata(command(['shell','dumpsys','package',PRODUCTION]));if(JSON.stringify(before)!==JSON.stringify(after))throw Error('日常App安装元数据发生变化，需要核对。');
 }
 if(passed){
  const reports=path.resolve(root,'../../outputs/sync-pilot-2026-10-27'),names=fs.readdirSync(reports).filter(n=>/^remote-physical-.*\.json$/.test(n)).sort();
  if(!names.length)throw Error('未找到远程实测回执。');
  const acceptance=JSON.parse(fs.readFileSync(path.join(reports,names.at(-1)),'utf8'));
  if(acceptance.remote_synthetic_fixture!==true||acceptance.remote_fixture_closed!==true||acceptance.production_database_accessed!==false)throw Error('远程测试或清理状态尚未全部确认。');
  const result={...acceptance,isolated_app_removed:true,production_install_metadata_unchanged:true,production_install_metadata:before};
  const target=path.join(reports,'remote-run-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json');fs.writeFileSync(target,JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
  console.log('远程同步联调全部通过；临时服务已停止，独立测试App已移除，日常App未更新。');console.log('结果已保存：'+target);
 }
}
module.exports={metadata};
if(require.main===module){(async()=>{const args=process.argv.slice(2);if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported arguments');const c=load(configAt(__dirname));if(args[0]==='--self-check'){console.log(JSON.stringify({launcher:'ok',checked_files:6,device:c.serial,synthetic_only:true,network_connected:false}));return;}await run(__dirname)})().catch(e=>{console.error('未完成：'+e.message);console.error('请保留结果，不要改动日常App或重复签到。');process.exitCode=1});}
